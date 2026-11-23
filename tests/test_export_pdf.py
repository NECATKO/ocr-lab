"""Aranabilir PDF: orijinal sayfa aynen kalır, üstüne satır satır görünmez metin katmanı eklenir."""
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw

from ocrnow import cli, export_pdf
from ocrnow.layout import Block, Line, Page

TEXT = "İstanbul şehri ğşçıöü"
BOX = (100, 200, 700, 240)


def gray_image(size=(1200, 1600)) -> Image.Image:
    img = Image.new("L", size, 235)
    draw = ImageDraw.Draw(img)
    for y in range(0, size[1], 40):
        draw.line([(0, y), (size[0], y)], fill=90, width=3)
    return img


def image_pdf(path: Path, size=(1200, 1600), dpi=144) -> Path:
    """1200×1600 px görsel 144 dpi ile → MediaBox [0 0 600 800]."""
    gray_image(size).save(path, "PDF", resolution=dpi)
    return path


def rotated_pdf(path: Path) -> Path:
    """/Rotate 90, CropBox [10 20 610 820]; MediaBox CropBox'ı kapsasın diye genişletildi."""
    image_pdf(path)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        pg = pdf.pages[0]
        pg.obj.MediaBox = pikepdf.Array([0, 0, 620, 840])
        pg.obj.CropBox = pikepdf.Array([10, 20, 610, 820])
        pg.obj.Rotate = 90
        pdf.save(path)
    return path


def text_pdf(path: Path) -> Path:
    """Görünür Helvetica metni olan tek sayfalık PDF (CamScanner OCR'ı açık dışa aktarım gibi)."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    content = pdf.make_stream(b"BT /F1 14 Tf 72 700 Td (Kaynakta zaten metin var) Tj ET")
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=pikepdf.Array([0, 0, 600, 800]),
                              Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)), Contents=content)
    pdf.pages.append(pikepdf.Page(page))
    pdf.save(path)
    return path


def block(text: str, box, lines=None) -> Block:
    return Block("Text", box, text, lines if lines is not None else [Line(0, len(text), False, box)])


def page(source, *blocks, index=1, width=1200, height=1600, failed=False) -> Page:
    blocks = blocks or (block(TEXT, BOX),)
    return Page(str(source), index, width, height, "dots.mocr", "duzen", list(blocks), failed=failed)


def extract(path: Path) -> list[str]:
    doc = pdfium.PdfDocument(str(path))
    try:
        return [doc[i].get_textpage().get_text_range() for i in range(len(doc))]
    finally:
        doc.close()


def charboxes(path: Path, index=0, loose=False) -> list[tuple[str, tuple[float, float, float, float]]]:
    """Sayfa uzayında karakter kutuları; loose: fontun ascent/descent'iyle (seçim vurgusu bunu izler)."""
    doc = pdfium.PdfDocument(str(path))
    try:
        tp = doc[index].get_textpage()
        return [(tp.get_text_range(i, 1), tp.get_charbox(i, loose=loose)) for i in range(tp.count_chars())]
    finally:
        doc.close()


def streams(pg) -> list:
    c = pg.obj.Contents
    return list(c) if isinstance(c, pikepdf.Array) else [c]


def assert_boxes_within(path: Path, xs, ys):
    boxes = charboxes(path)
    assert boxes
    for ch, (left, bottom, right, top) in boxes + charboxes(path, loose=True):
        assert xs[0] <= left <= right <= xs[1], (ch, left, right)
        assert ys[0] <= bottom <= top <= ys[1], (ch, bottom, top)
    return boxes


def test_pdf_keeps_original_page_bytes(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    out = export_pdf.write_pdf([page(src)], tmp_path / "cikti.pdf")
    with pikepdf.open(src) as a, pikepdf.open(out) as b:
        pa, pb = a.pages[0], b.pages[0]
        out_streams = [s.read_raw_bytes() for s in streams(pb)]
        assert streams(pa)[0].read_raw_bytes() in out_streams
        src_imgs = [im.read_raw_bytes() for im in pa.get_images().values()]
        out_imgs = [im.read_raw_bytes() for im in pb.get_images().values()]
        assert src_imgs and src_imgs == out_imgs
        assert list(pb.mediabox) == list(pa.mediabox) == [0, 0, 600, 800]


def test_pdf_text_extracts_and_sits_on_line(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    out = export_pdf.write_pdf([page(src)], tmp_path / "cikti.pdf")
    assert TEXT in extract(out)[0]
    boxes = assert_boxes_within(out, (48, 352), (676, 702))
    assert boxes[-1][1][2] >= 340


def test_pdf_layer_scales_from_page_size_not_dpi(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    p = page(src, block(TEXT, (150, 300, 1050, 360)), width=1800, height=2400)
    out = export_pdf.write_pdf([p], tmp_path / "cikti.pdf")
    assert TEXT in extract(out)[0]
    boxes = assert_boxes_within(out, (48, 352), (676, 702))
    assert boxes[-1][1][2] >= 340


def test_pdf_layer_on_rotated_page(tmp_path):
    src = rotated_pdf(tmp_path / "kaynak.pdf")
    out = export_pdf.write_pdf([page(src, width=1600, height=1200)], tmp_path / "cikti.pdf")
    assert TEXT in extract(out)[0]
    assert_boxes_within(out, (108, 132), (68, 372))


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_pdf_layer_matches_pdfium_render(tmp_path, rotate):
    """Uçtan uca: pdfium'un çizdiği görüntüde siyah çubuğun piksel kutusu satır kutusu olur; katman o çubuğun
    sayfa uzayındaki yerine oturmalı (cm tablosu pdfium'un /Rotate + CropBox yorumuyla bağımsızca sınanır)."""
    bar = (100, 400, 400, 420) if rotate in (0, 180) else (300, 100, 320, 400)   # görünende hep yatay
    src = tmp_path / "kaynak.pdf"
    pdf = pikepdf.new()
    x0, y0, x1, y1 = bar
    pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
        Type=pikepdf.Name.Page, MediaBox=pikepdf.Array([0, 0, 620, 840]), CropBox=pikepdf.Array([10, 20, 610, 820]),
        Rotate=rotate, Contents=pdf.make_stream(f"0 g {x0} {y0} {x1 - x0} {y1 - y0} re f".encode()))))
    pdf.save(src)
    doc = pdfium.PdfDocument(str(src))
    try:
        img = doc[0].render(scale=2).to_pil().convert("L")
    finally:
        doc.close()
    dark = img.point(lambda v: 255 if v < 128 else 0).getbbox()
    out = export_pdf.write_pdf([page(src, block(TEXT, dark), width=img.width, height=img.height)],
                               tmp_path / "cikti.pdf")
    assert TEXT in extract(out)[0]
    assert_boxes_within(out, (x0 - 3, x1 + 3), (y0 - 3, y1 + 3))


def test_pdf_hyphen_and_rtl_extract(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    hyph = block("bu senâkâr adam", (100, 200, 700, 300),
                 [Line(0, 7, True, (100, 200, 700, 240)), Line(7, 15, False, (100, 260, 700, 300))])
    arabic = block("بسم الله الرحمن الرحيم", (100, 400, 700, 440))
    out = export_pdf.write_pdf([page(src, hyph, arabic)], tmp_path / "cikti.pdf")
    text = extract(out)[0]
    assert "senâkâr" in text
    assert text.count("kâr") == 1
    assert "بسم الله" in text


def test_pdf_rtl_line_runs_right_to_left(tmp_path):
    """Arapça satır mantıksal sırada yazılır ama ilk harf görüntüdeki gibi sağda durur (negatif Tz)."""
    src = image_pdf(tmp_path / "kaynak.pdf")
    out = export_pdf.write_pdf([page(src, block("بسم الله", BOX))], tmp_path / "cikti.pdf")
    assert extract(out) == ["بسم الله"]
    boxes = assert_boxes_within(out, (48, 352), (676, 702))
    lefts = [b[0] for _, b in boxes]
    assert lefts == sorted(lefts, reverse=True)


def test_pdf_skips_layer_when_source_has_text(tmp_path):
    src = text_pdf(tmp_path / "metinli.pdf")
    warnings: list[str] = []
    out = export_pdf.write_pdf([page(src)], tmp_path / "cikti.pdf", warnings)
    assert extract(out) == extract(src)
    assert any("zaten metin katmanı" in w for w in warnings)


def test_pdf_mixes_two_pdfs_and_image(tmp_path):
    a = image_pdf(tmp_path / "a.pdf")
    b = image_pdf(tmp_path / "b.pdf", size=(800, 1000), dpi=100)
    jpg = tmp_path / "c.jpg"
    gray_image((600, 900)).convert("RGB").save(jpg, "JPEG", quality=80, dpi=(150, 150))
    pages = [page(a, block("birinci sayfa", BOX)),
             page(b, block("ikinci sayfa", (50, 100, 500, 130)), width=800, height=1000),
             page(jpg, block("üçüncü sayfa", (50, 100, 500, 130)), width=600, height=900)]
    out = export_pdf.write_pdf(pages, tmp_path / "cikti.pdf")
    texts = extract(out)
    assert len(texts) == 3
    assert "birinci" in texts[0] and "ikinci" in texts[1] and "üçüncü" in texts[2]
    with pikepdf.open(out) as pdf:
        assert list(pdf.pages[1].mediabox) == [0, 0, 576, 720]
        assert list(pdf.pages[2].mediabox) == [0, 0, 288, 432]
        imgs = list(pdf.pages[2].get_images().values())
        assert len(imgs) == 1 and imgs[0].Filter == pikepdf.Name.DCTDecode
        assert imgs[0].read_raw_bytes() == jpg.read_bytes()


def test_pdf_same_source_page_twice_gets_own_layer(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    out = export_pdf.write_pdf([page(src, block("birinci kopya", BOX)), page(src, block("ikinci kopya", BOX))],
                               tmp_path / "cikti.pdf")
    assert extract(out) == ["birinci kopya", "ikinci kopya"]


def test_pdf_png_source_embedded_losslessly(tmp_path):
    png = tmp_path / "sayfa.png"
    img = gray_image((400, 600))
    img.save(png)
    out = export_pdf.write_pdf([page(png, block(TEXT, (20, 40, 360, 80)), width=400, height=600)],
                               tmp_path / "cikti.pdf")
    assert TEXT in extract(out)[0]
    with pikepdf.open(out) as pdf:
        assert list(pdf.pages[0].mediabox) == [0, 0, 144, 216]   # DPI yok → DEFAULT_DPI (200)
        (raw,) = pdf.pages[0].get_images().values()
        assert pikepdf.PdfImage(raw).as_pil_image().convert("L").tobytes() == img.tobytes()


def test_searchable_pdf_unboxed_blocks_from_image(tmp_path):
    """Satır kutusu olmayan bloklar (tesseract yok) blok kutusuna bölünerek yine aranabilir olur."""
    img = tmp_path / "sayfa.png"
    Image.new("RGB", (400, 600), "white").save(img)
    p = Page(source=str(img), index=1, width=400, height=600, engine="dots.mocr", mode="duzen", blocks=[
        Block("Text", (20, 40, 360, 80), "İstanbul şehri ğşçıöü âîû"),
        Block("Text", (20, 120, 360, 160), "بسم الله الرحمن الرحيم"),
    ])
    out = export_pdf.write_pdf([p], tmp_path / "aranabilir.pdf")
    (extracted,) = extract(out)
    assert "İstanbul" in extracted
    assert "ğşçıöü" in extracted
    assert "بسم الله" in extracted


def test_pdf_missing_source_gets_placeholder_with_warning(tmp_path):
    warnings: list[str] = []
    out = export_pdf.write_pdf([page(tmp_path / "yok.pdf")], tmp_path / "cikti.pdf", warnings)
    texts = extract(out)
    assert len(texts) == 1 and TEXT in texts[0]
    assert any("bulunamadı" in w and "yok.pdf" in w for w in warnings)


def test_pdf_failed_page_has_no_text(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    out = export_pdf.write_pdf([page(src, failed=True)], tmp_path / "cikti.pdf")
    assert extract(out) == [""]


def test_pdf_skips_degenerate_line_box(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    p = page(src, block("sıfır", (300, 200, 300, 240)), block(TEXT, BOX))
    out = export_pdf.write_pdf([p], tmp_path / "cikti.pdf")
    text = extract(out)[0]
    assert TEXT in text and "sıfır" not in text


def _content(path: Path) -> bytes:
    with pikepdf.open(path) as pdf:
        return b"".join(s.read_bytes() for s in streams(pdf.pages[0]))


def test_pdf_actual_text_flag(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    two = block("bir iki", (100, 200, 700, 300),
                [Line(0, 3, False, (100, 200, 700, 240)), Line(4, 7, False, (100, 260, 700, 300))])
    on = export_pdf.write_pdf([page(src, two)], tmp_path / "acik.pdf", actual_text=True)
    off = export_pdf.write_pdf([page(src, two)], tmp_path / "kapali.pdf")
    assert b"/ActualText" in _content(on)
    assert "bir iki".encode("utf-16-be").hex().upper().encode() in _content(on).upper()
    assert b"/ActualText" not in _content(off)


def test_cli_pdf_exporter_passes_warnings(tmp_path):
    w: list[str] = []
    out = tmp_path / "cikti.pdf"
    cli.EXPORTERS["pdf"]([page(tmp_path / "yok.pdf")], out, {"warnings": w, "highlight": False, "bold": None})
    assert out.is_file()
    assert any("bulunamadı" in x for x in w)
