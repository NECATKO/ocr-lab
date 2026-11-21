"""Faz 4 testleri: aranabilir Unicode PDF (-f pdf), son belgeler geçmişi ve yan yana görsel servisi."""
from pathlib import Path
import threading
import urllib.request

import pypdfium2 as pdfium
from PIL import Image

from ocrnow import export_pdf, paths
from ocrnow.gui import server
from ocrnow.layout import Block, Page


def test_searchable_pdf_embeds_image_and_turkish_arabic_unicode(tmp_path: Path):
    img = tmp_path / "sayfa.png"
    Image.new("RGB", (400, 600), "white").save(img)
    p = Page(source=str(img), index=1, width=400, height=600, engine="dots.mocr", mode="duzen", blocks=[
        Block("Text", (20, 40, 360, 80), "İstanbul şehri ğşçıöü âîû"),
        Block("Text", (20, 120, 360, 160), "بسم الله الرحمن الرحيم"),
    ])
    out = export_pdf.write_pdf([p], tmp_path / "aranabilir.pdf")
    assert out.is_file() and out.stat().st_size > 500

    doc = pdfium.PdfDocument(str(out))
    try:
        assert len(doc) == 1
        extracted = doc[0].get_textpage().get_text_range()
    finally:
        doc.close()
    assert "İstanbul" in extracted
    assert "ğşçıöü" in extracted
    assert "بسم الله" in extracted


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
