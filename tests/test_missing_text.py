"""Modelin hiçbir bloğa almadığı, tesseract'ın gördüğü metin satırları (olası eksik dipnot/satır)."""
from ocrnow.layout import Block
from ocrnow.tesslines import TessLine, uncovered_text

W, H = 1000, 1400
GOVDE = Block("Text", (100, 100, 900, 300), "Tarih ilmi insanların geçmişini anlatır ve öğretir")


def satir(y, metin, x1=100, x2=900):
    return TessLine((x1, y, x2, y + 30), metin)


def gorulen(bloklar, satirlar):
    return [l.text for l in uncovered_text(bloklar, satirlar, (W, H))]


def test_two_line_footnote_missing_from_model_is_found():
    dipnot = [satir(1250, "Bkz. İbn Haldun Mukaddime Beyrut"), satir(1290, "baskısı tarihsiz sayfa üç")]
    assert gorulen([GOVDE], [satir(120, "Tarih ilmi"), *dipnot]) == [d.text for d in dipnot]


def test_single_numbered_footnote_at_bottom_is_found():
    assert gorulen([GOVDE], [satir(1300, "12 Felsefe-i Târih Tahran baskısı")]) == ["12 Felsefe-i Târih Tahran baskısı"]


def test_single_unnumbered_or_high_line_is_ignored():
    assert gorulen([GOVDE], [satir(1300, "Felsefe-i Târih Tahran baskısı")]) == []
    assert gorulen([GOVDE], [satir(500, "12 Felsefe-i Târih Tahran baskısı")]) == []


def test_text_already_read_by_model_is_not_reported():
    dipnot = Block("Footnote", (100, 1240, 300, 1260), "45 Ay. yer.")
    assert gorulen([GOVDE, dipnot], [satir(1250, "45 Ay.yer. marn", x2=860)]) == []
    tekrar = [satir(400, "Tarih ilmi insanların geçmişini"), satir(440, "anlatır ve öğretir")]
    assert gorulen([GOVDE], tekrar) == []


def test_noise_and_page_numbers_are_ignored():
    gurultu = [satir(1300, "Nana > RA ea"), satir(1330, "ba Ğ A İYİ"), satir(1360, "75")]
    assert gorulen([GOVDE], gurultu) == []


def test_lines_inside_any_block_category_are_covered():
    altlik = Block("Page-footer", (100, 1240, 900, 1340), "75")
    resim = Block("Picture", (100, 400, 900, 900), "")
    satirlar = [satir(1250, "Bkz. İbn Haldun Mukaddime Beyrut"), satir(1290, "baskısı tarihsiz sayfa üç"),
                satir(500, "Resimdeki levhada yazan sözler"), satir(540, "okunaklı biçimde görünüyor")]
    assert gorulen([GOVDE, altlik, resim], satirlar) == []


def test_far_apart_lines_do_not_form_a_group():
    assert gorulen([GOVDE], [satir(500, "Bkz. İbn Haldun Mukaddime"), satir(900, "baskısı tarihsiz sayfa")]) == []


def test_plain_mode_without_boxes_is_skipped():
    assert gorulen([Block("Text", None, "x")], [satir(1250, "Bkz. İbn Haldun Mukaddime"),
                                               satir(1290, "baskısı tarihsiz sayfa")]) == []
