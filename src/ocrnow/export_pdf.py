"""Aranabilir PDF (.pdf): orijinal sayfa olduğu gibi kalır, üstüne satır satır görünmez Unicode metin katmanı eklenir.

Kaynak PDF sayfası pikepdf ile kopyalanır; içerik akışlarına ve görüntülerine dokunulmaz, /Contents'e yalnız yeni
akışlar eklenir (başa `q`, sona `Q` + katman). Görsel kaynaktan yeni sayfa kurulur (JPEG yeniden kodlanmaz).
Katman glifsiz bir Type0/Identity-H fonttur: ToUnicode CMap sayesinde Türkçe/Arapça metin seçilir, aranır, kopyalanır;
`3 Tr` ile görünmez. Satır yerleşimi `pdf_textlayer.layer_lines`'tan gelir.
"""
import unicodedata
import zlib
from contextlib import ExitStack
from pathlib import Path

import pikepdf
import pypdfium2 as pdfium
from pikepdf import Array, Dictionary, Name
from PIL import Image

from ocrnow import pages as page_loader
from ocrnow import paths
from ocrnow.layout import Page
from ocrnow.pdf_textlayer import LayerLine, layer_lines

GLYPH_WIDTH = 500          # /DW: her glif yarım em; Tz bu sabite göre satır genişliğine uydurulur
LINE_HEIGHT = 1.1          # satır kutusu yüksekliği / font boyu
BASELINE = 0.2             # taban çizgisinin kutu altından yüksekliği (font boyu cinsinden)
TEXT_MIN_CHARS = 10        # kaynak sayfada bu kadar boşluk dışı karakter varsa sayfa zaten metinli sayılır
MAX_CIDS = 65000
FONT_PREFIX = "OcrNowF"
A4_PT = (595.0, 842.0)     # boyutu bilinmeyen yer tutucu sayfa

# /Rotate → görünen alandan (y yukarı) sayfa uzayına cm; cx0 cy0 cx1 cy1 = etkin CropBox
_ROTATE_CM = {
    0: lambda cx0, cy0, cx1, cy1: (1, 0, 0, 1, cx0, cy0),
    90: lambda cx0, cy0, cx1, cy1: (0, 1, -1, 0, cx1, cy0),
    180: lambda cx0, cy0, cx1, cy1: (-1, 0, 0, -1, cx1, cy1),
    270: lambda cx0, cy0, cx1, cy1: (0, -1, 1, 0, cx0, cy1),
}


def _num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _rtl(text: str) -> bool:
    """Satırın yönü ilk güçlü karakterinden (Unicode bidi P2): Arapça/İbranice ise sağdan sola."""
    for ch in text:
        kind = unicodedata.bidirectional(ch)
        if kind in ("R", "AL"):
            return True
        if kind == "L":
            return False
    return False


def _flate(pdf: pikepdf.Pdf, data: bytes) -> pikepdf.Stream:
    """Kendi akışlarımız sıkıştırılır; kayıt kaynak akışlara dokunmasın diye pikepdf'e sıkıştırma yaptırılmaz."""
    return pdf.make_stream(zlib.compress(data), Filter=Name.FlateDecode)


def _utf16_hex(text: str) -> str:
    return text.encode("utf-16-be").hex().upper()


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


class _HiddenFont:
    """Belge geneli tek glifsiz font; CID ataması bütün sayfalarda ortak, ToUnicode kayıtta doldurulur."""

    def __init__(self, pdf: pikepdf.Pdf):
        self.char_map: dict[str, int] = {}
        self.cmap = pdf.make_stream(b"")
        descriptor = pdf.make_indirect(Dictionary(
            Type=Name.FontDescriptor, FontName=Name.HiddenUnicode, Flags=4,
            FontBBox=Array([0, -200, GLYPH_WIDTH, 800]), ItalicAngle=0, Ascent=800, Descent=-200,
            CapHeight=700, StemV=80))
        cid_font = pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.CIDFontType2, BaseFont=Name.HiddenUnicode,
            CIDSystemInfo=Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"),
                                     Supplement=0),
            FontDescriptor=descriptor, DW=GLYPH_WIDTH))
        self.font = pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.Type0, BaseFont=Name.HiddenUnicode, Encoding=Name("/Identity-H"),
            DescendantFonts=Array([cid_font]), ToUnicode=self.cmap))

    def encode(self, text: str) -> str:
        codes = []
        for ch in text:
            if ch not in self.char_map:
                if len(self.char_map) >= MAX_CIDS:
                    continue
                self.char_map[ch] = len(self.char_map) + 1
            codes.append(f"{self.char_map[ch]:04X}")
        return "".join(codes)

    def finish(self) -> None:
        self.cmap.write(zlib.compress(_tounicode_cmap(self.char_map)), filter=Name.FlateDecode)

    def attach(self, page_obj: pikepdf.Dictionary) -> Name:
        """Fontu sayfanın /Resources /Font'una, var olan adlarla çakışmayan bir adla ekler."""
        if "/Resources" not in page_obj:
            page_obj.Resources = Dictionary()
        res = page_obj.Resources
        if "/Font" not in res:
            res.Font = Dictionary()
        fonts = res.Font
        for key, value in fonts.items():
            if value.is_indirect and value.objgen == self.font.objgen:
                return Name(key)    # aynı kaynak sözlüğünü paylaşan önceki sayfada eklendi
        i = 0
        while f"/{FONT_PREFIX}{i}" in fonts:
            i += 1
        name = Name(f"/{FONT_PREFIX}{i}")
        fonts[name] = self.font
        return name


def _rect(box) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def _view(page_obj: pikepdf.Dictionary) -> tuple[tuple[float, float, float, float], int]:
    """Etkin CropBox (MediaBox'la kesişimi; pdfium da böyle okur) ve 0/90/180/270'e indirgenmiş /Rotate."""
    media = _rect(page_obj.MediaBox)
    crop = _rect(page_obj.CropBox) if "/CropBox" in page_obj else media
    box = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
    if box[2] <= box[0] or box[3] <= box[1]:
        box = media
    rotate = int(page_obj.get("/Rotate", 0))
    return box, int(rotate / 90) % 4 * 90     # pdfium gibi: sıfıra doğru kesilir


def _layer_stream(page: Page, lines: list[LayerLine], page_obj: pikepdf.Dictionary, font: _HiddenFont,
                  font_name: Name, actual_text: bool) -> bytes:
    (cx0, cy0, cx1, cy1), rotate = _view(page_obj)
    wv, hv = cx1 - cx0, cy1 - cy0
    if rotate in (90, 270):
        wv, hv = hv, wv
    sx, sy = wv / page.width, hv / page.height
    cm = " ".join(_num(v) for v in _ROTATE_CM[rotate](cx0, cy0, cx1, cy1))
    out = [f"{cm} cm"]
    open_block = None
    for ln in lines:
        if actual_text and ln.block != open_block:
            if open_block is not None:
                out.append("EMC")
            whole = " ".join(x.text for x in lines if x.block == ln.block)
            out.append(f"/Span << /ActualText <FEFF{_utf16_hex(whole)}> >> BDC")
            open_block = ln.block
        x1, y1, x2, y2 = ln.box
        if x2 <= x1 or y2 <= y1:
            continue        # bozuk kutu: genişlik/yükseklik sıfırsa yerleştirilemez
        u1, u2 = x1 * sx, x2 * sx
        yb, yt = hv - y2 * sy, hv - y1 * sy
        codes = font.encode(ln.text)
        if not codes:
            continue
        fs = (yt - yb) / LINE_HEIGHT
        tz = 100 * (u2 - u1) / (len(codes) // 4 * GLYPH_WIDTH / 1000 * fs)
        x = u1
        if _rtl(ln.text):   # metin mantıksal sırada kalır; negatif Tz ile sağ kenardan sola doğru dizilir (pdfium
            tz, x = -tz, u2  # çıkarımı böyle doğru sırayı verir; ters çevrilmiş metin Arapçayı ters kopyalatır)
        out.append(f"BT 3 Tr {font_name} {_num(fs)} Tf {_num(tz)} Tz "
                   f"1 0 0 1 {_num(x)} {_num(yb + BASELINE * fs)} Tm <{codes}> Tj ET")
    if open_block is not None:
        out.append("EMC")
    return ("\n".join(out) + "\n").encode("ascii")


def _image_xobject(pdf: pikepdf.Pdf, path: Path) -> tuple[pikepdf.Stream, float, float]:
    """Görseli XObject yapar: EXIF döndürmesiz L/RGB JPEG baytları aynen (DCTDecode), öbürleri kayıpsız (Flate).
    Dönen boyut pt cinsinden; DPI görselde yoksa DEFAULT_DPI."""
    with Image.open(path) as im:
        dpi = im.info.get("dpi") or (page_loader.DEFAULT_DPI, page_loader.DEFAULT_DPI)
        orientation = im.getexif().get(0x0112, 1)
        if im.format == "JPEG" and im.mode in ("L", "RGB") and orientation in (0, 1):
            data, filt, mode, (w, h) = path.read_bytes(), Name.DCTDecode, im.mode, im.size
        else:   # okuma (pages.render) ile aynı dönüşüm: EXIF yönü uygulanır, 8 bit L/RGB
            img = page_loader._to_8bit(im)
            data, filt, mode, (w, h) = zlib.compress(img.tobytes()), Name.FlateDecode, img.mode, img.size
    dx, dy = (float(d) if d and float(d) > 0 else page_loader.DEFAULT_DPI for d in tuple(dpi)[:2])
    xobj = pdf.make_stream(data, Type=Name.XObject, Subtype=Name.Image, Width=w, Height=h,
                           ColorSpace=Name.DeviceGray if mode == "L" else Name.DeviceRGB, BitsPerComponent=8,
                           Filter=filt)
    return xobj, w * 72 / dx, h * 72 / dy


def _new_page(pdf: pikepdf.Pdf, width: float, height: float, image: pikepdf.Stream | None = None) -> pikepdf.Page:
    resources = Dictionary()
    content = b""
    if image is not None:
        resources.XObject = Dictionary(Im0=image)
        content = f"q {_num(width)} 0 0 {_num(height)} 0 0 cm /Im0 Do Q\n".encode("ascii")
    pdf.pages.append(pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, width, height]), Resources=resources,
        Contents=pdf.make_stream(content))))
    return pdf.pages[-1]


def _source_page(stack: ExitStack, sources: dict[Path, list[tuple[pikepdf.Pdf, set[int]]]], src: Path,
                 index: int) -> pikepdf.Page:
    """Kaynak bir kez açılır ve akış verisi kayıtta okunacağı için açık kalır. Aynı sayfa ikinci kez istenirse kaynak
    yeniden açılır: pikepdf aynı tanıdık sayfayı önceki kopyanın (katmanı eklenmiş) sığ kopyasıyla verirdi."""
    handles = sources.setdefault(src, [])
    for handle, used in handles:
        if index not in used:
            used.add(index)
            return handle.pages[index - 1]
    handle = stack.enter_context(pikepdf.open(src))
    handles.append((handle, {index}))
    return handle.pages[index - 1]


def _text_doc(stack: ExitStack, docs: dict[Path, pdfium.PdfDocument], src: Path) -> pdfium.PdfDocument:
    """Metin denetimi için kaynak pdfium ile bir kez açılır; yazım bitince kapanır."""
    if src not in docs:
        with page_loader._PDFIUM_LOCK:
            docs[src] = pdfium.PdfDocument(str(src))
        stack.callback(_close_locked, docs[src])
    return docs[src]


def _close_locked(doc: pdfium.PdfDocument) -> None:
    with page_loader._PDFIUM_LOCK:
        doc.close()


def _has_text(doc: pdfium.PdfDocument, index: int) -> bool:
    with page_loader._PDFIUM_LOCK:
        text = doc[index - 1].get_textpage().get_text_range()
    return sum(not c.isspace() for c in text) >= TEXT_MIN_CHARS


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def write_pdf(pages: list[Page], out: Path, warnings: list[str] | None = None, *,
              actual_text: bool = False) -> Path:
    """Sayfaları sırayla orijinal görünümüyle yazar; okunan her satır görüntüdeki yerine görünmez metin olarak oturur."""
    warnings = warnings if warnings is not None else []
    with ExitStack() as stack:
        pdf = stack.enter_context(pikepdf.new())
        font = _HiddenFont(pdf)
        sources: dict[Path, list[tuple[pikepdf.Pdf, set[int]]]] = {}
        text_docs: dict[Path, pdfium.PdfDocument] = {}
        for page in pages:
            src = Path(page.source)
            layer = True
            if not src.is_file():
                scale = 72 / page_loader.DEFAULT_DPI
                size = (page.width * scale, page.height * scale) if page.width > 0 and page.height > 0 else A4_PT
                target = _new_page(pdf, *size)
                warnings.append(f"pdf: {src.name} bulunamadı; s. {page.index} boş sayfa üstüne yazıldı")
            elif src.suffix.lower() == ".pdf":
                pdf.pages.append(_source_page(stack, sources, src, page.index))
                target = pdf.pages[-1]
                if _has_text(_text_doc(stack, text_docs, src), page.index):
                    layer = False
                    warnings.append(f"pdf: {src.name} s. {page.index} zaten metin katmanı içeriyor; "
                                    "aranabilir katman eklenmedi")
            else:
                xobj, w_pt, h_pt = _image_xobject(pdf, src)
                target = _new_page(pdf, w_pt, h_pt, image=xobj)
            lines = layer_lines(page) if layer and page.width > 0 and page.height > 0 else []
            if lines:
                name = font.attach(target.obj)
                # Kaynak akışlarının q/Q'sunun dengeli olduğu varsayılır; katman böylece varsayılan durumla başlar
                target.contents_add(pdf.make_stream(b"q\n"), prepend=True)
                target.contents_add(_flate(pdf, b"Q\n" + _layer_stream(
                    page, lines, target.obj, font, name, actual_text)))
        font.finish()
        # Çıktı sürümü en az en yüksek kaynak sürümü olur; kaynağın sürüm gerektiren özellikleri geçerli kalır
        version = max((h.pdf_version for hs in sources.values() for h, _ in hs), key=_version_key,
                      default=pdf.pdf_version)
        with paths.atomic_output(out) as tmp:
            # Kaynak akışlar olduğu gibi kopyalansın: açılıp yeniden sıkıştırılmaz
            pdf.save(tmp, compress_streams=False, stream_decode_level=pikepdf.StreamDecodeLevel.none,
                     min_version=version)
    return out
