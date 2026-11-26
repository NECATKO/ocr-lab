"""tesseract TSV çıktısından satırlar: satır kutusu (level 4) ve kelimeleri (level 5)."""
import csv
import re
from dataclasses import dataclass, field

Box = tuple[int, int, int, int]


@dataclass
class TessLine:
    box: Box  # (x1, y1, x2, y2) piksel
    text: str
    # kelimeler okuma sırasıyla: (kutu, metin); text bunların boşlukla birleşimi. Eşitliğe katılmaz.
    words: list[tuple[Box, str]] = field(default_factory=list, compare=False)


def parse_tsv(tsv: str) -> list[TessLine]:
    """TSV'deki satırları sırasıyla döndürür; kelimesi olmayan satırlar atlanır."""
    boxes: dict[tuple, Box] = {}
    words: dict[tuple, list[tuple[Box, str]]] = {}
    for r in csv.DictReader(tsv.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        key = (r["page_num"], r["block_num"], r["par_num"], r["line_num"])
        if r["level"] not in ("4", "5"):
            continue
        x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
        if r["level"] == "4":
            boxes[key] = (x, y, x + w, y + h)
            words[key] = []
        elif key in words:
            word = (r.get("text") or "").strip()
            if word:
                words[key].append(((x, y, x + w, y + h), word))
    return [TessLine(boxes[k], " ".join(t for _, t in w), w) for k, w in words.items() if w]


def lines_in(block_bbox, lines: list[TessLine], pad: int = 15) -> list[TessLine]:
    """Merkezi blok kutusunun (pad kadar genişletilmiş) içine düşen satırlar; kutu yoksa (düz mod) bütün satırlar."""
    if block_bbox is None:
        return list(lines)
    bx1, by1, bx2, by2 = block_bbox
    out = []
    for line in lines:
        x1, y1, x2, y2 = line.box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if bx1 - pad <= cx <= bx2 + pad and by1 - pad <= cy <= by2 + pad:
            out.append(line)
    return out


def assign_lines(blocks, lines: list[TessLine], *, categories: frozenset[str] | None = None) -> list[list[TessLine]]:
    """Her tesseract satırını kutusu onu kapsayan en uygun bloğa (yalnız bir bloğa) atar.

    Küçük puntolu ardışık dipnot bloklarında komşu satırın içeri taşmasını önler; yatay/dikey
    paylar satır yüksekliğine ölçeklenir (DPI bağımsız). Kutusuz bloklar (düz mod) bütün satırları alır.
    """
    out: list[list[TessLine]] = [[] for _ in blocks]
    boxed: list[tuple[int, tuple[int, int, int, int]]] = []
    for idx, b in enumerate(blocks):
        if not b.text or (categories is not None and b.category not in categories):
            continue
        if b.bbox is None:
            out[idx] = list(lines)
        else:
            boxed.append((idx, b.bbox))
    if not boxed:
        return out
    for line in lines:
        x1, y1, x2, y2 = line.box
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        lh = max(1, y2 - y1)
        xpad = max(15, round(0.75 * lh))
        ypad = max(4, round(0.35 * lh))
        inside = [(idx, box) for idx, box in boxed
                  if box[0] - xpad <= cx <= box[2] + xpad and box[1] <= cy <= box[3]]
        if inside:
            best = max(inside, key=lambda it: (min(y2, it[1][3]) - max(y1, it[1][1]),
                                               -abs(cy - (it[1][1] + it[1][3]) / 2)))[0]
            out[best].append(line)
            continue
        near = [(idx, box) for idx, box in boxed
                if box[0] - xpad <= cx <= box[2] + xpad and box[1] - ypad <= cy <= box[3] + ypad]
        if near:
            best = min(near, key=lambda it: max(it[1][1] - cy, cy - it[1][3]))[0]
            out[best].append(line)
    return out


_WORD = re.compile(r"[^\W\d_]{3,}")          # anlamlı sözcük: en az 3 harf (gürültü ve tek harfler sayılmaz)
_TOKEN = re.compile(r"[^\W_]+")
_NOTE_MARK = re.compile(r"^\s*(\d{1,3}|[¹²³⁰-⁹]+|\*+)(?!\d)")   # dipnot numarası / yıldız


def _covered(line: TessLine, boxes: list[tuple[int, int, int, int]]) -> bool:
    x1, y1, x2, y2 = line.box
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    pad = max(15, round(0.75 * max(1, y2 - y1)))
    return any(b[0] - pad <= cx <= b[2] + pad and b[1] - pad <= cy <= b[3] + pad for b in boxes)


def uncovered_text(blocks, lines: list[TessLine], size: tuple[int, int]) -> list[TessLine]:
    """Hiçbir model bloğunun (her kategori: altlık, görsel dahil) kapsamadığı ve metni modelin çıktısında
    bulunmayan anlamlı tesseract satırları (olası eksik dipnot ya da atlanan satır).

    Uyarı için en az iki ardışık satır gerekir; tek satır yalnız sayfanın alt üçte birinde ve dipnot numarasıyla
    başlıyorsa sayılır. Gürültü (parmak, süs, tek harfler) ve yalnız sayfa numarası anlamlı sözcük içermez.
    Kutusuz (düz) modda konum bilinmediği için boş döner.
    """
    if any(b.bbox is None and b.text for b in blocks):
        return []
    boxes = [b.bbox for b in blocks if b.bbox is not None]
    known = {t.casefold() for b in blocks for t in _TOKEN.findall(b.text or "")}
    adaylar = []
    for line in lines:
        if _covered(line, boxes) or len(_WORD.findall(line.text)) < 2:
            continue
        tokens = [t.casefold() for t in _TOKEN.findall(line.text)]
        if sum(t in known for t in tokens) >= len(tokens) / 2:     # model bu metni zaten okumuş
            continue
        adaylar.append(line)
    adaylar.sort(key=lambda l: l.box[1])
    gruplar: list[list[TessLine]] = []
    for line in adaylar:
        if gruplar:
            onceki = gruplar[-1][-1]
            if line.box[1] - onceki.box[3] <= 2 * max(1, onceki.box[3] - onceki.box[1]):
                gruplar[-1].append(line)
                continue
        gruplar.append([line])
    sonuc = []
    for grup in gruplar:
        tek = grup[0]
        alt = (tek.box[1] + tek.box[3]) / 2 > size[1] * 2 / 3
        if len(grup) >= 2 or (alt and _NOTE_MARK.match(tek.text)):
            sonuc += grup
    return sonuc
