"""ocr-gui başlatıcısı: zaten açıksa tarayıcıyı açar, port başkasındaysa açık hata verir; masaüstü kısayolu."""
import socket
import threading

from ocrnow import paths
from ocrnow.gui import main as gui_main
from ocrnow.gui.server import App, make_server


def test_already_running_gui_only_opens_browser(monkeypatch):
    httpd = make_server(App(), 0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    opened = []
    monkeypatch.setattr(gui_main, "open_browser", opened.append)
    try:
        assert gui_main.main(["--port", str(port), "--tarayici"]) == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert opened == [f"http://localhost:{port}/"]


def test_port_used_by_another_program_is_an_error(monkeypatch, capsys):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    monkeypatch.setattr(gui_main, "open_browser", lambda url: None)
    try:
        assert gui_main.main(["--port", str(port)]) == 2
    finally:
        sock.close()
    assert str(port) in capsys.readouterr().err


def test_shortcut_script_starts_gui_in_this_distro_minimized():
    script = gui_main.shortcut_script("Ubuntu", paths.ROOT, r"\\wsl.localhost\Ubuntu\b", r"\\wsl.localhost\Ubuntu\t")
    assert r"C:\Windows\System32\wsl.exe" in script
    assert f"-d Ubuntu --cd {paths.ROOT} -e {paths.ROOT / 'ocr-gui'} --tarayici" in script
    assert "WindowStyle = 7" in script and "OCR-Now.lnk" in script


def test_shortcut_script_puts_everything_in_one_desktop_folder():
    # masaüstünde tek klasör: programı açan simge + belgeler ve taramalar klasörlerinin kısayolları
    script = gui_main.shortcut_script("Ubuntu", paths.ROOT, r"\\wsl.localhost\Ubuntu\o'b", r"\\wsl.localhost\Ubuntu\t")
    assert "ocr-now-windows" in script
    for name in ("OCR-Now.lnk", "Belgeler.lnk", "Taramalar.lnk"):
        assert f'"$f\\{name}"' in script, name
    assert "'\"" + r"\\wsl.localhost\Ubuntu\o''b" + "\"'" in script     # yol tırnakta; PowerShell ' ikilenir
    assert "'\"" + r"\\wsl.localhost\Ubuntu\t" + "\"'" in script
    assert "Remove-Item" in script and '"$d\\OCR-Now.lnk"' in script   # eski masaüstü simgesi kalkar
