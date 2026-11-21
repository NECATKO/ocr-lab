"""Ham metin (.txt) dışa aktarıcı: sayfa başına ayraç, blok başına tek satırlık paragraf, dipnotlar sayfa sonunda."""
import html
import re
from pathlib import Path

from ocrnow import paths
from ocrnow.layout import Block, Page

SKIPPED = frozenset({"Page-header", "Page-footer"})   # .txt'ye yazılmaz (numara ayraçta)
FAILED_TEXT = "[Bu sayfa okunamadı]"                 # okunamayan sayfanın yer tutucusu (docx ve txt)
REVIEW_TEXT = "[Kontrol gerekli: Bu sayfada metin bulunamadı]"
TRUNCATED_TEXT = "[Kontrol gerekli: Model çıktısı kesildi, sayfa sonu eksik olabilir]"
_TAG = re.compile(r"<[^>]+>")


def page_label(page: Page) -> str:
    """Kitap sayfa numarası; tahminse köşeli parantezli ([413], [PDF s. 3]); yoksa boş."""
    if page.book_page is None:
        return ""
    return f"[{page.book_page}]" if page.book_page_inferred else page.book_page


def plain_text(block: Block) -> str:
    """Bloğun yazılacak metni: Table'ın HTML'i etiketleri soyulmuş düz metne çevrilir, diğerleri olduğu gibi."""
    if block.category != "Table":
        return block.text
    return " ".join(html.unescape(_TAG.sub(" ", block.text)).split())


def review_notice(page: Page) -> str | None:
    """Eksik sayfa için ortak belge içi uyarı; okunamayan sayfanın kendi yer tutucusu var."""
    if not page.failed:
        if page.truncated:
            return TRUNCATED_TEXT
        if page.needs_review:
            return REVIEW_TEXT
    return None


def split_blocks(page: Page) -> tuple[list[Block], list[Block], list[Block]]:
    """(Page-header'lar, gövde blokları, dipnotlar); Page-footer atılır. Her grup kendi sırasını korur."""
    headers = [b for b in page.blocks if b.category == "Page-header"]
    footnotes = [b for b in page.blocks if b.category == "Footnote"]
    body = [b for b in page.blocks if b.category not in SKIPPED and b.category != "Footnote"]
    return headers, body, footnotes


def render_txt(pages: list[Page], *, page_breaks: bool = True) -> str:
    chunks = []
    for page in pages:
        label = page_label(page)
        _, body, footnotes = split_blocks(page)
        paragraphs = [FAILED_TEXT] if page.failed else [t for b in body + footnotes if (t := plain_text(b))]
        if notice := review_notice(page):
            paragraphs.insert(0, notice)
        if page_breaks:
            head = f"--- s. {label} ---" if label else "---"
            chunks.append("\n".join([head, "\n\n".join(paragraphs)]) if paragraphs else head)
        elif paragraphs:
            chunks.append("\n\n".join(paragraphs))
    return "\n\n".join(chunks) + ("\n" if chunks else "")


def write_txt(pages: list[Page], out: Path) -> Path:
    with paths.atomic_output(out) as tmp:
        tmp.write_text(render_txt(pages), encoding="utf-8")
    return out
