"""Kitap sayfa numarası: blok metninden okuma ve komşu sayfalardan tahmin."""
import re
import unicodedata

from ocrnow.layout import Block

_ROMAN = re.compile(r"(?i)m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")
_ROMAN_MAX = 200  # ön söz sayfaları kısa olur; "mi", "di" gibi sözcükler roma sayılmasın
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_DIGITS = re.compile(r"[0-9]{1,4}")
SUPPORT_WINDOW = 2   # bir okuma, önceki/sonraki bu kadar okuma içinde aynı ofsetli okuma varsa destekli
MAJORITY = 0.75      # taraf/başlık–çiftlik kuralı için açık çoğunluk oranı
SIDE_MIN_VOTES = 2   # taraf–çiftlik kuralı en az bu kadar okumadan öğrenilir


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


Key = tuple[int, int]   # (yön, ofset): yön +1 artan, -1 azalan koşu; numara = ofset + yön × sıra


def _keys(i: int, n: int) -> tuple[Key, Key]:
    """Sayfa sırası i'de okunan n'nin artan ve azalan koşu anahtarları."""
    return (1, n - i), (-1, n + i)


def _value(key: Key, i: int) -> int:
    return key[1] + key[0] * i


def _majority(votes: list[bool], min_votes: int) -> bool | None:
    """Oyların açık çoğunluğu (en az MAJORITY oranı); min_votes oydan azsa ya da çoğunluk yoksa None."""
    if len(votes) < min_votes:
        return None
    yes = sum(votes)
    if yes >= MAJORITY * len(votes):
        return True
    if len(votes) - yes >= MAJORITY * len(votes):
        return False
    return None


def _header_key(text: str | None) -> str:
    """Başlık metninin karşılaştırma anahtarı: yalnız harfler, küçük harf, aksansız (ÂLİ = ALİ = Ali)."""
    s = unicodedata.normalize("NFD", (text or "").lower().replace("ı", "i"))
    return "".join(c for c in s if c.isalpha())


class _Clues:
    """Sayfanın numarası çift mi olmalı (R25): kitabın taraf–çiftlik kuralı ve başlık–çiftlik eşlemesi okumalardan
    öğrenilir (Arap rakamlı okumalardan). Taraf kuralı en az SIDE_MIN_VOTES okuma ve açık çoğunluk ister; başlık
    eşlemesinde açık çoğunluk yoksa (aynı başlık hem tek hem çift sayfada) o başlık ipucu vermez."""

    def __init__(self, readings: list[tuple[int, int]], sides: list[str | None], headers: list[str | None]):
        self.sides = sides
        self.headers = [_header_key(h) for h in headers]
        self.even_left = _majority([(n % 2 == 0) == (sides[i] == "sol") for i, n in readings if sides[i]],
                                   SIDE_MIN_VOTES)
        by_header: dict[str, list[bool]] = {}
        for i, n in readings:
            if self.headers[i]:
                by_header.setdefault(self.headers[i], []).append(n % 2 == 0)
        self.header_even = {h: v for h, votes in by_header.items() if (v := _majority(votes, 1)) is not None}

    def side_even(self, i: int) -> bool | None:
        """Sayfanın kendi tarafından beklenen çiftlik; taraf ya da kural yoksa None."""
        if self.sides[i] is None or self.even_left is None:
            return None
        return (self.sides[i] == "sol") == self.even_left

    def even(self, i: int) -> bool | None:
        """Beklenen çiftlik: önce kendi tarafından, yoksa başlığından; ipucu yoksa None."""
        side = self.side_even(i)
        return side if side is not None else self.header_even.get(self.headers[i])

    def allows(self, i: int, n: int) -> bool:
        expected = self.even(i)
        return expected is None or expected == (n % 2 == 0)


def _run_keys(readings: list[tuple[int, int]], clues: _Clues) -> tuple[list[Key | None], dict[int, int]]:
    """(her okumanın koşu anahtarı ya da None, düzeltilen okumalar: sayfa sırası → numara).

    Bir okuma, önceki/sonraki SUPPORT_WINDOW okuma içinde aynı artan (yoksa azalan) anahtarlı bir okuma varsa
    destekli bir koşudadır. Okuma, iki yanındaki okuma kendisinin olmayan aynı koşudansa (R21: tutarlı koşu içindeki
    tek aykırı okuma) koşudan düzeltilir, düzeltme sayfanın çiftlik ipucuyla çelişmiyorsa; desteksiz ya da ters yönde
    tesadüfen desteklenmiş (412, 413, 412, 415: 412 azalan yönde 413'le eşleşir) okuma da buna dahildir. Diğer
    okumalar okunduğu gibi kalır.
    """
    keys: list[Key | None] = []
    for k, (i, n) in enumerate(readings):
        near = readings[max(0, k - SUPPORT_WINDOW):k] + readings[k + 1:k + 1 + SUPPORT_WINDOW]
        near_keys = {key for j, m in near for key in _keys(j, m)}
        keys.append(next((key for key in _keys(i, n) if key in near_keys), None))
    corrected: dict[int, int] = {}
    for k in range(1, len(readings) - 1):
        key, i = keys[k - 1], readings[k][0]
        if key is not None and keys[k + 1] == key and keys[k] != key:
            derived = _value(key, i)
            if derived >= 1 and clues.allows(i, derived):
                keys[k], corrected[i] = key, derived
    return keys, corrected


def _guess(i: int, readings: list[tuple[int, int]], keys: list[Key | None]) -> int | None:
    """Okunamayan sayfa için koşudan tahmin; yalnız en yakın önceki okuma bir koşudaysa (anahtarı olan okumanın
    koşusunda en az iki okuma vardır: sayfa koşunun içinde ya da hemen devamında) ya da bütün dizide tek okuma
    varsa ve sayfa ondan sonraysa (kısa alıntı). İlk okumadan önceki sayfalar (numarasız bölüm başı) yalnız ilk okuma
    bir koşudaysa geriye doğru sayılır; aradaki boşluğun sonraki koşudan geriye doğru sayılması yoktur."""
    before = [k for k, (j, _) in enumerate(readings) if j < i]
    if not before:
        return _value(keys[0], i) if readings and keys[0] is not None else None
    k = before[-1]
    if keys[k] is not None:
        return _value(keys[k], i)
    if len(readings) == 1:
        j, n = readings[0]
        return n + (i - j)
    return None


def infer(read: list[str | None], start: int | None = None, sides: list[str | None] | None = None,
          headers: list[str | None] | None = None) -> list[tuple[str, bool]]:
    """Okunan numaralardan her sayfa için (etiket, tahmin_mi) üretir (R25).

    Okunan Arap rakamı numaraya güvenilir. Kitabın taraf–çiftlik kuralına (sides: okunan numaranın tarafı,
    "sol"|"sag"|None) aykırı okuma okunduğu gibi kalır ama tahmin işaretlenir. Artan ya da azalan tutarlı bir koşunun
    içindeki tek aykırı okuma koşudan düzeltilir. Okunamayan sayfa yalnız bir koşunun içindeyse ya da en az iki
    okumalı bir koşunun hemen devamıysa (ya da dizideki tek okumadan sonraysa, ya da ilk okumadan önce ve o okuma
    bir koşudaysa) tahmin edilir; tahmin sayfanın
    çiftlik ipucuyla (headers: sayfanın Page-header metni) çelişirse, 1'in altına düşerse ya da tahmin yapılamazsa
    etiket "PDF s. N" olur. Roma rakamları okunduğu gibi kalır ve oy vermez. `start` verilirse bütün sayfalar
    start + sıra olur; okunan numara tutmuyorsa tahmin işaretlenir (taraf ve başlık kullanılmaz).
    """
    out: list[tuple[str, bool]] = []
    if start is not None:   # eski davranış: sıra esas
        for i, value in enumerate(read):
            derived = start + i
            if value is not None and not value.isdigit():
                out.append((value, False))
            elif derived < 1:
                out.append((f"PDF s. {i + 1}", True))
            else:
                out.append((str(derived), value is None or int(value) != derived))
        return out
    sides = sides or [None] * len(read)
    headers = headers or [None] * len(read)
    readings = [(i, int(v)) for i, v in enumerate(read) if v is not None and v.isdigit()]
    clues = _Clues(readings, sides, headers)
    keys, corrected = _run_keys(readings, clues)
    for i, value in enumerate(read):
        if value is not None and not value.isdigit():
            out.append((value, False))  # roma: okunduğu gibi
        elif i in corrected:
            out.append((str(corrected[i]), True))
        elif value is not None:
            expected = clues.side_even(i)
            out.append((value, expected is not None and expected != (int(value) % 2 == 0)))
        else:
            guess = _guess(i, readings, keys)
            ok = guess is not None and guess >= 1 and clues.allows(i, guess)
            out.append((str(guess), True) if ok else (f"PDF s. {i + 1}", True))
    return out
