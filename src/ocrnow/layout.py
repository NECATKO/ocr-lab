"""Sayfa veri modeli (Page/Block/Line/Mark), JSON'a yazma/okuma ve model çıktısını bloklara çevirme."""
import json
import math
import re
from dataclasses import asdict, dataclass, field

PLAIN_WARNING = "düz metin modu: başlık, dipnot ve sayfa numarası ayrılamadı"
PICTURE_TEXT = "[Görsel]"


@dataclass
class Mark:
    start: int
    end: int
    kind: str  # "belirsiz" | "kesme"; Block.text içindeki karakter aralığı


@dataclass
class Line:
    start: int
    end: int
    hyphen: bool  # satır sonuna tire EKLENECEK (metinde yok)


@dataclass
class Block:
    category: str
    bbox: tuple[int, int, int, int] | None
    text: str  # boşlukları sadeleştirilmiş tek satır
    lines: list[Line] = field(default_factory=list)
    indent: bool = False
    marks: list[Mark] = field(default_factory=list)


@dataclass
class Page:
    source: str
    index: int
    width: int
    height: int
    engine: str
    mode: str
    blocks: list[Block]
    tess_tsv: str = ""
    book_page: str | None = None
    book_page_inferred: bool = False
    book_page_side: str | None = None  # "sol" | "sag"
    warnings: list[str] = field(default_factory=list)


def page_to_json(p: Page) -> str:
    return json.dumps(asdict(p), ensure_ascii=False)


def page_from_json(s: str) -> Page:
    d = json.loads(s)
    blocks = []
    for b in d.pop("blocks"):
        bbox = b.pop("bbox")
        lines = [Line(**x) for x in b.pop("lines")]
        marks = [Mark(**x) for x in b.pop("marks")]
        blocks.append(Block(bbox=tuple(bbox) if bbox is not None else None, lines=lines, marks=marks, **b))
    return Page(blocks=blocks, **d)


def smart_resize(h: int, w: int, factor: int = 28, min_pixels: int = 3136,
                 max_pixels: int = 4_000_000) -> tuple[int, int]:
    """Qwen2-VL'nin görüntü yeniden boyutlandırması: modelin gerçekte gördüğü (yükseklik, genişlik)."""
    h_bar, w_bar = round(h / factor) * factor, round(w / factor) * factor
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt(h * w / max_pixels)
        h_bar, w_bar = math.floor(h / beta / factor) * factor, math.floor(w / beta / factor) * factor
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (h * w))
        h_bar, w_bar = math.ceil(h * beta / factor) * factor, math.ceil(w * beta / factor) * factor
    return h_bar, w_bar


def _norm(text: object) -> str:
    """Boşlukları sadeleştirir; metin listesi boşlukla birleştirilir, başka türler boş sayılır."""
    if isinstance(text, list):
        text = " ".join(t for t in text if isinstance(t, str))
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    s = re.sub(r"^```[A-Za-z]*\s*", "", s)
    return re.sub(r"\s*```$", "", s).strip()


def _scale_bbox(bbox: object, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """Model bbox'ını (smart_resize görüntüsünde) orijinal piksellere ölçekler; geçersizse None."""
    if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                       for v in bbox)):
        return None
    w, h = size
    if w <= 0 or h <= 0:
        return None
    rh, rw = smart_resize(h, w)
    x1, y1, x2, y2 = bbox
    return (round(x1 * w / rw), round(y1 * h / rh), round(x2 * w / rw), round(y2 * h / rh))


def _block_from_item(item: object, size: tuple[int, int]) -> tuple[Block | None, bool]:
    """(blok, bozuk_mu): nesne değilse blok yok; kategori ya da bbox eksik/geçersizse Text/None ile tamamlanır."""
    if not isinstance(item, dict):
        return None, True
    bbox = _scale_bbox(item.get("bbox"), size)
    category = item.get("category")
    bad_category = not isinstance(category, str) or not category
    if bad_category:
        category = "Text"
    bad = bbox is None or bad_category
    text = PICTURE_TEXT if category == "Picture" else _norm(item.get("text"))
    return Block(category, bbox, text), bad


_TEXT_KEY = re.compile(r'"text"\s*:\s*"')
_START = re.compile(r'\[\s*(?=\{)|\{\s*(?="(?:bbox|category|text)")')


def _partial_string(s: str, quote: int) -> str:
    """s[quote] açılış tırnağıdır; (kesik de olsa) JSON dizesinin içeriğini kaçışlarıyla çözer, hiçbir karakteri atmaz."""
    try:
        return json.JSONDecoder(strict=False).raw_decode(s, quote)[0]
    except ValueError:
        pass
    body = s[quote + 1:]
    body = re.sub(r"\\u[0-9a-fA-F]{0,3}$", "", body)           # yarım \uXXXX
    if (len(body) - len(body.rstrip("\\"))) % 2:                # yarım kalmış kaçış
        body = body[:-1]
    try:
        return json.loads('"' + body + '"', strict=False)
    except ValueError:
        return body


def _fragment_text(frag: str) -> str:
    """Kesik kalan nesne parçasından yalnız "text" değerini alır; değer yoksa boş (JSON kalıntısı belgeye girmez)."""
    m = _TEXT_KEY.search(frag)
    return _norm(_partial_string(frag, m.end() - 1)) if m else ""


def _recover(s: str) -> tuple[str, list[object], str]:
    """Geçersiz/kesik/önekli JSON'dan (ön metin, tamamlanmış nesneler, sondaki metin) çıkarır.

    Dizinin dışında kalan metin (önek, kapanıştan sonraki söz) ve kesik son nesnenin "text" değeri
    sondaki metne gider; JSON bulunamazsa bütün cevap ön metin olur.
    """
    m = _START.search(s)
    if not m:
        return s, [], ""
    prefix, pos = s[:m.start()], m.end() if s[m.start()] == "[" else m.start()
    dec = json.JSONDecoder()
    items: list[object] = []
    while True:
        while pos < len(s) and (s[pos].isspace() or s[pos] == ","):
            pos += 1
        if pos >= len(s):
            return prefix, items, ""
        if s[pos] == "]":
            return prefix, items, s[pos + 1:]
        try:
            obj, pos = dec.raw_decode(s, pos)
        except ValueError:
            if s[pos] == "{":
                return prefix, items, _fragment_text(s[pos:])
            return prefix, items, _partial_string(s, pos) if s[pos] == '"' else s[pos:]
        items.append(obj)


def parse_output(raw: str, layout: bool, size: tuple[int, int]) -> tuple[list[Block], list[str]]:
    """Ham model cevabını bloklara çevirir. size=(genişlik, yükseklik) orijinal görüntünün pikselidir."""
    if not layout:
        return [Block("Text", None, _norm(raw))], [PLAIN_WARNING]
    s = _strip_fences(raw)
    if not s:
        return [], ["model boş yanıt verdi"]
    warnings: list[str] = []
    head = tail = ""
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise ValueError("liste değil")
        items = data
    except ValueError:
        head, items, tail = _recover(s)
        recovered = sum(isinstance(i, dict) for i in items)
        warnings.append(f"model çıktısı eksik/bozuk JSON: {recovered} blok kurtarıldı")
    blocks: list[Block] = []
    if _norm(head):
        blocks.append(Block("Text", None, _norm(head)))
    bad_count = 0
    for item in items:
        block, bad = _block_from_item(item, size)
        bad_count += bad
        if block is not None:
            blocks.append(block)
    if _norm(tail):
        blocks.append(Block("Text", None, _norm(tail)))
    if bad_count:
        warnings.append(f"model çıktısında {bad_count} bozuk öğe: eksik alanlar varsayılanla dolduruldu, nesne olmayanlar atlandı")
    return blocks, warnings
