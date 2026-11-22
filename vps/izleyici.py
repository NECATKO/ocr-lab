#!/usr/bin/env python3
"""VPS izleyicisi: Drive'daki `ocr-lab/input/` klasörünü yoklar, yeni dosya varsa Colab'da GPU açıp okutur.

systemd zamanlayıcısı birkaç dakikada bir bu betiği çalıştırır (bkz. vps/kur.sh). VPS'te GPU ya da OCR modeli
gerekmez: Drive erişimi rclone ile, GPU Google'ın Colab komut satırı aracıyla (`colab run`) sağlanır.
Yalnız standart kitaplık kullanır.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from colab import runner  # noqa: E402  (yalnız standart kitaplık kullanır)

SABLON = Path(__file__).with_name("colab_is.py")
SONUC_ISARETI = "OCR-VPS-SONUC"
HAZIR_ISARETI = "Session READY"
GERI_CEKILME = 30 * 60          # GPU açılamazsa yeniden denemeden önce beklenen süre (sn)
EN_UZUN_IS = 4 * 3600           # tek bir Colab işinin üst sınırı (sn)
SAKLANAN_KAYIT = 20
EN_FAZLA_KOPMA = 2              # iş yarıda kalırsa (bağlantı koptu vb.) dosya bu kadar kez denenir


@dataclass(frozen=True)
class Girdi:
    ad: str
    boyut: int
    zaman: str

    @property
    def kimlik(self) -> str:
        return f"{self.boyut}:{self.zaman}"


def calistir_komut(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def girdileri_listele(uzak: str, _run=calistir_komut) -> list[Girdi]:
    res = _run(["rclone", "lsf", "--files-only", "--format", "pst", "--separator", "\t", f"{uzak}/input"])
    if res.returncode != 0:
        raise RuntimeError(f"rclone lsf başarısız: {res.stderr.strip()[-500:]}")
    girdiler = []
    for line in res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        ad, boyut, zaman = parts
        if ad.startswith(".") or Path(ad).suffix.lower() not in runner.IMAGE_EXTS | {".pdf"}:
            continue
        girdiler.append(Girdi(ad, int(boyut or 0), zaman))
    return sorted(girdiler, key=lambda g: g.ad)


def durum_oku(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    d.setdefault("basarisiz", {})
    d.setdefault("bekle_kadar", 0.0)
    d.setdefault("bildirildi", False)
    d.setdefault("kopmalar", {})
    return d


def durum_yaz(path: Path, durum: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(durum, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def secilecekler(girdiler: list[Girdi], durum: dict) -> list[Girdi]:
    """Daha önce başarısız olmuş ve o günden beri değişmemiş dosyalar yeniden denenmez (birim yakmasın)."""
    return [g for g in girdiler if durum["basarisiz"].get(g.ad) != g.kimlik]


def is_betigi(sablon: str, *, uzak: str, rclone_conf: str, dosyalar: list[str], repo: str, dal: str,
              model: str) -> str:
    ayar = {"uzak": uzak, "rclone_conf_b64": base64.b64encode(rclone_conf.encode()).decode(),
            "dosyalar": dosyalar, "repo": repo, "dal": dal, "model": model}
    return f"AYAR = {json.dumps(ayar, ensure_ascii=False)}\n" + sablon


def rclone_ayar_dosyasi(_run=calistir_komut) -> Path:
    res = _run(["rclone", "config", "file"])
    lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
    if res.returncode != 0 or not lines:
        raise RuntimeError("rclone ayar dosyası bulunamadı; önce `rclone config` ile Drive bağlantısı kurun")
    return Path(lines[-1])


def bildirim_konusu(uzak: str, _run=calistir_komut) -> str:
    res = _run(["rclone", "cat", f"{uzak}/bildirim.txt"])
    return runner.konu_ayikla(res.stdout) if res.returncode == 0 else ""


def colab_calistir(colab: list[str], betik: Path, *, oturum: str, gpu: str, log: Path,
                   en_uzun: float = EN_UZUN_IS) -> tuple[int, str]:
    """`colab run` çıktısını hem günlüğe hem ekrana akıtır. Süre aşılırsa önce Ctrl-C (CLI oturumu
    kapatsın), olmazsa öldürür ve oturumu adıyla ayrıca durdurur."""
    cmd = [*colab, "run", "-s", oturum, "--gpu", gpu, "--timeout", "0", str(betik)]
    print("Komut:", " ".join(cmd), flush=True)
    satirlar: list[str] = []
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, bufsize=1)
        assert proc.stdout is not None
        bitis = time.time() + en_uzun
        os.set_blocking(proc.stdout.fileno(), True)
        for line in proc.stdout:
            lf.write(line)
            lf.flush()
            print(line, end="", flush=True)
            satirlar.append(line)
            if time.time() > bitis:
                break
        if proc.poll() is None and time.time() > bitis:
            print("\nSüre aşıldı: Colab işi durduruluyor...", flush=True)
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=120)
            except subprocess.TimeoutExpired:
                proc.kill()
            subprocess.run([*colab, "stop", "-s", oturum], stdin=subprocess.DEVNULL)
        rc = proc.wait()
    return rc, "".join(satirlar)


def son_satirlar(metin: str, n: int = 12) -> str:
    return "\n".join(metin.strip().splitlines()[-n:])


def eski_kayitlari_sil(klasor: Path, sakla: int = SAKLANAN_KAYIT) -> None:
    for eski in sorted(klasor.glob("*.log"))[:-sakla]:
        eski.unlink(missing_ok=True)


def bir_tur(args: argparse.Namespace, *, _run=calistir_komut, _colab=colab_calistir, _bildir=runner.bildir,
            _now=time.time) -> int:
    durum_dosyasi = args.durum_dizini / "izleyici.json"
    durum = durum_oku(durum_dosyasi)
    if _now() < durum["bekle_kadar"]:
        print("GPU açılamadığı için bekleniyor; bu tur atlandı.")
        return 0

    girdiler = girdileri_listele(args.uzak, _run)
    adlar = {g.ad for g in girdiler}
    durum["basarisiz"] = {k: v for k, v in durum["basarisiz"].items() if k in adlar}   # silinenleri unut
    durum["kopmalar"] = {k: v for k, v in durum["kopmalar"].items() if k in adlar}
    secilen = secilecekler(girdiler, durum)
    if not secilen:
        durum_yaz(durum_dosyasi, durum)
        return 0

    print(f"Yeni girdi: {', '.join(g.ad for g in secilen)}", flush=True)
    konu = bildirim_konusu(args.uzak, _run)
    conf = rclone_ayar_dosyasi(_run).read_text(encoding="utf-8")
    betik_metni = is_betigi(SABLON.read_text(encoding="utf-8"), uzak=args.uzak, rclone_conf=conf,
                            dosyalar=[g.ad for g in secilen], repo=args.repo, dal=args.dal, model=args.model)
    kayitlar = args.durum_dizini / "kayitlar"
    kayitlar.mkdir(parents=True, exist_ok=True)
    zaman = time.strftime("%Y%m%d-%H%M%S")
    with tempfile.TemporaryDirectory(prefix="ocr-vps-") as tmp:
        betik = Path(tmp) / "colab_is.py"
        betik.write_text(betik_metni, encoding="utf-8")
        betik.chmod(0o600)                       # içinde Drive anahtarı var
        rc, cikti = _colab(args.colab, betik, oturum=f"ocr-vps-{zaman}", gpu=args.gpu,
                           log=kayitlar / f"{zaman}.log")
    eski_kayitlari_sil(kayitlar)

    if HAZIR_ISARETI not in cikti:
        durum["bekle_kadar"] = _now() + GERI_CEKILME
        if not durum["bildirildi"]:
            _bildir(konu, f"OCR: Colab {args.gpu} açılamadı",
                    f"{GERI_CEKILME // 60} dk sonra yeniden denenecek.\n{son_satirlar(cikti, 6)}",
                    etiketler=["warning"], oncelik=4)
            durum["bildirildi"] = True
        durum_yaz(durum_dosyasi, durum)
        return 1

    durum["bildirildi"] = False
    durum["bekle_kadar"] = 0.0
    tamamlandi = SONUC_ISARETI in cikti

    kalanlar = {g.ad: g for g in girdileri_listele(args.uzak, _run)}
    durum["basarisiz"] = {k: v for k, v in durum["basarisiz"].items() if k in kalanlar}
    durum["kopmalar"] = {k: v for k, v in durum["kopmalar"].items() if k in kalanlar}
    vazgecilen = []
    for g in secilen:
        if g.ad not in kalanlar or kalanlar[g.ad].kimlik != g.kimlik:
            continue
        if not tamamlandi:                  # iş yarıda kaldı: dosyanın suçu olmayabilir, bir tur sonra yeniden
            durum["kopmalar"][g.ad] = durum["kopmalar"].get(g.ad, 0) + 1
            if durum["kopmalar"][g.ad] < EN_FAZLA_KOPMA:
                print(f"İş yarıda kaldı, sonraki turda yeniden denenecek: {g.ad}", flush=True)
                continue
            vazgecilen.append(g.ad)
        durum["basarisiz"][g.ad] = g.kimlik
        durum["kopmalar"].pop(g.ad, None)
        print(f"İşlenemedi, değişene kadar yeniden denenmeyecek: {g.ad}", flush=True)
    if not tamamlandi and vazgecilen:
        _bildir(konu, "OCR: Colab işi yarıda kaldı",
                f"{', '.join(vazgecilen)} {EN_FAZLA_KOPMA} kez denendi; dosyayı yeniden yükleyince tekrar denenir.\n"
                + son_satirlar(cikti), etiketler=["x"], oncelik=4)
    durum_yaz(durum_dosyasi, durum)
    # İş tamamlandıysa (kontrol gereken sayfa olsa bile) tur başarılıdır; systemd yalnız altyapı hatasını görsün.
    return 0 if tamamlandi else 1


def ayristir(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--uzak", default=os.environ.get("OCR_VPS_UZAK", "gdrive:ocr-lab"),
                   help="rclone uzak yolu (varsayılan: gdrive:ocr-lab)")
    p.add_argument("--gpu", default=os.environ.get("OCR_VPS_GPU", "L4"))
    p.add_argument("--model", default=os.environ.get("OCR_VPS_MODEL", "dots.mocr"))
    p.add_argument("--repo", default=os.environ.get("OCR_VPS_REPO", "https://github.com/NECATKO/ocr-lab.git"))
    p.add_argument("--dal", default=os.environ.get("OCR_VPS_DAL", "main"))
    p.add_argument("--colab", type=shlex.split, default=shlex.split(os.environ.get("OCR_VPS_COLAB", "colab")),
                   help="Colab komut satırı aracı (varsayılan: colab)")
    p.add_argument("--durum-dizini", type=Path,
                   default=Path(os.environ.get("OCR_VPS_DURUM", Path.home() / ".local/state/ocr-lab")))
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = ayristir(argv)
    args.durum_dizini.mkdir(parents=True, exist_ok=True)
    with open(args.durum_dizini / "kilit", "w") as kilit:
        try:
            fcntl.flock(kilit, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Önceki tur hâlâ çalışıyor; çıkılıyor.")
            return 0
        return bir_tur(args)


if __name__ == "__main__":
    sys.exit(main())
