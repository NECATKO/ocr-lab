"""Faz 3 testleri: çoklu Kraken HTR modeli, dil seçimi (--dil) ve RTL Arapça/Osmanlıca Word fontu."""
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from ocrnow import cli, engines, export_docx, identity, paths
from ocrnow.gui import server
from ocrnow.layout import Block, Page


def test_kraken_models_discovery_and_selection(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "MODELS", tmp_path)
    monkeypatch.delenv("KRAKEN_MODEL", raising=False)
    m1 = tmp_path / "catmus-print-large.mlmodel"
    m2 = tmp_path / "ottoman-manuscript.mlmodel"
    m1.write_bytes(b"a")
    m2.write_bytes(b"b")
    (tmp_path / "other.gguf").write_bytes(b"c")

    assert [p.name for p in engines.kraken_models()] == ["catmus-print-large.mlmodel", "ottoman-manuscript.mlmodel"]
    assert engines._kraken_model("ottoman-manuscript.mlmodel") == m2
    assert engines._kraken_model(None) == m1


def test_kraken_identity_differs_per_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "MODELS", tmp_path)
    m1 = tmp_path / "a.mlmodel"
    m2 = tmp_path / "b.mlmodel"
    m1.write_bytes(b"111")
    m2.write_bytes(b"2222")
    tess = {"version": "5.0", "language": "tur", "data_files": {}}
    p1 = identity.runtime("kraken", tess=tess, kraken_model="a.mlmodel").profile("duz")
    p2 = identity.runtime("kraken", tess=tess, kraken_model="b.mlmodel").profile("duz")
    assert p1["id"] != p2["id"]


def test_rtl_docx_sets_arabic_cs_font(tmp_path: Path):
    page = Page(source="yazma.png", index=1, width=800, height=1200, engine="kraken", mode="duz",
                blocks=[Block("Text", None, "بسم الله الرحمن الرحيم")])
    out = export_docx.write_docx([page], tmp_path / "rtl.docx", highlight=False, bold_pattern=None,
                                 flowing=True, rtl=True)
    doc = Document(out)
    rfonts = doc.paragraphs[0].runs[0]._r.find(f".//{qn('w:rFonts')}")
    assert rfonts is not None
    assert rfonts.get(qn("w:cs")) == "Amiri"


def test_cli_and_gui_lang_and_kraken_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths, "INPUT", tmp_path)
    monkeypatch.setattr(paths, "MODELS", tmp_path)
    (tmp_path / "m.png").write_bytes(b"x")
    (tmp_path / "arabic.mlmodel").write_bytes(b"y")

    parsed = cli.parse_args(["m.png", "--profil", "yazma", "-m", "kraken", "--kraken-model", "arabic.mlmodel", "--dil", "ara"])
    assert parsed.kraken_model == "arabic.mlmodel"
    assert parsed.dil == "ara"

    st = server.App().state()
    assert "arabic.mlmodel" in st["kraken_modelleri"]
    args = server.build_args({
        "profil": "yazma", "dosya": "m.png", "model": "kraken", "okuma": "duz",
        "formatlar": ["txt", "docx"], "dil": "ara", "kraken_model": "arabic.mlmodel",
    })
    assert "--dil" in args and "ara" in args
    assert "--kraken-model" in args and "arabic.mlmodel" in args
