"""Markdown (.md) dışa aktarıcı: başlıklar, listeler, tablolar, formüller ve biçimlendirme korunur."""
import html
import re
from html.parser import HTMLParser
from pathlib import Path

from ocrnow import paths
from ocrnow.export_txt import FAILED_TEXT, page_label, plain_text, review_notice, split_blocks
from ocrnow.layout import Block, Page

_LIST_PREFIX = re.compile(r"^(?:[-*+•]|\d+[.)])\s+")


class _TableParser(HTMLParser):
    """Basit HTML <table> çözücü: satır ve hücre metinlerini çıkarır."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        t = tag.lower()
        if t == "tr":
            self._row = []
        elif t in ("td", "th") and self._row is not None:
            self._cell = []
        elif t == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self._row is not None and self._cell is not None:
            text = " ".join("".join(self._cell).split()).replace("|", r"\|")
            self._row.append(text)
            self._cell = None
        elif t == "tr" and self._row is not None:
            if any(c for c in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(html.unescape(data))


def _table_to_md(raw_html: str) -> str:
    parser = _TableParser()
    parser.feed(raw_html)
    parser.close()
    if not parser.rows:
        return plain_text(Block("Table", None, raw_html))
    cols = max(len(r) for r in parser.rows)
    padded = [r + [""] * (cols - len(r)) for r in parser.rows]
    lines = [
        "| " + " | ".join(padded[0]) + " |",
        "| " + " | ".join("---" for _ in range(cols)) + " |",
        *("| " + " | ".join(r) + " |" for r in padded[1:]),
    ]
    return "\n".join(lines)


def _format_inline(block: Block) -> str:
    """Block içindeki kalin/italik/ust işaretlerini Markdown biçimine geri yazar."""
    text = block.text
    marks = [m for m in block.marks if m.kind in {"kalin", "italik", "ust"} and 0 <= m.start < m.end <= len(text)]
    if not marks:
        return text
    # Dıştan içe çakışma olmadan sarmalamak için başlangıç/bitiş noktalarına göre böl
    cuts = sorted({0, len(text), *(x for m in marks for x in (m.start, m.end))})
    out: list[str] = []
    for a, b in zip(cuts, cuts[1:]):
        seg = text[a:b]
        active = {m.kind for m in marks if m.start <= a and m.end >= b}
        if "ust" in active:
            seg = f"<sup>{seg}</sup>"
        if "kalin" in active and "italik" in active:
            seg = f"***{seg}***"
        elif "kalin" in active:
            seg = f"**{seg}**"
        elif "italik" in active:
            seg = f"*{seg}*"
        out.append(seg)
    return "".join(out)


def _render_block(block: Block) -> str:
    if block.category == "Table":
        return _table_to_md(block.text)
    if block.category == "Formula":
        t = block.text.strip()
        if not t:
            return ""
        if t.startswith(("$", r"\[", r"\(")):
            return t
        return f"$$\n{t}\n$$"
    text = _format_inline(block).strip()
    if not text:
        return ""
    if block.category == "Title":
        return f"# {text}"
    if block.category == "Section-header":
        return f"## {text}"
    if block.category == "List-item":
        return text if _LIST_PREFIX.match(text) else f"- {text}"
    if block.category == "Caption":
        return f"*{text}*"
    return text


def render_md(pages: list[Page]) -> str:
    """Sayfaları tek bir Markdown metnine çevirir."""
    multi = len(pages) > 1
    chunks: list[str] = []
    for page in pages:
        label = page_label(page)
        _, body, footnotes = split_blocks(page)
        parts: list[str] = []
        if notice := review_notice(page):
            parts.append(f"> {notice}")
        if page.failed:
            parts.append(f"> {FAILED_TEXT}")
        else:
            for b in body:
                if rendered := _render_block(b):
                    parts.append(rendered)
            fn_lines = [f"> {t}" for b in footnotes if (t := _render_block(b))]
            if fn_lines:
                parts.append("\n>\n".join(fn_lines))
        if label:
            parts.insert(0, f"<!-- s. {label} -->")
        if parts:
            chunks.append("\n\n".join(parts))
        elif multi:
            chunks.append(f"<!-- s. {label} -->" if label else "")
    sep = "\n\n---\n\n" if multi else "\n\n"
    return sep.join(chunks) + ("\n" if chunks else "")


def write_md(pages: list[Page], out: Path) -> Path:
    with paths.atomic_output(out) as tmp:
        tmp.write_text(render_md(pages), encoding="utf-8")
    return out
