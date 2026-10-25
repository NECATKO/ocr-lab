import re

import pytest

from ocrnow.apostrophe import APOS, build_word_list, restore


@pytest.mark.parametrize("src,exp", [
    ("Bi'l-âhır", "Bi’l-âhır"), ("Haleb'de", "Haleb’de"), ("'illet ve", "‘illet ve"),
    ("Ba'de", "Ba‘de")])                                   # "Ba" 2 harf: kural 2 değil, kural 5 → ‘
def test_rules(src, exp):
    assert restore(src, {})[0] == exp


def test_list_wins_and_undecided_marked():
    out, marks = restore("cür'et ve bâ'is", {"cür'et": "cür’et"})
    assert out == "cür’et ve bâ‘is" and [m.start for m in marks] == [12]
    assert all(m.kind == "kesme" and m.end == m.start + 1 for m in marks)


def test_word_start_after_hyphen_and_opening_punctuation():
    # üçüncü işaret "l-" sonrası kelime başı; (, [, " sonrası da kelime başı
    out, marks = restore("Ba'de'l-'amel ('ilm) ['arz] \"'unvan\"", {})
    assert out == "Ba‘de’l-‘amel (‘ilm) [‘arz] \"‘unvan\""
    assert [m.start for m in marks] == [2]  # yalnız Ba'de'nin ilki belirsiz


def test_rule2_needs_capital_and_three_letter_stem():
    assert restore("haleb'de", {})[0] == "haleb‘de"   # küçük harf: kural 2 değil
    assert restore("Ali'ye", {})[0] == "Ali‘ye"       # ek listede yok
    assert restore("Ali'de", {})[0] == "Ali’de"


def test_other_apostrophe_variants_are_replaced():
    out, _ = restore("Haleb`de ´arz ʿilm", {})
    assert out == "Haleb’de ‘arz ‘ilm"


def test_text_fidelity_length_and_other_chars():
    src = "İstanbul'da 'ilâc, mü'min! (Ba'de) İ'tiraf 'ı  \t x'"
    out, _ = restore(src, {"mü'min": "mü’min"})
    assert len(out) == len(src)
    assert [c for c in src if c not in APOS] == [c for c in out if c not in "’‘"]
    assert all(o in "’‘" for s, o in zip(src, out) if s in APOS)
    assert all(s == o for s, o in zip(src, out) if s not in APOS)


def test_build_word_list(tmp_path):
    (tmp_path / "a.txt").write_text("Ba‘de’l-‘amel, mes’ele; düz kelime. mes‘ele", encoding="utf-8")
    user = tmp_path / "kesme.txt"
    user.write_text("# yorum mes'ele\n  # girinti yorum\nmes’ele   \nbâ‘is\n", encoding="utf-8")
    words = build_word_list(tmp_path, user)
    assert words["ba'de'l-'amel"] == "Ba‘de’l-‘amel"
    assert words["bâ'is"] == "bâ‘is"
    assert "düz" not in words
    # gt'de çelişen yazımda ilki kalır; kullanıcı satırı gt'yi ezer
    assert build_word_list(tmp_path, tmp_path / "yok.txt")["mes'ele"] == "mes’ele"
    (tmp_path / "kesme.txt").write_text("mes‘ele\n", encoding="utf-8")
    assert build_word_list(tmp_path, user)["mes'ele"] == "mes‘ele"


def test_gt_page_rules_only(gt_syf1):
    flat = re.sub("[’‘]", "'", gt_syf1)
    out, marks = restore(flat, {})
    decided = [(g, o) for i, (g, o) in enumerate(zip(gt_syf1, out))
               if g in "’‘" and not any(m.start == i for m in marks)]
    assert len(decided) >= 15 and sum(g != o for g, o in decided) <= 1


def test_gt_page_with_list(gt_syf1, tmp_path):
    (tmp_path / "g.txt").write_text(gt_syf1, encoding="utf-8")
    words = build_word_list(tmp_path, tmp_path / "yok.txt")
    assert restore(re.sub("[’‘]", "'", gt_syf1), words)[0] == gt_syf1


def test_list_lookup_ignores_edge_marks_and_hyphen_suffix():
    words = {"mes'ele": "mes’ele"}
    out, marks = restore("'mes'ele' mes'ele-i mes'ele", words)
    # kenar işaretleri kurallara kalır: ikisi de belirsiz (ünlüden önce değil)
    assert out == "‘mes’ele‘ mes’ele-i mes’ele"
    assert [m.start for m in marks] == [0, 8]


def test_list_hyphen_parts_when_whole_missing():
    words = {"mes'ele": "mes’ele", "bâ'is": "bâ‘is"}
    assert restore("mes'ele-bâ'is-ve", words)[0] == "mes’ele-bâ‘is-ve"


def test_build_word_list_strips_quotes(tmp_path):
    (tmp_path / "g.txt").write_text("dedi ‘mes’ele’ ve “bâ‘is”.", encoding="utf-8")
    words = build_word_list(tmp_path, tmp_path / "yok.txt")
    assert words == {"mes'ele": "mes’ele", "bâ'is": "bâ‘is"}


def test_undecided_keeps_explicit_incoming_type():
    out, marks = restore("bâ’is maʾnâ bâ‘is bâ'is", {})
    assert out == "bâ’is ma’nâ bâ‘is bâ‘is"
    assert [m.start for m in marks] == [2, 8, 14, 20]
    assert all(m.kind == "kesme" for m in marks)


def test_rules_override_explicit_incoming_type():
    assert restore("Haleb‘de ’illet", {})[0] == "Haleb’de ‘illet"
