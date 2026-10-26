import json

from ocrnow.layout import (Block, Line, Mark, Page, page_from_json, page_to_json,
                           parse_output, smart_resize)


def test_spike_blocks(spike_layout):
    blocks, warns = parse_output(spike_layout, True, (1653, 2339))
    assert [b.category for b in blocks] == ["Page-header"] + ["Text"] * 5 + ["Footnote", "Page-footer"]
    assert blocks[-1].text == "412" and not warns


def test_large_image_bbox_scaled():
    raw = '[{"bbox": [100, 100, 200, 200], "category": "Text", "text": "a"}]'
    (b,), _ = parse_output(raw, True, (3306, 4678))    # 15.5 MP → küçültülür
    rh, rw = smart_resize(4678, 3306)
    assert b.bbox == (round(100 * 3306 / rw), round(100 * 4678 / rh), round(200 * 3306 / rw), round(200 * 4678 / rh))


def test_truncated_json_recovers():
    raw = '[{"bbox": [1,1,2,2], "category": "Text", "text": "bir"}, {"bbox": [1,3,2,4], "category": "Text", "text": "iki ya'
    blocks, warns = parse_output(raw, True, (100, 100))
    assert blocks[0].text == "bir" and "iki ya" in blocks[-1].text and "bozuk JSON" in warns[0]


def test_json_roundtrip():
    p = Page("a.pdf", 1, 10, 10, "dots.mocr", "duzen",
             [Block("Text", (0, 0, 1, 1), "x y", [Line(0, 3, False)], True, [Mark(0, 1, "kesme")])])
    assert page_from_json(page_to_json(p)) == p


def test_roundtrip_full_page_and_none_bbox_non_ascii():
    p = Page("a.pdf", 2, 10, 20, "dots.mocr", "duzen",
             [Block("Text", None, "çğıöşü ‘ayın’"), Block("Picture", (1, 2, 3, 4), "[Görsel]")],
             tess_tsv="a\tb", book_page="412", book_page_inferred=True, book_page_side="sag",
             warnings=["uyarı"])
    s = page_to_json(p)
    assert "çğıöşü" in s                                  # ensure_ascii=False
    q = page_from_json(s)
    assert q == p and q.blocks[1].bbox == (1, 2, 3, 4) and isinstance(q.blocks[1].bbox, tuple)
    assert q.blocks[0].bbox is None


def test_failed_flag_roundtrip_and_old_json_default():
    p = Page("a.pdf", 3, 0, 0, "dots.mocr", "duzen", [], failed=True)
    assert page_from_json(page_to_json(p)).failed is True
    old = json.loads(page_to_json(Page("a.pdf", 1, 10, 10, "dots.mocr", "duzen", [])))
    del old["failed"]                                     # alan eklenmeden önce yazılmış önbellek
    assert page_from_json(json.dumps(old)).failed is False


def test_smart_resize_values():
    assert smart_resize(2339, 1653) == (2352, 1652)       # 28'in katları, 4 MP altında
    h, w = smart_resize(4678, 3306)
    assert h % 28 == 0 and w % 28 == 0 and h * w <= 4_000_000
    h, w = smart_resize(10, 10)                           # min_pixels'e yükseltilir
    assert h * w >= 3136 and h % 28 == 0 and w % 28 == 0


def test_plain_mode_single_text_block():
    blocks, warns = parse_output("  bir  iki\nüç  ", False, (100, 100))
    assert blocks == [Block("Text", None, "bir iki üç")]
    assert warns == ["düz metin modu: başlık, dipnot ve sayfa numarası ayrılamadı"]


def test_fences_stripped_and_text_normalized_and_picture():
    raw = ('```json\n[{"bbox": [0, 0, 10, 10], "category": "Picture"},'
           ' {"bbox": [0, 0, 10, 10], "category": "Text", "text": "a \\n  b"}]\n```')
    blocks, warns = parse_output(raw, True, (100, 100))
    assert [b.text for b in blocks] == ["[Görsel]", "a b"] and not warns


def test_picture_text_replaced_even_if_model_gave_text():
    raw = '[{"bbox": [0, 0, 1, 1], "category": "Picture", "text": "x"}]'
    (b,), _ = parse_output(raw, True, (100, 100))
    assert b.text == "[Görsel]"


def test_truncated_inside_fence():
    raw = '```json\n[{"bbox": [1,1,2,2], "category": "Title", "text": "bir"}, {"bbox": [1,3,'
    blocks, warns = parse_output(raw, True, (100, 100))
    assert [b.category for b in blocks] == ["Title"]       # {"bbox": [1,3, parçası belgeye girmez
    assert blocks[0].text == "bir" and "1 blok kurtarıldı" in warns[0]


def test_empty_and_degenerate_outputs_do_not_crash():
    assert parse_output("[]", True, (100, 100)) == ([], [])
    assert parse_output("", True, (100, 100)) == ([], ["model boş yanıt verdi"])
    assert parse_output("  \n ", True, (100, 100)) == ([], ["model boş yanıt verdi"])
    assert parse_output("```json\n[]\n```", True, (100, 100)) == ([], [])
    # JSON nesnesi: tek blok sayılır
    (b,), w = parse_output('{"bbox": [0,0,5,5], "category": "Title", "text": "T"}', True, (100, 100))
    assert b.category == "Title" and not w
    # düz metin cevabı: kurtarılacak blok yok, metin Text olur
    (b,), w = parse_output("merhaba dünya", True, (100, 100))
    assert b.text == "merhaba dünya" and "0 blok kurtarıldı" in w[0]


def test_malformed_items():
    raw = json.dumps([{"category": "Text", "text": "bbox yok"},
                      {"bbox": [1, 2, 3, 4], "text": "kategori yok"},
                      {"bbox": "kötü", "category": "Text", "text": "bbox bozuk"},
                      "dize", 7, {"bbox": [1, 2, 3, 4], "category": "Text", "text": None}])
    blocks, warns = parse_output(raw, True, (100, 100))
    assert [b.text for b in blocks] == ["bbox yok", "kategori yok", "bbox bozuk", ""]
    assert blocks[0].bbox is None and blocks[2].bbox is None
    assert blocks[1].category == "Text"
    assert warns                                          # atlananlar ve düzeltilenler için uyarı var


def test_non_json_reply_kept_as_text():
    blocks, warns = parse_output("Dipnot [1] metin burada", True, (100, 100))
    assert [(b.category, b.text) for b in blocks] == [("Text", "Dipnot [1] metin burada")]
    assert "0 blok kurtarıldı" in warns[0]


def test_prefix_and_trailing_text_kept():
    raw = 'İşte sonuç: [{"bbox": [1,1,2,2], "category": "Text", "text": "bir"}] ve son söz [2]'
    blocks, warns = parse_output(raw, True, (100, 100))
    assert [b.text for b in blocks] == ["İşte sonuç:", "bir", "ve son söz [2]"]
    assert "1 blok kurtarıldı" in warns[0]


def test_truncated_text_keeps_brackets_and_decodes_escapes():
    raw = '[{"bbox": [1,1,2,2], "category": "Text", "text": "bkz. [1]'
    blocks, _ = parse_output(raw, True, (100, 100))
    assert blocks[-1].text == "bkz. [1]"
    raw = r'[{"bbox": [1,1,2,2], "category": "Text", "text": "o \"dedi\" ki\n\u00e7a\u00'
    blocks, _ = parse_output(raw, True, (100, 100))
    assert blocks[-1].text == 'o "dedi" ki ça'
    raw = '[{"bbox": [1,1,2,2], "category": "Text", "text": "tamam"'      # değer tam, nesne kapanmamış
    blocks, _ = parse_output(raw, True, (100, 100))
    assert blocks[-1].text == "tamam"


def test_fragment_without_text_value_emits_no_block():
    for raw in ('[{"bbox": [1,3,', '[{"bbox": [1,3,2,4], "category": "Text", "te',
                '[{"bbox": [1,3,2,4], "category": "Text", "text": '):
        blocks, warns = parse_output(raw, True, (100, 100))
        assert blocks == [] and "bozuk JSON" in warns[0]


def test_non_finite_bbox_and_non_string_text():
    raw = ('[{"bbox": [NaN, 0, 1, 1], "category": "Text", "text": "a"},'
           ' {"bbox": [0, 0, Infinity, 1], "category": "Text", "text": ["b", 3, "c"]},'
           ' {"bbox": [0, 0, 1, 1], "category": "Text", "text": 42}]')
    blocks, _ = parse_output(raw, True, (100, 100))
    assert [(b.bbox, b.text) for b in blocks] == [(None, "a"), (None, "b c"), ((0, 0, 1, 1), "")]


def test_inner_unescaped_quotes_closed_and_next_object_survives():
    raw = ('[{"bbox": [1,1,2,2], "category": "Text", "text": "ilk"}, '
           '{"bbox": [1,3,2,4], "category": "Text", "text": "he said "hi" and more"}, '
           '{"bbox": [1,5,2,6], "category": "Footnote", "text": "sonraki"}]')
    blocks, warns = parse_output(raw, True, (100, 100))
    assert [(b.category, b.text) for b in blocks] == [
        ("Text", "ilk"), ("Text", 'he said "hi" and more'), ("Footnote", "sonraki")]
    assert blocks[1].bbox == (1, 3, 2, 4) and "3 blok kurtarıldı" in warns[0]


def test_inner_unescaped_quotes_in_last_object_with_trailing_text():
    raw = ('[{"bbox": [1,1,2,2], "category": "Text", "text": "o "dedi", "yok" ki"}] Son söz')
    blocks, _ = parse_output(raw, True, (100, 100))
    assert [b.text for b in blocks] == ['o "dedi", "yok" ki', "Son söz"]


def test_inner_unescaped_quotes_truncated():
    raw = ('[{"bbox": [1,1,2,2], "category": "Text", "text": "ilk"}, '
           '{"bbox": [1,3,2,4], "category": "Text", "text": "he said "hi" and mo')
    blocks, _ = parse_output(raw, True, (100, 100))
    assert [b.text for b in blocks] == ["ilk", 'he said "hi" and mo']


def test_fences_around_array_removed_from_prefix_and_tail():
    raw = 'İşte:\n```json\n[{"bbox": [1,1,2,2], "category": "Text", "text": "bir"}]\n```\nSon söz'
    blocks, _ = parse_output(raw, True, (100, 100))
    assert [b.text for b in blocks] == ["İşte:", "bir", "Son söz"]


def test_item_without_text_uses_single_other_string_field():
    raw = ('[{"bbox": [1,1,2,2], "category": "Text", "content": "içerik"},'
           ' {"bbox": [1,1,2,2], "category": "Text", "a": "x", "b": "y"},'
           ' {"bbox": [1,1,2,2], "category": "Text"}]')
    blocks, _ = parse_output(raw, True, (100, 100))
    assert [b.text for b in blocks] == ["içerik", "", ""]


def emphasis(text, layout=True):
    """Tek Text bloklu cevaptan (metin, [(başlangıç, bitiş, tür)])."""
    raw = json.dumps([{"bbox": [0, 0, 10, 10], "category": "Text", "text": text}]) if layout else text
    (b,), _ = parse_output(raw, layout, (100, 100))
    return b.text, [(m.start, m.end, m.kind) for m in b.marks]


def test_markdown_emphasis_becomes_marks():
    # R26: model italiği *...*, kalını **...** ile işaretliyor; yıldızlar metne girmez
    assert emphasis("bkz. *a.g.e.*, s. 5") == ("bkz. a.g.e., s. 5", [(5, 11, "italik")])
    assert emphasis("*Sâniyen:* dedi") == ("Sâniyen: dedi", [(0, 8, "italik")])
    assert emphasis("bir **kalın** ve *eğik*") == ("bir kalın ve eğik", [(4, 9, "kalin"), (13, 17, "italik")])
    assert emphasis("***ikisi***") == ("ikisi", [(0, 5, "italik"), (0, 5, "kalin")])
    assert emphasis("**dış *iç* dış**") == ("dış iç dış", [(0, 10, "kalin"), (4, 6, "italik")])
    assert emphasis("düz *metin*", layout=False) == ("düz metin", [(4, 9, "italik")])   # düz mod da


def test_lone_or_unpaired_asterisks_are_kept():
    # tek başına * kitaplarda dipnot simgesi olabilir: dokunulmaz
    for text in ("kelime* bu", "* Dipnot metni", "a * b * c", "kelime*, öteki*.", "**", "* *", "*yarım",
                 "3*4*5", "boş ** iç", "*a *"):
        assert emphasis(text) == (text, []), text
    # iç boşlukla başlayamaz/bitemez
    assert emphasis("* a* ve *b *") == ("* a* ve *b *", [])


def test_emphasis_in_header_and_footer_blocks():
    raw = json.dumps([{"bbox": [0, 0, 10, 10], "category": "Page-header", "text": "*KİTAP*"},
                      {"bbox": [0, 90, 10, 99], "category": "Page-footer", "text": "*412*"}])
    blocks, _ = parse_output(raw, True, (100, 100))
    assert [(b.text, [m.kind for m in b.marks]) for b in blocks] == [("KİTAP", ["italik"]), ("412", ["italik"])]
