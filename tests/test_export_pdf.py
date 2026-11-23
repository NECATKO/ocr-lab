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


def inherited_pdf(path: Path) -> Path:
    """Elle yazılmış PDF: MediaBox [0 0 600 800], /Rotate 90 ve Resources (görüntü) /Pages düğümünde, sayfada değil."""
    pixels = bytes(range(0, 256, 64)) * 2      # 4×2 gri
    content = b"q 600 0 0 800 0 0 cm /Im1 Do Q"
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 /MediaBox [0 0 600 800] /Rotate 90 "
        b"/Resources << /XObject << /Im1 5 0 R >> >> >>",
        b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 4 /Height 2 /ColorSpace /DeviceGray "
        b"/BitsPerComponent 8 /Length %d >>\nstream\n" % len(pixels) + pixels + b"\nendstream",
    ]
    data, offsets = b"%PDF-1.4\n", []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(data))
        data += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref = len(data)
    data += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    data += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    data += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    path.write_bytes(data)
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


def test_pdf_keeps_source_pdf_version(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.save(src, min_version="1.7")
    out = export_pdf.write_pdf([page(src)], tmp_path / "cikti.pdf")
    with pikepdf.open(out) as pdf:
        assert tuple(map(int, pdf.pdf_version.split("."))) >= (1, 7)
    assert out.read_bytes()[:8] >= b"%PDF-1.7"


def test_pdf_font_name_does_not_collide_with_source(tmp_path):
    """Kaynak sayfada /OcrNowF0 adlı (görünür) bir font zaten varsa katmanın fontu başka ad alır."""
    src = tmp_path / "kaynak.pdf"
    pdf = pikepdf.new()
    helv = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica))
    for i in range(2):
        content = pdf.make_stream(b"BT /OcrNowF0 1 Tf 72 700 Td (x) Tj ET")   # 1 karakter: metinli sayılmaz
        pdf.pages.append(pikepdf.Page(pikepdf.Dictionary(
            Type=pikepdf.Name.Page, MediaBox=pikepdf.Array([0, 0, 600, 800]),
            Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(OcrNowF0=helv)), Contents=content)))
    pdf.save(src)
    out = export_pdf.write_pdf([page(src, block("birinci sayfa", BOX)),
                                page(src, block("ikinci sayfa", BOX), index=2)], tmp_path / "cikti.pdf")
    texts = extract(out)
    assert "birinci sayfa" in texts[0] and "ikinci sayfa" in texts[1]
    with pikepdf.open(out) as pdf:
        for pg in pdf.pages:
            fonts = pg.obj.Resources.Font
            assert fonts.OcrNowF0.BaseFont == pikepdf.Name.Helvetica
            assert any(k != "/OcrNowF0" and v.Subtype == pikepdf.Name.Type0 for k, v in fonts.items())


def test_pdf_inherited_page_attributes(tmp_path):
    """MediaBox, Rotate ve Resources /Pages düğümünde (sayfada değil): çıktı geçerli, katman yerinde, görüntü var."""
    src = inherited_pdf(tmp_path / "kaynak.pdf")
    # pikepdf açarken öznitelikleri sayfaya iter; önkoşul dosyanın kendisinde denetlenir
    assert b"<< /Type /Page /Parent 2 0 R /Contents 4 0 R >>" in src.read_bytes()
    src_imgs = [bytes(range(0, 256, 64)) * 2]
    out = export_pdf.write_pdf([page(src, width=1600, height=1200)], tmp_path / "cikti.pdf")
    with pikepdf.open(out) as pdf:
        assert pdf.check_pdf_syntax() == []
        pg = pdf.pages[0]
        assert [im.read_raw_bytes() for im in pg.get_images().values()] == src_imgs
        assert "/Im1" in pg.obj.Resources.XObject and list(pg.mediabox) == [0, 0, 600, 800]
    assert TEXT in extract(out)[0]
    # görünen 800×600: u 50..350, v (yukarı) 480..500 → X = 600 - v, Y = u
    assert_boxes_within(out, (98, 122), (48, 352))


def test_pdf_opens_each_source_once_for_text_check(tmp_path, monkeypatch):
    src = image_pdf(tmp_path / "kaynak.pdf")
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages.append(pdf.pages[0])
        pdf.save(src)
    opened = []
    real = pdfium.PdfDocument

    def counting(path, *args, **kwargs):
        opened.append(Path(str(path)))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(export_pdf.pdfium, "PdfDocument", counting)
    export_pdf.write_pdf([page(src), page(src, index=2)], tmp_path / "cikti.pdf")
    assert opened.count(src) == 1


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


TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def tsv_row(n, y1, y2, words) -> list[str]:
    """Tek tesseract satırı (level 4) ve kelimeleri (level 5); words: (metin, x1, x2)."""
    x1, x2 = min(w[1] for w in words), max(w[2] for w in words)
    out = [f"4\t1\t1\t1\t{n}\t0\t{x1}\t{y1}\t{x2 - x1}\t{y2 - y1}\t-1\t"]
    out += [f"5\t1\t1\t1\t{n}\t{k}\t{a}\t{y1}\t{b - a}\t{y2 - y1}\t95\t{t}" for k, (t, a, b) in enumerate(words, 1)]
    return out


def test_pdf_descartes_lands_on_its_tesseract_word_box(tmp_path):
    """Satır yapısı kurulamamış blok: aranan kelime tesseract'ın kelime kutusunda vurgulanır."""
    src = image_pdf(tmp_path / "kaynak.pdf")
    text = "Batı düşünür Hume, Descartes, Kant, Sartre, Bergson vs."
    r1 = [("Batı", 100, 180), ("düşünür", 200, 340), ("Hume,", 360, 460), ("Dcscartcs,", 480, 680),
          ("Kant,", 700, 800), ("Sartre,", 820, 960)]
    r2 = [("Bergson", 100, 240), ("vs.", 260, 320)]
    p = page(src, Block("Text", (100, 200, 1000, 300), text, [Line(0, len(text), False)]))
    p.tess_tsv = "\n".join([TSV_HEAD] + tsv_row(1, 200, 240, r1) + tsv_row(2, 260, 300, r2)) + "\n"
    out = export_pdf.write_pdf([p], tmp_path / "cikti.pdf")
    full = extract(out)[0]
    start = full.index("Descartes")
    boxes = charboxes(out)[start:start + len("Descartes")]
    assert "".join(ch for ch, _ in boxes) == "Descartes"
    # piksel → pt (×0,5, y yukarı): x 240..340, y 680..700; boşluk Kant'a kadar uzandığı için sağda küçük pay
    for ch, (left, bottom, right, top) in boxes:
        assert 239 <= left <= right <= 345, (ch, left, right)
        assert 679 <= bottom <= top <= 701, (ch, bottom, top)


def test_pdf_rtl_words_copy_in_logical_order(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    p = page(src, Block("Text", (100, 200, 1000, 240), "بسم الله الرحمن", [Line(0, 15, False)]))
    words = [("بسم", 800, 900), ("الله", 650, 760), ("الرحمن", 450, 610)]
    p.tess_tsv = "\n".join([TSV_HEAD] + tsv_row(1, 200, 240, words)) + "\n"
    out = export_pdf.write_pdf([p], tmp_path / "cikti.pdf")
    assert extract(out) == ["بسم الله الرحمن"]


def test_pdf_actual_text_has_single_spaces_with_word_layer(tmp_path):
    src = image_pdf(tmp_path / "kaynak.pdf")
    p = page(src, Block("Text", (100, 200, 1000, 300), "bir iki üç", [Line(0, 10, False)]))
    p.tess_tsv = "\n".join([TSV_HEAD] + tsv_row(1, 200, 240, [("bir", 100, 160), ("iki", 180, 240)])
                           + tsv_row(2, 260, 300, [("üç", 100, 140)])) + "\n"
    out = export_pdf.write_pdf([p], tmp_path / "cikti.pdf", actual_text=True)
    assert "bir iki üç".encode("utf-16-be").hex().upper().encode() in _content(out).upper()


def test_pdf_arabic_words_inside_turkish_line_keep_spaces(tmp_path):
    """Türkçe satırdaki Arapça kelimeler sağdan sola çizilir; boşlukları komşu kelimeye yapışmamalı."""
    src = image_pdf(tmp_path / "kaynak.pdf")
    text = "Farsça’daki ماه روز sözcüğünden alındığı"
    p = page(src, Block("Text", (100, 200, 1000, 240), text, [Line(0, len(text), False)]))
    words = [("Farsça'daki", 100, 280), ("ماه", 300, 360), ("روز", 380, 440), ("sözcüğünden", 460, 660),
             ("alındığı", 680, 800)]
    p.tess_tsv = "\n".join([TSV_HEAD] + tsv_row(1, 200, 240, words)) + "\n"
    out = export_pdf.write_pdf([p], tmp_path / "cikti.pdf")
    assert extract(out)[0].split() == text.split()
