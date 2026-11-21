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
    ),
    Profile(
        id="fatura",
        label="Fatura / Belge",
        description="Fatura, makbuz, form ve tablo ağırlıklı belgeler.",
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
    ),
]}


def get(name: str | None) -> Profile:
    key = name or DEFAULT_PROFILE
    if key not in PROFILES:
        raise KeyError(f"bilinmeyen profil '{key}'")
    return PROFILES[key]
