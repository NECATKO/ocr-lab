"""Aranabilir PDF'in görünmez metin katmanı: sayfayı, görüntü üzerine konacak metin satırlarına çevirir (PDF koduna dokunmaz)."""
import math
import statistics
from dataclasses import dataclass

from ocrnow.export_txt import plain_text
from ocrnow.layout import Block, Page

FALLBACK_CHAR_WIDTH = 0.5   # kutusuz blokta ortalama harf genişliği / satır yüksekliği
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


def _median_line_height(page: Page) -> float:
    heights = [ln.box[3] - ln.box[1] for b in page.blocks for ln in b.lines if ln.box is not None]
    heights = [h for h in heights if h > 0]
    return statistics.median(heights) if heights else page.height / 50


def _split_words(text: str, k: int) -> list[str]:
    """Metni kelime sınırlarından yaklaşık eşit uzunlukta (en çok) k satıra böler."""
    words = text.split(" ")
    total = len(text)
    groups: list[list[str]] = [[] for _ in range(k)]
    pos = 0
    for w in words:
        groups[min(k - 1, int((pos + len(w) / 2) / total * k))].append(w)
        pos += len(w) + 1
    return [" ".join(g) for g in groups if g]


def _unboxed_lines(index: int, block: Block, page: Page, lh: float) -> list[LayerLine]:
    text = plain_text(block).strip()
    if not text:
        return []
    x1, y1, x2, y2 = block.bbox or (page.width * PAGE_MARGIN, page.height * PAGE_MARGIN,
                                    page.width * (1 - PAGE_MARGIN), page.height * (1 - PAGE_MARGIN))
    width, height = max(x2 - x1, 1), max(y2 - y1, 1)
    k = max(1, math.ceil(len(text) * FALLBACK_CHAR_WIDTH * lh / width))
    k = min(k, max(1, math.floor(height / lh)))
    out = []
    for i, piece in enumerate(_split_words(text, k)):
        top = y1 + i * lh
        out.append(LayerLine(piece, (float(x1), float(top), float(x2), float(min(top + lh, y2))), index))
    return out


def layer_lines(page: Page) -> list[LayerLine]:
    """Sayfadaki metin satırları, okuma sırasıyla. Okunamayan sayfada, Picture'da ve boş metinli blokta satır yoktur."""
    if page.failed:
        return []
    lh = _median_line_height(page)
    out: list[LayerLine] = []
    for i, block in enumerate(page.blocks):
        if block.category == "Picture" or not plain_text(block).strip():
            continue
        out += _boxed_lines(i, block) if _boxed(block) else _unboxed_lines(i, block, page, lh)
    return out
