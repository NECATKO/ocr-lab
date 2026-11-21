"""OCR profilleri: her belge türü için varsayılan motor, boru hattı adımları ve çıktı ayarları."""
from dataclasses import dataclass

from ocrnow import engines

BOLD_DEFAULT = r"\[\d+[ab] [^\]]*\]"   # yazma nüsha işaretleri: [106b sayfanın sağ yanı]
DEFAULT_PROFILE = "kitap"


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    description: str
    engine: str
    mode: str
    formats: tuple[str, ...]
    use_tesseract: bool = False
    apostrophes: bool = False
    detect_pagenum: bool = False
    highlight: bool = False
    cjk_check: bool = True
    flowing_docx: bool = True
    rtl: bool = False
    bold_pattern: str | None = None
    prompt: str | None = None


_PROMPT_GUNLUK = (
    "Extract all visible text from this image accurately in natural reading order. "
    "Do not translate, summarize, or add commentary. Output only the extracted text."
)
_PROMPT_DERS_NOTU = (
    "Transcribe this lecture note, whiteboard photo, slide, or PDF page into clean Markdown. "
    "Preserve headings (# / ##), bullet lists, and write mathematical formulas in LaTeX ($...$ or $$...$$). "
    "Keep the original language faithfully without translating or omitting handwritten notes."
)
_PROMPT_FATURA = (
    "Extract the invoice or receipt data from this image as a single valid JSON object with keys: "
    "satici, tarih, fatura_no, para_birimi, kalemler (array of {aciklama, adet, birim_fiyat, tutar}), "
    "kdv, toplam, ham_metin. Use null for missing fields. Output only the JSON object."
)
_PROMPT_YAZMA = (
    "Transcribe this Arabic or Ottoman Turkish manuscript/document faithfully in its original Arabic script, "
    "preserving line order and original orthography. Do not translate or modernize."
)


PROFILES: dict[str, Profile] = {p.id: p for p in [
    Profile(
        id="kitap",
        label="Kitap",
        description="Taranmış kitap: satır kırılımı, dipnot, ayın/hemze ve sayfa numarası korunur.",
        engine=engines.DEFAULT_ENGINE,
        mode=engines.DEFAULT_MODE,
        formats=("docx",),
        use_tesseract=True,
        apostrophes=True,
        detect_pagenum=True,
        highlight=True,
        cjk_check=True,
        flowing_docx=False,
        rtl=False,
        bold_pattern=BOLD_DEFAULT,
        prompt=None,
    ),
    Profile(
        id="gunluk",
        label="Günlük / Hızlı",
        description="Fotoğraf, ekran görüntüsü ve hızlı metin; anında kopyalanabilir çıktı.",
        engine="glm-ocr",
        mode="duz",
        formats=("txt",),
        use_tesseract=False,
        apostrophes=False,
        detect_pagenum=False,
        highlight=False,
        cjk_check=True,
        flowing_docx=True,
        rtl=False,
        bold_pattern=None,
        prompt=_PROMPT_GUNLUK,
    ),
    Profile(
        id="ders-notu",
        label="Ders Notu",
        description="El yazısı, tahta fotoğrafı, slayt ve PDF ders notları; başlık ve formüllü Markdown/Word.",
        engine=engines.DEFAULT_ENGINE,
        mode=engines.DEFAULT_MODE,
        formats=("md",),
        use_tesseract=False,
        apostrophes=False,
        detect_pagenum=False,
        highlight=False,
        cjk_check=True,
        flowing_docx=True,
        rtl=False,
        bold_pattern=None,
        prompt=_PROMPT_DERS_NOTU,
    ),
    Profile(
        id="fatura",
        label="Fatura / Belge",
        description="Fatura, makbuz, form ve tablo ağırlıklı belgeler; Markdown, JSON ve CSV.",
        engine=engines.DEFAULT_ENGINE,
        mode=engines.DEFAULT_MODE,
        formats=("md", "csv"),
        use_tesseract=False,
        apostrophes=False,
        detect_pagenum=False,
        highlight=False,
        cjk_check=True,
        flowing_docx=True,
        rtl=False,
        bold_pattern=None,
        prompt=_PROMPT_FATURA,
    ),
    Profile(
        id="yazma",
        label="Yazma (Arapça / Osmanlıca)",
        description="Sağdan sola (RTL) Arapça ve Osmanlıca yazma eserler.",
        engine=engines.DEFAULT_ENGINE,
        mode="duz",
        formats=("txt", "docx"),
        use_tesseract=False,
        apostrophes=False,
        detect_pagenum=False,
        highlight=False,
        cjk_check=False,
        flowing_docx=True,
        rtl=True,
        bold_pattern=None,
        prompt=_PROMPT_YAZMA,
    ),
]}


def get(name: str | None) -> Profile:
    key = name or DEFAULT_PROFILE
    if key not in PROFILES:
        raise KeyError(f"bilinmeyen profil '{key}'")
    return PROFILES[key]
