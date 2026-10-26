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
IMAGE_ORDER_WARNING = "kitap sayfa numarası bulunamadı, görsel sırası kullanıldı"


@dataclass
class Options:
    engine: str = DEFAULT_ENGINE
    mode: str = DEFAULT_MODE
    apostrophes: bool = True
    first_page: int | None = None
    dpi: int = DEFAULT_DPI


def _is_pdf(src: SourcePage) -> bool:
    return src.source.suffix.lower() == ".pdf"


def page_id(src: SourcePage) -> str:
    """Uyarılardaki sayfa kimliği: PDF'te "s. N", görüntüde dosya adı (görüntünün index'i hep 1)."""
    return f"s. {src.index}" if _is_pdf(src) else src.source.name


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
    pid = page_id(src)
    warnings = [f"{pid}: {w}" for w in parse_warnings]
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
            warnings.append(f"{pid}, blok {k}: {warning}")
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
    dpi = f"_{opts.dpi}dpi" if _is_pdf(src) and opts.dpi != DEFAULT_DPI else ""
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
    try:
        tmp.write_text(page_to_json(page), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _error_text(error: Exception) -> str:
    return str(error) or type(error).__name__  # iz kaydı (traceback) değil, yalnız ileti


def _job_page(src: SourcePage, opts: Options, run, tsv, words, say: Callable[[str], None]) -> tuple[Page, str | None]:
    """(sayfa, önbellek yazma hatası). Önbellekte varsa ne render ne model çağrılır; yoksa işlenip önbelleğe yazılır.

    Önbellekteki uyarılar tek kaynaklı kimlikle (page_id) durur; birleşik işin öneki çağıran tarafta eklenir.
    """
    path = cache_path(src, opts)
    if (page := _load(path)) is not None:
        say("önbellekten")
        return page, None
    say("okunuyor…")
    page = process_page(render(src, dpi=opts.dpi), src, opts, run, tsv, words=words)
    try:
        _save(path, page)
    except OSError as e:
        return page, str(e)
    return page, None


def _runs(job_pages: list[SourcePage]) -> list[list[int]]:
    """İşteki sıra konumlarını koşulara böler: aynı PDF'in ardışık sayfaları ya da ardışık görüntüler."""
    runs: list[list[int]] = []
    prev = None
    for i, src in enumerate(job_pages):
        key = src.source if _is_pdf(src) else None
        if runs and key == prev:
            runs[-1].append(i)
        else:
            runs.append([i])
        prev = key
    return runs


def _book_pages(job_pages: list[SourcePage], read: list[str | None],
                first_page: int | None) -> list[tuple[str, bool]]:
    """Kitap sayfa numaraları (R17): PDF koşularında PDF sayfa indeksi uzayında (seçilmeyen sayfalar boşluk sayılır),
    görüntü koşularında sırayla. first_page işin ilk seçili sayfasını numaralar ve yalnız ilk koşuya uygulanır."""
    out: list[tuple[str, bool]] = [("", False)] * len(job_pages)
    for r, run in enumerate(_runs(job_pages)):
        start = first_page if r == 0 else None
        if _is_pdf(job_pages[run[0]]):
            base = min(job_pages[i].index for i in run)
            dense: list[str | None] = [None] * (max(job_pages[i].index for i in run) - base + 1)
            for i in run:
                dense[job_pages[i].index - base] = read[i]
            if start is not None:
                start -= job_pages[run[0]].index - base   # ilk seçili sayfa first_page olsun
            labels = infer(dense, start)
            for i in run:
                label, inferred = labels[job_pages[i].index - base]
                if label.startswith("PDF s. "):
                    label = f"PDF s. {job_pages[i].index}"
                out[i] = (label, inferred)
        else:
            labels = infer([read[i] for i in run], start)
            for i, (label, inferred) in zip(run, labels):
                out[i] = (f"görsel {i + 1}" if label.startswith("PDF s. ") else label, inferred)
    return out


def _job_id(src: SourcePage, multi: bool) -> str:
    return f"{src.source.name} {page_id(src)}" if multi and _is_pdf(src) else page_id(src)


def process_job(job: DocJob, opts: Options, run, tsv=engines.tesseract_tsv,
                progress: Callable[[str], None] = print) -> list[Page]:
    """Belgenin sayfalarını sırayla işler; hatalı sayfa uyarıyla boş geçilir, sonunda kitap numaraları tahmin edilir.

    Uyarı ve ilerleme kimliği page_id; birden fazla PDF'li işte PDF sayfasının önüne dosya adı gelir ("k.pdf s. 3").
    """
    words = _word_list() if opts.apostrophes else None
    multi = len({p.source for p in job.pages if _is_pdf(p)}) > 1
    pages: list[Page] = []
    read: list[str | None] = []
    n = len(job.pages)
    for i, src in enumerate(job.pages, 1):
        pid = _job_id(src, multi)
        head = f"{job.name}: {pid} ({i}/{n})"
        try:
            page, save_error = _job_page(src, opts, run, tsv, words, lambda state: progress(f"{head} {state}"))
        except Exception as e:  # sayfa düzeyinde hata: iş devam eder
            progress(f"{head} {READ_FAILED}: {_error_text(e)}")
            page = Page(source=str(src.source), index=src.index, width=0, height=0, engine=opts.engine,
                        mode=opts.mode, blocks=[], warnings=[f"{pid}: {READ_FAILED}: {_error_text(e)}"],
                        failed=True)
            read.append(None)
        else:
            if pid != page_id(src):
                page.warnings = [f"{src.source.name} {w}" for w in page.warnings]
            if save_error:
                page.warnings.append(f"{pid}: önbelleğe yazılamadı ({save_error})")
            read.append(page.book_page)
        pages.append(page)
    for page, src, value, (label, inferred) in zip(pages, job.pages, read,
                                                   _book_pages(job.pages, read, opts.first_page)):
        pid = _job_id(src, multi)
        if value is not None and value.isdigit() and label != value:   # okunan numara düzeltildi (R21)
            page.warnings.append(f"{pid}: okunan {value} → [{label}]")
        if label.startswith(("PDF s. ", "görsel ")):
            page.warnings.append(f"{pid}: {PDF_ORDER_WARNING if _is_pdf(src) else IMAGE_ORDER_WARNING}")
        page.book_page, page.book_page_inferred = label, inferred
    return pages
