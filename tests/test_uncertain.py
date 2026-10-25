import pytest

from ocrnow import paths
from ocrnow.layout import parse_output
from ocrnow.uncertain import flag_uncertain


def flagged(text, tess):
    return [text[m.start:m.end] for m in flag_uncertain(text, tess)]


def test_tesseract_confusions_not_flagged():
    assert flagged("dûde gördi mîr", "düde gördi mir") == []


def test_apostrophe_and_hyphen_ignored():
    assert flagged("bâ'is ol", "bâ’is ol") == []
    assert flagged("göstermediler ol", "gösterme-\ndiler ol") == []


def test_real_difference_flagged():
    assert flagged("ve ğayte muhâlif", "ve ğayre muhâlif") == ["ğayte"]


def test_marks_are_ranges_in_text():
    text = "ve  ğayte muhâlif"
    marks = flag_uncertain(text, "ve ğayre muhâlif")
    assert [(m.start, m.end, m.kind) for m in marks] == [(4, 9, "belirsiz")]


def test_surrounding_punctuation_ignored():
    assert flagged("ki, ol (ve) “bu”.", "ki ol ve bu") == []
    assert flagged("ol — ve", "ol ve") == []


def test_letter_digit_split_flags_whole_token_once():
    assert flagged("taştında724 ol", "taştında 724 ol") == []
    assert flagged("taştında724 ol", "taştında 727 ol") == ["taştında724"]


def test_extra_and_missing_words():
    assert flagged("ve bir ol", "ve ol") == ["bir"]
    assert flagged("ve ol", "ve bir ol") == []


def test_no_overlapping_or_duplicate_marks():
    marks = flag_uncertain("a1 b2 cc dd", "x y zz")
    spans = [(m.start, m.end) for m in marks]
    assert spans == sorted(set(spans))
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))


def test_empty_inputs():
    assert flag_uncertain("", "ve ol") == []
    assert flagged("ve ol", "") == ["ve", "ol"]


def test_spike_page_rate(spike_layout):
    tess = paths.OUTPUT / "tesseract" / "tarama5_tesseract_syf1.md"
    if not tess.exists():
        pytest.skip("kitap verisi yok")
    blocks, _ = parse_output(spike_layout, True, (1653, 2339))
    text = " ".join(b.text for b in blocks if b.category in ("Text", "Footnote"))
    body = "\n".join(tess.read_text().splitlines()[3:])
    got = flagged(text, body)
    assert len({"mahsús", "ğayte", "itemînân", "müsâre'at", "fuc'eten"} & set(got)) >= 4
    assert len(got) <= 0.12 * len(text.split())
