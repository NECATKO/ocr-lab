"""Google Colab + Google Drive çalışma yardımcıları.

Notebook (`colab/ocr_now.ipynb`) yalnızca form alanlarını taşır; asıl mantık buradadır.
Yerelde düzenleyip `git push` yaptığınızda Colab Hücre 2 `git pull` ile otomatik günceller.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
import traceback
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
DEFAULT_LOCAL_ROOT = Path("/content/ocr-lab")
DEFAULT_LOCAL_HF = Path("/content/hf_cache")
DEFAULT_WORK_DIR = Path("/content/work_input")
NTFY_URL = "https://ntfy.sh"
DRIVE_FALLBACK_URL = "https://drive.google.com/drive/my-drive"
CACHE_SYNC_SECONDS = 120


def drive_paths(drive_root: str | Path) -> dict[str, Path]:
    root = Path(drive_root)
    return {
        "root": root,
        "input": root / "input",
        "output": root / "output",
        "cache": root / "cache",
        "models": root / "colab-models",
        "kesme": root / "kesme.txt",
        "islenenler": root / "input" / "islenenler",
        "durum": root / "DURUM.txt",
        "bildirim": root / "bildirim.txt",
        "hf_tar": root / "colab-models" / "hf_cache.tar",
    }


def list_inputs(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        return []
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
        and (p.suffix.lower() == ".pdf" or p.suffix.lower() in IMAGE_EXTS)
    )


def prepare_drive(drive_root: str | Path) -> dict[str, Path]:
    """Drive klasör yapısını oluşturur ve `input/` içindeki bekleyen dosyaları listeler."""
    dp = drive_paths(drive_root)
    for key in ("input", "output", "cache", "models"):
        dp[key].mkdir(parents=True, exist_ok=True)
    files = list_inputs(dp["input"])
    print(f"Drive klasörü hazır: {dp['root']}")
    print(f"Girdi klasörü (input/): {len(files)} dosya")
    for f in files:
        print(f"  - {f.name}")
    if not files:
        print("\nPDF veya görsellerinizi Google Drive'da 'ocr-lab/input/' klasörüne yükleyin.")
    return dp


def unique_dest(folder: Path, name: str) -> Path:
    dest = folder / name
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 2
    while (folder / f"{stem}_{i}{suffix}").exists():
        i += 1
    return folder / f"{stem}_{i}{suffix}"


def _gpu_memory_gb() -> float | None:
    res = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
        capture_output=True, text=True,
    )
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        return float(res.stdout.strip().splitlines()[0]) / 1024.0
    except ValueError:
        return None


def _has_tesseract_tur() -> bool:
    if not shutil.which("tesseract"):
        return False
    res = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    return "tur" in res.stdout.splitlines()


def setup_env(
    drive_root: str | Path,
    *,
    model: str = "dots.mocr",
    local_root: str | Path = DEFAULT_LOCAL_ROOT,
    local_hf: str | Path = DEFAULT_LOCAL_HF,
) -> None:
    """Colab ortamını (tesseract, uv, .venv, model ağırlıkları, Drive yedeği) hazırlar."""
    dp = drive_paths(drive_root)
    root = Path(local_root)
    hf_dir = Path(local_hf)

    # GPU denetimi
    gpu_res = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        capture_output=True, text=True,
    )
    if gpu_res.returncode != 0 or not gpu_res.stdout.strip():
        print("UYARI: GPU bulunamadı! Yalnızca tesseract veya önbellekten dışa aktarma çalışır.")
    else:
        gpu_info = gpu_res.stdout.strip().splitlines()[0]
        print(f"GPU: {gpu_info}")
        if "T4" in gpu_info and model in ("dots.mocr", "deepseek-ocr2", "onerilenler"):
            print("UYARI: T4 GPU fp8 desteklemez! dots.mocr için Çalışma Zamanı -> L4 veya A100 seçin, "
                  "ya da glm-ocr/lightonocr kullanın.")

    # Sistem paketleri
    if not _has_tesseract_tur() or not shutil.which("rsync"):
        print("\n== tesseract-ocr-tur ve rsync kuruluyor...")
        subprocess.run(["apt-get", "update", "-qq"], check=True)
        subprocess.run(["apt-get", "install", "-y", "-qq", "tesseract-ocr", "tesseract-ocr-tur", "rsync"], check=True)

    # uv
    uv_bin = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    if not Path(uv_bin).exists():
        print("\n== uv kuruluyor...")
        subprocess.run("curl -LsSf https://astral.sh/uv/install.sh | sh", shell=True, check=True)
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = f"{local_bin}:{os.environ.get('PATH', '')}"

    # Ana Python ortamı (.venv)
    if not (root / ".venv/bin/ocr-now").exists():
        print("\n== Ana ortam (.venv) kuruluyor...")
        subprocess.run(["uv", "venv", str(root / ".venv"), "--python", "3.12", "-q"], check=True)
        subprocess.run(
            ["uv", "pip", "install", "-q", "--python", str(root / ".venv/bin/python"), "-e", str(root)],
            check=True,
        )

    # Hugging Face önbelleği: Drive'daki .tar arşivinden yerel SSD'ye aç (symlink'leri korur)
    hf_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_dir)
    hub_dir = hf_dir / "hub"

    had_local_hf = hub_dir.is_dir() and any(hub_dir.glob("models--*"))
    if not had_local_hf and dp["hf_tar"].is_file():
        size_gb = dp["hf_tar"].stat().st_size / 1e9
        print(f"\n== Model ağırlıkları Drive'dan yerel SSD'ye açılıyor ({size_gb:.1f} GB)...")
        subprocess.run(["tar", "-xf", str(dp["hf_tar"]), "-C", str(hf_dir)], check=True)

    before_models = {p.name for p in hub_dir.glob("models--*")} if hub_dir.is_dir() else set()

    # Seçilen model + vLLM kurulumu
    if model != "tesseract":
        install_cmd = [str(root / "ocr-now"), "models", "install"]
        if model != "onerilenler":
            install_cmd.append(model)
        print(f"\n== Model kuruluyor: {' '.join(install_cmd)}")
        subprocess.run(install_cmd, cwd=str(root), check=True)

    # Yeni model indirildiyse Drive arşivini güncelle
    after_models = {p.name for p in hub_dir.glob("models--*")} if hub_dir.is_dir() else set()
    if after_models and (after_models != before_models or not dp["hf_tar"].is_file()):
        print("\n== Model ağırlıkları sonraki oturumlar için Drive'a yedekleniyor...")
        tmp_tar = Path("/content/hf_cache.tar")
        subprocess.run(["tar", "-cf", str(tmp_tar), "-C", str(hf_dir), "hub"], check=True)
        shutil.move(str(tmp_tar), str(dp["hf_tar"]))
        print(f"Yedeklendi: {dp['hf_tar']} ({dp['hf_tar'].stat().st_size / 1e9:.1f} GB)")

    # kesme.txt eşitleme
    local_kesme = root / "ayarlar/kesme.txt"
    if dp["kesme"].is_file():
        shutil.copy2(dp["kesme"], local_kesme)
        print(f"Drive'daki kesme.txt yüklendi: {dp['kesme']}")
    elif local_kesme.is_file():
        shutil.copy2(local_kesme, dp["kesme"])
        print(f"Varsayılan kesme.txt Drive'a kopyalandı: {dp['kesme']}")

    print("\nKurulum tamam!")
    subprocess.run([str(root / "ocr-now"), "models"], cwd=str(root), check=True)


@dataclass
class OcrSonucu:
    rc: int
    rapor: dict | None
    yazilanlar: list[Path] = field(default_factory=list)
    hedefler: list[Path] = field(default_factory=list)


def _onbellek_esitle(src: Path, dst: Path) -> None:
    """Yalnız tamamlanmış önbellek dosyalarını kopyalar (`.ocr-*` yarım yazılmış geçici dosyalardır)."""
    subprocess.run(["rsync", "-rt", "--exclude=.ocr-*", f"{src}/", f"{dst}/"], check=True)


def ocr_calistir(
    drive_root: str | Path,
    *,
    dosya: str = "HEPSI",
    sayfalar: str = "hepsi",
    profil: str = "kitap",
    model: str = "dots.mocr",
    okuma: str = "duzen",
    formatlar: str = "docx txt",
    ilk_sayfa: int = 0,
    isaretle: bool = True,
    kesme: bool = True,
    dpi: int = 200,
    tam_kalite_bf16: bool | None = None,
    local_root: str | Path = DEFAULT_LOCAL_ROOT,
    local_hf: str | Path = DEFAULT_LOCAL_HF,
    work_dir: str | Path = DEFAULT_WORK_DIR,
    esitleme_araligi: float = CACHE_SYNC_SECONDS,
) -> OcrSonucu:
    """Drive'daki girdileri yerel SSD'de işler, çıktı ve önbelleği Drive'a geri yazar.

    tam_kalite_bf16: None ise 14+ GB GPU'da (L4/A100) otomatik bf16 + yüksek çözünürlük açılır.
    OCR sürerken önbellek düzenli aralıklarla Drive'a yazılır; oturum koparsa okunan sayfalar kaybolmaz.
    """
    dp = drive_paths(drive_root)
    root = Path(local_root)
    local_cache = root / "output/_cache"
    local_docs = root / "output/belgeler"
    rapor_yolu = root / "output/_colab_rapor.json"
    os.environ["HF_HOME"] = str(local_hf)

    gpu_gb = _gpu_memory_gb()
    use_bf16 = (gpu_gb is not None and gpu_gb >= 14.0) if tam_kalite_bf16 is None else bool(tam_kalite_bf16)
    if use_bf16:
        os.environ["OCR_BF16"] = "1"
        print(f"Mod: Tam kalite BF16 + yüksek çözünürlük (GPU: {gpu_gb:.1f} GB)" if gpu_gb else "Mod: Tam kalite BF16")
    else:
        os.environ.pop("OCR_BF16", None)
        print("Mod: Standart (8 GB uyumlu fp8)")

    # 1) Güncel kesme.txt ve OCR önbelleğini Drive'dan al
    if dp["kesme"].is_file():
        shutil.copy2(dp["kesme"], root / "ayarlar/kesme.txt")

    local_cache.mkdir(parents=True, exist_ok=True)
    if dp["cache"].is_dir() and any(dp["cache"].iterdir()):
        subprocess.run(["rsync", "-rt", f"{dp['cache']}/", f"{local_cache}/"], check=True)

    # 2) Seçili girdileri yerel SSD'ye kopyala
    if dosya.strip().upper() == "HEPSI":
        hedefler = list_inputs(dp["input"])
    else:
        p = dp["input"] / dosya.strip()
        if not p.exists():
            raise FileNotFoundError(f"Drive'da bulunamadı: {p}")
        hedefler = [p]

    if not hedefler:
        raise RuntimeError(f"İşlenecek dosya yok: {dp['input']}")

    work_in = Path(work_dir)
    if work_in.exists():
        shutil.rmtree(work_in)
    work_in.mkdir(parents=True)
    for h in hedefler:
        shutil.copy2(h, work_in / h.name)

    if local_docs.exists():
        shutil.rmtree(local_docs)
    local_docs.mkdir(parents=True)
    rapor_yolu.unlink(missing_ok=True)

    # 3) ocr-now çalıştır (canlı çıktı akışıyla)
    cmd = [
        str(root / "ocr-now"),
        "--profil", profil,
        "-m", model,
        "-p", sayfalar.strip() or "hepsi",
        "-f", *formatlar.split(),
        "--dpi", str(dpi),
        "--isaretle" if isaretle else "--isaretleme",
        "--kesme" if kesme else "--kesme-yok",
        "--rapor", str(rapor_yolu),
    ]
    if model == "dots.mocr":
        cmd += ["-o", okuma]
    if ilk_sayfa and ilk_sayfa > 0:
        cmd += ["--ilk-sayfa", str(ilk_sayfa)]
    cmd += ["--", *[str(work_in / h.name) for h in hedefler]]

    dp["cache"].mkdir(parents=True, exist_ok=True)
    dur = threading.Event()

    def ara_esitle() -> None:
        while not dur.wait(esitleme_araligi):
            try:
                _onbellek_esitle(local_cache, dp["cache"])
            except (OSError, subprocess.CalledProcessError) as e:
                print(f"\nUYARI: önbellek ara eşitlemesi başarısız: {e}", flush=True)

    print("Komut:", " ".join(cmd), flush=True)
    esitleyici = threading.Thread(target=ara_esitle, daemon=True)
    esitleyici.start()
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
        rc = proc.wait()
    finally:
        dur.set()
        esitleyici.join()

    # 4) Yeni OCR önbelleğini Drive'a geri yaz
    _onbellek_esitle(local_cache, dp["cache"])

    # 5) Üretilen belgeleri Drive'a kopyala (üzerine yazmaz)
    dp["output"].mkdir(parents=True, exist_ok=True)
    yazilanlar = []
    for out_file in sorted(local_docs.iterdir()):
        if out_file.is_file():
            hedef = unique_dest(dp["output"], out_file.name)
            shutil.copy2(out_file, hedef)
            yazilanlar.append(hedef)

    print(f"\n== Drive'a kaydedilen belgeler ({len(yazilanlar)}):")
    for y in yazilanlar:
        print(f"  - {y}")

    rapor = None
    if rapor_yolu.is_file():
        try:
            rapor = json.loads(rapor_yolu.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            print(f"UYARI: iş raporu okunamadı: {e}")

    if rc != 0:
        print(f"\nUYARI: ocr-now çıkış kodu {rc} (yukarıdaki uyarıları kontrol edin; okunan sayfalar kaydedildi).")
    return OcrSonucu(rc=rc, rapor=rapor, yazilanlar=yazilanlar, hedefler=hedefler)


def run_ocr(drive_root: str | Path, *, islenenleri_tasi: bool = False, **kwargs) -> int:
    """Ayrıntılı defterin (ocr_now.ipynb) Hücre 3'ü: `ocr_calistir` + isteğe bağlı girdi taşıma."""
    sonuc = ocr_calistir(drive_root, **kwargs)
    if islenenleri_tasi and sonuc.rc == 0:
        done_dir = drive_paths(drive_root)["islenenler"]
        done_dir.mkdir(exist_ok=True)
        for h in sonuc.hedefler:
            shutil.move(str(h), str(unique_dest(done_dir, h.name)))
        print(f"İşlenen {len(sonuc.hedefler)} dosya '{done_dir}' klasörüne taşındı.")
    return sonuc.rc


# ---------------------------------------------------------------------------
# Telefondan tek dokunuş (colab/telefon.ipynb)
# ---------------------------------------------------------------------------

BILDIRIM_ACIKLAMA = """\
# OCR Lab telefon bildirimleri (ntfy)
# 1) Telefona "ntfy" uygulamasını kurun (Android / iOS, ücretsiz, hesap gerekmez).
# 2) Uygulamada "+" ile aşağıdaki konuya abone olun (sunucu: ntfy.sh).
# Bu adı kimseyle paylaşmayın: adı bilen herkes bildirimleri görebilir.
# Bildirimleri kapatmak için konunun başına # koyun.
"""


def konu_ayikla(metin: str) -> str:
    """`bildirim.txt` içeriğinden ilk yorum olmayan satır; yoksa '' (bildirimler kapalı)."""
    for line in metin.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def bildirim_konusu(dp: dict[str, Path]) -> str:
    """Drive'daki `bildirim.txt` dosyasından ntfy konusunu okur; dosya yoksa gizli bir konu üretir."""
    f = dp["bildirim"]
    if f.is_file():
        return konu_ayikla(f.read_text(encoding="utf-8"))   # konu yoksa kullanıcı bildirimleri kapattı
    konu = f"ocr-lab-{secrets.token_hex(10)}"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(BILDIRIM_ACIKLAMA + konu + "\n", encoding="utf-8")
    return konu


def _ntfy_post(body: bytes) -> None:
    req = urllib.request.Request(NTFY_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def bildir(konu: str, baslik: str, mesaj: str, *, tiklama: str | None = None,
           etiketler: list[str] | tuple[str, ...] = (), oncelik: int = 3, _post=None) -> bool:
    """Telefona ntfy bildirimi gönderir. Ağ hatası işi durdurmaz; yalnız uyarı yazar."""
    if not konu:
        return False
    veri = {"topic": konu, "title": baslik, "message": mesaj or baslik,
            "tags": list(etiketler), "priority": oncelik}
    if tiklama:
        veri["click"] = tiklama
    try:
        (_post or _ntfy_post)(json.dumps(veri, ensure_ascii=False).encode("utf-8"))
        return True
    except Exception as e:  # noqa: BLE001 - bildirim hiçbir koşulda OCR'ı düşürmemeli
        print(f"UYARI: bildirim gönderilemedi: {e}")
        return False


def durum_yaz(dp: dict[str, Path], metin: str) -> None:
    """Telefondan Drive uygulamasıyla bakılabilen `DURUM.txt` dosyasını günceller."""
    zaman = datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%Y-%m-%d %H:%M")
    try:
        dp["durum"].parent.mkdir(parents=True, exist_ok=True)
        dp["durum"].write_text(f"{zaman} {metin}\n", encoding="utf-8")
    except OSError as e:
        print(f"UYARI: DURUM.txt yazılamadı: {e}")


def klasor_baglantisi(path: str | Path) -> str:
    """Colab'ın Drive bağlantısı klasör kimliğini `user.drive.id` niteliğinde tutar; yoksa Drive ana sayfası."""
    try:
        drive_id = os.getxattr(str(path), "user.drive.id").decode().strip()
    except (OSError, AttributeError, UnicodeDecodeError):
        return DRIVE_FALLBACK_URL
    return f"https://drive.google.com/drive/folders/{drive_id}" if drive_id else DRIVE_FALLBACK_URL


def girdi_bekle(dp: dict[str, Path], *, sure: float = 120, aralik: float = 10, _sleep=time.sleep) -> list[Path]:
    """Telefondan az önce yüklenen dosya Colab'ın Drive görünümüne gecikmeli düşebilir; kısa süre yoklar."""
    beklenen = 0.0
    while True:
        files = list_inputs(dp["input"])
        if files or beklenen >= sure:
            return files
        if beklenen == 0:
            print(f"input/ boş görünüyor; Drive eşitlemesi için {int(sure)} sn bekleniyor...", flush=True)
        _sleep(aralik)
        beklenen += aralik


def _kisalt(etiketler: list[str], en_fazla: int = 5) -> str:
    metin = ", ".join(etiketler[:en_fazla])
    return metin + (f" …(+{len(etiketler) - en_fazla})" if len(etiketler) > en_fazla else "")


def is_ozeti(dosya_adi: str, sonuc: OcrSonucu) -> tuple[str, bool]:
    """Bildirim için tek satırlık özet ve 'kontrol gerekli mi' bilgisi."""
    isler = (sonuc.rapor or {}).get("isler") or []
    if not isler:
        return f"{dosya_adi}: OCR çalışmadı, belge yazılmadı (çıkış kodu {sonuc.rc})", True
    sorunlu = bool(sonuc.rapor.get("okunamayan")) or not sonuc.yazilanlar
    parcalar = []
    for job in isler:
        parcalar.append(f"{job.get('sayfa', '?')} sayfa")
        if not job.get("yazildi"):
            parcalar.append("belge yazılamadı")
            sorunlu = True
        if job.get("uyarilar"):
            parcalar.append(f"{len(job['uyarilar'])} uyarı")
        for anahtar, ad in (("metin_kontrol", "metin kontrolü"), ("kesilen", "kesilen"),
                            ("kontrol", "sayfa no kontrolü")):
            if job.get(anahtar):
                parcalar.append(f"{ad}: {_kisalt(job[anahtar])}")
                sorunlu = True
        if job.get("yazma_hatalari"):
            parcalar.append(f"yazma hatası: {len(job['yazma_hatalari'])}")
            sorunlu = True
    return f"{dosya_adi}: " + " · ".join(parcalar), sorunlu


def calisma_zamanini_kapat() -> None:
    """Colab çalışma zamanını kapatır; boşta GPU birimi harcanmaz."""
    try:
        from google.colab import runtime  # type: ignore[import-not-found]
    except ImportError:
        print("Colab dışında: çalışma zamanı kapatılmadı.")
        return
    print("\nGPU bırakılıyor (oturum kapanacak)...", flush=True)
    runtime.unassign()


# Telefon akışının istediği çıktılar: Word, aranabilir PDF ve ham metin.
# (ocr_calistir'ın kendi varsayılanı "docx txt" kalır; ocr_now.ipynb onu kullanır.)
TELEFON_FORMATLARI = "docx pdf txt"


def telefon_calistir(
    drive_root: str | Path,
    *,
    model: str = "dots.mocr",
    gpu_birak: bool = True,
    yalniz: list[str] | None = None,
    _kur=None,
    _ocr=None,
    _bildir=None,
    _birak=None,
    _bekle=None,
) -> int:
    """Telefon akışı: input/'daki dosyaları tek tek okur, taşır, bildirir ve GPU'yu bırakır.

    yalniz: verilirse yalnız bu adlardaki girdiler işlenir (VPS izleyicisi daha önce başarısız olanları ayıklar).
    Dönüş: 0 sorunsuz, 1 kontrol gereken/okunamayan dosya var, 2 akış hatayla durdu.
    """
    kur = _kur or (lambda root, model: setup_env(root, model=model))
    ocr = _ocr or (lambda root, dosya, model: ocr_calistir(root, dosya=dosya, model=model,
                                                           formatlar=TELEFON_FORMATLARI))
    gonder = _bildir or bildir
    birak = _birak or calisma_zamanini_kapat
    bekle = _bekle or girdi_bekle

    dp = prepare_drive(drive_root)
    konu = bildirim_konusu(dp)
    if konu:
        print(f"\nBildirim konusu (ntfy): {konu}  → telefonda ntfy uygulamasında bu konuya abone olun.")
    cikis_url = klasor_baglantisi(dp["output"])
    try:
        files = bekle(dp)
        if yalniz is not None:
            files = [f for f in files if f.name in set(yalniz)]
        if not files:
            durum_yaz(dp, "Gelen kutusu boş: input/ klasöründe dosya yok.")
            gonder(konu, "OCR: işlenecek dosya yok", "input/ klasöründe PDF bulunamadı; GPU bırakıldı.",
                   etiketler=["warning"])
            return 0

        n = len(files)
        durum_yaz(dp, f"Kurulum: {n} dosya bekliyor")
        gonder(konu, f"OCR başladı: {n} dosya", "\n".join(f.name for f in files), etiketler=["hourglass"])
        kur(drive_root, model=model)

        satirlar: list[str] = []
        sorunlu = False
        for i, f in enumerate(files, 1):
            durum_yaz(dp, f"İşleniyor {i}/{n}: {f.name}")
            print(f"\n==== {i}/{n}: {f.name}", flush=True)
            try:
                sonuc = ocr(drive_root, dosya=f.name, model=model)
            except Exception as e:  # noqa: BLE001 - bir dosyanın hatası diğerlerini durdurmamalı
                traceback.print_exc()
                satirlar.append(f"{f.name}: HATA — {e}")
                sorunlu = True
                continue
            satir, kotu = is_ozeti(f.name, sonuc)
            satirlar.append(satir)
            sorunlu |= kotu
            if sonuc.yazilanlar:
                dp["islenenler"].mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(unique_dest(dp["islenenler"], f.name)))

        baslik = "OCR bitti — kontrol gerekli" if sorunlu else "OCR bitti"
        ozet = "\n".join(satirlar)
        durum_yaz(dp, f"{baslik}\n{ozet}")
        print(f"\n{baslik}\n{ozet}")
        gonder(konu, baslik, ozet, tiklama=cikis_url,
               etiketler=["warning"] if sorunlu else ["white_check_mark"], oncelik=4 if sorunlu else 3)
        return 1 if sorunlu else 0
    except Exception as e:  # noqa: BLE001 - hata telefona bildirilmeli, GPU yine bırakılmalı
        tb = traceback.format_exc()
        print(tb)
        durum_yaz(dp, f"HATA: {e}\n\n{tb[-3000:]}")
        gonder(konu, "OCR durdu (hata)", str(e)[:500] or type(e).__name__, etiketler=["x"], oncelik=4)
        return 2
    finally:
        if gpu_birak:
            birak()
