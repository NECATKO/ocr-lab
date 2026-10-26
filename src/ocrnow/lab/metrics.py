"""Doğruluk ölçüleri. Referans uygulama: docs/superpowers/specs/2026-10-25-ocr-now-ekler/cmp.py.

Karakter ölçüleri hata oranıdır (Levenshtein mesafesi / referans uzunluğu); düşük iyidir.
line_end_accuracy ve apostrophe_accuracy doğruluktur (1.0 hepsi doğru); yüksek iyidir.
"""
import re
import unicodedata
from difflib import SequenceMatcher

from ocrnow.apostrophe import AIN, APOS, HAMZA

# Ayın/hemze türü: ʿ ‘ ile, ʾ ’ ile aynı sayılır; düz ' ` ´ ise tür bildirmez (referansta yoksa sayılmaz)
_KIND = {AIN: AIN, "ʿ": AIN, HAMZA: HAMZA, "ʾ": HAMZA}
_SUP_ETC = re.compile(r"</?sup>|\^|\*\*|^#+\s*|^---+$|\|", re.M)
_HYPHEN_BREAK = re.compile(r"(\w)[-¬]\s*\n\s*(\w)")
_LETTER_DIGIT = re.compile(r"(?<=[^\W\d])(?=\d)")
_TR = str.maketrans("ıİşŞğĞçÇöÖüÜ", "iIsSgGcCoOuU")
_TOKEN = re.compile(rf"[\w{re.escape(APOS)}-]+")
_PAGE_NO = re.compile(r"\[?(?:\d{1,4}|[ivxlcdm]{1,7})\]?", re.I)
_MAX_HEADER = 60   # çalışan başlık kısa bir satırdır


# ---- Levenshtein ----

def _banded(a: str, b: str, k: int) -> int:
    """|i-j| <= k bandında Levenshtein; sonuç k'yi aşıyorsa kesin değildir (üst sınırdır)."""
    n, m = len(a), len(b)
    inf = n + m + 1
    prev = [j if j <= k else inf for j in range(m + 1)]
    for i in range(1, n + 1):
        cur = [inf] * (m + 1)
        lo, hi = max(1, i - k), min(m, i + k)
        if i <= k:
            cur[0] = i
        ai = a[i - 1]
        for j in range(lo, hi + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ai != b[j - 1]))
        prev = cur
    return prev[m]


def distance(a: str, b: str) -> int:
    """Levenshtein mesafesi. Ortak baş/son atılır, sonra band iki katına çıkarılarak tam sonuç bulunur (Ukkonen)."""
    start = 0
    while start < min(len(a), len(b)) and a[start] == b[start]:
        start += 1
    a, b = a[start:], b[start:]
    while a and b and a[-1] == b[-1]:
        a, b = a[:-1], b[:-1]
    if not a or not b:
        return len(a) + len(b)
    k = max(abs(len(a) - len(b)), 16)
    while True:
        d = _banded(a, b, k)
        if d <= k:   # k'dan pahalı bir yol band dışına çıkamadığı için d tamdır
            return d
        k *= 2


def _align(a: list, b: list) -> list[tuple[int | None, int | None]]:
    """Levenshtein hizalaması: (i, j) eşleşme/değişim, (i, None) silme, (None, j) ekleme."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        row, up, ai = d[i], d[i - 1], a[i - 1]
        for j in range(1, m + 1):
            row[j] = min(up[j] + 1, row[j - 1] + 1, up[j - 1] + (ai != b[j - 1]))
    out, i, j = [], n, m
    while i or j:
        if i and j and d[i][j] == d[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            out.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif i and d[i][j] == d[i - 1][j] + 1:
            out.append((i - 1, None))
            i -= 1
        else:
            out.append((None, j - 1))
            j -= 1
    return out[::-1]


# ---- normalizasyonlar ----

def _raw(t: str) -> str:
    return " ".join(t.split())


def _norm(t: str) -> str:
    """Markdown işaretleri atılır, kesme işaretleri ' olur, satır sonu tireleri birleşir, dipnot numarası ayrılır."""
    t = unicodedata.normalize("NFC", t)
    t = _SUP_ETC.sub(" ", t)
    t = _HYPHEN_BREAK.sub(r"\1\2", t)   # kraken satır sonu işareti ¬ de
    t = _LETTER_DIGIT.sub(" ", t)       # taştında724 -> taştında 724
    t = re.sub(f"[{re.escape(APOS)}“”\"]", "'", t)
    return " ".join(t.split())


def _base(t: str) -> str:
    """norm + ı/ş/ğ/ç/ö/ü ve işaretli harfler sade harfe, kesme işareti atılır, küçük harf."""
    t = _norm(t).translate(_TR)
    t = "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c))
    return " ".join(t.replace("'", "").lower().split())


def _words(t: str) -> list[str]:
    """Kelime düzeyi için tire birleştirmeleri eşitlenir: "senâ-kâr" ~ "senâkâr"."""
    return re.sub(r"[^\w\s']", " ", t.replace("-", "")).split()


def _body(t: str) -> str:
    """Başta kısa BÜYÜK HARFLİ çalışan başlık (ya da yalnız numara) ve sonda yalnız sayfa numarası satırı atılır."""
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    if lines and (_PAGE_NO.fullmatch(lines[0]) or (len(lines[0]) <= _MAX_HEADER and lines[0].isupper())):
        lines = lines[1:]
    if lines and _PAGE_NO.fullmatch(lines[-1]):
        lines = lines[:-1]
    return "\n".join(lines)


# ---- hata oranları ----

def _rate(ref: str, hyp: str) -> float:
    return distance(ref, hyp) / max(len(ref), 1)


def cer_raw(ref: str, hyp: str) -> float:
    """Ham CER: yalnız boşluklar sadeleştirilir (eski benchmark betiğindeki cer ile aynı)."""
    return _rate(_raw(ref), _raw(hyp))


def cer_norm(ref: str, hyp: str) -> float:
    """Normalize CER: Markdown ve kesme işareti farkları ile satır sonu tireleri yok sayılır."""
    return _rate(_norm(ref), _norm(hyp))


def cer_body(ref: str, hyp: str) -> float:
    """Gövde CER: normalize CER, çalışan başlık ve sayfa numarası iki metinden de atılarak."""
    return _rate(_norm(_body(ref)), _norm(_body(hyp)))


def cer_base(ref: str, hyp: str) -> float:
    """Harf CER: işaretler (â î û ...), kesme işareti ve büyük/küçük harf yok sayılır."""
    return _rate(_base(ref), _base(hyp))


def word_errors(ref: str, hyp: str) -> list[tuple[str, str]]:
    """Hatalı kelimeler (referans, çıktı); eksik kelimede çıktı '', fazla kelimede referans '' olur."""
    a, b = _words(_norm(ref)), _words(_norm(hyp))
    return [(a[i] if i is not None else "", b[j] if j is not None else "")
            for i, j in _align(a, b) if i is None or j is None or a[i] != b[j]]


def wer(ref: str, hyp: str) -> float:
    return len(word_errors(ref, hyp)) / max(len(_words(_norm(ref))), 1)


# ---- doğruluklar ----

def _tokens(t: str) -> list[str]:
    return _TOKEN.findall(_HYPHEN_BREAK.sub(r"\1\2", unicodedata.normalize("NFC", t)))


def _token_key(tok: str) -> str:
    return "".join("'" if c in APOS else c for c in tok.lower())


def apostrophe_accuracy(ref: str, hyp: str) -> tuple[float, int]:
    """(tür doğruluğu, referanstaki ayın/hemze sayısı). Kelimeler hizalanır; hizalanan kelimedeki her işaret
    referanstakiyle sırayla karşılaştırılır. Hizalanamayan kelime ya da işaret sayısı tutmayan kelime yanlış sayılır.
    Referansta işaret yoksa (0.0, 0) döner: tanımsız."""
    a, b = _tokens(ref), _tokens(hyp)
    pair = {i: j for i, j in _align([_token_key(t) for t in a], [_token_key(t) for t in b]) if i is not None}
    total = correct = 0
    for i, tok in enumerate(a):
        want = [_KIND[c] for c in tok if c in _KIND]
        if not want:
            continue
        total += len(want)
        j = pair.get(i)
        if j is None:
            continue
        got = [_KIND.get(c) for c in b[j] if c in APOS]
        if len(got) == len(want):
            correct += sum(x == y for x, y in zip(want, got))
    return (correct / total if total else 0.0), total


def _line_key(line: str) -> str:
    return "".join("'" if c in APOS else c for c in line.strip())


def line_end_accuracy(ref: str, hyp_lines: list[str]) -> float:
    """Referans satırlarının kaçının son 3 karakteri çıktıdaki karşılığıyla aynı (kesme işaretleri eşit sayılır).

    Satırlar benzerliğe göre hizalanır; bir bloğun satırları kayarsa sonrası bozulmaz.
    Karşılığı olmayan referans satırı yanlış sayılır; referans boşsa 0.0 döner.
    """
    a = [_line_key(ln) for ln in ref.splitlines() if ln.strip()]
    b = [_line_key(ln) for ln in hyp_lines if ln.strip()]
    if not a:
        return 0.0
    n, m = len(a), len(b)
    sub = [[0.0 if x == y else 1 - SequenceMatcher(None, x, y, autojunk=False).ratio() for y in b] for x in a]
    cost = [[float(i + j) if not (i and j) else 0.0 for j in range(m + 1)] for i in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i - 1][j] + 1, cost[i][j - 1] + 1, cost[i - 1][j - 1] + sub[i - 1][j - 1])
    i, j, hits = n, m, 0
    while i and j:
        if cost[i][j] == cost[i - 1][j - 1] + sub[i - 1][j - 1]:
            hits += a[i - 1][-3:] == b[j - 1][-3:]
            i, j = i - 1, j - 1
        elif cost[i][j] == cost[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return hits / n
