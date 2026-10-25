"""Kitap sayfa numarası: blok metninden okuma ve komşu sayfalardan tahmin."""
import re
from collections import Counter

from ocrnow.layout import Block

_ROMAN = re.compile(r"(?i)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")
_ROMAN_MAX = 200  # ön söz sayfaları kısa olur; "mi", "di" gibi sözcükler roma sayılmasın
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_DIGITS = re.compile(r"[0-9]{1,4}")


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


def infer(read: list[str | None], start: int | None = None) -> list[tuple[str, bool]]:
    """Okunan numaralardan her sayfa için (etiket, tahmin_mi) üretir.

    Arap rakamları sayfa sırasına göre tek bir ofsete (numara - sıra) oy verir; en çok oy alan ofset
    zinciri belirler (eşitlikte ilk sayfanın okuması). `start` verilirse ofset budur. Roma rakamları
    okunduğu gibi kalır ve oy vermez. Türetilen değer 1'in altına düşerse ya da hiç ipucu yoksa
    etiket "PDF s. N" olur.
    """
    votes: Counter[int] = Counter()
    first_seen: dict[int, int] = {}
    for i, value in enumerate(read):
        if value is not None and value.isdigit():
            offset = int(value) - i
            votes[offset] += 1
            first_seen.setdefault(offset, i)
    if start is not None:
        offset: int | None = start
    elif votes:
        offset = max(votes, key=lambda o: (votes[o], -first_seen[o]))
    else:
        offset = None

    out: list[tuple[str, bool]] = []
    for i, value in enumerate(read):
        if value is not None and not value.isdigit():
            out.append((value, False))  # roma: okunduğu gibi
            continue
        derived = offset + i if offset is not None else 0
        if derived < 1:
            out.append((f"PDF s. {i + 1}", True))
        else:
            out.append((str(derived), value is None or int(value) != derived))
    return out
