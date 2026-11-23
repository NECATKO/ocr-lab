"""Aranabilir PDF'in görünmez metin katmanı: sayfayı, görüntü üzerine konacak metin parçalarına çevirir (PDF koduna
dokunmaz).

Metin bloğunun her kelimesi tesseract TSV'sindeki karşılık gelen kelime kutusuna konur (blok metni ile bloğa atanmış
tesseract kelimeleri karakter düzeyinde hizalanır). Tesseract'ın okumadığı kelimeler komşularının arasına, bütün bir
satır kaçmışsa iki satırın arasına kurulan sentetik satıra yerleşir. Bloğa hiç tesseract kelimesi düşmemişse (ya da
blok Table ise) eski yol kullanılır: satır kutuları varsa satır satır, yoksa metin blok kutusuna yayılır.
"""
import re
import statistics
import unicodedata
from dataclasses import dataclass

from ocrnow.align import _align_map
from ocrnow.export_txt import plain_text
from ocrnow.layout import Block, Page
from ocrnow.tesslines import TessLine, assign_lines, parse_tsv

PITCH_PER_HEIGHT = 1.5      # sayfada ardışık satır yoksa satır aralığı ≈ satır kutusu yüksekliği × bu
MAX_GAP = 3                 # bundan (× satır yüksekliği) büyük üst-üste aralık satır aralığı sayılmaz (sütun/boşluk)
PAGE_MARGIN = 0.05          # blok kutusu da yoksa sayfanın her kenarından bırakılan pay
MISSING_ROW = 1.5           # ardışık eşleşen iki kelimenin satır üstleri arası > bu × satır aralığı: satır kaçmış
NO_WORDS = frozenset({"Picture", "Table"})   # kelime yerleşimi yapılmayan kategoriler
MIN_MATCHED = 0.5           # satır kutulu blokta eşleşen kelime payı bundan azsa satır satır yerleşir
MIN_GAP = 2.0               # dar boşluğa konan kelime grubunun en küçük genişliği, piksel
_WORD = re.compile(r"\S+")


@dataclass(frozen=True)
class LayerLine:
    text: str
    box: tuple[float, float, float, float]   # piksel (x1, y1, x2, y2), y aşağı
    block: int                               # sayfadaki blok sırası (ActualText gruplaması için)


def _boxed(block: Block) -> bool:
    # Table'ın metni HTML'dir; satır aralıkları ona uymaz, düz metinle yerleştirilir
    return bool(block.lines) and block.category != "Table" and all(ln.box is not None for ln in block.lines)


def _boxed_lines(index: int, block: Block) -> list[LayerLine]:
    """Her satır kendi kutusuna; tireli kırılan kelime birinci satırda bütün kalır, sonraki satır kelimeden sonra başlar."""
    text, out, done = block.text, [], 0   # done: önceki satırların metinde tükettiği son konum
    for ln in block.lines:
        start, end = max(ln.start, done), ln.end
        if ln.hyphen:
            space = text.find(" ", max(end, start))
            end = len(text) if space < 0 else space
        end = max(end, start)
        done = end
        piece = text[start:end].strip()
        if piece:
            out.append(LayerLine(piece, tuple(float(v) for v in ln.box), index))
    return out


def _line_metrics(page: Page, assigned: list[list[TessLine]] = ()) -> tuple[float, float]:
    """Sayfanın kutulu satırlarından ve bloklara atanmış tesseract satırlarından (satır yüksekliği, satır aralığı)
    tahmini, piksel.

    Aralık: aynı bloktaki ardışık satır üstleri arasındaki farkların ortancası. Ardışık satır yoksa yükseklik × 1,5;
    hiç kutu yoksa ikisi de sayfa yüksekliği / 50."""
    heights, gaps = [], []
    seqs = [[ln.box for ln in b.lines if ln.box is not None] for b in page.blocks]
    seqs += [[t.box for t in group] for group in assigned]
    for boxes in seqs:
        tops = []
        for box in boxes:
            if box[3] <= box[1]:
                continue
            heights.append(box[3] - box[1])
            tops.append(box[1])
        gaps += [t2 - t1 for t1, t2 in zip(tops, tops[1:])]
    if not heights:
        return page.height / 50, page.height / 50
    lh = statistics.median(heights)
    gaps = [g for g in gaps if 0 < g <= MAX_GAP * lh]
    return lh, (statistics.median(gaps) if gaps else lh * PITCH_PER_HEIGHT)


def _split_words(text: str, k: int) -> list[str]:
    """Metni kelime sınırlarından yaklaşık eşit uzunlukta, boş olmayan (en çok) k satıra böler."""
    words = text.split()
    k = max(1, min(k, len(words)))
    mids, pos = [], 0   # her kelimenin ortasının metindeki konumu
    for w in words:
        mids.append(pos + len(w) / 2)
        pos += len(w) + 1
    total = max(pos - 1, 1)
    rows, i = [], 0
    for g in range(k - 1):
        j = i + 1                                     # her satıra en az bir kelime
        last = len(words) - (k - 1 - g)               # kalan satırlara da birer kelime kalsın
        while j < last and mids[j] <= (g + 1) * total / k:
            j += 1
        rows.append(" ".join(words[i:j]))
        i = j
    rows.append(" ".join(words[i:]))
    return rows


def _unboxed_lines(index: int, block: Block, page: Page, lh: float, pitch: float) -> list[LayerLine]:
    """Satır kutusu olmayan blok: satır sayısı blok yüksekliği / sayfanın satır aralığı; satırlar kutuya yayılır
    (ilk satır kutunun üstünde, son satır altında)."""
    text = plain_text(block).strip()
    if not text:
        return []
    x1, y1, x2, y2 = block.bbox or (page.width * PAGE_MARGIN, page.height * PAGE_MARGIN,
                                    page.width * (1 - PAGE_MARGIN), page.height * (1 - PAGE_MARGIN))
    height = max(y2 - y1, 1)
    rows = _split_words(text, max(1, round(height / pitch)))
    k = len(rows)
    h = min(height / k, lh)
    step = (height - h) / (k - 1) if k > 1 else 0
    out = []
    for i, piece in enumerate(rows):
        top = y1 + i * step
        out.append(LayerLine(piece, (float(x1), float(top), float(x2), float(min(top + h, y2))), index))
    return out


@dataclass
class Word:
    """Yerleştirilmiş bir model kelimesi (katmana yazılmadan önce)."""
    text: str
    box: tuple[float, float, float, float]   # piksel (x1, y1, x2, y2), y aşağı
    # Satır kimliği: tesseract satırının bloktaki sırası (int; place_words'ün `rows`/`rtl` sözlüklerinin anahtarı) ya
    # da ("sentetik", n). Eşleşen kelimeler ve gruplanacak komşular (P/N) her zaman int satırdadır; sentetik satırlar
    # yalnız grup kelimelerine verilir ve `rows`'ta aranmaz. _word_lines yalnız eşitliğe bakar (aynı satır mı).
    row: int | tuple[str, int]
    # Kelimenin nasıl yerleştiği: "tess" (kendi kutusu) | "ara" | "kenar" (komşusunun yanında) | "okunamayan" (satırı)
    # | "sentetik". Yerleşime etkisi yoktur; yalnız ölçüm ve hata ayıklama içindir.
    how: str


def _rtl_text(text: str) -> bool:
    """İlk güçlü karakter Arapça/İbranice mi (Unicode bidi P2)."""
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind in ("R", "AL"):
            return True
        if kind == "L":
            return False
    return False


def assigned_lines(page: Page) -> list[list[TessLine]]:
    """Her bloğa atanmış tesseract satırları (kelimeleriyle). Picture ve Table'a satır atanmaz."""
    tlines = parse_tsv(page.tess_tsv) if page.tess_tsv.strip() else []
    if not tlines:
        return [[] for _ in page.blocks]
    return assign_lines(page.blocks, tlines, categories=frozenset(b.category for b in page.blocks) - NO_WORDS)


# hizalamada aynı sayılan işaretler (harf harf çevrilir, konumlar kaymaz): tesseract ’ yerine ' okur
_SAME = str.maketrans({"’": "'", "‘": "'", "`": "'", "´": "'", "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
                       "–": "-", "—": "-", "¬": "-"})

Flat = list[tuple[int, tuple[int, int, int, int], str]]   # (bloktaki satır sırası, kelime kutusu, kelime)


def _norm(text: str) -> str:
    """Hizalama için harf harf sadeleştirme: işaret birliği ve küçük harf; uzunluk korunur ("İ".lower() iki harftir,
    öyle harfler olduğu gibi kalır)."""
    return "".join(low if len(low := ch.lower()) == 1 else ch for ch in text.translate(_SAME))


def _owners(text: str, flat: Flat) -> list[dict[int, list[int]] | None]:
    """Metnin her kelimesi için {flat sırası: o tesseract kelimesindeki eşleşen harf sıraları}; eşleşmezse None.

    Blok metni bloğun bütün tesseract kelimeleriyle harf düzeyinde hizalanır. Tesseract'ın kaçırdığı metin hizalamaya
    eklenti olarak girer; Levenshtein eklentinin yerini umursamadığı için bir tesseract kelimesinin harfleri o
    eklentiye saçılabilir. Aynen okunan harfleri kendi uzunluğundan geniş bir alana dağılmış ya da yarısından azı
    aynen okunmuş tesseract kelimesi güvenilmez sayılır, kullanılmaz (`_rematch` kelime düzeyinde yeniden dener).
    Bir model kelimesi, harflerinin en az yarısı güvenilir kelimelerden aynen geldiyse eşleşmiştir."""
    chars, owner = [], []
    for wi, (_, _, w) in enumerate(flat):
        if wi:
            chars.append(" ")
            owner.append(None)
        for ci, ch in enumerate(w):
            chars.append(ch)
            owner.append((wi, ci))
    tess, norm = _norm("".join(chars)), _norm(text)
    amap, _ = _align_map(tess, norm)
    hits: dict[int, list[tuple[int, int, bool]]] = {}   # tesseract kelimesi → (metin konumu, harf sırası, aynı mı)
    for i, o in enumerate(owner):
        j = amap[i]
        if o is not None and j < len(text) and not text[j].isspace():
            hits.setdefault(o[0], []).append((j, o[1], tess[i] == norm[j]))
    strong: list[tuple[int, int] | None] = [None] * len(text)   # aynı harf
    weak: list[tuple[int, int] | None] = [None] * len(text)     # başka harf ya da silinmiş tesseract harfi
    for wi, hs in hits.items():
        same = [j for j, _, ok in hs if ok]
        if 2 * len(same) < len(flat[wi][2]) or max(same) - min(same) >= len(flat[wi][2]) + 1:
            continue
        for j, ci, ok in hs:
            (strong if ok else weak)[j] = (wi, ci)
    out = []
    for m in _WORD.finditer(text):
        span = range(m.start(), m.end())
        if 2 * sum(strong[j] is not None for j in span) < len(span):
            out.append(None)
            continue
        mine: dict[int, list[int]] = {}
        for j in span:
            if o := strong[j] or weak[j]:
                mine.setdefault(o[0], []).append(o[1])
        out.append(mine)
    return out


def _flat_words(tlines: list[TessLine]) -> tuple[Flat, dict[int, bool]]:
    """Bloğun tesseract kelimeleri okuma sırasıyla ve her satırın yönü (True: sağdan sola). Tesseract bazen bir
    kelimenin kutusunu sonraki kelimelerin üstüne taşırır: kutu satırdaki sonraki kelimenin başında kesilir."""
    flat: Flat = []
    rtl: dict[int, bool] = {}
    for li, tl in enumerate(tlines):
        ws = tl.words
        rtl[li] = ws[0][0][0] > ws[-1][0][0] if len(ws) > 1 else _rtl_text(tl.text)
        for k, ((x1, y1, x2, y2), w) in enumerate(ws):
            if k + 1 < len(ws):
                nx1, _, nx2, _ = ws[k + 1][0]
                if not rtl[li] and x1 < nx1 < x2:
                    x2 = nx1
                elif rtl[li] and x1 < nx2 < x2:
                    x1 = nx2
            flat.append((li, (x1, y1, x2, y2), w))
    return flat, rtl


def _lay(words: list[str], a: float, b: float, rtl: bool, lead: bool, trail: bool) -> list[tuple[float, float]]:
    """Kelimeleri [a, b] aralığına harf sayılarıyla orantılı dizer (aralarında bir harflik boşluk; lead/trail: baştaki
    ve sondaki boşluk). Döner: her kelimenin (x1, x2)'si; rtl ise ilk kelime sağda."""
    units = sum(len(w) for w in words) + len(words) - 1 + lead + trail
    u = max(b - a, 0) / max(units, 1)
    out, pos = [], lead
    for w in words:
        lo, hi = pos * u, (pos + len(w)) * u
        out.append((b - hi, b - lo) if rtl else (a + lo, a + hi))
        pos += len(w) + 1
    return out


MAX_REMATCH = 4000          # bundan büyük (grup × aday) yeniden eşleme yapılmaz (hizalama bütünüyle tutmamış)
REMATCH_DIST = 0.5          # kelime düzeyinde yeniden eşlemede kabul edilen en büyük (uzaklık / uzun kelime)


def _rematch(spans: list[str], owners: list[dict[int, list[int]] | None], flat: Flat) -> None:
    """Eşleşmeyen her kelime grubunu, komşu eşleşen kelimelerin arasında kalan kullanılmamış tesseract kelimeleriyle
    kelime düzeyinde (sıra korunarak, en çok benzerlik) eşler; eşleşen kelime tesseract kelimesinin bütün kutusunu
    alır."""
    used = {wi for mine in owners if mine for wi in mine}
    i = 0
    while i < len(owners):
        if owners[i] is not None:
            i += 1
            continue
        j = i
        while j < len(owners) and owners[j] is None:
            j += 1
        lo = max(owners[i - 1]) if i else -1
        hi = min(owners[j]) if j < len(owners) else len(flat)
        free = [wi for wi in range(lo + 1, hi) if wi not in used]
        g = j - i
        if free and g * len(free) <= MAX_REMATCH:
            sim = [[0.0] * len(free) for _ in range(g)]
            for a in range(g):
                ma = _norm(spans[i + a])
                for b, wi in enumerate(free):
                    tb = _norm(flat[wi][2])
                    d = _align_map(tb, ma)[1] / max(len(ma), len(tb))
                    sim[a][b] = 1 - d if d <= REMATCH_DIST else 0.0
            best = [[0.0] * (len(free) + 1) for _ in range(g + 1)]   # en çok toplam benzerlik (sıralı eşleme)
            for a in range(g - 1, -1, -1):
                for b in range(len(free) - 1, -1, -1):
                    best[a][b] = max(best[a + 1][b], best[a][b + 1],
                                     sim[a][b] + best[a + 1][b + 1] if sim[a][b] else 0.0)
            a = b = 0
            while a < g and b < len(free):
                if sim[a][b] and best[a][b] == sim[a][b] + best[a + 1][b + 1]:
                    wi = free[b]
                    owners[i + a] = {wi: list(range(len(flat[wi][2])))}
                    used.add(wi)
                    a, b = a + 1, b + 1
                elif best[a][b] == best[a + 1][b]:
                    a += 1
                else:
                    b += 1
        i = j


FULL_ROW = 0.8              # tahmini genişlik satırın bu kadarını aşıyorsa metin bütün satıra yayılır (iki yana yaslı)


def _fit(x1: float, x2: float, need: float, rtl: bool, lo: float, hi: float) -> tuple[float, float]:
    """Satırdaki metnin yatay aralığı: tahmini genişlik (need) satırı neredeyse dolduruyorsa bütün satır, değilse
    satırın başından (sağdan sola yazıda sağından) need kadar; satır need'den kısaysa need kadar uzatılır. Sonuç hiçbir
    zaman [lo, hi] (blok kutusu) dışına taşmaz."""
    width = max(need, x2 - x1) if need >= FULL_ROW * (x2 - x1) else need
    if rtl:
        start = min(x2, hi)
        return max(lo, start - width), start
    start = max(x1, lo)
    return start, min(hi, start + width)


def _matched(spans: list[str], owners: list[dict[int, list[int]] | None], flat: Flat,
             rows: dict[int, tuple[int, int, int, int]]) -> list[Word | None]:
    """Eşleşen kelimelerin kutuları: tireyle bölünmüşse yalnız ilk satırdaki tesseract parçalarının birleşimi; aynı
    tesseract kelimesi birden çok model kelimesine düşmüşse kutusu harflere göre bölünür. y her zaman satırın."""
    firsts: list[dict[int, list[int]] | None] = []
    users: dict[int, int] = {}
    for mine in owners:
        if mine is None:
            firsts.append(None)
            continue
        first = min(flat[wi][0] for wi in mine)
        firsts.append({wi: cis for wi, cis in mine.items() if flat[wi][0] == first})
        for wi in firsts[-1]:
            users[wi] = users.get(wi, 0) + 1
    out: list[Word | None] = []
    for word, mine in zip(spans, firsts):
        if mine is None:
            out.append(None)
            continue
        xs = []
        for wi, cis in mine.items():
            li, (bx1, _, bx2, _), w = flat[wi]
            if users[wi] == 1:
                xs += [bx1, bx2]
            else:
                xs += [bx1 + (bx2 - bx1) * min(cis) / len(w), bx1 + (bx2 - bx1) * (max(cis) + 1) / len(w)]
        _, ry1, _, ry2 = rows[li]
        out.append(Word(word, (float(min(xs)), float(ry1), float(max(xs)), float(ry2)), li, "tess"))
    return out


def place_words(block: Block, tlines: list[TessLine], page: Page, lh: float, pitch: float) -> list[Word] | None:
    """Bloğun kelimelerini okuma sırasıyla yerleştirir; hiçbir kelime tesseract'la eşleşmezse None.

    Eşleşmeyen ardışık kelimeler (grup) önceki (P) ve sonraki (N) eşleşen kelimeye göre konur:
    - P ile N aynı satırda: aradaki boşluğa;
    - aralarında tesseract'ın bulup okuyamadığı (hiç eşleşen kelimesi olmayan) satırlar var: o satırlara;
    - N'nin satırı P'ninkinden satır aralığının MISSING_ROW katından aşağıda: aradaki sentetik satır(lar)a;
    - blok kutusunun altında/üstünde satır sığacak yer var ve grup P'nin satırına sığmıyor: tesseract bloğun son/ilk
      satırını kaçırmış, kutunun kenarına sentetik satır(lar);
    - yoksa P'nin satırında ondan sonra (blok kenarına kadar; tahmini genişlikte) ya da N'nin satırında ondan önce.
    """
    text = block.text
    spans = [m.group() for m in _WORD.finditer(text)]
    if not spans or not any(tl.words for tl in tlines):
        return None
    flat, rtl = _flat_words(tlines)
    owners = _owners(text, flat)
    _rematch(spans, owners, flat)
    if not any(owners):
        return None
    if _boxed(block) and sum(o is not None for o in owners) < MIN_MATCHED * len(owners):
        return None     # satır kutuları kesin, kelime eşleşmesi zayıf: satır satır yerleşim daha güvenilir
    rows = {li: tl.box for li, tl in enumerate(tlines)}
    placed = _matched(spans, owners, flat, rows)
    matched_rows = {w.row for w in placed if w is not None}
    widths = [(box[2] - box[0]) / len(w) for _, box, w in flat if box[2] > box[0]]
    cw = statistics.median(widths) if widths else lh / 2   # tesseract'ın ortalama harf genişliği
    left, right = (block.bbox[0], block.bbox[2]) if block.bbox else (0, page.width)
    synthetic = 0

    def on_row(group, row, a, b, is_rtl, lead, trail, how):
        _, y1, _, y2 = rows[row]
        return [Word(w, (x1, y1, x2, y2), row, how)
                for w, (x1, x2) in zip(group, _lay(group, a, b, is_rtl, lead, trail))]

    def new_rows(group, tops, x1, x2, is_rtl, natural=False):
        """Sentetik satırlar; natural: son satır kendi tahmini genişliğinde (paragraf sonu kısa olabilir)."""
        nonlocal synthetic
        out = []
        for top, piece in zip(tops, _split_words(" ".join(group), len(tops))):
            ws = piece.split()
            synthetic += 1
            a, b = _fit(x1, x2, (len(piece) + 1) * cw, is_rtl, x1, x2) if natural and top == tops[-1] else (x1, x2)
            out += [Word(w, (wa, top, wb, top + lh), ("sentetik", synthetic), "sentetik")
                    for w, (wa, wb) in zip(ws, _lay(ws, a, b, is_rtl, False, False))]
        return out

    def unread_rows(group, free):
        """Tesseract'ın bulup kelimelerini okuyamadığı satırlar: grup bu satırlara bölünür."""
        out = []
        for li, piece in zip(free, _split_words(" ".join(group), len(free))):
            ws = piece.split()
            a, b = _fit(rows[li][0], rows[li][2], (len(piece) + 1) * cw, rtl[li], left, right)
            out += on_row(ws, li, a, b, rtl[li], False, False, "okunamayan")
        return out

    def beside(group, ref, after, room, need):
        """Grubu ref'in satırında ondan sonra (after) ya da önce, satırın yönüne göre dizer."""
        is_rtl = rtl[ref.row]
        rightward = after != is_rtl
        width = max(min(need, room), MIN_GAP)   # blok kutusunun içinde kalır; dar olabilir (Tz sıkıştırır)
        x1, y1, x2, y2 = ref.box
        if room < MIN_GAP:                      # komşu blok kenarına dayalı: kırpılarak kenarın içinde yer açılır
            if rightward:
                x2 = max(x1 + 1, right - MIN_GAP)
            else:
                x1 = min(x2 - 1, left + MIN_GAP)
            ref.box = (x1, y1, x2, y2)
        if rightward:
            return on_row(group, ref.row, x2, x2 + width, is_rtl, after, not after, "kenar")
        return on_row(group, ref.row, x1 - width, x1, is_rtl, after, not after, "kenar")

    i = 0
    while i < len(placed):
        if placed[i] is not None:
            i += 1
            continue
        j = i
        while j < len(placed) and placed[j] is None:
            j += 1
        group = spans[i:j]
        prev = placed[i - 1] if i else None
        nxt = placed[j] if j < len(placed) else None
        need = (sum(len(w) for w in group) + len(group)) * cw
        room_after = room_before = 0.0
        if prev:
            room_after = prev.box[0] - left if rtl[prev.row] else right - prev.box[2]
        if nxt:
            room_before = right - nxt.box[2] if rtl[nxt.row] else nxt.box[0] - left
        rows_below = round((block.bbox[3] - rows[prev.row][3]) / pitch) if block.bbox and prev and not nxt else 0
        rows_above = round((rows[nxt.row][1] - block.bbox[1]) / pitch) if block.bbox and nxt and not prev else 0
        first_row = prev.row + 1 if prev else 0
        free = [li for li in range(first_row, nxt.row if nxt else len(rows)) if li not in matched_rows]
        if prev and nxt and prev.row == nxt.row:                                   # satır içindeki boşluk
            # grup dar da olsa komşuların arasında kalır (Tz sıkıştırır): komşu kutuya taşan metin pdfium'da
            # kopya sırasını bozar. Boşluk yoksa komşulardan biri kırpılarak yer açılır.
            is_rtl = rtl[prev.row]
            a, b = (nxt.box[2], prev.box[0]) if is_rtl else (prev.box[2], nxt.box[0])
            if b - a < MIN_GAP:
                left_word, right_word = (nxt, prev) if is_rtl else (prev, nxt)
                if left_word.box[2] - left_word.box[0] >= right_word.box[2] - right_word.box[0]:
                    a = b - MIN_GAP
                    left_word.box = (left_word.box[0], left_word.box[1], min(left_word.box[2], a), left_word.box[3])
                else:
                    b = a + MIN_GAP
                    right_word.box = (max(right_word.box[0], b), *right_word.box[1:])
            new = on_row(group, prev.row, a, b, is_rtl, True, True, "ara")
        elif free and (bool(prev and nxt) or (room_after if prev else room_before) < need):   # okunamayan satır
            new = unread_rows(group, free)
        elif prev and nxt and rows[nxt.row][1] - rows[prev.row][1] > MISSING_ROW * pitch:   # satır kaçmış
            p_top, n_top = rows[prev.row][1], rows[nxt.row][1]
            k = max(1, round((n_top - p_top) / pitch) - 1)
            x1, x2 = block.bbox[0::2] if block.bbox else (min(rows[prev.row][0], rows[nxt.row][0]),
                                                          max(rows[prev.row][2], rows[nxt.row][2]))
            new = new_rows(group, [p_top + (n_top - p_top) * (r + 1) / (k + 1) for r in range(k)], x1, x2,
                           rtl[prev.row])
        elif rows_below >= 1 and room_after < need:                                # bloğun son satırı kaçmış
            bottom = block.bbox[3] - lh
            new = new_rows(group, [bottom - pitch * (rows_below - 1 - r) for r in range(rows_below)],
                           *block.bbox[0::2], rtl[prev.row], natural=True)
        elif rows_above >= 1 and room_before < need:                               # bloğun ilk satırı kaçmış
            new = new_rows(group, [block.bbox[1] + pitch * r for r in range(rows_above)], *block.bbox[0::2],
                           rtl[nxt.row])
        elif prev and (not nxt or room_after >= min(need, room_before)):           # satır sonu
            new = beside(group, prev, True, room_after, need)
        else:                                                                      # satır başı
            new = beside(group, nxt, False, room_before, need)
        size = (page.width, page.height) * 2
        placed[i:j] = [Word(w.text, tuple(min(max(v, 0.0), lim) for v, lim in zip(w.box, size)), w.row, w.how)
                       for w in new]
        i += len(new)
    return placed


def _word_lines(index: int, words: list[Word]) -> list[LayerLine]:
    """Her kelime bir LayerLine; satırın son kelimesi değilse sonuna boşluk eklenir ve kutusu sonraki kelimeye uzar.

    Yazıcı sağdan sola kelimeyi kutusunun sağından sola doğru dizer; sondaki boşluk böylece kelimenin solunda kalır.
    Kelimenin yönü sonraki kelimenin tarafına uymuyorsa (Türkçe satırdaki Arapça kelime ya da tersi) boşluk kelimeye
    eklenmez, iki kelimenin arasına ayrı bir parça olarak yazılır; yoksa kopyada komşu kelimeye yapışırdı."""
    out = []
    for k, w in enumerate(words):
        nxt = words[k + 1] if k + 1 < len(words) and words[k + 1].row == w.row else None
        text, (x1, y1, x2, y2) = w.text, w.box
        x2 = max(x2, x1 + 1)                          # sıfır genişlikli kutu yazıcıda atlanırdı
        space = None
        if nxt is not None:
            right = nxt.box[0] + nxt.box[2] >= x1 + x2   # sonraki kelime sağda
            if right != _rtl_text(text):
                text += " "
                if right:
                    x2 = max(x2, nxt.box[0])
                else:
                    x1 = min(x1, nxt.box[2])
            else:
                a, b = (x2, nxt.box[0]) if right else (nxt.box[2], x1)
                space = (a, max(b, a + 1))
        out.append(LayerLine(text, (float(x1), float(y1), float(x2), float(y2)), index))
        if space:
            out.append(LayerLine(" ", (float(space[0]), float(y1), float(space[1]), float(y2)), index))
    return out


def layer_lines(page: Page) -> list[LayerLine]:
    """Sayfadaki metin parçaları (kelimeler ya da satırlar), okuma sırasıyla. Okunamayan sayfada, Picture'da ve boş
    metinli blokta parça yoktur."""
    if page.failed:
        return []
    assigned = assigned_lines(page)
    lh, pitch = _line_metrics(page, assigned)
    out: list[LayerLine] = []
    for i, block in enumerate(page.blocks):
        if block.category == "Picture" or not plain_text(block).strip():
            continue
        words = place_words(block, assigned[i], page, lh, pitch) if block.category not in NO_WORDS else None
        if words:
            out += _word_lines(i, words)
        elif _boxed(block):
            out += _boxed_lines(i, block)
        else:
            out += _unboxed_lines(i, block, page, lh, pitch)
    return out
