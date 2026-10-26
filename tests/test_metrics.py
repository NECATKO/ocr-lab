import json
import random

from ocrnow.lab.metrics import (apostrophe_accuracy, cer_base, cer_body, cer_norm, cer_raw,
                                line_end_accuracy, wer, word_errors)


def old_bench_cer(ref, hyp):
    """Eski benchmark betiğindeki cer(): iki satırlı Levenshtein, boşluklar sadeleştirilir."""
    ref, hyp = " ".join(ref.split()), " ".join(hyp.split())
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        cur = [i]
        for j, b in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a != b)))
        prev = cur
    return prev[-1] / max(len(ref), 1)


def test_cer_raw_matches_old_bench():
    assert cer_raw("a b  c", "a  b c") == 0 and round(cer_raw("abcd", "abxd"), 2) == 0.25


def test_cer_raw_equals_old_bench_on_random_strings():
    rnd = random.Random(7)
    for _ in range(60):
        ref = "".join(rnd.choice("abc ıâ\n") for _ in range(rnd.randint(0, 60)))
        hyp = "".join(rnd.choice("abc ıâ\n") for _ in range(rnd.randint(0, 60)))
        assert cer_raw(ref, hyp) == old_bench_cer(ref, hyp)


def test_cer_raw_long_distant_strings_use_band_doubling():
    ref = "ab" * 200
    hyp = "cd" * 150
    assert cer_raw(ref, hyp) == old_bench_cer(ref, hyp)


def test_cer_raw_empty_reference():
    assert cer_raw("", "abc") == 3


def test_norm_ignores_markdown_and_apostrophes():
    assert cer_norm("bâ‘is ol", "**bâ'is** ol") == 0


def test_norm_joins_line_end_hyphens_and_footnote_digits():
    assert cer_norm("senâ-\nkâr taştında 724", "senâkâr taştında724") == 0


def test_norm_counts_letter_errors():
    assert round(cer_norm("abcd", "abxd"), 2) == 0.25


def test_base_ignores_case_diacritics_and_apostrophes():
    assert cer_base("Bâ‘is IŞIK", "bais isik") == 0
    assert cer_norm("Bâ‘is", "bais") > 0


def test_body_drops_running_header_and_page_number():
    gt = "BAŞLIK ÜST\n\ngövde metni burada\n\n12"
    assert cer_body(gt, "gövde metni burada") == 0
    assert cer_body(gt, "BAŞLIK ÜST\n\ngövde metni burada\n\n13") == 0
    assert cer_norm(gt, "gövde metni burada") > 0


def test_body_keeps_ordinary_first_line():
    assert cer_body("ilk satır\nikinci", "ilk satır\nikinci") == 0
    assert cer_body("ilk satır\nikinci", "ikinci") > 0


def test_wer_and_word_errors():
    assert wer("bir iki üç", "bir iki üç") == 0
    assert round(wer("bir iki üç dört", "bir ikl üç"), 2) == 0.5
    assert word_errors("bir iki üç dört", "bir ikl üç") == [("iki", "ikl"), ("dört", "")]
    assert word_errors("bir", "bir fazla") == [("", "fazla")]


def test_wer_equates_hyphenation():
    assert wer("senâ-kâr gördü", "senâkâr gördü") == 0


def test_apostrophe_accuracy():
    assert apostrophe_accuracy("bâ‘is cür’et", "bâ‘is cür‘et")[0] == 0.5


def test_apostrophe_accuracy_counts_marks_and_plain_apostrophe_is_wrong():
    assert apostrophe_accuracy("bâ‘is cür’et", "bâ'is cür’et") == (0.5, 2)
    assert apostrophe_accuracy("ba‘z ‘ilâc", "ba‘z ‘ilâc") == (1.0, 2)


def test_apostrophe_accuracy_survives_letter_errors_and_missing_words():
    assert apostrophe_accuracy("ba‘z ‘ilâc", "ba‘x ‘ilâc") == (1.0, 2)
    assert apostrophe_accuracy("ba‘z ‘ilâc", "bax ‘ilâc") == (0.5, 2)
    assert apostrophe_accuracy("ba‘z ‘ilâc", "‘ilâc") == (0.5, 2)


def test_apostrophe_accuracy_without_marks():
    assert apostrophe_accuracy("düz metin", "düz metin") == (0.0, 0)


def test_line_end_accuracy():
    assert line_end_accuracy("bir iki\nüç dört", ["bir iki", "üç dörx"]) == 0.5


def test_line_end_treats_apostrophes_equal_and_survives_shift():
    ref = "baş satır\nbâ‘is\n\nüç dört\nson kelime"
    assert line_end_accuracy(ref, ["fazla bir satır", "baş satır", "bâ'is", "üç dört", "son kelime"]) == 1.0


def test_line_end_missing_lines_count_wrong():
    assert line_end_accuracy("bir iki\nüç dört", ["bir iki"]) == 0.5
    assert line_end_accuracy("", ["x"]) == 0.0


def test_reproduces_manual_analysis(gt_syf1, spike_layout):
    text = "\n\n".join(b["text"] for b in json.loads(spike_layout))
    assert abs(cer_norm(gt_syf1, text) - 0.0032) < 0.0005
