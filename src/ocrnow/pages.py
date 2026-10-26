"""Girdi toplama, sayfa seçimi, belge gruplama ve sayfaları PNG'ye çevirme."""
import re
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps
from pypdfium2 import PdfiumError

from ocrnow import paths

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
DEFAULT_DPI = 200


class InputError(ValueError):
    """Sayfa seçimi ya da girdi dosyası hatası; iletisi kullanıcıya olduğu gibi gösterilir."""


@dataclass
class SourcePage:
    source: Path
    index: int  # 1 tabanlı PDF sayfası (görüntüde 1)


@dataclass
class DocJob:
    name: str  # çıktı dosya adı (uzantısız)
    pages: list[SourcePage]


def _bad_spec(spec: str) -> InputError:
    return InputError(
        f"Sayfa seçimi anlaşılamadı: '{spec}'. Örnekler: hepsi, 5, 1,3,7, 10-25, ilk10, 1-3,7")


def parse_pages(spec: str | None, n: int) -> tuple[list[int], list[str]]:
    """Sayfa seçimini 0 tabanlı, sıralı ve tekrarsız indekslere çevirir; taşan kısım kırpılır ve uyarı verilir."""
    if spec is None or spec.strip().lower() == "hepsi":
        return list(range(n)), []
    ranges = []  # 1 tabanlı, kapalı [a, b]
    for part in spec.split(","):
        part = part.strip().lower()
        if m := re.fullmatch(r"ilk\s*([0-9]+)", part):
            a, b = 1, int(m[1])
        elif m := re.fullmatch(r"([0-9]+)\s*(?:-\s*([0-9]+))?", part):
            a = int(m[1])
            b = int(m[2]) if m[2] else a
        else:
            raise _bad_spec(spec)
        if a < 1 or b < a:
            raise _bad_spec(spec)
        ranges.append((a, b))
    pages = sorted({p - 1 for a, b in ranges for p in range(a, min(b, n) + 1)})
    if not pages:
        raise InputError(f"İstenen sayfalar ({spec}) belgede yok; belgede {n} sayfa var.")
    warns = []
    if any(b > n for _, b in ranges):
        warns.append(f"Sayfa seçimi '{spec}' belgedeki {n} sayfaya kırpıldı.")
    return pages, warns


def _natural_key(path: Path) -> list:
    # split tek sayıda ASCII rakam öbeği ayırır: çift sıra metin, tek sıra sayı (Unicode rakamlar sayı sayılmaz)
    return [int(t) if i % 2 else t for i, t in enumerate(re.split(r"([0-9]+)", path.name.lower()))]


def pdf_page_count(path: Path) -> int:
    try:
        pdf = pdfium.PdfDocument(str(path))
    except (PdfiumError, OSError) as e:
        raise InputError(f"PDF açılamadı: {path} ({e})") from e
    try:
        n = len(pdf)
    finally:
        pdf.close()
    if n == 0:
        raise InputError(f"PDF'te hiç sayfa yok: {path}")
    return n


def _pdf_job(path: Path, spec: str | None, warnings: list[str]) -> DocJob:
    n = pdf_page_count(path)
    try:
        idx, warns = parse_pages(spec, n)
    except InputError as e:
        raise InputError(f"{path.name}: {e}") from e
    warnings += [f"{path.name}: {w}" for w in warns]
    return DocJob(path.stem, [SourcePage(path, i + 1) for i in idx])


def _is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_EXTS


def collect_jobs(inputs: list[Path], spec: str | None, merge: bool = False) -> tuple[list[DocJob], list[str]]:
    """Girdileri belge işlerine ayırır. Sayfa seçimi yalnız PDF'lere uygulanır (görüntü tek sayfadır)."""
    if not inputs:
        raise InputError("Girdi verilmedi: bir PDF, görüntü ya da klasör verin.")
    warnings: list[str] = []
    jobs: list[DocJob] = []
    loose: list[Path] = []  # komut satırındaki tek tek görüntüler
    images_seen = False
    loose_slot: int | None = None  # birleşik görüntü işinin jobs içindeki yeri (ilk görüntünün sırası)

    for path in inputs:
        if not path.exists():
            raise InputError(f"Dosya ya da klasör bulunamadı: {path}")
        if path.is_dir():
            try:
                files = sorted((f for f in path.iterdir() if f.is_file() and not f.name.startswith(".")),
                               key=_natural_key)
            except OSError as e:
                raise InputError(f"Klasör okunamadı: {path} ({e})") from e
            pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
            images = [f for f in files if _is_image(f)]
            if not pdfs and not images:
                raise InputError(f"Klasörde desteklenen görüntü ya da PDF yok: {path} "
                                 "(PDF, PNG, JPG, TIF desteklenir)")
            jobs += [_pdf_job(f, spec, warnings) for f in pdfs]
            if images:
                images_seen = True
                jobs.append(DocJob(path.name or path.resolve().name, [SourcePage(f, 1) for f in images]))
        elif path.suffix.lower() == ".pdf":
            jobs.append(_pdf_job(path, spec, warnings))
        elif _is_image(path):
            if loose_slot is None:
                loose_slot = len(jobs)
                jobs.append(DocJob("", []))  # yer tutucu; döngü sonunda doldurulur
            loose.append(path)
            images_seen = True
        else:
            raise InputError(f"Desteklenmeyen dosya türü: {path} (PDF, PNG, JPG, TIF desteklenir)")
    if loose_slot is not None:
        jobs[loose_slot] = DocJob(loose[0].stem, [SourcePage(f, 1) for f in loose])
    if images_seen and spec is not None and spec.strip().lower() != "hepsi":
        warnings.append(f"Sayfa seçimi '{spec}' görüntülere uygulanmaz (her görüntü tek sayfadır).")
    if merge and len(jobs) > 1:  # tek iş varsa birleştirilecek bir şey yok, ad değişmez
        jobs = [DocJob(jobs[0].name + "_birlesik", [p for j in jobs for p in j.pages])]
    make_names_unique(jobs)
    return jobs, warnings


def make_names_unique(jobs: list[DocJob]) -> None:
    """Aynı adlı işler çıktıyı ezmesin: ikincisi _2, üçüncüsü _3 ... (büyük/küçük harf ayırt edilmez)."""
    seen: set[str] = set()
    for job in jobs:
        name, k = job.name, 2
        while name.lower() in seen:
            name = f"{job.name}_{k}"
            k += 1
        seen.add(name.lower())
        job.name = name


def _to_8bit(im: Image.Image) -> Image.Image:
    """EXIF yönünü uygular; 16/32 bit gri görüntüyü 8 bite indirir, gerisini RGB (ya da L) yapar."""
    im = ImageOps.exif_transpose(im)
    if im.mode in ("I", "I;16", "I;16L", "I;16B"):
        im = im.point(lambda v: v / 256).convert("L")
    return im.convert("L" if im.mode == "L" else "RGB")


def render(page: SourcePage, dpi: int = DEFAULT_DPI) -> Path:
    """Sayfayı paths.PAGES altına PNG olarak yazar; varsa yeniden üretmez.

    Klasör adına dosya boyutu girer: farklı klasörlerdeki aynı adlı iki dosya karışmaz.
    Görüntü kaynağında dpi anlamsızdır, dosya adına girmez.
    """
    src = page.source
    is_pdf = src.suffix.lower() == ".pdf"
    suffix = f"_{dpi}dpi" if is_pdf and dpi != DEFAULT_DPI else ""
    try:
        size = src.stat().st_size
    except OSError as e:
        raise InputError(f"Dosya okunamadı: {src} ({e})") from e
    out = paths.PAGES / f"{src.stem}-{size}" / f"syf{page.index}{suffix}.png"
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.stem + ".tmp.png")  # yarım yazılmış PNG yeniden kullanılmasın
    if is_pdf:
        try:
            pdf = pdfium.PdfDocument(str(src))
        except (PdfiumError, OSError) as e:
            raise InputError(f"PDF açılamadı: {src} ({e})") from e
        try:
            if not 1 <= page.index <= len(pdf):
                raise InputError(f"{src.name}: sayfa {page.index} yok; belgede {len(pdf)} sayfa var.")
            img = pdf[page.index - 1].render(scale=dpi / 72).to_pil()
        finally:
            pdf.close()
    else:
        try:
            with Image.open(src) as im:
                img = _to_8bit(im)
        except OSError as e:
            raise InputError(f"Görüntü açılamadı: {src} ({e})") from e
    img.save(tmp)
    tmp.replace(out)
    return out
