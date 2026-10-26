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


def _core(text: str, start: int, end: int) -> tuple[int, int]:
    """Baştaki ve sondaki kesme işaretleri (tırnak olabilir) çıkarılmış aralık; onlara kurallar karar verir."""
    while start < end and text[start] in APOS:
        start += 1
    while end > start and text[end - 1] in APOS:
        end -= 1
    return start, end


def _add_words(text: str, words: dict[str, str], *, override: bool) -> None:
    """Her kelime dizisi için iki anahtar: bütün dizi (kenar işaretleriyle, ör. mâni‘) ve iç işareti varsa
    kenarsız çekirdek (ör. mes’ele, tırnak içinde geçse de bulunsun)."""
    for m in _WORD.finditer(text):
        cs, ce = _core(text, m.start(), m.end())
        for s, e, inner_only in ((m.start(), m.end(), False), (cs, ce, True)):
            w = text[s:e]
            marks = [c for c in w if c in APOS]
            # yalnız bütün işaretleri ‘ ya da ’ olan kelimeler bir karar taşır
            if not marks or any(c not in (AIN, HAMZA) for c in marks):
                continue
            if inner_only and (s, e) == (m.start(), m.end()):
                continue  # kenar işareti yok: çekirdek zaten bütün dizi
            k = _key(w)
            if override or k not in words:  # gt'de çelişen yazımda ilki kalır
                words[k] = w


def build_word_list(gt_dir: Path, user_file: Path, *, exclude: Path | None = None) -> dict[str, str]:
    """gt/*.txt içindeki ’/‘ geçen kelimeler + kullanıcı dosyası (satır başı # yorum; kullanıcı gt'yi ezer).

    exclude: listeye alınmayacak gt dosyası (benchmark ölçtüğü sayfanın kendi cevabını görmesin).
    """
    words: dict[str, str] = {}
    skip = Path(exclude).resolve() if exclude else None
    for f in sorted(Path(gt_dir).glob("*.txt")):
        if skip and f.resolve() == skip:
            continue
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


def _lookup(text: str, start: int, end: int, words: dict[str, str]) -> dict[int, str]:
    """Aralıktaki bütün işaretlerin konum → listedeki karakter karşılığı; yoksa boş."""
    pos = [i for i in range(start, end) if text[i] in APOS]
    if not pos:
        return {}
    listed = [c for c in words.get(_key(text[start:end]), "") if c in APOS]
    return dict(zip(pos, listed)) if len(listed) == len(pos) else {}


def _segment(text: str, start: int, end: int, words: dict[str, str]) -> dict[int, str]:
    """Önce kenar işaretleriyle bütün kelime, olmazsa kenarsız çekirdek (kenar işaretlerini kurallar belirler)."""
    return _lookup(text, start, end, words) or _lookup(text, *_core(text, start, end), words)


def _decide_listed(text: str, m: re.Match, words: dict[str, str]) -> dict[int, str]:
    """Önce bütün dizi, bulunamazsa tireyle ayrılmış her parça listede aranır."""
    decided = _segment(text, m.start(), m.end(), words)
    if decided or "-" not in m.group():
        return decided
    s = m.start()
    for part in m.group().split("-"):
        decided.update(_segment(text, s, s + len(part), words))
        s += len(part) + 1
    return decided


def restore(text: str, words: dict[str, str]) -> tuple[str, list[Mark]]:
    """Her kesme işaretini tek karakterle (‘/’) değiştirir; uzunluk ve diğer karakterler değişmez.

    Sıra: kelime listesi, sonra kurallar 1-3; hiçbiri tutmazsa gelen tür korunur (‘ ʿ → ‘, ’ ʾ → ’;
    düz ' ` ´ → ‘) ve Mark(kind="kesme") eklenir.
    """
    out = list(text)
    marks: list[Mark] = []
    for m in _WORD.finditer(text):
        positions = [i for i in range(m.start(), m.end()) if text[i] in APOS]
        if not positions:
            continue
        decided = _decide_listed(text, m, words)
        for i in positions:
            c = decided.get(i) or _decide(text, i)
            if c is None:
                c = HAMZA if text[i] in (HAMZA, "ʾ") else AIN
                marks.append(Mark(i, i + 1, "kesme"))
            out[i] = c
    return "".join(out), marks
