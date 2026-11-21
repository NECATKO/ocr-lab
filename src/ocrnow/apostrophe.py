"""Ayın (‘) ve hemze (’) geri getirme: OCR modelleri ikisini de düz ' yazar; kurallar ve kelime listesi ayırır."""
import re
from pathlib import Path

from ocrnow.layout import Mark

APOS = "'’‘`´ʿʾ"
AIN, HAMZA = "‘", "’"

# kural 2: hemze alan ekler (kelimenin işaretten sonraki kısmı bunlardan biri olmalı); ikinci satır günümüz
# Türkçesi sayfaları için (R28: Kahire’ye, İstanbul’daki). Ayınlı özel adlar (Ka‘be, Sa‘d) listeye girmez.
SUFFIXES = frozenset("de da te ta e a ı i u ü in ın un ün nin nın nun nün den dan ten tan le la".split()
                     + "ye ya yi yı yu yü yle yla yse ysa deki daki teki taki dir dır dur dür tir tır tur tür "
                       "na ne nı ni nu nü nda nde ndan nden ler lar".split())
VOWELS = frozenset("aeıioöuüâîûêôAEIİOÖUÜÂÎÛÊÔ")
BACK, ROUND = frozenset("aıouâûAIOUÂÛ"), frozenset("oöuüôûOÖUÜÔÛ")
HIGH = frozenset("ıiuü")
WORD_OPENERS = frozenset('-([{"«“„')  # boşluk ve metin başı dışında "kelime başı" sayılan öncüller
NUMBER_ENDS = frozenset(").]")   # rakam ya da bunlardan sonra gelen kesme + küçük harf: ek (1979’da, (s.a.s)’in)
ELISIONS = frozenset("lLdD")      # Batı dillerinde kelime başı tek harf + kesme: l’Islam, d’Alembert
MIN_STEM = 3

# kelime: harf, kesme işareti ve tireden oluşan en uzun dizi
_WORD = re.compile(rf"(?:[^\W\d_]|[{re.escape(APOS)}]|-)+")


def _tr_lower(text: str) -> str:
    """Türkçe büyük İ/I dönüşümü: Python'da 'İ'.lower() 'i\\u0307', 'I'.lower() 'i' üretir."""
    return text.replace("İ", "i").replace("I", "ı").lower()


def _key(word: str) -> str:
    """Liste anahtarı: küçük harf, bütün kesme türleri "'" (yalnız anahtar için; metne uygulanmaz)."""
    return "".join("'" if c in APOS else c for c in _tr_lower(word))


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


def build_word_list(gt_dir: Path, user_file: Path, *, exclude: Path | None = None,
                    exclude_book: str | None = None) -> dict[str, str]:
    """gt/*.txt içindeki ’/‘ geçen kelimeler + kullanıcı dosyası (satır başı # yorum; kullanıcı gt'yi ezer).

    exclude / exclude_book: dışarıda bırakılacak gt dosyası / kitap gövde adı.
    """
    words: dict[str, str] = {}
    skip = Path(exclude).resolve() if exclude else None
    book = re.compile(rf"{re.escape(exclude_book)}_syf[0-9]+\.txt") if exclude_book is not None else None
    for f in sorted(Path(gt_dir).glob("*.txt")):
        if (skip and f.resolve() == skip) or (book and book.fullmatch(f.name)):
            continue
        _add_words(f.read_text(encoding="utf-8"), words, override=False)
    if Path(user_file).is_file():
        for line in Path(user_file).read_text(encoding="utf-8").splitlines():
            if not line.lstrip().startswith("#"):
                _add_words(line, words, override=True)
    return words


def _harmonizes(stem: str, suffix: str) -> bool:
    """Kısa kökte ekin Türkçe olup olmadığı: ünlü ünlüye çarpmaz, ekin ilk ünlüsü kökün son ünlüsüyle kalınlık
    (dar ünlüde düzlük-yuvarlaklık da) uyumundadır. Ay+ın, O+dur evet; Ba+de, Ya+ni, Ma+a hayır."""
    vowels = [c for c in stem if c in VOWELS]
    if not vowels or (stem[-1] in VOWELS and suffix[0] in VOWELS):
        return False
    first = next((c for c in suffix if c in VOWELS), None)
    if first is None:
        return True
    last = vowels[-1]
    if (last in BACK) != (first in BACK):
        return False
    return first not in HIGH or (last in ROUND) == (first in ROUND)


def _decide(text: str, i: int) -> str | None:
    """Kurallar 1-3; karar yoksa None."""
    n = len(text)
    k = i + 1
    while k < n and text[k].isalpha():
        k += 1
    suffix = _tr_lower(text[i + 1:k])
    # 1. ardından harf-i tarif geliyorsa hemze: l- (Bi’l-âhır) ya da tireden sonra tekrarlanan ünsüz (bi’t-Tevbîh,
    # Nûru’d-dîn); Sa‘d-ı gibi izafette ünsüz tekrarlanmaz
    article = _tr_lower(text[i + 1:i + 4])
    if article[:2] == "l-" or (len(article) == 3 and article[1] == "-" and article[0] == article[2]
                               and article[0].isalpha() and article[0] not in VOWELS):
        return HAMZA
    # 1b. rakamdan, kapanan ayraçtan ya da kısaltma noktasından sonra küçük harfli ek: hemze (1979’da, (s.a.s)’in)
    if i > 0 and (text[i - 1].isdigit() or text[i - 1] in NUMBER_ENDS) and k > i + 1 and text[i + 1].islower():
        return HAMZA
    # 1c. kelime başında tek harf l/d + kesme + harf: Batı dillerinde ses düşmesi (l’Islam, d’Alembert)
    if (i > 0 and text[i - 1] in ELISIONS and k > i + 1
            and (i == 1 or text[i - 2].isspace() or text[i - 2] in WORD_OPENERS)):
        return HAMZA
    # 2. büyük harfle başlayan kelime + ek → hemze (Haleb’de). Kök, iç kesme işaretlerini de kapsar (Şa‘ban’dır);
    # en az 3 harfli olmalı, 1-2 harfli kökte (Ay’ın, O’dur) ek ünlü uyumuna da uymalı (Ba‘de, Ma‘a ayınlı kalır)
    j = i
    while j > 0 and (text[j - 1].isalpha() or (text[j - 1] in APOS and j > 1 and text[j - 2].isalpha())):
        j -= 1
    stem = text[j:i]
    letters = sum(c.isalpha() for c in stem)
    if stem and text[j].isupper() and suffix in SUFFIXES and (
            letters >= MIN_STEM or (stem.isalpha() and _harmonizes(stem, suffix))):
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
    """Önce bütün dizi, bulunamazsa tireyle ayrılmış her parça listede aranır; parça yine bulunamazsa kesmeli ekten
    önceki kısmı (Kur'an'a → Kur'an) aranır, ekin işaretine kurallar karar verir."""
    decided = _segment(text, m.start(), m.end(), words)
    if decided:
        return decided
    s = m.start()
    for part in m.group().split("-"):
        found = _segment(text, s, s + len(part), words)
        for cut in (s + p for p in range(len(part) - 1, 0, -1) if part[p] in APOS):
            if found:
                break
            found = _segment(text, s, cut, words)
        decided.update(found)
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
