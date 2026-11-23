"""Satır kırılımı hizalaması: ana modelin blok metni, tesseract satırlarının sonlarından yeniden kırılır."""
import statistics

from ocrnow.layout import Block, Line
from ocrnow.tesslines import TessLine

HYPH = "-.¬,"        # tesseract satır sonu tiresini bazen . ¬ ya da , okuyor
MAX_RATIO = 0.30     # bundan pahalı hizalamada satır yapısı kurulmaz (akan paragraf)
INDENT_PX = 30       # 200 DPI taban girinti eşiği (yüksek DPI'da medyan satır yüksekliğiyle büyür)
LONG_LINE = 1.6      # 3+ satırlı blokta ortancanın bu katından uzun satır: iki satır birleşmiş (tesseract kaçırmış)
MIN_OVERLONG = 60    # bundan kısa satır iki tam satırın birleşimi sayılmaz (diyalog/kısa blok yanlış alarmı)


def _banded_align_map(a: str, b: str, k: int) -> tuple[list[int], int] | None:
    """|i-j| <= k bandında Levenshtein hizalaması; uzaklık k'yi aşıyorsa None döner."""
    n, m = len(a), len(b)
    inf = n + m + 1
    rows: list[list[int]] = [list(range(min(m, k) + 1))]
    for i in range(1, n + 1):
        lo, hi = max(0, i - k), min(m, i + k)
        prev_lo = max(0, i - 1 - k)
        prev_hi = min(m, i - 1 + k)
        prev = rows[i - 1]
        row = [inf] * (hi - lo + 1)
        if lo == 0:
            row[0] = i
        ai = a[i - 1]
        for j in range(max(1, lo), hi + 1):
            c = prev[j - prev_lo] + 1 if prev_lo <= j <= prev_hi else inf
            if j - 1 >= lo and (v := row[j - 1 - lo] + 1) < c:
                c = v
            if prev_lo <= j - 1 <= prev_hi and (v := prev[j - 1 - prev_lo] + (ai != b[j - 1])) < c:
                c = v
            row[j - lo] = c
        rows.append(row)
    lo_n = max(0, n - k)
    if m < lo_n or m > min(m, n + k):
        return None
    dist = rows[n][m - lo_n]
    if dist > k:
        return None

    def cell(r: int, c: int) -> int:
        lo, hi = max(0, r - k), min(m, r + k)
        return rows[r][c - lo] if lo <= c <= hi else inf

    amap = [0] * (n + 1)
    i, j = n, m
    amap[n] = m
    while i > 0:
        cur = cell(i, j)
        if j > 0 and cur == cell(i - 1, j - 1) + (a[i - 1] != b[j - 1]):
            i, j = i - 1, j - 1
        elif cur == cell(i - 1, j) + 1:
            i -= 1
        else:
            j -= 1
            continue
        amap[i] = j
    return amap, dist


def _align_map(a: str, b: str) -> tuple[list[int], int]:
    """Levenshtein hizalaması: (a'daki her konum için b'deki karşılık gelen konum, uzaklık).

    Ukkonen şerit ikiye katlama ile tam sonuç verir: yakın dizelerde O(n·k) bellek ve zaman kullanır.
    """
    n, m = len(a), len(b)
    if n == 0:
        return [m], m
    if m == 0:
        return [0] * (n + 1), n
    limit = max(n, m)
    k = min(limit, max(abs(n - m), 32))
    while True:
        if (result := _banded_align_map(a, b, k)) is not None:
            return result
        k = min(limit, k * 2)


def rebreak(text: str, tlines: list[TessLine]) -> tuple[list[Line], float]:
    """text'i (boşlukları sadeleştirilmiş) tesseract satır sonlarından kırar. Döner: (satırlar, maliyet oranı).

    Satırlar text'i tam kaplar: tiresiz kırılımda aradaki tek boşluk iki satıra da girmez, tireli kırılımda sonraki
    satır bir sonraki harfle başlar; hiçbir harf düşmez ya da yinelenmez.
    """
    T, breaks = "", []
    for k, line in enumerate(tlines):
        t = line.text
        last = k == len(tlines) - 1
        hyph = (not last and len(t) > 1 and t[-1] in HYPH and t[-2].isalpha()
                and tlines[k + 1].text[:1].islower())
        if hyph:
            t = t[:-1]
        T += t
        if not last:
            breaks.append((len(T), hyph))
            T += "" if hyph else " "
    amap, dist = _align_map(T, text)
    n = len(text)
    lines, prev = [], 0
    for pos, hyph in breaks:
        j = amap[pos]
        if hyph and (j >= n or " " in text[max(0, j - 2):j + 2]):
            hyph = False                          # ana metinde burada kelime sınırı var: tire değil (ör. "ki,")
        if hyph:
            if j > 0 and text[j - 1] == "-":
                end = start = j                   # ana model tireyi korumuş: tireden sonra kır
                hyph = False
            elif text[j] == "-":
                end = start = j + 1
                hyph = False
            else:
                end = start = j                   # birleşik kelimeyi böl, tire eklenecek
        else:
            cand = [k for k in range(max(0, j - 4), min(n, j + 5)) if text[k] == " "]
            if cand:                              # en yakın boşlukta kır, boşluk iki satıra da girmez
                end = min(cand, key=lambda k: abs(k - j))
                start = end + 1
            else:
                end = start = j
        end = max(end, prev)                      # sıra bozulursa harf yinelenmesin
        start = max(start, end)
        lines.append(Line(prev, end, hyph))
        prev = start
    lines.append(Line(prev, n, False))
    return lines, dist / max(len(T), 1)


def _overlong(lines: list[Line]) -> str | None:
    """3+ satırlı blokta ortanca uzunluğun LONG_LINE katını aşan ilk satırın tarifi; yoksa None."""
    if len(lines) < 3:
        return None
    lengths = [l.end - l.start for l in lines]
    median = statistics.median(lengths)
    for k, n in enumerate(lengths, 1):
        if n >= MIN_OVERLONG and n > LONG_LINE * median:
            return f"satır {k} çok uzun ({n} karakter, ortanca {median:g}; tesseract bir satırı kaçırmış olabilir)"
    return None


def apply_line_structure(block: Block, tlines: list[TessLine]) -> str | None:
    """block.lines ve block.indent'i doldurur. Satır yapısı kurulamazsa akan paragraf yapar ve uyarı döner."""
    if not tlines:
        reason = "tesseract satırı yok"
    else:
        lines, ratio = rebreak(block.text, tlines)
        if ratio > MAX_RATIO:
            reason = f"oran {ratio:.2f}"
        elif any(l.start >= l.end for l in lines):    # bloğa yabancı satır düşmüş (başlık, sayfa no, komşu blok)
            reason = "satır sayısı tutmuyor"
        elif long := _overlong(lines):
            reason = long
        else:
            for line, tl in zip(lines, tlines):       # rebreak her TessLine için bir satır döndürür
                line.box = tl.box
            block.lines = lines
            median_h = statistics.median(max(1, l.box[3] - l.box[1]) for l in tlines)
            indent_px = max(INDENT_PX, round(median_h))
            block.indent = block.bbox is not None and tlines[0].box[0] - block.bbox[0] > indent_px
            return None
    block.lines = [Line(0, len(block.text), False)]
    block.indent = False
    return f"satır yapısı kurulamadı: {block.category} bloğu, {reason}"
