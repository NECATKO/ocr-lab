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
    assert " ".join(t.text for t in out) == "bir iki üç"   # 300 px kutu ≈ 10 satır; kelime sayısı kadar satır


def test_unboxed_block_without_bbox_uses_page_margin():
    b = Block("Text", None, "bir iki üç", [])
    out = layer_lines(page_of(b, width=1000, height=1500))
    x1, y1, x2, y2 = out[0].box
    assert (x1, y1) == (50, 75) and x2 <= 950 and y2 <= 1425


def test_mixed_boxed_and_unboxed_lines_use_fallback():
    b = Block("Text", (0, 0, 100, 50), "bir iki", [Line(0, 3, False, B1), Line(4, 7, False)])
    out = layer_lines(page_of(b))
    assert [t.text for t in out] == ["bir", "iki"]          # 50 px kutu, 20 px satır → 2 satır
    assert out[0].box[1] == 0 and out[1].box[3] == 50


def test_picture_and_empty_blocks_skipped():
    pic = Block("Picture", (0, 0, 100, 100), "[Görsel]", [])
    empty = Block("Text", (0, 0, 100, 20), "  ", [Line(0, 2, False, B1)])
    real = Block("Text", (0, 0, 100, 20), "metin", [Line(0, 5, False, B1)])
    out = layer_lines(page_of(pic, empty, real))
    assert [(t.text, t.block) for t in out] == [("metin", 2)]


def test_table_uses_plain_text():
    t = Block("Table", (0, 0, 400, 100), "<table><tr><td>a</td><td>b</td></tr></table>", [])
    assert " ".join(texts(page_of(t))) == "a b"


def reference_block(n=3, top=0, pitch=63, h=42):
    """Satır kutulu bir blok: satır aralığı (pitch) ve satır yüksekliği (h) sayfadan buradan tahmin edilir."""
    words = [f"s{i}" for i in range(n)]
    text, lines, pos = " ".join(words), [], 0
    for i, w in enumerate(words):
        lines.append(Line(pos, pos + len(w), False, (0, top + i * pitch, 400, top + i * pitch + h)))
        pos += len(w) + 1
    return Block("Text", (0, top, 400, top + (n - 1) * pitch + h), text, lines)


def test_unboxed_two_line_block_gets_two_rows_spread_over_bbox():
    bbox = (100, 500, 900, 500 + 2 * 63)
    unboxed = Block("Text", bbox, "bir iki üç dört beş altı yedi sekiz", [Line(0, 35, False)])
    mine = [t for t in layer_lines(page_of(reference_block(), unboxed)) if t.block == 1]
    assert len(mine) == 2
    mid = (bbox[1] + bbox[3]) / 2
    assert mine[0].box[1] == bbox[1] and mine[0].box[3] <= mid
    assert mid <= mine[1].box[1] < mine[1].box[3] <= bbox[3]
    assert " ".join(t.text for t in mine) == unboxed.text


def test_unboxed_paragraph_rows_follow_page_line_pitch():
    text = " ".join(["kelime"] * 120)
    bbox = (100, 300, 900, 300 + 12 * 63 + 42)       # 13 basılı satır
    unboxed = Block("Text", bbox, text, [Line(0, len(text), False)])
    mine = [t for t in layer_lines(page_of(reference_block(), unboxed)) if t.block == 1]
    assert len(mine) == 13
    tops = [t.box[1] for t in mine]
    assert all(abs((b - a) - 63) < 1 for a, b in zip(tops, tops[1:]))
    assert all(t.box[3] - t.box[1] <= 42 for t in mine)
    assert mine[-1].box[3] <= bbox[3]
    assert " ".join(t.text for t in mine) == text


def test_unboxed_rows_never_exceed_word_count():
    b = Block("Text", (100, 100, 500, 1000), "tek", [])
    out = layer_lines(page_of(reference_block(), b))
    assert [t.text for t in out if t.block == 1] == ["tek"]


# --- kelime düzeyinde yerleşim: sentetik tesseract TSV'si ---

TSV_HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def words_at(text, x=100, cw=20):
    """Metnin kelimeleri x'ten başlayarak, karakter başına cw piksel, aralarında bir karakterlik boşlukla."""
    out = []
    for w in text.split():
        out.append((w, x, x + len(w) * cw))
        x += (len(w) + 1) * cw
    return out


def tsv_of(*rows):
    """rows: (y1, y2, [(kelime, x1, x2), ...]); her öğe bir tesseract satırı (level 4) ve kelimeleri (level 5)."""
    out = [TSV_HEAD]
    for n, (y1, y2, words) in enumerate(rows, 1):
        x1, x2 = min(w[1] for w in words), max(w[2] for w in words)
        out.append(f"4\t1\t1\t1\t{n}\t0\t{x1}\t{y1}\t{x2 - x1}\t{y2 - y1}\t-1\t")
        for k, (t, a, b) in enumerate(words, 1):
            out.append(f"5\t1\t1\t1\t{n}\t{k}\t{a}\t{y1}\t{b - a}\t{y2 - y1}\t95\t{t}")
    return "\n".join(out) + "\n"


def tess_page(*blocks, rows, width=1000, height=1500):
    p = page_of(*blocks, width=width, height=height)
    p.tess_tsv = tsv_of(*rows)
    return p


def flowing(text, bbox):
    """Satır yapısı kurulamamış blok: tek kutusuz satır (apply_line_structure'ın reddi)."""
    return Block("Text", bbox, text, [Line(0, len(text), False)])


def test_words_land_on_tesseract_word_boxes():
    r1, r2 = words_at("bu senâkâr geldi"), words_at("ve gitti")
    p = tess_page(flowing("bu senâkâr geldi ve gitti", (100, 100, 900, 203)),
                  rows=[(100, 140, r1), (163, 203, r2)])
    out = layer_lines(p)
    assert [t.text for t in out] == ["bu ", "senâkâr ", "geldi", "ve ", "gitti"]
    expect = [(x1, 100, x2, 140) for _, x1, x2 in r1] + [(x1, 163, x2, 203) for _, x1, x2 in r2]
    for k, (t, (x1, y1, x2, y2)) in enumerate(zip(out, expect)):
        assert t.box[0] == x1 and t.box[1] == y1 and t.box[3] == y2
        nxt = expect[k + 1][0] if t.text.endswith(" ") else x2
        assert t.box[2] == nxt
    assert all(t.block == 0 for t in out)


def test_misread_word_still_placed_on_its_box():
    tess = [("Batı", 100, 180), ("düşünür", 200, 340), ("Hume,", 360, 460), ("Dcscartcs,", 480, 680),
            ("Kant", 700, 780)]
    p = tess_page(flowing("Batı düşünür Hume, Descartes, Kant", (100, 100, 900, 140)), rows=[(100, 140, tess)])
    out = {t.text.strip(): t.box for t in layer_lines(p)}
    assert out["Descartes,"][:2] == (480, 100) and out["Descartes,"][3] == 140
    assert out["Descartes,"][2] == 700     # boşlukla Kant'a kadar uzar


def test_unmatched_word_fills_horizontal_gap():
    tess = [("bir", 100, 160), ("üç", 260, 300)]    # "iki" okunmamış: 180..240 arası boş
    p = tess_page(flowing("bir iki üç", (100, 100, 900, 140)), rows=[(100, 140, tess)])
    out = layer_lines(p)
    assert [t.text for t in out] == ["bir ", "iki ", "üç"]
    iki = out[1].box
    assert 160 <= iki[0] < iki[2] <= 260 and (iki[1], iki[3]) == (100, 140)


def test_unmatched_word_at_line_end_follows_previous_word():
    tess1, tess2 = words_at("bir iki"), words_at("dört beş")   # satır 1'in sonundaki "üç" okunmamış
    p = tess_page(flowing("bir iki üç dört beş", (100, 100, 900, 203)), rows=[(100, 140, tess1), (163, 203, tess2)])
    out = layer_lines(p)
    assert [t.text for t in out] == ["bir ", "iki ", "üç", "dört ", "beş"]
    uc = out[2].box
    assert uc[0] >= tess1[-1][2] and uc[2] <= 900 and (uc[1], uc[3]) == (100, 140)


def reference_rows(top=600, n=3, pitch=63, h=40):
    """Sayfanın satır aralığını belirleyen ayrı bir blok ve tesseract satırları."""
    rows = [(top + i * pitch, top + i * pitch + h, words_at(f"r{i} x")) for i in range(n)]
    text = " ".join(f"r{i} x" for i in range(n))
    return Block("Text", (100, top, 900, top + (n - 1) * pitch + h), text, [Line(0, len(text), False)]), rows


def test_missing_tesseract_line_gets_synthetic_row():
    l1, l3 = words_at("birinci satırın kelimeleri"), words_at("üçüncü satır burada")
    text = "birinci satırın kelimeleri ikinci satır okunmadı üçüncü satır burada"
    ref, ref_rows = reference_rows()
    p = tess_page(flowing(text, (100, 100, 900, 266)), ref, rows=[(100, 140, l1), (226, 266, l3)] + ref_rows)
    out = [t for t in layer_lines(p) if t.block == 0]
    assert [t.text.strip() for t in out] == text.split()
    for t in out[3:6]:                       # "ikinci satır okunmadı": 1. ve 3. satırın arasında
        assert 140 <= t.box[1] < t.box[3] <= 226, t
        assert 100 <= t.box[0] < t.box[2] <= 900
    assert out[2].text == "kelimeleri" and out[2].box[2] == l1[-1][2]   # 1. satırın sonuna sıkıştırılmadı
    assert out[5].text == "okunmadı"         # sentetik satırın son kelimesi: boşluk yok


def test_hyphenated_word_whole_on_first_line_only():
    r1 = [("bu", 100, 140), ("senâ-", 160, 260)]
    r2 = [("kâr", 100, 160), ("adam", 180, 260), ("geldi", 280, 380)]
    p = tess_page(flowing("bu senâkâr adam geldi", (100, 100, 900, 203)), rows=[(100, 140, r1), (163, 203, r2)])
    out = layer_lines(p)
    assert [t.text for t in out] == ["bu ", "senâkâr", "adam ", "geldi"]
    assert out[1].box == (160, 100, 260, 140)
    assert out[2].box[:2] == (180, 163)


def test_unboxed_block_uses_word_boxes_when_line_structure_failed():
    words = words_at("bir iki üç dört", x=300)
    p = tess_page(flowing("bir iki üç dört", (100, 100, 900, 400)), rows=[(250, 290, words)])
    out = layer_lines(p)
    assert [t.text.strip() for t in out] == ["bir", "iki", "üç", "dört"]
    for t, (_, x1, _) in zip(out, words):
        assert t.box[0] == x1 and (t.box[1], t.box[3]) == (250, 290)


def test_block_without_tesseract_words_falls_back_to_spread():
    b = flowing("bir iki üç dört beş altı yedi sekiz", (100, 500, 900, 626))
    spread = layer_lines(page_of(b))
    p = page_of(flowing("bir iki üç dört beş altı yedi sekiz", (100, 500, 900, 626)))
    p.tess_tsv = TSV_HEAD + "\n"
    assert layer_lines(p) == spread
    assert spread[0].box[0] == 100 and all(not t.text.endswith(" ") for t in spread)


def test_table_keeps_spread_even_with_tesseract_words():
    t = Block("Table", (100, 100, 900, 140), "<table><tr><td>a</td><td>b</td></tr></table>", [])
    p = tess_page(t, rows=[(100, 140, words_at("a b"))])
    assert [x.text for x in layer_lines(p)] == ["a b"]


def test_rtl_words_keep_logical_order_and_extend_leftward():
    tess = [("بسم", 600, 700), ("الله", 450, 560)]     # sağdan sola: ilk kelime sağda
    p = tess_page(flowing("بسم الله", (100, 100, 900, 140)), rows=[(100, 140, tess)])
    out = layer_lines(p)
    assert [t.text for t in out] == ["بسم ", "الله"]
    assert out[0].box == (560, 100, 700, 140)            # boşluk soldaki kelimeye doğru
    assert out[1].box == (450, 100, 560, 140)


def test_words_joined_with_single_spaces_when_copied(tmp_path):
    import pypdfium2 as pdfium
    from PIL import Image

    from ocrnow.export_pdf import write_pdf

    src = tmp_path / "kaynak.pdf"
    Image.new("L", (1000, 1500), 235).save(src, "PDF", resolution=200)
    r1 = [("bu", 100, 140), ("senâ-", 160, 260)]
    r2 = [("kâr", 100, 160), ("adam", 180, 260), ("Dcscartcs", 280, 460), ("geldi", 480, 580)]
    text = "bu senâkâr adam Descartes geldi"
    p = tess_page(flowing(text, (100, 100, 900, 203)), rows=[(100, 140, r1), (163, 203, r2)])
    p.source = str(src)
    out = write_pdf([p], tmp_path / "cikti.pdf")
    doc = pdfium.PdfDocument(str(out))
    try:
        extracted = doc[0].get_textpage().get_text_range()
    finally:
        doc.close()
    assert extracted.split("\r\n") == ["bu senâkâr", "adam Descartes geldi"]
    assert "  " not in extracted


def test_overlapping_tesseract_word_box_clipped_to_next_word():
    tess = [("sıl", 100, 160), ("Allah'ın", 180, 500), ("birer", 320, 420)]   # tesseract kutusu taşmış
    p = tess_page(flowing("sıl Allah’ın birer", (100, 100, 900, 140)), rows=[(100, 140, tess)])
    out = layer_lines(p)
    assert [t.text for t in out] == ["sıl ", "Allah’ın ", "birer"]
    assert out[1].box == (180, 100, 320, 140)


def test_missing_line_does_not_steal_similar_word_from_line_end():
    """Kaçan satır benzer bir kelimeyle başlıyorsa hizalama satır sonundaki kelimeyi oraya kaydırmamalı."""
    r1 = words_at("bir Fransız da Allah'ın")
    r3 = words_at("gibi İnsanlar birbirlerinin")
    text = "bir Fransız da Allah’ın birer âyetidir. Allah’ı inkâr etseler bile Sadi dediği gibi İnsanlar birbirlerinin"
    ref, ref_rows = reference_rows()
    p = tess_page(flowing(text, (100, 100, 900, 266)), ref, rows=[(100, 140, r1), (226, 266, r3)] + ref_rows)
    out = {t.text.strip(): t.box for t in layer_lines(p) if t.block == 0}
    assert out["Allah’ın"][:2] == (r1[-1][1], 100)
    assert 140 <= out["Allah’ı"][1] < out["Allah’ı"][3] <= 226


def test_unmatched_group_rematched_to_unused_tesseract_word():
    r1 = words_at("diye bir niyetimiz")
    r3 = words_at("başkasını öğrenmemiz")
    text = ("diye bir niyetimiz yok. Bizim dediğimiz, demeye çalıştığımız, önce kendimizi, sonra "
            "başkasını öğrenmemiz")
    ref, ref_rows = reference_rows()
    p = tess_page(flowing(text, (100, 100, 900, 266)), ref, rows=[(100, 140, r1), (226, 266, r3)] + ref_rows)
    out = {t.text.strip(): t.box for t in layer_lines(p) if t.block == 0}
    assert out["niyetimiz"][:2] == (r1[-1][1], 100)
    assert 140 <= out["yok."][1] < out["yok."][3] <= 226


def test_missing_last_line_sits_at_block_bottom():
    r1 = words_at("Kendimizi dahi öğrenmeye başladık olunca")
    text = "Kendimizi dahi öğrenmeye başladık olunca kendi tarihimizi de ancak onların bize öğrettiği dozda"
    ref, ref_rows = reference_rows()
    p = tess_page(flowing(text, (100, 100, 900, 205)), ref, rows=[(100, 140, r1)] + ref_rows)
    out = [t for t in layer_lines(p) if t.block == 0]
    last = [t for t in out if t.text.strip() in text.split()[5:]]
    assert len(last) == 8
    assert all(t.box[3] == 205 and t.box[1] >= 140 for t in last)


def test_unmatched_group_uses_unread_tesseract_row():
    """Tesseract satırı bulmuş ama kelimelerini okuyamamış: grup o satırın yerine konur (sentetik satır değil)."""
    r1, r3 = words_at("birinci satır"), words_at("üçüncü satır")
    junk = [("l", 120, 130), ("dil", 200, 260), (".", 300, 310), ("Ni", 400, 440)]
    ref, ref_rows = reference_rows()
    p = tess_page(flowing("birinci satır serdedilmiştir üçüncü satır", (100, 100, 900, 266)), ref,
                  rows=[(100, 140, r1), (165, 205, junk), (226, 266, r3)] + ref_rows)
    out = {t.text.strip(): t.box for t in layer_lines(p) if t.block == 0}
    x1, y1, x2, y2 = out["serdedilmiştir"]
    assert (x1, y1, y2) == (120, 165, 205) and x2 > x1


def test_missing_last_line_keeps_natural_width():
    r1 = words_at("Tarih kelimesinin nereden geldiği konusunda görüşler")
    ref, ref_rows = reference_rows()
    p = tess_page(flowing("Tarih kelimesinin nereden geldiği konusunda görüşler serdedilmiştir.", (100, 100, 1200, 205)),
                  ref, rows=[(100, 140, r1)] + ref_rows)
    last = [t for t in layer_lines(p) if t.block == 0][-1]
    assert last.text == "serdedilmiştir." and last.box[0] == 100 and last.box[3] == 205
    assert last.box[2] - last.box[0] <= 20 * 20      # harf genişliği × harf sayısı civarı, satır boyu değil


def test_case_insensitive_match_in_block_with_dotted_capital_i():
    """"İ".casefold() iki harftir: büyük/küçük ayrımsız eşleşme yine harf harf yapılmalı."""
    tess = [("İstanbul", 100, 260), ("DESCARTES", 500, 680)]
    p = tess_page(flowing("İstanbul Descartes", (100, 100, 900, 140)), rows=[(100, 140, tess)])
    out = layer_lines(p)
    assert [t.box[0] for t in out] == [100, 500]
    assert all(t.box[1] == 100 for t in out)


def test_zero_width_tesseract_box_still_written():
    tess = [("bir", 100, 160), ("iki", 300, 300)]
    p = tess_page(flowing("bir iki", (100, 100, 900, 140)), rows=[(100, 140, tess)])
    last = layer_lines(p)[-1]
    assert last.text == "iki" and last.box[2] > last.box[0]
