from ocrnow.layout import Block
from ocrnow.pagenum import infer, read_book_page


def test_gap_filled():
    assert infer(["412", None, "414"]) == [("412", False), ("413", True), ("414", False)]


def test_conflict_corrected():
    assert infer(["412", "431", "414"])[1] == ("413", True)


def test_start_given():
    assert infer([None, None], start=10) == [("10", True), ("11", True)]


def test_no_clue():
    assert infer([None, None]) == [("PDF s. 1", True), ("PDF s. 2", True)]


def test_roman_kept():
    assert infer(["xii", None])[0] == ("xii", False)


def test_roman_does_not_vote():
    out = infer(["xii", "xiii", "1", None])
    assert out[:2] == [("xii", False), ("xiii", False)]
    assert out[2:] == [("1", False), ("2", True)]


def test_start_overrides_read():
    assert infer(["5", "6", "7"], start=10) == [("10", True), ("11", True), ("12", True)]
    assert infer(["10", None], start=10) == [("10", False), ("11", True)]


def test_two_consistent_runs_are_both_kept():
    # R21: kendi içinde tutarlı iki koşu (atlanan tarama sayfası, iki ayrı alıntı) ikisi de okunduğu gibi kalır
    assert infer(["100", "101", "300", "301", "302"]) == [("100", False), ("101", False), ("300", False),
                                                          ("301", False), ("302", False)]
    assert [x[0] for x in infer(["100", "101", "300", "301"])] == ["100", "101", "300", "301"]


def test_second_excerpt_restarting_numbers_is_kept():
    out = infer(["1", "2", "3", "4", "5", "6", "1", "2"])
    assert out[6:] == [("1", False), ("2", False)]


def test_real_break_keeps_correct_readings():
    # C1: 412–415, sonra (iki tarama sayfası atlanmış) 418–425
    read = [str(n) for n in [*range(412, 416), *range(418, 426)]]
    assert infer(read) == [(n, False) for n in read]


def test_unsupported_reading_takes_previous_supported_offset():
    assert infer(["412", "413", "500", "415"]) == [("412", False), ("413", False), ("414", True), ("415", False)]


def test_unsupported_first_reading_takes_next_supported_offset():
    assert infer(["9", "413", "414"]) == [("412", True), ("413", False), ("414", False)]


def test_gap_filled_from_previous_supported_run():
    assert infer(["412", "413", None, "418", "419"]) == [("412", False), ("413", False), ("414", True),
                                                          ("418", False), ("419", False)]
    assert infer([None, "413", "414"]) == [("412", True), ("413", False), ("414", False)]


def test_support_window_is_two_readings():
    # 412 ile 414 arada bir aykırı okumayla iki okuma uzaklıkta: birbirini destekler
    assert infer(["412", "800", "414"]) == [("412", False), ("413", True), ("414", False)]
    # arada iki aykırı okuma varsa 412 ile 415 üç okuma uzakta, birbirini görmez; hiçbiri destekli değil → hepsi korunur
    assert [x[0] for x in infer(["412", "700", "800", "415"])] == ["412", "700", "800", "415"]


def test_no_supported_reading_keeps_all_readings():
    # iki farklı okuma: hangisinin yanlış olduğu bilinemez, okunduğu gibi kalır
    assert infer(["412", "431"]) == [("412", False), ("431", False)]
    assert infer(["412", None, "431"]) == [("412", False), ("413", True), ("431", False)]


def test_nonpositive_derived_falls_back_to_pdf_label():
    # ofset -1: ilk iki sayfa 0 ve altına düşer
    assert infer([None, None, "1", "2", "3"]) == [("PDF s. 1", True), ("PDF s. 2", True),
                                                   ("1", False), ("2", False), ("3", False)]


def test_header_number_and_side():
    blocks = [Block("Page-header", (1300, 100, 1500, 140), "TEZKİRE 413")]
    assert read_book_page(blocks, 1653) == ("413", "sag")


def test_footer_with_decoration_and_left_side():
    blocks = [Block("Text", (100, 200, 900, 300), "Metin 12"),
              Block("Page-footer", (100, 2200, 300, 2240), "— 412 —")]
    assert read_book_page(blocks, 1653) == ("412", "sol")
    blocks = [Block("Page-footer", (700, 2200, 900, 2240), "412 TEZKİRE")]
    assert read_book_page(blocks, 1653) == ("412", "sol")


def test_footer_preferred_over_header():
    blocks = [Block("Page-header", (100, 100, 300, 140), "7"),
              Block("Page-footer", (1300, 2200, 1500, 2240), "413")]
    assert read_book_page(blocks, 1653) == ("413", "sag")


def test_footer_without_number_falls_to_header():
    blocks = [Block("Page-footer", (100, 2200, 300, 2240), "TEZKİRE"),
              Block("Page-header", (1300, 100, 1500, 140), "413")]
    assert read_book_page(blocks, 1653) == ("413", "sag")


def test_roman_case_rules():
    foot = lambda t: [Block("Page-footer", (100, 2200, 300, 2240), t)]
    head = lambda t: [Block("Page-header", (100, 100, 300, 140), t)]
    assert read_book_page(foot("xii"), 1653) == ("xii", "sol")
    assert read_book_page(foot("XII"), 1653) == ("XII", "sol")
    assert read_book_page(head("xii"), 1653) == ("xii", "sol")
    assert read_book_page(head("CİLT I"), 1653) == (None, None)
    assert read_book_page(head("BÖLÜM V"), 1653) == (None, None)
    assert read_book_page(foot("xiix"), 1653) == (None, None)  # geçersiz roma


def test_no_bbox_gives_no_side_and_nothing_found():
    assert read_book_page([Block("Page-footer", None, "9")], 1653) == ("9", None)
    assert read_book_page([Block("Text", (0, 0, 10, 10), "5")], 1653) == (None, None)
    assert read_book_page([], 1653) == (None, None)
