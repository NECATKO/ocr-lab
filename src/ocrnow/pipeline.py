"""Sayfa ve belge işleme hattı: model + tesseract → bloklar → kesme, sarı işaret, satır yapısı → önbellek.

Sarı işaret her zaman hesaplanıp Page'e yazılır; açma/kapama yalnız dışa aktarmada yapılır.
Önbellekte kitap sayfa numarasının okunan hâli durur; tahmin (infer) her işte yeniden yapılır.
"""
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ocrnow import engines, paths
from ocrnow.align import apply_line_structure
from ocrnow.apostrophe import build_word_list, restore
from ocrnow.engines import DEFAULT_ENGINE, DEFAULT_MODE
from ocrnow.layout import Page, page_from_json, page_to_json, parse_output
from ocrnow.pagenum import infer, read_book_page
from ocrnow.pages import DEFAULT_DPI, DocJob, InputError, SourcePage, render
from ocrnow.tesslines import lines_in, parse_tsv
from ocrnow.uncertain import flag_uncertain

TEXT_CATEGORIES = frozenset({"Text", "Title", "Section-header", "List-item", "Footnote"})
READ_FAILED = "okunamadı"
PDF_ORDER_WARNING = "kitap sayfa numarası bulunamadı, PDF sırası kullanıldı"


@dataclass
class Options:
    engine: str = DEFAULT_ENGINE
    mode: str = DEFAULT_MODE
    apostrophes: bool = True
    first_page: int | None = None
    dpi: int = DEFAULT_DPI


def _word_list() -> dict[str, str]:
    return build_word_list(paths.GT, paths.SETTINGS / "kesme.txt")


def process_page(img: Path, src: SourcePage, opts: Options, run: Callable[[Path, str], str],
                 tsv: Callable[[Path], str] = engines.tesseract_tsv,
                 size: tuple[int, int] | None = None, *, words: dict[str, str] | None = None) -> Page:
    """Tek sayfayı işler. size=(genişlik, yükseklik); None ise img'den okunur. words None ise liste burada kurulur."""
    raw = run(img, opts.mode)
    tsv_text = tsv(img)
    if size is None:
        with Image.open(img) as im:
            size = im.size
    layout = engines.ENGINES[opts.engine].modes[opts.mode].layout
    blocks, parse_warnings = parse_output(raw, layout, size)
    warnings = [f"s. {src.index}: {w}" for w in parse_warnings]
    if opts.apostrophes and words is None:
        words = _word_list()
    tlines = parse_tsv(tsv_text)
    for k, block in enumerate(blocks, 1):
        if block.category not in TEXT_CATEGORIES:
            continue
        marks = []
        if opts.apostrophes:
            block.text, marks = restore(block.text, words)
        mine = lines_in(block.bbox, tlines)
        marks += flag_uncertain(block.text, "\n".join(line.text for line in mine))
        block.marks = sorted(marks, key=lambda m: (m.start, m.end))
        if warning := apply_line_structure(block, mine):
            warnings.append(f"s. {src.index}, blok {k}: {warning}")
    book_page, side = read_book_page(blocks, size[0])
    return Page(source=str(src.source), index=src.index, width=size[0], height=size[1],
                engine=opts.engine, mode=opts.mode, blocks=blocks, tess_tsv=tsv_text,
                book_page=book_page, book_page_side=side, warnings=warnings)


def cache_path(src: SourcePage, opts: Options) -> Path:
    """CACHE/<ad>-<boyut>/syf<N>[_<dpi>dpi].<motor>.<mod>[.kesmesiz].json

    Klasör adı pages.render ile aynı mantıkta; dpi eki yalnız PDF'te ve 200 dışında (görüntüde dpi anlamsız).
    """
    s = src.source
    try:
        size = s.stat().st_size
    except OSError as e:
        raise InputError(f"Dosya okunamadı: {s} ({e})") from e
    dpi = f"_{opts.dpi}dpi" if s.suffix.lower() == ".pdf" and opts.dpi != DEFAULT_DPI else ""
    plain = "" if opts.apostrophes else ".kesmesiz"
    return paths.CACHE / f"{s.stem}-{size}" / f"syf{src.index}{dpi}.{opts.engine}.{opts.mode}{plain}.json"


def _load(path: Path) -> Page | None:
    """Önbellekteki sayfa; yoksa, okunamıyorsa ya da şeması eskiyse None (yeniden OCR)."""
    try:
        return page_from_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _save(path: Path, page: Page) -> None:
    """Yarım yazılmış dosya önbellekte kalmasın: geçici dosyaya yazılıp yeniden adlandırılır."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(page_to_json(page), encoding="utf-8")
    tmp.replace(path)


def _error_text(error: Exception) -> str:
    return str(error) or type(error).__name__  # iz kaydı (traceback) değil, yalnız ileti


def _failed_page(src: SourcePage, opts: Options, error: Exception) -> Page:
    return Page(source=str(src.source), index=src.index, width=0, height=0, engine=opts.engine, mode=opts.mode,
                blocks=[], warnings=[f"s. {src.index}: {READ_FAILED}: {_error_text(error)}"])


def _job_page(src: SourcePage, opts: Options, run, tsv, words, say: Callable[[str], None]) -> Page:
    """Önbellekte varsa ne render ne model çağrılır; yoksa sayfa işlenip önbelleğe yazılır."""
    path = cache_path(src, opts)
    if (page := _load(path)) is not None:
        say("önbellekten")
        return page
    say("okunuyor…")
    page = process_page(render(src, dpi=opts.dpi), src, opts, run, tsv, words=words)
    try:
        _save(path, page)
    except OSError as e:
        page.warnings.append(f"s. {src.index}: önbelleğe yazılamadı ({e})")
    return page


def process_job(job: DocJob, opts: Options, run, tsv=engines.tesseract_tsv,
                progress: Callable[[str], None] = print) -> list[Page]:
    """Belgenin sayfalarını sırayla işler; hatalı sayfa uyarıyla boş geçilir, sonunda kitap numaraları tahmin edilir."""
    words = _word_list() if opts.apostrophes else None
    pages: list[Page] = []
    read: list[str | None] = []
    n = len(job.pages)
    for i, src in enumerate(job.pages, 1):
        head = f"{job.name} s. {i}/{n}"
        try:
            page = _job_page(src, opts, run, tsv, words, lambda state: progress(f"{head} {state}"))
        except Exception as e:  # sayfa düzeyinde hata: iş devam eder
            progress(f"{head} {READ_FAILED}: {_error_text(e)}")
            page = _failed_page(src, opts, e)
            read.append(None)
        else:
            read.append(page.book_page)
        pages.append(page)
    for page, src, (label, inferred) in zip(pages, job.pages, infer(read, start=opts.first_page)):
        if label.startswith("PDF s. "):
            if src.source.suffix.lower() == ".pdf":  # infer iş içindeki sırayı verir; PDF'te PDF'teki sıra gösterilir
                label = f"PDF s. {src.index}"
            page.warnings.append(f"s. {src.index}: {PDF_ORDER_WARNING}")
        page.book_page, page.book_page_inferred = label, inferred
    return pages
