"""Faz 1 testleri: profiller, Markdown/akan/RTL Word dışa aktarma, tesseract'sız hat ve GUI profil/önizleme."""
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

from ocrnow import cli, export_docx, export_md, pipeline, profiles
from ocrnow.gui import server
from ocrnow.layout import Block, Line, Mark, Page
from ocrnow.pages import DocJob, SourcePage


def _page(blocks: list[Block], *, index: int = 1, book_page: str | None = None, failed: bool = False) -> Page:
    return Page(source="ornek.pdf", index=index, width=1000, height=1400, engine="dots.mocr", mode="duzen",
                blocks=blocks, book_page=book_page, failed=failed)


def test_profiles_defined_and_lookup():
    assert set(profiles.PROFILES) == {"kitap", "gunluk", "ders-notu", "fatura", "yazma"}
    assert profiles.get(None).id == "kitap"
    assert profiles.get("kitap").use_tesseract is True
    assert profiles.get("gunluk").use_tesseract is False
    assert profiles.get("ders-notu").formats == ("md",)
    assert profiles.get("yazma").rtl is True
    assert profiles.get("yazma").cjk_check is False
    with pytest.raises(KeyError):
        profiles.get("yok-oyle-profil")


def test_export_md_blocks_table_formula_and_inline(tmp_path: Path):
    blocks = [
        Block("Title", None, "Fizik 101"),
        Block("Section-header", None, "Hareket"),
        Block("Text", None, "Hız vektörel bir niceliktir.", marks=[Mark(0, 3, "kalin"), Mark(4, 12, "italik")]),
        Block("List-item", None, "İvme sabittir"),
        Block("List-item", None, "- Zaman pozitiftir"),
        Block("Formula", None, "F = m \\cdot a"),
        Block("Table", None, "<table><tr><th>Ürün</th><th>Fiyat</th></tr><tr><td>Defter</td><td>50 TL</td></tr></table>"),
        Block("Footnote", None, "1 Ders kitabı s. 12."),
    ]
    out = export_md.write_md([_page(blocks)], tmp_path / "not.md")
    text = out.read_text(encoding="utf-8")
    assert "# Fizik 101" in text
    assert "## Hareket" in text
    assert "**Hız** *vektörel* bir niceliktir." in text
    assert "- İvme sabittir" in text
    assert "- Zaman pozitiftir" in text
    assert "$$\nF = m \\cdot a\n$$" in text
    assert "| Ürün | Fiyat |\n| --- | --- |\n| Defter | 50 TL |" in text
    assert "> 1 Ders kitabı s. 12." in text
    assert "\n---\n" not in text   # tek sayfada ayraç yok


def test_export_md_multipage_separator():
    p1 = _page([Block("Text", None, "Birinci sayfa")], index=1, book_page="10")
    p2 = _page([Block("Text", None, "İkinci sayfa")], index=2, book_page="11")
    md = export_md.render_md([p1, p2])
    assert "<!-- s. 10 -->" in md
    assert "<!-- s. 11 -->" in md
    assert "\n\n---\n\n" in md


def test_export_docx_flowing_and_rtl(tmp_path: Path):
    blocks = [
        Block("Title", None, "عنوان"),
        Block("Text", None, "السطر الأول والسطر الثاني", lines=[Line(0, 11, False), Line(12, 25, False)]),
    ]
    out = export_docx.write_docx([_page(blocks)], tmp_path / "yazma.docx", highlight=False, bold_pattern=None,
                                 flowing=True, rtl=True)
    doc = Document(out)
    # A4 genişlik ~21 cm
    assert round(doc.sections[0].page_width.cm, 1) == 21.0
    # Akan modda Shift+Enter (<w:br/>) eklenmez
    xml = doc.element.body.xml
    assert "<w:br/>" not in xml
    # RTL işaretleri mevcut
    bidi = doc.paragraphs[1]._p.find(f".//{qn('w:bidi')}")
    rtl_run = doc.paragraphs[1].runs[0]._r.find(f".//{qn('w:rtl')}")
    assert bidi is not None
    assert rtl_run is not None


def test_pipeline_without_tesseract_skips_tsv_and_pagenum_warnings(tmp_path: Path):
    img = tmp_path / "foto.png"
    from PIL import Image
    Image.new("RGB", (200, 100), "white").save(img)
    called = {"tsv": 0}

    def fail_tsv(_p):
        called["tsv"] += 1
        raise AssertionError("tesseract çağrılmamalıydı")

    opts = pipeline.Options(engine="glm-ocr", mode="duz", apostrophes=False,
                            use_tesseract=False, detect_pagenum=False, cjk_check=False)
    job = DocJob("foto", [SourcePage(img, 1)])
    prof = {"id": "test-prof", "cacheable": False}
    pages = pipeline.process_job(job, opts, run=lambda _p, _m: "Merhaba 漢字", tsv=fail_tsv,
                                 progress=lambda _: None, profile=prof)
    assert called["tsv"] == 0
    assert len(pages) == 1
    assert not any(pipeline.IMAGE_ORDER_WARNING in w or pipeline.PDF_ORDER_WARNING in w
                   or pipeline.UNEXPECTED_SCRIPT_WARNING in w for w in pages[0].warnings)
    assert pages[0].book_page is None


def test_pipeline_cache_tsv_compatibility(tmp_path: Path, monkeypatch):
    from ocrnow import paths
    monkeypatch.setattr(paths, "CACHE", tmp_path / "cache")
    src = SourcePage(tmp_path / "a.png", 1)
    prof = {"id": "p1", "cacheable": True}
    opts_no_tess = pipeline.Options(engine="dots.mocr", mode="duz", use_tesseract=False)
    opts_tess = pipeline.Options(engine="dots.mocr", mode="duz", use_tesseract=True)
    cpath = pipeline.cache_path(src, opts_no_tess, source_key="k1", profile=prof)

    # Tesseract'sız kayıt yazılır
    pipeline._save(cpath, pipeline.RawPage("metin", "", 100, 100), opts_no_tess, prof)
    assert pipeline._load(cpath, opts_no_tess, prof) is not None
    # Tesseract isteyen profil boş TSV'li kaydı kabul etmez
    assert pipeline._load(cpath, opts_tess, prof) is None

    # Dolu TSV'li kayıt yazılır; tesseract'sız profil bunu boş TSV ile ezmez
    pipeline._save(cpath, pipeline.RawPage("metin", "tsv-data", 100, 100), opts_tess, prof)
    pipeline._save(cpath, pipeline.RawPage("metin", "", 100, 100), opts_no_tess, prof)
    loaded = pipeline._load(cpath, opts_tess, prof)
    assert loaded is not None and loaded.tsv == "tsv-data"


def test_cli_profile_defaults_and_overrides():
    args = cli.parse_args(["--profil", "gunluk", "foto.png"])
    assert args.profil == "gunluk"
    assert args.model == "glm-ocr"
    assert args.okuma == "duz"
    assert args.kalin_kalip == ""

    args_override = cli.parse_args(["foto.png", "--profil", "gunluk", "-m", "dots.mocr", "-f", "md"])
    assert args_override.model == "dots.mocr"
    assert args_override.format == ["md"]


def test_gui_server_profiles_and_build_args(tmp_path: Path, monkeypatch):
    from ocrnow import paths
    monkeypatch.setattr(paths, "INPUT", tmp_path)
    img = tmp_path / "not.png"
    img.write_bytes(b"fake")

    app = server.App()
    st = app.state()
    assert [p["id"] for p in st["profiller"]] == ["kitap", "gunluk", "ders-notu", "fatura", "yazma"]
    assert st["varsayilan"]["profil"] == "kitap"

    args = server.build_args({
        "profil": "ders-notu",
        "dosya": "not.png",
        "model": "dots.mocr",
        "okuma": "duzen",
        "formatlar": ["md", "docx"],
        "isaretle": False,
        "kesme": False,
        "dpi": 200,
    })
    assert "--profil" in args and "ders-notu" in args
    assert "-f" in args and "md" in args and "docx" in args
    assert "--kesme-yok" not in args   # ders-notu profilinde zaten kapalı
