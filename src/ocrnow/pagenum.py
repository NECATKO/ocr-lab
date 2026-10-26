"""Kitap sayfa numarası: blok metninden okuma ve komşu sayfalardan tahmin."""
import re

from ocrnow.layout import Block

_ROMAN = re.compile(r"(?i)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")
_ROMAN_MAX = 200  # ön söz sayfaları kısa olur; "mi", "di" gibi sözcükler roma sayılmasın
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_DIGITS = re.compile(r"[0-9]{1,4}")
SUPPORT_WINDOW = 2   # bir okuma, önceki/sonraki bu kadar okuma içinde aynı ofsetli okuma varsa destekli


def _roman_value(token: str) -> int:
    total = 0
    for ch, nxt in zip(token.lower(), token.lower()[1:] + " "):
        v = _ROMAN_VALUES[ch]
        total += -v if _ROMAN_VALUES.get(nxt, 0) > v else v
    return total


def _as_number(token: str, allow_upper_roman: bool) -> str | None:
    """Belirteç Arap rakamı ya da geçerli roma rakamıysa etiketini verir (roma büyük/küçük harfi korunur)."""
    if _DIGITS.fullmatch(token):
        n = int(token)
        return str(n) if n > 0 else None
    if token.isascii() and token.isalpha() and _ROMAN.fullmatch(token):
        if not allow_upper_roman and token != token.lower():
            return None  # "CİLT I", "BÖLÜM V" gibi başlıklar sayfa numarası değildir
        if 0 < _roman_value(token) <= _ROMAN_MAX:
            return token
    return None


def _number_in(text: str, allow_upper_roman: bool) -> str | None:
    """Metnin başındaki ya da sonundaki sayı belirteci ("— 412 —", "412 TEZKİRE", "TEZKİRE 413")."""
    tokens = [t for t in (w.strip(".,;:!?()[]{}<>«»\"'‘’“”—–-_*·•|/\\ ") for w in text.split()) if t]
    if not tokens:
        return None
    for tok in (tokens[0], tokens[-1]):
        found = _as_number(tok, allow_upper_roman)
        if found:
            return found
    return None


def _side(block: Block, width: int) -> str | None:
    if block.bbox is None:
        return None
    return "sol" if (block.bbox[0] + block.bbox[2]) / 2 < width / 2 else "sag"


def read_book_page(blocks: list[Block], width: int) -> tuple[str | None, str | None]:
    """(numara, "sol"|"sag"): önce Page-footer, yoksa Page-header; bulunamazsa (None, None).

    Roma rakamı altlıkta her iki harf türüyle, üstlükte yalnız küçük harfle kabul edilir.
    """
    for category, allow_upper in (("Page-footer", True), ("Page-header", False)):
        for block in blocks:
            if block.category != category:
                continue
            number = _number_in(block.text, allow_upper)
            if number:
                return number, _side(block, width)
    return None, None


def _offsets(read: list[str | None]) -> list[int | None]:
    """Her sayfa için ofset (numara - sıra), parçalı (R21).

    Arap rakamıyla okunan numara, önceki/sonraki SUPPORT_WINDOW okuma içinde aynı ofsetli başka bir okuma varsa
    destekli sayılır ve ofsetini korur; tek okuma (ya da hiçbir okuma destekli değilse her okuma) da korunur.
    Desteksiz okuma ve boşluk, en yakın önceki destekli ofseti (yoksa sonrakini) alır.
    """
    readings = [(i, int(v) - i) for i, v in enumerate(read) if v is not None and v.isdigit()]
    supported: dict[int, int] = {}   # sayfa sırası → ofset, sıra düzeninde
    for k, (i, off) in enumerate(readings):
        near = readings[max(0, k - SUPPORT_WINDOW):k] + readings[k + 1:k + 1 + SUPPORT_WINDOW]
        if any(o == off for _, o in near):
            supported[i] = off
    if not supported:
        supported = dict(readings)   # zincir yok (tek okuma ya da hepsi farklı): hiçbir okuma aykırı sayılamaz
    out: list[int | None] = []
    last = None
    for i in range(len(read)):
        last = supported.get(i, last)
        out.append(last)
    first = next(iter(supported.values()), None)   # ilk destekli okumadan önceki sayfalar sonrakini alır
    return [first if o is None else o for o in out]


def infer(read: list[str | None], start: int | None = None) -> list[tuple[str, bool]]:
    """Okunan numaralardan her sayfa için (etiket, tahmin_mi) üretir.

    Ofset parçalıdır (bkz. _offsets): kendi içinde tutarlı koşular (atlanan tarama sayfası, iki ayrı alıntı)
    okunduğu gibi kalır, tek aykırı okuma komşu koşudan düzeltilir. `start` verilirse bütün sayfaların ofseti
    budur. Roma rakamları okunduğu gibi kalır ve ofsete girmez. Türetilen değer 1'in altına düşerse ya da hiç ipucu
    yoksa etiket "PDF s. N" olur.
    """
    offsets = [start] * len(read) if start is not None else _offsets(read)
    out: list[tuple[str, bool]] = []
    for i, (value, offset) in enumerate(zip(read, offsets)):
        if value is not None and not value.isdigit():
            out.append((value, False))  # roma: okunduğu gibi
            continue
        derived = offset + i if offset is not None else 0
        if derived < 1:
            out.append((f"PDF s. {i + 1}", True))
        else:
            out.append((str(derived), value is None or int(value) != derived))
    return out
