"""Sayfa ve belge işleme hattı: model + tesseract (→ önbellek) → bloklar → kesme, sarı işaret, satır yapısı.

Önbellekte yalnız OCR çıktısı durur (ham model cevabı + tesseract TSV, R27); sayfa yapısı her işte bundan yeniden
kurulur, böylece kod ve kelime listesi değişiklikleri GPU'suz uygulanır. Sarı işaret her zaman hesaplanıp Page'e
yazılır; açma/kapama yalnız dışa aktarmada yapılır. Kitap sayfa numarası tahmini (infer) her işte yeniden yapılır.
"""
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ocrnow import engines, paths
from ocrnow.align import apply_line_structure
from ocrnow.apostrophe import build_word_list, restore
from ocrnow.engines import DEFAULT_ENGINE, DEFAULT_MODE
from ocrnow.layout import Page, parse_output
from ocrnow.pagenum import infer, read_book_page
from ocrnow.pages import DEFAULT_DPI, DocJob, InputError, SourcePage, render
from ocrnow.tesslines import lines_in, parse_tsv
from ocrnow.uncertain import flag_uncertain

TEXT_CATEGORIES = frozenset({"Text", "Title", "Section-header", "List-item", "Footnote"})   # tam işlem
RESTORE_CATEGORIES = TEXT_CATEGORIES | {"Page-header", "Caption"}   # bunlara yalnız kesme geri getirme
READ_FAILED = "okunamadı"
PDF_ORDER_WARNING = "kitap sayfa numarası bulunamadı, PDF sırası kullanıldı"
IMAGE_ORDER_WARNING = "kitap sayfa numarası bulunamadı, görsel sırası kullanıldı"
TRUNCATED_WARNING = "model çıktısı uzunluk sınırında kesildi, sayfa sonu eksik olabilir"
CONFLICT_WARNING = "sayfa numarası komşularıyla tutmuyor, okunduğu gibi bırakıldı; kontrol edin"
CACHE_VERSION = 2   # 1: işlenmiş Page (eski; ıska sayılır), 2: ham OCR çıktısı (RawPage)


@dataclass
class Options:
    engine: str = DEFAULT_ENGINE
    mode: str = DEFAULT_MODE
    apostrophes: bool = True
    first_page: int | None = None
    dpi: int = DEFAULT_DPI


def _is_pdf(src: SourcePage) -> bool:
    return src.source.suffix.lower() == ".pdf"


@dataclass
class RawPage:
    """Sayfanın OCR çıktısı, önbellekte duran hâli: ham model cevabı, tesseract TSV, görüntü boyutu."""
    raw: str
    tsv: str
    width: int
    height: int
    truncated: bool = False   # model cevabı uzunluk sınırında kesildi (engines.Truncated)


def page_id(src: SourcePage) -> str:
    """Uyarılardaki sayfa kimliği: PDF'te "s. N", görüntüde dosya adı (görüntünün index'i hep 1)."""
    return f"s. {src.index}" if _is_pdf(src) else src.source.name


def _word_list() -> dict[str, str]:
    return build_word_list(paths.GT, paths.SETTINGS / "kesme.txt")


def ocr_page(img: Path, opts: Options, run: Callable[[Path, str], str], tsv: Callable[[Path], str] | None = None,
             size: tuple[int, int] | None = None) -> RawPage:
    """Sayfanın OCR'ı: tesseract ve model. size=(genişlik, yükseklik); None ise img'den okunur.
    tsv None ise engines.tesseract_tsv (çağrı anında okunur)."""
    tsv_text = (tsv or engines.tesseract_tsv)(img)   # önce: tesseract hata verirse model (VLM) boşuna beklenmez
    raw = run(img, opts.mode)
    if size is None:
        with Image.open(img) as im:
            size = im.size
    return RawPage(str(raw), tsv_text, size[0], size[1], isinstance(raw, engines.Truncated))


def build_page(ocr: RawPage, src: SourcePage, opts: Options, *, words: dict[str, str] | None = None) -> Page:
    """OCR çıktısından sayfa yapısı (OCR dışı bütün adımlar). words None ise kelime listesi burada kurulur."""
    size = (ocr.width, ocr.height)
    layout = engines.ENGINES[opts.engine].modes[opts.mode].layout
    blocks, parse_warnings = parse_output(ocr.raw, layout, size)
    pid = page_id(src)
    warnings = [f"{pid}: {w}" for w in parse_warnings]
    if ocr.truncated:    # yoğun sayfada son bloklar (çoğu kez dipnotlar) eksik kalır
        warnings.append(f"{pid}: {TRUNCATED_WARNING}")
    if opts.apostrophes and words is None:
        words = _word_list()
    tlines = parse_tsv(ocr.tsv)
    for k, block in enumerate(blocks, 1):
        if block.category not in RESTORE_CATEGORIES:
            continue
        marks = list(block.marks)    # parse'ın biçim işaretleri (italik/kalin); kesme metnin uzunluğunu değiştirmez
        if opts.apostrophes:
            block.text, restored = restore(block.text, words)
            marks += restored
        if block.category not in TEXT_CATEGORIES:
            block.marks = sorted(marks, key=lambda m: (m.start, m.end))
            continue
        mine = lines_in(block.bbox, tlines)
        marks += flag_uncertain(block.text, "\n".join(line.text for line in mine))
        block.marks = sorted(marks, key=lambda m: (m.start, m.end))
        if warning := apply_line_structure(block, mine):
            warnings.append(f"{pid}, blok {k}: {warning}")
    book_page, side = read_book_page(blocks, size[0])
    return Page(source=str(src.source), index=src.index, width=size[0], height=size[1],
                engine=opts.engine, mode=opts.mode, blocks=blocks, tess_tsv=ocr.tsv,
                book_page=book_page, book_page_side=side, warnings=warnings)


def process_page(img: Path, src: SourcePage, opts: Options, run: Callable[[Path, str], str],
                 tsv: Callable[[Path], str] | None = None,
                 size: tuple[int, int] | None = None, *, words: dict[str, str] | None = None) -> Page:
    """Tek sayfayı okur ve işler (ocr_page + build_page)."""
    return build_page(ocr_page(img, opts, run, tsv, size), src, opts, words=words)


def cache_path(src: SourcePage, opts: Options) -> Path:
    """CACHE/<ad>-<boyut>/syf<N>[_<dpi>dpi].<motor>.<mod>.json

    Klasör adı pages.render ile aynı mantıkta; dpi eki yalnız PDF'te ve 200 dışında (görüntüde dpi anlamsız).
    Kesme adımı önbellekten sonra uygulandığı için --kesme-yok aynı dosyayı kullanır (R27).
    """
    s = src.source
    try:
        size = s.stat().st_size
    except OSError as e:
        raise InputError(f"Dosya okunamadı: {s} ({e})") from e
    dpi = f"_{opts.dpi}dpi" if _is_pdf(src) and opts.dpi != DEFAULT_DPI else ""
    return paths.CACHE / f"{s.stem}-{size}" / f"syf{src.index}{dpi}.{opts.engine}.{opts.mode}.json"


def _load(path: Path, opts: Options) -> RawPage | None:
    """Önbellekteki OCR çıktısı; yoksa, okunamıyorsa, sürümü eskiyse ya da motor/mod tutmuyorsa None (yeniden OCR)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d["version"] != CACHE_VERSION or (d["engine"], d["mode"]) != (opts.engine, opts.mode):
            return None
        ocr = RawPage(d["raw"], d["tsv"], d["width"], d["height"], d["truncated"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    ok = (isinstance(ocr.raw, str) and isinstance(ocr.tsv, str) and isinstance(ocr.truncated, bool)
          and all(isinstance(v, int) and not isinstance(v, bool) for v in (ocr.width, ocr.height)))
    return ocr if ok else None


def _save(path: Path, ocr: RawPage, opts: Options) -> None:
    """Yarım yazılmış dosya önbellekte kalmasın: geçici dosyaya yazılıp yeniden adlandırılır."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    record = {"version": CACHE_VERSION, "engine": opts.engine, "mode": opts.mode, "width": ocr.width,
              "height": ocr.height, "truncated": ocr.truncated, "raw": ocr.raw, "tsv": ocr.tsv}
    try:
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _error_text(error: Exception) -> str:
    return str(error) or type(error).__name__  # iz kaydı (traceback) değil, yalnız ileti


def _job_page(src: SourcePage, opts: Options, run, tsv, words, say: Callable[[str], None]) -> tuple[Page, str | None]:
    """(sayfa, önbellek yazma hatası). OCR çıktısı önbellekte varsa ne render ne tesseract ne model çağrılır; yoksa
    okunup önbelleğe yazılır. Sayfa yapısı her iki durumda da OCR çıktısından kurulur (R27).

    Uyarılar tek kaynaklı kimlikle (page_id) kurulur; birleşik işin öneki çağıran tarafta eklenir.
    """
    path = cache_path(src, opts)
    if (ocr := _load(path, opts)) is not None:
        say("önbellekten")
        return build_page(ocr, src, opts, words=words), None
    say("okunuyor…")
    ocr = ocr_page(render(src, dpi=opts.dpi), opts, run, tsv)
    save_error = None
    try:
        _save(path, ocr, opts)
    except OSError as e:
        save_error = str(e)
    return build_page(ocr, src, opts, words=words), save_error


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


def process_job(job: DocJob, opts: Options, run, tsv: Callable[[Path], str] | None = None,
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
        except engines.ServerError:
            raise                 # sunucu açılamadı: sonraki sayfalar da okunamaz, koşu durur
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
        elif value is not None and value.isdigit() and inferred:   # çelişen okumalar: hangisi doğru bilinemez
            page.warnings.append(f"{pid}: {CONFLICT_WARNING} [{label}]")
        if label.startswith(("PDF s. ", "görsel ")):
            page.warnings.append(f"{pid}: {PDF_ORDER_WARNING if _is_pdf(src) else IMAGE_ORDER_WARNING}")
        page.book_page, page.book_page_inferred = label, inferred
    return pages
