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
EXPLORER_EXE = r"C:\Windows\explorer.exe"
FOLDER_ICON = r"C:\Windows\System32\shell32.dll,4"
DESKTOP_FOLDER = "ocr-now-windows"


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


def _ps(text: str) -> str:
    """PowerShell tek tırnaklı dize (içindeki ' ikilenir)."""
    return "'" + text.replace("'", "''") + "'"


def shortcut_script(distro: str, root: Path, docs: str, scans: str) -> str:
    """Masaüstündeki ocr-now-windows klasörüne üç kısayol koyan PowerShell betiği: OCR-Now (arayüzü simge
    küçültülmüş pencereyle açar), Belgeler ve Taramalar (Gezgin'de açılan klasörler; docs/scans Windows yolu).
    Eski masaüstü OCR-Now.lnk kaldırılır. Sonunda klasörün yolunu yazar."""
    args = f"-d {distro} --cd {root} -e {root / 'ocr-gui'} --tarayici"
    lines = [
        "$d = [Environment]::GetFolderPath('Desktop')",
        f"$f = Join-Path $d {_ps(DESKTOP_FOLDER)}",
        "New-Item -ItemType Directory -Force -Path $f | Out-Null",
        "$w = New-Object -ComObject WScript.Shell",
        "$s = $w.CreateShortcut(\"$f\\OCR-Now.lnk\")",
        f"$s.TargetPath = {_ps(WSL_EXE)}",
        f"$s.Arguments = {_ps(args)}",
        "$s.WindowStyle = 7",
        "$s.Description = 'Taranmis sayfalari Word belgesine cevirir (ocr-now)'",
        "$s.Save()",
    ]
    for name, folder, what in (("Belgeler", docs, "Word ve metin belgeleri"), ("Taramalar", scans, "Eklenen taramalar")):
        lines += [
            f"$s = $w.CreateShortcut(\"$f\\{name}.lnk\")",
            f"$s.TargetPath = {_ps(EXPLORER_EXE)}",
            f"$s.Arguments = {_ps(chr(34) + folder + chr(34))}",
            f"$s.IconLocation = {_ps(FOLDER_ICON)}",
            f"$s.Description = {_ps(what + ' (ocr-now)')}",
            "$s.Save()",
        ]
    lines += [
        "Remove-Item -LiteralPath \"$d\\OCR-Now.lnk\" -ErrorAction SilentlyContinue",
        "Write-Output $f",
    ]
    return "\n".join(lines)


def _windows_path(path: Path) -> str:
    return subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()


def make_shortcut() -> int:
    distro = os.environ.get("WSL_DISTRO_NAME")
    if not distro:
        print("Hata: masaüstü kısayolu yalnız WSL'de oluşturulabilir.", file=sys.stderr)
        return 2
    for folder in (paths.DOCS_OUT, paths.INPUT):    # kısayol boş da olsa var olan klasörü açsın
        folder.mkdir(parents=True, exist_ok=True)
    try:
        docs, scans = _windows_path(paths.DOCS_OUT), _windows_path(paths.INPUT)
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"Hata: Windows yolu bulunamadı ({e})", file=sys.stderr)
        return 1
    done = subprocess.run(["powershell.exe", "-NoProfile", "-Command", shortcut_script(distro, paths.ROOT, docs, scans)],
                          capture_output=True, text=True, check=False)
    if done.returncode != 0:
        print(f"Hata: kısayol oluşturulamadı: {done.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Kısayollar oluşturuldu: {done.stdout.strip()}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ocr-gui", description="ocr-now'ı tarayıcıdaki arayüzle kullanır.")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--tarayici", action="store_true", help="arayüzü tarayıcıda aç")
    p.add_argument("--masaustu-kisayolu", action="store_true", help="Windows masaüstündeki ocr-now-windows klasörüne kısayolları koy")
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
