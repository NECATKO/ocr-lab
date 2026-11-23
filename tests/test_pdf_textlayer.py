from ocrnow.layout import Block, Line, Page
from ocrnow.pdf_textlayer import layer_lines

B1, B2, B3 = (0, 0, 100, 20), (0, 30, 100, 50), (0, 60, 100, 80)


def page_of(*blocks, failed=False, width=1000, height=1500):
    return Page("k.pdf", 1, width, height, "dots.mocr", "duzen", list(blocks), failed=failed)


def texts(page):
    return [t.text for t in layer_lines(page)]


def test_lines_follow_boxes():
    b = Block("Text", (0, 0, 100, 50), "bu senâkâr geldi", [Line(0, 2, False, B1), Line(3, 16, False, B2)])
    out = layer_lines(page_of(b))
    assert [(t.text, t.box) for t in out] == [("bu", B1), ("senâkâr geldi", B2)]
    assert all(t.block == 0 for t in out)


def test_hyphenated_word_kept_whole_on_first_line():
    b = Block("Text", (0, 0, 100, 50), "bu senâkâr Vilâyet-i Haleb",
              [Line(0, 7, True, B1), Line(7, 26, False, B2)])
    assert texts(page_of(b)) == ["bu senâkâr", "Vilâyet-i Haleb"]


def test_existing_hyphen_not_joined():
    b = Block("Text", (0, 0, 100, 50), "Defter-dâr oldu", [Line(0, 7, False, B1), Line(7, 15, False, B2)])
    assert texts(page_of(b)) == ["Defter-", "dâr oldu"]


def test_hyphen_word_spanning_whole_next_line():
    b = Block("Text", (0, 0, 100, 80), "ab cdefgh ij",
              [Line(0, 5, True, B1), Line(5, 9, False, B2), Line(9, 12, False, B3)])
    out = layer_lines(page_of(b))
    assert [t.text for t in out] == ["ab cdefgh", "ij"]
    assert out[1].box == B3


def test_hyphen_at_end_of_text():
    b = Block("Text", (0, 0, 100, 50), "ab cdef", [Line(0, 5, True, B1), Line(5, 7, False, B2)])
    assert texts(page_of(b)) == ["ab cdef"]


def test_rtl_kept_in_logical_order():
    b = Block("Text", (0, 0, 100, 20), "بسم الله", [Line(0, 8, False, B1)])
    assert layer_lines(page_of(b))[0].text == "بسم الله"


def test_failed_page_has_no_layer():
    b = Block("Text", (0, 0, 100, 20), "metin", [Line(0, 5, False, B1)])
    assert layer_lines(page_of(b, failed=True)) == []


def test_unboxed_block_spread_inside_bbox():
    text = " ".join(["kelime"] * 28)[:200].rstrip()
    boxed = Block("Text", (0, 0, 100, 20), "x", [Line(0, 1, False, (0, 0, 100, 20))])
    bbox = (100, 100, 500, 400)
    unboxed = Block("Text", bbox, text, [Line(0, len(text), False)])
    out = layer_lines(page_of(boxed, unboxed))
    mine = [t for t in out if t.block == 1]
    assert len(mine) > 1
    for t in mine:
        x1, y1, x2, y2 = t.box
        assert bbox[0] <= x1 < x2 <= bbox[2] and bbox[1] <= y1 < y2 <= bbox[3]
    assert " ".join(t.text for t in mine) == text


def test_unboxed_block_with_empty_lines_list():
    b = Block("Text", (100, 100, 500, 400), "bir iki üç", [])
    out = layer_lines(page_of(b))
    assert [t.text for t in out] == ["bir iki üç"]


def test_unboxed_block_without_bbox_uses_page_margin():
    b = Block("Text", None, "bir iki üç", [])
    out = layer_lines(page_of(b, width=1000, height=1500))
    x1, y1, x2, y2 = out[0].box
    assert (x1, y1) == (50, 75) and x2 <= 950 and y2 <= 1425


def test_mixed_boxed_and_unboxed_lines_use_fallback():
    b = Block("Text", (0, 0, 100, 50), "bir iki", [Line(0, 3, False, B1), Line(4, 7, False)])
    assert texts(page_of(b)) == ["bir iki"]


def test_picture_and_empty_blocks_skipped():
    pic = Block("Picture", (0, 0, 100, 100), "[Görsel]", [])
    empty = Block("Text", (0, 0, 100, 20), "  ", [Line(0, 2, False, B1)])
    real = Block("Text", (0, 0, 100, 20), "metin", [Line(0, 5, False, B1)])
    out = layer_lines(page_of(pic, empty, real))
    assert [(t.text, t.block) for t in out] == [("metin", 2)]


def test_table_uses_plain_text():
    t = Block("Table", (0, 0, 400, 100), "<table><tr><td>a</td><td>b</td></tr></table>", [])
    assert texts(page_of(t)) == ["a b"]
