"""Aranabilir PDF'in görünmez metin katmanı: sayfayı, görüntü üzerine konacak metin satırlarına çevirir (PDF koduna dokunmaz)."""
import statistics
from dataclasses import dataclass

from ocrnow.export_txt import plain_text
from ocrnow.layout import Block, Page

PITCH_PER_HEIGHT = 1.5      # sayfada ardışık satır yoksa satır aralığı ≈ satır kutusu yüksekliği × bu
MAX_GAP = 3                 # bundan (× satır yüksekliği) büyük üst-üste aralık satır aralığı sayılmaz (sütun/boşluk)
PAGE_MARGIN = 0.05          # blok kutusu da yoksa sayfanın her kenarından bırakılan pay


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


def _line_metrics(page: Page) -> tuple[float, float]:
    """Sayfanın kutulu satırlarından (satır yüksekliği, satır aralığı) tahmini, piksel.

    Aralık: aynı bloktaki ardışık satır üstleri arasındaki farkların ortancası. Ardışık satır yoksa yükseklik × 1,5;
    hiç kutu yoksa ikisi de sayfa yüksekliği / 50."""
    heights, gaps = [], []
    for b in page.blocks:
        tops = []
        for ln in b.lines:
            if ln.box is None or ln.box[3] <= ln.box[1]:
                continue
            heights.append(ln.box[3] - ln.box[1])
            tops.append(ln.box[1])
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


def layer_lines(page: Page) -> list[LayerLine]:
    """Sayfadaki metin satırları, okuma sırasıyla. Okunamayan sayfada, Picture'da ve boş metinli blokta satır yoktur."""
    if page.failed:
        return []
    lh, pitch = _line_metrics(page)
    out: list[LayerLine] = []
    for i, block in enumerate(page.blocks):
        if block.category == "Picture" or not plain_text(block).strip():
            continue
        out += _boxed_lines(i, block) if _boxed(block) else _unboxed_lines(i, block, page, lh, pitch)
    return out
