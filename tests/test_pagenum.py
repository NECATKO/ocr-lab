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


def test_outlier_at_run_edge_is_kept():
    # R25: koşunun içinde değil (yalnız bir yanında koşu var): okunduğu gibi kalır
    assert infer(["9", "413", "414"]) == [("9", False), ("413", False), ("414", False)]
    assert infer(["412", "413", "9"]) == [("412", False), ("413", False), ("9", False)]


def test_gap_filled_from_previous_supported_run():
    assert infer(["412", "413", None, "418", "419"]) == [("412", False), ("413", False), ("414", True),
                                                          ("418", False), ("419", False)]
    # R25: koşudan önceki okunamayan sayfa tahmin edilmez (koşunun içinde ya da devamında değil)
    assert infer([None, "413", "414"]) == [("PDF s. 1", True), ("413", False), ("414", False)]


def test_support_window_is_two_readings():
    # 412 ile 414 arada bir aykırı okumayla iki okuma uzaklıkta: birbirini destekler
    assert infer(["412", "800", "414"]) == [("412", False), ("413", True), ("414", False)]
    # arada iki aykırı okuma varsa 412 ile 415 üç okuma uzakta, birbirini görmez; hiçbiri destekli değil → hepsi korunur
    assert [x[0] for x in infer(["412", "700", "800", "415"])] == ["412", "700", "800", "415"]


def test_readings_that_do_not_agree_are_trusted():
    # R25: okunan numara, komşularıyla tutmadığı için köşeli paranteze alınmaz
    assert infer(["412", "431"]) == [("412", False), ("431", False)]
    assert [x[1] for x in infer(["412", "700", "800", "415"])] == [False] * 4
    # aradaki okunamayan sayfa hiçbir koşunun içinde ya da devamında değil: tahmin yok
    assert infer(["412", None, "431"]) == [("412", False), ("PDF s. 2", True), ("431", False)]


def test_single_reading_is_not_a_conflict():
    assert infer(["412", None]) == [("412", False), ("413", True)]
    assert infer(["412", "431"], start=412)[0] == ("412", False)   # start verilince çelişki kavramı yok


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


def test_descending_runs():
    # R25: ters sırayla taranmış koşu da tutarlıdır; ofset iki yönde aranır
    assert infer(["412", "411", "410"]) == [("412", False), ("411", False), ("410", False)]
    assert infer(["412", "411", "430", "409"])[2] == ("410", True)          # koşu içi tek aykırı okuma
    assert infer(["412", None, "410"])[1] == ("411", True)                  # koşu içi boşluk
    assert infer(["412", "411", None]) == [("412", False), ("411", False), ("410", True)]   # koşunun devamı
    assert infer(["412", "411", "390", "389"]) == [(n, False) for n in ("412", "411", "390", "389")]


def test_unread_page_after_single_unsupported_reading_is_not_guessed():
    # tek okumalı "koşu"nun devamı tahmin edilmez; s. 2 ile s. 4 farklı koşular
    assert infer(["412", "411", "300", None]) == [("412", False), ("411", False), ("300", False), ("PDF s. 4", True)]
    # ama bütün dizide tek okuma varsa (kısa alıntı) sonraki sayfalar ondan sayılır
    assert infer(["412", None, None]) == [("412", False), ("413", True), ("414", True)]


def test_side_parity_rule_flags_contradicting_reading():
    # R25: okumalardan "çift=sol" öğrenilir (3'e 1); sağ sayfada okunan çift numara işaretlenir, numara kalır
    sides = ["sol", "sag", "sol", "sag"]
    assert infer(["412", "413", "414", "416"], sides=sides) == [("412", False), ("413", False), ("414", False),
                                                                 ("416", True)]
    # kural ters de olabilir: çift=sağ
    assert infer(["2", "3", "4", "6"], sides=["sag", "sol", "sag", "sol"])[3] == ("6", True)


def test_side_rule_needs_two_readings_and_clear_majority():
    assert infer(["412"], sides=["sag"]) == [("412", False)]                      # tek okuma: kural yok
    assert infer(["412", "431"], sides=["sol", "sol"]) == [("412", False), ("431", False)]   # 1'e 1
    assert [x[1] for x in infer(["412", "413", "431"], sides=["sol", "sag", "sol"])] == [False] * 3   # 2'ye 1
    assert infer(["412", "431"], sides=[None, "sol"]) == [("412", False), ("431", False)]   # taraf bilinmiyor


def test_guess_contradicting_header_parity_is_not_made():
    # R25: başlıktan çiftlik öğrenilir (yazar adı hep çift sayfada); devam tahmini 409 tek: tahmin yapılmaz
    headers = ["GELİBOLULU MUSTAFA ÂLİ", "KİTAP ADI", "Gelibolulu Mustafa Ali", "GELİBOLULU MUSTAFA ALİ"]
    assert infer(["412", "411", "410", None], headers=headers)[3] == ("PDF s. 4", True)
    # başlık tek sayfaya işaret ediyorsa (büyük/küçük harf, aksan ve kesme farkı yok sayılır) tahmin yapılır
    headers[3] = "Kitap Âdı"
    assert infer(["412", "411", "410", None], headers=headers)[3] == ("409", True)
    # tutarsız eşleme (aynı başlık hem tek hem çift sayfada) ipucu vermez
    mixed = ["BAŞLIK", "BAŞLIK", "BAŞLIK"]
    assert infer(["412", "411", None], headers=mixed)[2] == ("410", True)
    assert infer(["412", None, "410"], headers=["A", "B", "A"])[1] == ("411", True)


def test_own_side_is_checked_before_header():
    # okunamayan sayfanın tarafı bilinmez; düzeltilecek okumada kendi tarafı başlıktan önce gelir
    read = ["410", "411", "412", "431", "414"]
    sides = ["sol", "sag", "sol", "sol", "sol"]           # 4'e 1: çift=sol; s. 4 solda ama koşuya göre 413 (tek)
    out = infer(read, sides=sides, headers=[None, None, None, "KİTAP", None])
    assert out[3] == ("431", True)                         # düzeltme tarafla çelişir: okunduğu gibi + işaret
    assert infer(read, sides=[*sides[:3], "sag", "sol"])[3] == ("413", True)


def test_start_ignores_sides_and_headers():
    assert infer(["412", "431"], start=412, sides=["sag", "sag"], headers=["A", "A"]) == [("412", False),
                                                                                           ("413", True)]


def test_gap_after_run_with_uncorrected_outlier_is_guessed():
    # 412 ile 414 aynı koşu (aradaki aykırı okuma tarafla çeliştiği için düzeltilmedi): 414'ün devamı tahmin edilir
    read = ["410", "411", "412", "431", "414", None]
    out = infer(read, sides=["sol", "sag", "sol", "sol", "sol", None])
    assert out[3] == ("431", True) and out[5] == ("415", True)


def test_start_nonpositive_falls_back_to_pdf_label():
    assert infer([None, None], start=0) == [("PDF s. 1", True), ("1", True)]
