"""Girdi toplama, sayfa seçimi, belge gruplama ve sayfaları PNG'ye çevirme."""
import re
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image
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
    return [int(t) if t.isdigit() else t for t in re.split(r"([0-9]+)", path.name.lower())]


def _pdf_page_count(path: Path) -> int:
    try:
        pdf = pdfium.PdfDocument(str(path))
    except (PdfiumError, OSError) as e:
        raise InputError(f"PDF açılamadı: {path} ({e})") from e
    try:
        return len(pdf)
    finally:
        pdf.close()


def _pdf_job(path: Path, spec: str | None, warnings: list[str]) -> DocJob:
    n = _pdf_page_count(path)
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
    loose_slot: int | None = None  # birleşik görüntü işinin jobs içindeki yeri (ilk görüntünün sırası)

    for path in inputs:
        if not path.exists():
            raise InputError(f"Dosya ya da klasör bulunamadı: {path}")
        if path.is_dir():
            files = sorted((f for f in path.iterdir() if f.is_file() and not f.name.startswith(".")),
                           key=_natural_key)
            pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
            images = [f for f in files if _is_image(f)]
            if not pdfs and not images:
                raise InputError(f"Klasörde desteklenen görüntü ya da PDF yok: {path} "
                                 "(PDF, PNG, JPG, TIF desteklenir)")
            jobs += [_pdf_job(f, spec, warnings) for f in pdfs]
            if images:
                jobs.append(DocJob(path.name or path.resolve().name, [SourcePage(f, 1) for f in images]))
        elif path.suffix.lower() == ".pdf":
            jobs.append(_pdf_job(path, spec, warnings))
        elif _is_image(path):
            if loose_slot is None:
                loose_slot = len(jobs)
                jobs.append(DocJob("", []))  # yer tutucu; döngü sonunda doldurulur
            loose.append(path)
        else:
            raise InputError(f"Desteklenmeyen dosya türü: {path} (PDF, PNG, JPG, TIF desteklenir)")
    if loose_slot is not None:
        jobs[loose_slot] = DocJob(loose[0].stem, [SourcePage(f, 1) for f in loose])
    if merge and len(jobs) > 1:
        jobs = [DocJob(jobs[0].name + "_birlesik", [p for j in jobs for p in j.pages])]
    return jobs, warnings


def render(page: SourcePage, dpi: int = DEFAULT_DPI) -> Path:
    """Sayfayı paths.PAGES altına PNG olarak yazar; varsa yeniden üretmez.

    Klasör adına dosya boyutu girer: farklı klasörlerdeki aynı adlı iki dosya karışmaz.
    Görüntü kaynağında dpi anlamsızdır, dosya adına girmez.
    """
    src = page.source
    is_pdf = src.suffix.lower() == ".pdf"
    suffix = f"_{dpi}dpi" if is_pdf and dpi != DEFAULT_DPI else ""
    out = paths.PAGES / f"{src.stem}-{src.stat().st_size}" / f"syf{page.index}{suffix}.png"
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
                img = im.convert("L" if im.mode == "L" else "RGB")
        except OSError as e:
            raise InputError(f"Görüntü açılamadı: {src} ({e})") from e
    img.save(tmp)
    tmp.replace(out)
    return out
