"""`ocr-gui` komutu: tarayıcıda açılan arayüzü başlatır.

    ocr-gui                       sunucuyu açar (http://localhost:8765)
    ocr-gui --tarayici            ayrıca tarayıcıda açar; arayüz zaten açıksa yalnız tarayıcıyı açar
    ocr-gui --masaustu-kisayolu   Windows masaüstüne "OCR-Now" kısayolunu koyar (WSL)
"""
import argparse
import errno
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from ocrnow import paths
from ocrnow.gui.server import App, make_server

DEFAULT_PORT = 8765
WSL_EXE = r"C:\Windows\System32\wsl.exe"


def open_browser(url: str) -> None:
    """WSL'de Windows'un varsayılan tarayıcısı, başka yerde Python'un webbrowser modülü."""
    try:
        subprocess.Popen(["explorer.exe", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        webbrowser.open(url)


def _is_gui(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/saglik", timeout=3) as r:
            return json.load(r) == {"ocr-gui": True}
    except (OSError, ValueError):
        return False


def shortcut_script(distro: str, root: Path) -> str:
    """Masaüstüne simge küçültülmüş pencereyle açılan OCR-Now.lnk koyan PowerShell betiği; sonunda yolunu yazar."""
    args = f"-d {distro} --cd {root} -e {root / 'ocr-gui'} --tarayici"
    return "\n".join([
        "$d = [Environment]::GetFolderPath('Desktop')",
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut(\"$d\\OCR-Now.lnk\")",
        f"$s.TargetPath = '{WSL_EXE}'",
        f"$s.Arguments = '{args}'",
        "$s.WindowStyle = 7",
        "$s.Description = 'Taranmis sayfalari Word belgesine cevirir (ocr-now)'",
        "$s.Save()",
        "Write-Output \"$d\\OCR-Now.lnk\"",
    ])


def make_shortcut() -> int:
    distro = os.environ.get("WSL_DISTRO_NAME")
    if not distro:
        print("Hata: masaüstü kısayolu yalnız WSL'de oluşturulabilir.", file=sys.stderr)
        return 2
    done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", shortcut_script(distro, paths.ROOT)],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        print(f"Hata: kısayol oluşturulamadı: {done.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Kısayol oluşturuldu: {done.stdout.strip()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ocr-gui", description="ocr-now'ı tarayıcıdaki arayüzle kullanır.")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--tarayici", action="store_true", help="arayüzü tarayıcıda aç")
    p.add_argument("--masaustu-kisayolu", action="store_true", help="Windows masaüstüne OCR-Now kısayolu koy")
    args = p.parse_args(argv)
    if args.masaustu_kisayolu:
        return make_shortcut()

    url = f"http://localhost:{args.port}/"
    app = App()
    try:
        httpd = make_server(app, args.port)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        if _is_gui(args.port):            # ikinci kez tıklandı: açık arayüzü göster
            if args.tarayici:
                open_browser(url)
            print(f"Arayüz zaten açık: {url}")
            return 0
        print(f"Hata: {args.port} portu başka bir programda; --port ile başka bir port verin.", file=sys.stderr)
        return 2
    app.on_close = lambda: threading.Thread(target=httpd.shutdown, daemon=True).start()

    def stop(signum, frame):
        raise SystemExit(0)
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)       # pencere kapanınca: süren okuma iptal edilir, model sunucusu kapanır

    print(f"ocr-now arayüzü açık: {url}\nKapatmak için sayfadaki Kapat düğmesini kullanın ya da bu pencereyi kapatın.")
    if args.tarayici:
        open_browser(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
