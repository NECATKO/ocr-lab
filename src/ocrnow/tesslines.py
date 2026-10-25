"""tesseract TSV çıktısından satırlar: satır kutusu (level 4) ve kelimeleri (level 5)."""
import csv
from dataclasses import dataclass


@dataclass
class TessLine:
    box: tuple[int, int, int, int]  # (x1, y1, x2, y2) piksel
    text: str


def parse_tsv(tsv: str) -> list[TessLine]:
    """TSV'deki satırları sırasıyla döndürür; kelimesi olmayan satırlar atlanır."""
    boxes: dict[tuple, tuple[int, int, int, int]] = {}
    words: dict[tuple, list[str]] = {}
    for r in csv.DictReader(tsv.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        key = (r["page_num"], r["block_num"], r["par_num"], r["line_num"])
        if r["level"] == "4":
            x, y, w, h = (int(r[k]) for k in ("left", "top", "width", "height"))
            boxes[key] = (x, y, x + w, y + h)
            words[key] = []
        elif r["level"] == "5" and key in words:
            word = (r.get("text") or "").strip()
            if word:
                words[key].append(word)
    return [TessLine(boxes[k], " ".join(w)) for k, w in words.items() if w]


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
