"""Satır kırılımı hizalaması: ana modelin blok metni, tesseract satırlarının sonlarından yeniden kırılır."""
from ocrnow.layout import Block, Line
from ocrnow.tesslines import TessLine

HYPH = "-.¬,"        # tesseract satır sonu tiresini bazen . ¬ ya da , okuyor
MAX_RATIO = 0.30     # bundan pahalı hizalamada satır yapısı kurulmaz (akan paragraf)
INDENT_PX = 30       # ilk satır blok kutusundan bundan fazla içerideyse paragraf girintili


def _align_map(a: str, b: str) -> tuple[list[int], int]:
    """Levenshtein hizalaması: (a'daki her konum için b'deki karşılık gelen konum, uzaklık)."""
    n, m = len(a), len(b)
    D = [list(range(m + 1))] + [[i] + [0] * m for i in range(1, n + 1)]
    for i in range(1, n + 1):
        ai, Di, Dp = a[i - 1], D[i], D[i - 1]
        for j in range(1, m + 1):
            Di[j] = min(Dp[j] + 1, Di[j - 1] + 1, Dp[j - 1] + (ai != b[j - 1]))
    amap = [0] * (n + 1)
    i, j = n, m
    amap[n] = m
    while i > 0:
        if j > 0 and D[i][j] == D[i - 1][j - 1] + (a[i - 1] != b[j - 1]):
            i, j = i - 1, j - 1
        elif D[i][j] == D[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
            continue
        amap[i] = j
    return amap, D[n][m]


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
        else:
            block.lines = lines
            block.indent = block.bbox is not None and tlines[0].box[0] - block.bbox[0] > INDENT_PX
            return None
    block.lines = [Line(0, len(block.text), False)]
    block.indent = False
    return f"satır yapısı kurulamadı: {block.category} bloğu, {reason}"
