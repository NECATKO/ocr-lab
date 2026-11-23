"""Faz 4 testleri: son belgeler geçmişi ve yan yana görsel servisi (aranabilir PDF: test_export_pdf.py)."""
from pathlib import Path
import threading
import urllib.request

from PIL import Image

from ocrnow import paths
from ocrnow.gui import server


def test_recent_documents_and_image_preview_endpoint(tmp_path: Path, monkeypatch):
    in_dir = tmp_path / "input"
    out_dir = tmp_path / "belgeler"
    in_dir.mkdir()
    out_dir.mkdir()
    monkeypatch.setattr(paths, "INPUT", in_dir)
    monkeypatch.setattr(paths, "DOCS_OUT", out_dir)

    img = in_dir / "tahta.png"
    Image.new("RGB", (20, 20), "white").save(img)
    (out_dir / "not.md").write_text("# Not", encoding="utf-8")

    app = server.App()
    st = app.state()
    assert [d["ad"] for d in st["son_belgeler"]] == ["not.md"]

    httpd = server.make_server(app, 0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/tarama/tahta.png") as r:
            assert r.status == 200
            assert r.headers["Content-Type"] == "image/png"
            assert len(r.read()) > 10
    finally:
        httpd.shutdown()
        httpd.server_close()
