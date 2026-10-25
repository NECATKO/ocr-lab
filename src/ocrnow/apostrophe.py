"""Ayın (‘) ve hemze (’) geri getirme: OCR modelleri ikisini de düz ' yazar; kurallar ve kelime listesi ayırır."""
import re
from pathlib import Path

from ocrnow.layout import Mark

APOS = "'’‘`´ʿʾ"
AIN, HAMZA = "‘", "’"

# kural 2: hemze alan ekler (kelimenin işaretten sonraki kısmı bunlardan biri olmalı)
SUFFIXES = frozenset("de da te ta e a ı i u ü in ın un ün nin nın den dan ten tan le la".split())
VOWELS = frozenset("aeıioöuüâîûêôAEIİOÖUÜÂÎÛÊÔ")
WORD_OPENERS = frozenset('-([{"«“„')  # boşluk ve metin başı dışında "kelime başı" sayılan öncüller
MIN_STEM = 3

# kelime: harf, kesme işareti ve tireden oluşan en uzun dizi
_WORD = re.compile(rf"(?:[^\W\d_]|[{re.escape(APOS)}]|-)+")


def _key(word: str) -> str:
    """Liste anahtarı: küçük harf, bütün kesme türleri "'" (yalnız anahtar için; metne uygulanmaz)."""
    return "".join("'" if c in APOS else c for c in word.lower())


def _add_words(text: str, words: dict[str, str], *, override: bool) -> None:
    for m in _WORD.finditer(text):
        w = m.group()
        marks = [c for c in w if c in APOS]
        # yalnız bütün işaretleri ‘ ya da ’ olan kelimeler bir karar taşır
        if not marks or any(c not in (AIN, HAMZA) for c in marks):
            continue
        k = _key(w)
        if override or k not in words:  # gt'de çelişen yazımda ilki kalır
            words[k] = w


def build_word_list(gt_dir: Path, user_file: Path) -> dict[str, str]:
    """gt/*.txt içindeki ’/‘ geçen kelimeler + kullanıcı dosyası (satır başı # yorum; kullanıcı gt'yi ezer)."""
    words: dict[str, str] = {}
    for f in sorted(Path(gt_dir).glob("*.txt")):
        _add_words(f.read_text(encoding="utf-8"), words, override=False)
    if Path(user_file).is_file():
        for line in Path(user_file).read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("#"):
                _add_words(line, words, override=True)
    return words


def _decide(text: str, i: int) -> str | None:
    """Kurallar 1-3; karar yoksa None."""
    n = len(text)
    # 1. ardından l- geliyorsa hemze (Bi’l-âhır)
    if text[i + 1:i + 3].lower() == "l-":
        return HAMZA
    # 2. büyük harfle başlayan, işaretten önce en az 3 harfli kelime + ek → hemze (Haleb’de)
    j = i
    while j > 0 and text[j - 1].isalpha():
        j -= 1
    k = i + 1
    while k < n and text[k].isalpha():
        k += 1
    if i - j >= MIN_STEM and text[j].isupper() and text[i + 1:k].lower() in SUFFIXES:
        return HAMZA
    # 3. kelime başında, ünlüden önce → ayın (‘illet)
    at_start = i == 0 or text[i - 1].isspace() or text[i - 1] in WORD_OPENERS
    if at_start and i + 1 < n and text[i + 1] in VOWELS:
        return AIN
    return None


def restore(text: str, words: dict[str, str]) -> tuple[str, list[Mark]]:
    """Her kesme işaretini tek karakterle (‘/’) değiştirir; uzunluk ve diğer karakterler değişmez.

    Sıra: kelime listesi, sonra kurallar 1-3; hiçbiri tutmazsa ‘ konur ve Mark(kind="kesme") eklenir.
    """
    out = list(text)
    marks: list[Mark] = []
    for m in _WORD.finditer(text):
        positions = [i for i in range(m.start(), m.end()) if text[i] in APOS]
        if not positions:
            continue
        listed = [c for c in words.get(_key(m.group()), "") if c in APOS]
        if len(listed) == len(positions):
            for i, c in zip(positions, listed):
                out[i] = c
            continue
        for i in positions:
            c = _decide(text, i)
            if c is None:
                c = AIN
                marks.append(Mark(i, i + 1, "kesme"))
            out[i] = c
    return "".join(out), marks
