#!/usr/bin/env python3
"""Bir PDF'yi ya da sayfa görselini Drive'daki `ocr-lab/input/` klasörüne koyar ve izleyiciyi hemen dürter.

Hermes Agent (Telegram) bu betiği `ocr-lab` becerisiyle çağırır: kullanıcı Telegram'dan PDF gönderir, Hermes
dosyayı yerel önbelleğine indirir, bu betik onu Drive'a yükler. Okuma, bildirim ve Word dosyası ile aranabilir
PDF'nin Telegram'a dönmesi izleyicinin (vps/izleyici.py) işidir. Yalnız standart kitaplık kullanır.

    python3 ~/ocr-lab/vps/ocr_ekle.py /yol/dosya.pdf [başka.pdf ...]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from colab import runner  # noqa: E402  (yalnız standart kitaplık kullanır)

KABUL = runner.IMAGE_EXTS | {".pdf"}
# Hermes gelen belgeleri `doc_<12 hex>_<asıl ad>` adıyla önbelleğe yazar.
HERMES_ONEKI = re.compile(r"^doc_[0-9a-f]{12}_")


def calistir_komut(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def hedef_ad(yol: Path) -> str:
    ad = HERMES_ONEKI.sub("", yol.name)
    ad = re.sub(r"[/\\\x00-\x1f]+", "_", ad).strip() or "tarama.pdf"
    return "_" + ad if ad.startswith(".") else ad       # izleyici gizli dosyaları atlar


def bos_ad(ad: str, mevcut: set[str]) -> str:
    """input/ ya da islenenler/ içinde aynı ad varsa `ad_2.pdf`, `ad_3.pdf` ... verir."""
    if ad not in mevcut:
        return ad
    kok, uzanti = os.path.splitext(ad)
    i = 2
    while f"{kok}_{i}{uzanti}" in mevcut:
        i += 1
    return f"{kok}_{i}{uzanti}"


def mevcut_adlar(uzak: str, _run=calistir_komut) -> set[str]:
    adlar: set[str] = set()
    for klasor in ("input", "input/islenenler"):
        res = _run(["rclone", "lsf", "--files-only", f"{uzak}/{klasor}"])
        if res.returncode == 0:
            adlar |= {l.strip() for l in res.stdout.splitlines() if l.strip()}
    return adlar


def izleyiciyi_durt(_run=calistir_komut) -> bool:
    """Zamanlayıcıyı beklemeden izleyiciyi başlatır. İzleyici zaten çalışıyorsa dosya sonraki turda alınır."""
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        res = _run(["systemctl", "--user", "start", "--no-block", "ocr-vps.service"], env=env)
    except OSError:
        return False
    return res.returncode == 0


def ekle(dosyalar: list[Path], *, uzak: str, _run=calistir_komut) -> int:
    hatalar = 0
    mevcut = mevcut_adlar(uzak, _run)
    eklenen = []
    for yol in dosyalar:
        if not yol.is_file():
            print(f"HATA: dosya yok: {yol}")
            hatalar += 1
            continue
        if yol.suffix.lower() not in KABUL:
            print(f"HATA: {yol.name} okunamaz; yalnız PDF ve görsel ({', '.join(sorted(KABUL))}) kabul edilir.")
            hatalar += 1
            continue
        ad = bos_ad(hedef_ad(yol), mevcut)
        res = _run(["rclone", "copyto", str(yol), f"{uzak}/input/{ad}"])
        if res.returncode != 0:
            print(f"HATA: {ad} Drive'a yüklenemedi: {res.stderr.strip()[-300:]}")
            hatalar += 1
            continue
        mevcut.add(ad)
        eklenen.append(ad)
        print(f"Drive'a eklendi: ocr-lab/input/{ad}")
    if eklenen:
        durtuldu = izleyiciyi_durt(_run)
        print("Okuma " + ("birkaç dakika içinde başlar" if durtuldu else "en geç birkaç dakika içinde başlar")
              + ". Başlayınca ve bitince bildirim gelir; Word dosyası ve aranabilir PDF Telegram'a ek olarak "
                "gönderilir.")
    return 1 if hatalar else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("dosyalar", nargs="+", type=Path)
    p.add_argument("--uzak", default=os.environ.get("OCR_VPS_UZAK", "gdrive:ocr-lab"))
    args = p.parse_args(argv)
    return ekle(args.dosyalar, uzak=args.uzak)


if __name__ == "__main__":
    sys.exit(main())
