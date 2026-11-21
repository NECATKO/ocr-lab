"""Aranabilir PDF (.pdf) dışa aktarıcı: sayfa görüntüsü + görünmez Unicode metin katmanı (3 Tr).

Ekstra harici PDF kütüphanesi gerektirmez; Pillow ile sayfa görselini JPEG XObject olarak gömer,
ToUnicode CMap kullanan görünmez bir Type3 yazı tipiyle Türkçe/Arapça dahil tüm Unicode metni
PDF okuyucularda seçilebilir ve aranabilir kılar.
"""
import io
import re
from pathlib import Path

from PIL import Image

from ocrnow import pages as page_loader
from ocrnow import paths
from ocrnow.export_txt import FAILED_TEXT, plain_text, split_blocks
from ocrnow.layout import Page

_RTL_CHARS = re.compile(r"[\u0590-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")


def _visual_order(text: str) -> str:
    """PDF metin akışı soldan sağa çizildiği için RTL satırları görsel sıraya çevirir."""
    if _RTL_CHARS.search(text):
        return text[::-1]
    return text


def _utf16_hex(ch: str) -> str:
    return ch.encode("utf-16-be").hex().upper()


def _page_image_jpeg(page: Page) -> tuple[bytes, int, int]:
    """Sayfanın kaynak görselini JPEG baytlarına çevirir; kaynak yoksa beyaz yer tutucu üretir."""
    src = Path(page.source)
    img: Image.Image | None = None
    if src.is_file():
        try:
            rendered = page_loader.render(page_loader.SourcePage(src, page.index))
            with Image.open(rendered) as im:
                img = im.convert("RGB")
        except Exception:
            img = None
    if img is None:
        w, h = max(page.width or 595, 200), max(page.height or 842, 200)
        img = Image.new("RGB", (w, h), "white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue(), img.width, img.height


def _encode_unicode_stream(lines: list[tuple[str, float, float, float]], char_map: dict[str, int]) -> bytes:
    """(metin, x_pt, y_pt, font_pt) satırlarını görünmez (3 Tr) PDF metin komutlarına çevirir."""
    cmds = [b"BT", b"3 Tr"]
    for text, x, y, pt in lines:
        if not text:
            continue
        codes = []
        for ch in _visual_order(text):
            if ch not in char_map:
                if len(char_map) >= 65000:
                    continue
                char_map[ch] = len(char_map) + 1
            codes.append(f"{char_map[ch]:04X}")
        if not codes:
            continue
        cmds.append(f"/F0 {pt:.1f} Tf 1 0 0 1 {x:.1f} {y:.1f} Tm <{''.join(codes)}> Tj".encode("ascii"))
    cmds.append(b"ET")
    return b"\n".join(cmds)


def _tounicode_cmap(char_map: dict[str, int]) -> bytes:
    entries = [f"<{cid:04X}> <{_utf16_hex(ch)}>" for ch, cid in char_map.items()]
    chunks = []
    for i in range(0, len(entries), 100):
        batch = entries[i:i + 100]
        chunks.append(f"{len(batch)} beginbfchar\n" + "\n".join(batch) + "\nendbfchar")
    body = "\n".join(chunks)
    cmap = (
        "/CIDInit /ProcSet findresource begin\n"
        "12 dict begin\n"
        "begincmap\n"
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
        "/CMapName /Adobe-Identity-UCS def\n"
        "/CMapType 2 def\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{body}\n"
        "endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
    )
    return cmap.encode("ascii")


def write_pdf(pages: list[Page], out: Path) -> Path:
    """Her sayfa için görüntü + görünmez Unicode metin katmanlı aranabilir PDF yazar."""
    objects: list[bytes] = []

    def add_obj(data: bytes) -> int:
        objects.append(data)
        return len(objects)

    # 1: Catalog, 2: Pages, 3: Type0 Font, 4: CIDFont, 5: FontDescriptor, 6: ToUnicode CMap (yer tutucu)
    for _ in range(6):
        objects.append(b"")

    char_map: dict[str, int] = {}
    page_ids: list[int] = []

    for page in pages:
        jpeg, iw, ih = _page_image_jpeg(page)
        # 200 DPI varsayımıyla A4 ölçeği (~595 pt genişlik)
        pw = 595.0
        ph = pw * (ih / iw) if iw else 842.0
        sx = pw / (page.width or iw or 1)
        sy = ph / (page.height or ih or 1)

        img_id = add_obj(
            f"<< /Type /XObject /Subtype /Image /Width {iw} /Height {ih} "
            f"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)} >>\n"
            f"stream\n".encode("ascii") + jpeg + b"\nendstream"
        )

        _, body, footnotes = split_blocks(page)
        text_lines: list[tuple[str, float, float, float]] = []
        if page.failed:
            text_lines.append((FAILED_TEXT, 36.0, ph - 48.0, 10.0))
        else:
            cursor_y = ph - 48.0
            for block in body + footnotes:
                t = plain_text(block).strip()
                if not t:
                    continue
                if block.bbox and len(block.bbox) == 4:
                    x1, y1, _x2, y2 = block.bbox
                    x_pt = max(12.0, min(pw - 24.0, x1 * sx))
                    y_pt = max(12.0, min(ph - 12.0, ph - y2 * sy))
                    pt = max(6.0, min(14.0, (y2 - y1) * sy * 0.7))
                else:
                    x_pt, y_pt, pt = 36.0, max(18.0, cursor_y), 10.0
                    cursor_y -= 14.0
                text_lines.append((t, x_pt, y_pt, pt))

        draw_img = f"q {pw:.2f} 0 0 {ph:.2f} 0 0 cm /Im0 Do Q\n".encode("ascii")
        text_stream = _encode_unicode_stream(text_lines, char_map)
        content = draw_img + text_stream
        content_id = add_obj(
            f"<< /Length {len(content)} >>\nstream\n".encode("ascii") + content + b"\nendstream"
        )
        page_id = add_obj(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.2f} {ph:.2f}] "
            f"/Resources << /XObject << /Im0 {img_id} 0 R >> /Font << /F0 3 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode("ascii")
        )
        page_ids.append(page_id)

    cmap = _tounicode_cmap(char_map)
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")
    objects[2] = (
        b"<< /Type /Font /Subtype /Type0 /BaseFont /HiddenUnicode "
        b"/Encoding /Identity-H /DescendantFonts [4 0 R] /ToUnicode 6 0 R >>"
    )
    objects[3] = (
        b"<< /Type /Font /Subtype /CIDFontType2 /BaseFont /HiddenUnicode "
        b"/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
        b"/FontDescriptor 5 0 R /DW 500 >>"
    )
    objects[4] = (
        b"<< /Type /FontDescriptor /FontName /HiddenUnicode /Flags 4 "
        b"/FontBBox [0 -200 1000 900] /ItalicAngle 0 /Ascent 800 /Descent -200 /CapHeight 700 /StemV 80 >>"
    )
    objects[5] = f"<< /Length {len(cmap)} >>\nstream\n".encode("ascii") + cmap + b"\nendstream"

    buf = io.BytesIO()
    buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for idx, obj in enumerate(objects, 1):
        offsets.append(buf.tell())
        buf.write(f"{idx} 0 obj\n".encode("ascii"))
        buf.write(obj)
        buf.write(b"\nendobj\n")
    xref_pos = buf.tell()
    buf.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        buf.write(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode("ascii")
    )
    with paths.atomic_output(out) as tmp:
        tmp.write_bytes(buf.getvalue())
    return out
