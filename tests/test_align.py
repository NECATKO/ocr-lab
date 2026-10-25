from ocrnow.align import apply_line_structure, rebreak
from ocrnow.layout import Block, Line, parse_output
from ocrnow.tesslines import TessLine, lines_in, parse_tsv


def L(text, x=0): return TessLine((x, 0, x + 100, 10), text)


def lines_text(text, lines):
    return [text[l.start:l.end] + ("-" if l.hyphen else "") for l in lines]


def assert_covers(text, lines):
    """Metne sadakat: satırlar metni tam kaplar; aradan yalnız tek bir boşluk düşebilir, hiçbir harf yitmez/yinelenmez."""
    assert lines and lines[0].start == 0 and lines[-1].end == len(text)
    assert all(l.start < l.end for l in lines) or lines == [Line(0, 0, False)]   # boş satır yok
    joined = ""
    for prev, cur in zip(lines, lines[1:]):
        gap = text[prev.end:cur.start]
        assert gap in ("", " ") and not (prev.hyphen and gap)
        joined += text[prev.start:prev.end] + gap
    joined += text[lines[-1].start:lines[-1].end]
    assert joined == text


def test_joined_word_gets_hyphen():
    text = "ne kadar göstermediler bunu"
    lines, _ = rebreak(text, [L("ne kadar gösterme-"), L("diler bunu")])
    assert lines_text(text, lines) == ["ne kadar gösterme-", "diler bunu"]
    assert_covers(text, lines)


def test_kept_hyphen_breaks_after_it():
    text = "bu senâ-kâr adam"
    lines, _ = rebreak(text, [L("bu senâ."), L("kâr adam")])        # tesseract tireyi nokta okumuş
    assert lines_text(text, lines) == ["bu senâ-", "kâr adam"]
    assert lines[0].hyphen is False
    assert_covers(text, lines)


def test_comma_at_line_end_is_not_hyphen():
    text = "Her çend ki, rızâ virmediler"
    lines, _ = rebreak(text, [L("Her çend ki,"), L("rızâ virmediler")])
    assert lines_text(text, lines) == ["Her çend ki,", "rızâ virmediler"]
    assert_covers(text, lines)


def test_break_moves_to_nearest_space():
    text = "birinci satır ikinci satır üçüncü"
    lines, ratio = rebreak(text, [L("birinci satr"), L("ikinci satır"), L("üçüncü")])   # tesseract harf kaçırmış
    assert lines_text(text, lines) == ["birinci satır", "ikinci satır", "üçüncü"]
    assert 0 < ratio < 0.1
    assert_covers(text, lines)


def test_ratio_is_distance_over_tess_length():
    _, ratio = rebreak("abcd", [L("abxd")])
    assert ratio == 1 / 4


def test_no_tess_lines_fallback():
    b = Block("Text", (0, 0, 10, 10), "akan metin")
    assert "kurulamadı" in apply_line_structure(b, []) and b.lines == [Line(0, 10, False)]
    assert apply_line_structure(b, []) == "satır yapısı kurulamadı: Text bloğu, tesseract satırı yok"


def test_stray_leading_line_fallback():
    b = Block("Text", (100, 0, 600, 100), "birinci satır ikinci satır")
    warn = apply_line_structure(b, [L("BAŞLIK", x=300), L("birinci satır", x=100), L("ikinci satır", x=100)])
    assert warn == "satır yapısı kurulamadı: Text bloğu, satır sayısı tutmuyor"
    assert b.lines == [Line(0, len(b.text), False)] and b.indent is False   # girintili görünen ilk satır yok sayılır


def test_stray_trailing_line_fallback():
    b = Block("Text", (100, 0, 600, 100), "birinci satır ikinci satır")
    warn = apply_line_structure(b, [L("birinci satır"), L("ikinci satır"), L("412")])
    assert warn == "satır yapısı kurulamadı: Text bloğu, satır sayısı tutmuyor"
    assert b.lines == [Line(0, len(b.text), False)]


def test_high_cost_fallback():
    b = Block("Footnote", (0, 0, 100, 100), "bambaşka bir metin burada duruyor")
    warn = apply_line_structure(b, [L("xyz qwv", x=200), L("kkk")])
    assert warn.startswith("satır yapısı kurulamadı: Footnote bloğu, oran ")
    assert float(warn.rsplit(" ", 1)[1]) > 0.30
    assert b.lines == [Line(0, len(b.text), False)]
    assert b.indent is False


def test_indent():
    b = Block("Text", (100, 0, 600, 100), "girintili bir paragraf ikinci satır")
    assert apply_line_structure(b, [L("girintili bir paragraf", x=150), L("ikinci satır", x=100)]) is None
    assert b.indent is True and len(b.lines) == 2
    b = Block("Text", (100, 0, 600, 100), "girintisiz bir paragraf ikinci satır")
    apply_line_structure(b, [L("girintisiz bir paragraf", x=120), L("ikinci satır", x=100)])
    assert b.indent is False


TSV = "\n".join([
    "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
    "1\t1\t0\t0\t0\t0\t0\t0\t1000\t1000\t-1\t",
    "4\t1\t1\t1\t1\t0\t100\t200\t300\t20\t-1\t",
    "5\t1\t1\t1\t1\t1\t100\t200\t100\t20\t95.1\tilk",
    "5\t1\t1\t1\t1\t2\t210\t200\t190\t20\t90.0\t\"satır",
    "4\t1\t1\t1\t2\t0\t100\t230\t250\t20\t-1\t",
    "5\t1\t1\t1\t2\t1\t100\t230\t250\t20\t88.0\tikinci",
    "4\t1\t2\t1\t1\t0\t100\t900\t40\t20\t-1\t",
    "5\t1\t2\t1\t1\t1\t100\t900\t40\t20\t-1\t ",
    "",
])


def test_parse_tsv():
    tl = parse_tsv(TSV)
    assert tl == [TessLine((100, 200, 400, 220), 'ilk "satır'), TessLine((100, 230, 350, 250), "ikinci")]


def test_lines_in():
    tl = [TessLine((100, 200, 400, 220), "a"), TessLine((100, 600, 400, 620), "b")]
    assert lines_in((110, 195, 390, 300), tl) == tl[:1]
    assert lines_in((110, 230, 390, 300), tl) == []                # merkez 210, kutu 230-15'ten yukarıda
    assert lines_in((110, 220, 390, 300), tl) == tl[:1]            # pad 15 içinde
    assert lines_in(None, tl) == tl                                 # düz mod: bütün satırlar


def test_spike_page_line_ends(spike_layout, spike_tsv, gt_syf1):
    blocks, _ = parse_output(spike_layout, True, (1653, 2339))
    tl = parse_tsv(spike_tsv)
    texts = [b for b in blocks if b.category == "Text"]
    for b in texts:
        assert apply_line_structure(b, lines_in(b.bbox, tl)) is None
        assert_covers(b.text, b.lines)
    assert [len(b.lines) for b in texts] == [9, 6, 5, 6, 10]
    got = [lines_text(b.text, b.lines)[i][-3:] for b in texts for i in range(len(b.lines))]
    gt_body = [l for l in gt_syf1.splitlines()[2:] if l.strip()][:36]
    assert sum(a == g.strip()[-3:].replace("’", "'").replace("‘", "'") for a, g in zip(got, gt_body)) >= 33


def test_spike_page_all_blocks_cover_text(spike_layout, spike_tsv):
    blocks, _ = parse_output(spike_layout, True, (1653, 2339))
    tl = parse_tsv(spike_tsv)
    for b in blocks:
        apply_line_structure(b, lines_in(b.bbox, tl))
        assert_covers(b.text, b.lines)
