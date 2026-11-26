# Bu dosya bir şablondur: VPS izleyicisi başına `AYAR = {...}` satırını ekleyip `colab run` ile
# Colab GPU makinesinde çalıştırır. Drive'a rclone ile bağlanır ve telefon akışını (runner.telefon_calistir)
# olduğu gibi kullanır. Elle çalıştırılmaz.
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

AYAR = globals().get("AYAR") or {}
MOUNT = Path("/content/drive/MyDrive/ocr-lab")
LOCAL_ROOT = Path("/content/ocr-lab")
RC_ADDR = "127.0.0.1:5572"
SONUC_ISARETI = "OCR-VPS-SONUC"
BASLADI_ISARETI = "OCR-VPS-BASLADI"   # izleyici: bu satır yoksa iş hiç başlamadan koptu (yeniden denenebilir)
HAZIRLIK_HATASI = "OCR-VPS-HAZIRLIK-HATASI"
YUKLEME_DOGRULANAMADI = "OCR-VPS-YUKLEME-DOGRULANAMADI"   # bağlantı kaldırılırken bekleyen yükleme kalmış olabilir
# İzleyici bu satırları okuyup bildirimi ve belgeleri Hermes üzerinden Telegram'a iletir (JSON, tek satır).
BILDIRIM_ISARETI = "OCR-VPS-BILDIRIM"
BELGE_ISARETI = "OCR-VPS-BELGE"


def isaret_bas(isaret: str, veri: dict) -> None:
    print(f"{isaret} {json.dumps(veri, ensure_ascii=False)}", flush=True)


def sh(cmd, **kw):
    print("$", cmd if isinstance(cmd, str) else " ".join(map(str, cmd)), flush=True)
    return subprocess.run(cmd, check=True, **kw)


def rclone_kur() -> None:
    if not shutil.which("rclone"):
        # install.sh yerine doğrudan ikili dosya: unzip/man sayfası adımlarına takılmaz
        # (Python-urllib istemcisine 403 döndürüyor; curl ile indirilir.)
        import zipfile
        print("rclone indiriliyor...", flush=True)
        sh(["curl", "-fsSL", "--retry", "3", "-o", "/tmp/rclone.zip",
            "https://downloads.rclone.org/rclone-current-linux-amd64.zip"])
        with zipfile.ZipFile("/tmp/rclone.zip") as z:
            ad = next(n for n in z.namelist() if n.endswith("/rclone"))
            Path("/usr/local/bin/rclone").write_bytes(z.read(ad))
        os.chmod("/usr/local/bin/rclone", 0o755)
    if not shutil.which("fusermount3"):     # rclone mount fusermount3 ister; Colab'da yalnız fuse2 var
        sh("apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq fuse3", shell=True)
    conf = Path.home() / ".config/rclone/rclone.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_bytes(base64.b64decode(AYAR["rclone_conf_b64"]))
    conf.chmod(0o600)


def bagla() -> subprocess.Popen:
    MOUNT.mkdir(parents=True, exist_ok=True)
    log = open("/content/rclone-mount.log", "w")
    proc = subprocess.Popen(
        ["rclone", "mount", AYAR["uzak"], str(MOUNT),
         "--vfs-cache-mode", "writes", "--vfs-write-back", "2s", "--dir-cache-time", "15s",
         "--rc", "--rc-addr", RC_ADDR, "--rc-no-auth"],
        stdout=log, stderr=subprocess.STDOUT,
    )
    for _ in range(60):
        if os.path.ismount(MOUNT):
            print(f"Drive bağlandı: {AYAR['uzak']} -> {MOUNT}", flush=True)
            return proc
        if proc.poll() is not None:
            break
        time.sleep(1)
    raise RuntimeError("rclone mount açılamadı:\n" + Path("/content/rclone-mount.log").read_text()[-2000:])


def bekleyen_yukleme() -> int | None:
    """Drive'a gitmeyi bekleyen dosya sayısı; sorgulanamazsa None (bilinmiyor — boş kuyruk sayılmaz)."""
    try:
        res = subprocess.run(["rclone", "rc", "--rc-addr", RC_ADDR, "vfs/stats"], capture_output=True, text=True,
                             timeout=60)
        if res.returncode != 0:
            return None
        disk = json.loads(res.stdout).get("diskCache") or {}
        return int(disk.get("uploadsInProgress", 0)) + int(disk.get("uploadsQueued", 0))
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return None


def ayir(proc: subprocess.Popen, en_fazla: float = 1800) -> bool:
    """Önbellekte bekleyen yazılar Drive'a gidene kadar bekler, sonra bağlantıyı kaldırır.
    Dönüş: bütün yüklemelerin bittiği doğrulandıysa True."""
    bitis = time.time() + en_fazla
    while (kalan := bekleyen_yukleme()) != 0 and time.time() < bitis:
        print(f"Drive'a yükleniyor: {kalan} dosya..." if kalan else "Drive yükleme durumu sorgulanamadı...",
              flush=True)
        time.sleep(5)
    tamam = kalan == 0
    subprocess.run(["fusermount3" if shutil.which("fusermount3") else "fusermount", "-u", str(MOUNT)])
    try:
        proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
        proc.terminate()
    return tamam


def repo_hazirla() -> None:
    if not (LOCAL_ROOT / ".git").is_dir():
        sh(["git", "clone", "--depth", "1", "-b", AYAR["dal"], AYAR["repo"], str(LOCAL_ROOT)])
    else:
        sh(["git", "-C", str(LOCAL_ROOT), "pull", "--ff-only"])
    if str(LOCAL_ROOT) not in sys.path:
        sys.path.insert(0, str(LOCAL_ROOT))


def calis() -> int:
    print(BASLADI_ISARETI, flush=True)
    mount = None
    try:
        rclone_kur()
        mount = bagla()
        repo_hazirla()
        from colab import runner

        def ocr(root, dosya, model):
            sonuc = runner.ocr_calistir(root, dosya=dosya, model=model, formatlar=runner.TELEFON_FORMATLARI)
            isaret_bas(BELGE_ISARETI, {"girdi": dosya, "belgeler": [Path(y).name for y in sonuc.yazilanlar]})
            return sonuc

        def bildir(konu, baslik, mesaj, **kw):
            runner.bildir(konu, baslik, mesaj, **kw)
            isaret_bas(BILDIRIM_ISARETI, {"baslik": baslik, "mesaj": mesaj})

        rc = runner.telefon_calistir(MOUNT, model=AYAR.get("model", "dots.mocr"), gpu_birak=False,
                                     yalniz=AYAR["dosyalar"], _ocr=ocr, _bildir=bildir)
    except Exception:  # noqa: BLE001 - VPS izleyicisi bu işareti görüp bildirim gönderir
        traceback.print_exc()
        print(HAZIRLIK_HATASI, flush=True)
        rc = 3
    finally:
        if mount is not None and not ayir(mount):
            print(YUKLEME_DOGRULANAMADI, flush=True)
    print(f"{SONUC_ISARETI} {rc}", flush=True)
    return rc


if __name__ == "__main__" and AYAR:
    sys.exit(calis())
