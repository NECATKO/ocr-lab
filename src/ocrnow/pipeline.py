"""Sayfa ve belge işleme hattı: model + tesseract (→ önbellek) → bloklar → kesme, sarı işaret, satır yapısı.

Önbellekte yalnız OCR çıktısı durur (ham model cevabı + tesseract TSV, R27); sayfa yapısı her işte bundan yeniden
kurulur, böylece kod ve kelime listesi değişiklikleri GPU'suz uygulanır. Sarı işaret her zaman hesaplanıp Page'e
yazılır; açma/kapama yalnız dışa aktarmada yapılır. Kitap sayfa numarası tahmini (infer) her işte yeniden yapılır.
"""
import json
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ocrnow import engines, identity, paths
from ocrnow.align import apply_line_structure
from ocrnow.apostrophe import build_word_list, restore
from ocrnow.engines import DEFAULT_ENGINE, DEFAULT_MODE
from ocrnow.export_txt import plain_text
from ocrnow.layout import Mark, Page, parse_output
from ocrnow.pagenum import infer, read_book_page
from ocrnow.pages import DEFAULT_DPI, DocJob, InputError, SourceFingerprint, SourcePage, fingerprint_source, render
from ocrnow.tesslines import assign_lines, parse_tsv, uncovered_text
from ocrnow.uncertain import flag_uncertain

TEXT_CATEGORIES = frozenset({"Text", "Title", "Section-header", "List-item", "Footnote"})   # tam işlem
RESTORE_CATEGORIES = TEXT_CATEGORIES | {"Page-header", "Caption"}   # bunlara yalnız kesme geri getirme
READ_FAILED = "okunamadı"
REVIEW_WARNING = "kontrol gerekli: model okunabilir metin vermedi"
PDF_ORDER_WARNING = "kitap sayfa numarası bulunamadı, PDF sırası kullanıldı"
IMAGE_ORDER_WARNING = "kitap sayfa numarası bulunamadı, görsel sırası kullanıldı"
TRUNCATED_WARNING = "model çıktısı uzunluk sınırında kesildi, sayfa sonu eksik olabilir"
RECOVERED_WARNING = "model tekrar döngüsüne girdi; sayfa tekrar cezasıyla yeniden okundu, metni kontrol edin"
SIDE_WARNING = "sayfanın tarafıyla çelişiyor"
DUPLICATE_WARNING = "aynı sayfa iki kez taranmış olabilir"
UNEXPECTED_SCRIPT_WARNING = "beklenmedik yazı karakteri (CJK) bulundu"
MISSING_TEXT_WARNING = "olası eksik metin (tesseract'ın gördüğü satırlar model çıktısında yok)"
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_NONSPACE = re.compile(r"\S+")
CACHE_VERSION = 4   # ham OCR çıktısı ve onu üreten ayarların kimliği


@dataclass
class Options:
    engine: str = DEFAULT_ENGINE
    mode: str = DEFAULT_MODE
    apostrophes: bool = True
    first_page: int | None = None
    dpi: int = DEFAULT_DPI
    use_tesseract: bool = True
    detect_pagenum: bool = True
    cjk_check: bool = True
    lang: str | None = None


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
    recovered: bool = False   # kesilen ilk okuma tekrar cezasıyla tamamlandı (engines.Recovered)


def page_id(src: SourcePage) -> str:
    """Uyarılardaki sayfa kimliği: PDF'te "s. N", görüntüde dosya adı (görüntünün index'i hep 1)."""
    return f"s. {src.index}" if _is_pdf(src) else src.source.name


def _regions(lines, height: int) -> str:
    """Satırların sayfadaki bölgesi: üst/orta/alt bölüm (yukarıdan aşağı, tekrarsız)."""
    names = ("üst bölüm", "orta bölüm", "alt bölüm")
    found = [names[min(2, int(3 * (l.box[1] + l.box[3]) / 2 / max(1, height)))] for l in lines]
    return ", ".join(dict.fromkeys(found))


def review_reasons(page: Page) -> list[str]:
    """Kullanıcının bu sayfada neye bakması gerektiği: kısa nedenler, en ağırı başta (yoksa boş)."""
    joined = "\n".join(page.warnings)
    reasons = []
    if page.failed:
        reasons.append("okunamadı")
    if page.truncated:
        reasons.append("sayfa sonu eksik olabilir")
    if page.needs_review:
        reasons.append("metin çıkmadı")
    if m := re.search(re.escape(MISSING_TEXT_WARNING) + r": \d+ satır, ([^\n]+)", joined):
        reasons.append(f"olası eksik metin ({m.group(1)})")
    if RECOVERED_WARNING in joined:
        reasons.append("tekrar döngüsü")
    if m := re.search(re.escape(UNEXPECTED_SCRIPT_WARNING) + r" \((.*?)\)", joined):
        reasons.append(f"beklenmedik karakter ({m.group(1)})")
    if page.book_page_check:
        reasons.append("sayfa numarası çelişkili")
    if DUPLICATE_WARNING in joined:
        reasons.append("iki kez taranmış olabilir")
    return reasons


REVIEW_ORDER = ("okunamadı", "sayfa sonu eksik olabilir", "metin çıkmadı", "olası eksik metin", "tekrar döngüsü", "beklenmedik karakter",
                "sayfa numarası çelişkili", "iki kez taranmış olabilir")


def review_rank(reasons: list[str]) -> int:
    """En ağır nedenin sırası (sayfaları önem sırasına dizmek için)."""
    return min(next(i for i, r in enumerate(REVIEW_ORDER) if reason.startswith(r)) for reason in reasons)


def _word_list() -> dict[str, str]:
    return build_word_list(paths.GT, paths.SETTINGS / "kesme.txt")


def ocr_page(img: Path, opts: Options, run: Callable[[Path, str], str], tsv: Callable[[Path], str] | None = None,
             size: tuple[int, int] | None = None) -> RawPage:
    """Sayfanın OCR'ı: (gerekirse) tesseract ve model. size=(genişlik, yükseklik); None ise img'den okunur.
    tsv None ise engines.tesseract_tsv (çağrı anında okunur)."""
    if opts.use_tesseract:
        tsv_text = tsv(img) if tsv is not None else (engines.tesseract_tsv(img, lang=opts.lang) if opts.lang else engines.tesseract_tsv(img))
    else:
        tsv_text = ""
    raw = run(img, opts.mode)
    if size is None:
        with Image.open(img) as im:
            size = im.size
    if raw is None:                        # bazı OpenAI uyumlu sunucular boş yanıtı null döner
        raw = ""
    if not isinstance(raw, str):
        raise ValueError("model yanıtı metin değil")
    return RawPage(str(raw), tsv_text, size[0], size[1], isinstance(raw, engines.Truncated),
                   isinstance(raw, engines.Recovered))


def build_page(ocr: RawPage, src: SourcePage, opts: Options, *, words: dict[str, str] | None = None) -> Page:
    """OCR çıktısından sayfa yapısı (OCR dışı bütün adımlar). words None ise kelime listesi burada kurulur."""
    size = (ocr.width, ocr.height)
    eng = engines.ENGINES[opts.engine]
    blocks, parse_warnings = parse_output(ocr.raw, eng.modes[opts.mode].layout, size, resize=engines.effective_resize(eng))
    pid = page_id(src)
    warnings = [f"{pid}: {w}" for w in parse_warnings]
    needs_review = not any(b.category not in {"Picture", "Page-footer"} and plain_text(b).strip() for b in blocks)
    if needs_review:
        warnings.append(f"{pid}: {REVIEW_WARNING}")
    if ocr.truncated:    # yoğun sayfada son bloklar (çoğu kez dipnotlar) eksik kalır
        warnings.append(f"{pid}: {TRUNCATED_WARNING}")
    if ocr.recovered:
        warnings.append(f"{pid}: {RECOVERED_WARNING}")
    if opts.apostrophes and words is None:
        words = _word_list()
    tlines = parse_tsv(ocr.tsv) if opts.use_tesseract and ocr.tsv else []
    assigned = assign_lines(blocks, tlines, categories=TEXT_CATEGORIES) if opts.use_tesseract else [[] for _ in blocks]
    cjk_chars: list[str] = []
    for k, block in enumerate(blocks, 1):
        if opts.cjk_check and block.category != "Picture" and (text := plain_text(block)):
            cjk_chars += _CJK.findall(text)
        if not block.text or block.category not in RESTORE_CATEGORIES:
            continue
        marks = list(block.marks)    # parse'ın biçim işaretleri (italik/kalin/ust); kesme metnin uzunluğunu değiştirmez
        if opts.apostrophes:
            block.text, restored = restore(block.text, words)
            marks += restored
        if block.category not in TEXT_CATEGORIES:
            block.marks = sorted(marks, key=lambda m: (m.start, m.end))
            continue
        if opts.use_tesseract:
            mine = assigned[k - 1]
            uncertain = flag_uncertain(block.text, "\n".join(line.text for line in mine))
            flagged = {(m.start, m.end) for m in uncertain}
            marks += uncertain
        else:
            mine = []
            flagged = set()
        if opts.cjk_check:
            for m in _NONSPACE.finditer(block.text):
                if _CJK.search(m.group()) and (m.start(), m.end()) not in flagged:
                    marks.append(Mark(m.start(), m.end(), "belirsiz"))
        block.marks = sorted(marks, key=lambda m: (m.start, m.end))
        if opts.use_tesseract and (warning := apply_line_structure(block, mine)):
            warnings.append(f"{pid}, blok {k}: {warning}")
    if opts.use_tesseract and (missing := uncovered_text(blocks, tlines, size)):
        warnings.append(f"{pid}: {MISSING_TEXT_WARNING}: {len(missing)} satır, {_regions(missing, size[1])}")
    if cjk_chars:
        sample = "".join(dict.fromkeys(cjk_chars))[:8]
        warnings.append(f"{pid}: {UNEXPECTED_SCRIPT_WARNING} ({sample}), kontrol edin")
    book_page, side = read_book_page(blocks, size[0]) if opts.detect_pagenum else (None, None)
    return Page(source=str(src.source), index=src.index, width=size[0], height=size[1],
                engine=opts.engine, mode=opts.mode, blocks=blocks, tess_tsv=ocr.tsv,
                book_page=book_page, book_page_side=side, warnings=warnings, needs_review=needs_review,
                truncated=ocr.truncated)


def process_page(img: Path, src: SourcePage, opts: Options, run: Callable[[Path, str], str],
                 tsv: Callable[[Path], str] | None = None,
                 size: tuple[int, int] | None = None, *, words: dict[str, str] | None = None) -> Page:
    """Tek sayfayı okur ve işler (ocr_page + build_page)."""
    return build_page(ocr_page(img, opts, run, tsv, size), src, opts, words=words)


def cache_path(src: SourcePage, opts: Options, *, source_key: str | None = None, profile: dict | None = None) -> Path:
    """Ham OCR önbelleği: kaynak içeriği, sayfa, DPI (PDF) ve OCR ayar kimliği."""
    dpi = f"_{opts.dpi}dpi" if _is_pdf(src) and opts.dpi != DEFAULT_DPI else ""
    key = source_key or fingerprint_source(src.source).key
    profile = profile if profile is not None else identity.runtime(opts.engine).profile(opts.mode)
    return paths.CACHE / key / f"syf{src.index}{dpi}.{opts.engine}.{opts.mode}.{profile['id']}.json"


def _load(path: Path, opts: Options, profile: dict) -> RawPage | None:
    """Kullanılabilir ham OCR çıktısı; eksik kayıtlar ve eski sürümler yeniden okunur."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if d["version"] != CACHE_VERSION or (d["engine"], d["mode"]) != (opts.engine, opts.mode):
            return None
        if d["ocr_profile"] != profile["id"]:
            return None
        if opts.use_tesseract and not d.get("use_tesseract", True):
            return None
        ocr = RawPage(d["raw"], d["tsv"], d["width"], d["height"], d["truncated"], d.get("recovered", False) is True)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    ok = (isinstance(ocr.raw, str) and isinstance(ocr.tsv, str) and ocr.truncated is False
          and all(type(v) is int and v > 0 for v in (ocr.width, ocr.height)))
    return ocr if ok else None


def _save(path: Path, ocr: RawPage, opts: Options, profile: dict) -> None:
    """Yarım yazılmış dosya önbellekte kalmasın: geçici dosyaya yazılıp yeniden adlandırılır."""
    if not opts.use_tesseract and path.is_file():
        # Mevcut önbellekte tesseract'lı geçerli kayıt varsa tesseract'sız kayıtla ezme
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("ocr_profile") == profile["id"] and existing.get("use_tesseract", True):
                return
        except (OSError, ValueError, TypeError):
            pass
    record = {"version": CACHE_VERSION, "engine": opts.engine, "mode": opts.mode, "ocr_profile": profile["id"],
              "width": ocr.width, "height": ocr.height, "truncated": ocr.truncated, "raw": ocr.raw, "tsv": ocr.tsv}
    if not opts.use_tesseract:
        record["use_tesseract"] = False
    if ocr.recovered:
        record["recovered"] = True
    with paths.atomic_output(path) as tmp:
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def _error_text(error: Exception) -> str:
    return str(error) or type(error).__name__  # iz kaydı (traceback) değil, yalnız ileti


def _read(src: SourcePage, fingerprint: SourceFingerprint, opts: Options, run, tsv) -> RawPage:
    return ocr_page(render(src, dpi=opts.dpi, fingerprint=fingerprint), opts, run, tsv)


def _workers(opts: Options) -> int:
    return engines.ENGINES[opts.engine].parallel


def _schedule(src: SourcePage, fingerprint: SourceFingerprint | InputError, opts: Options, run, tsv,
              pool: ThreadPoolExecutor, profile: dict) -> tuple[str | None, Callable[[], RawPage], Path | None]:
    """(ilerleme, OCR sonucu, yeni sonuç için önbellek yolu); kaynak hatası sayfa düzeyinde bildirilir."""
    if isinstance(fingerprint, InputError):
        def failed() -> RawPage:
            # Aynı kaynak hatası farklı sayfalara ait ayrı izler taşısın.
            raise InputError(str(fingerprint))
        return None, failed, None
    path = None
    if profile["cacheable"]:
        path = cache_path(src, opts, source_key=fingerprint.key, profile=profile)
        if (ocr := _load(path, opts, profile)) is not None:
            return "önbellekten", lambda: ocr, None
    return "okunuyor…", pool.submit(_read, src, fingerprint, opts, run, tsv).result, path


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


def _book_pages(job_pages: list[SourcePage], read: list[str | None], sides: list[str | None],
                headers: list[str | None], first_page: int | None) -> list[tuple[str, bool]]:
    """Kitap sayfa numaraları (R17): PDF koşularında PDF sayfa indeksi uzayında (seçilmeyen sayfalar boşluk sayılır),
    görüntü koşularında sırayla. first_page işin ilk seçili sayfasını numaralar ve yalnız ilk koşuya uygulanır.
    sides/headers: okunan numaranın tarafı ve sayfanın başlık metni (R25, çiftlik ipuçları)."""
    out: list[tuple[str, bool]] = [("", False)] * len(job_pages)
    for r, run in enumerate(_runs(job_pages)):
        start = first_page if r == 0 else None
        if _is_pdf(job_pages[run[0]]):
            base = min(job_pages[i].index for i in run)
            size = max(job_pages[i].index for i in run) - base + 1
            d_read, d_sides, d_headers = ([None] * size for _ in range(3))
            for i in run:
                k = job_pages[i].index - base
                d_read[k], d_sides[k], d_headers[k] = read[i], sides[i], headers[i]
            if start is not None:
                start -= job_pages[run[0]].index - base   # ilk seçili sayfa first_page olsun
            labels = infer(d_read, start, d_sides, d_headers)
            for i in run:
                label, inferred = labels[job_pages[i].index - base]
                if label.startswith("PDF s. "):
                    label = f"PDF s. {job_pages[i].index}"
                out[i] = (label, inferred)
        else:
            labels = infer([read[i] for i in run], start, [sides[i] for i in run], [headers[i] for i in run])
            for i, (label, inferred) in zip(run, labels):
                out[i] = (f"görsel {i + 1}" if label.startswith("PDF s. ") else label, inferred)
    return out


def _duplicates(job_pages: list[SourcePage], read: list[str | None], labels: list[tuple[str, bool]],
                first_page: int | None) -> dict[int, list[int]]:
    """Aynı kaynakta aynı Arap rakamı birden fazla sayfada okunduysa: iş konumu → aynı numaralı diğer konumlar (R25).
    Koşudan düzeltilen okuma (etiketi okunandan farklı) sayılmaz; first_page verilen ilk koşuda (eski davranış)
    aranmaz."""
    out: dict[int, list[int]] = {}
    for r, run in enumerate(_runs(job_pages)):
        if r == 0 and first_page is not None:
            continue
        same: dict[str, list[int]] = {}
        for i in run:
            if read[i] is not None and read[i].isdigit() and labels[i][0] == read[i]:
                same.setdefault(read[i], []).append(i)
        for group in same.values():
            if len(group) > 1:
                out.update({i: [j for j in group if j != i] for i in group})
    return out


_LOCATIVE_UNITS = dict(zip("123456789", "de de te te te da de de da".split()))    # bir, iki, üç … dokuz
_LOCATIVE_TENS = dict(zip("123456789", "da de da ta de ta te de da".split()))     # on, yirmi, otuz … doksan


def _also_in(pid: str) -> str:
    """Sayfa kimliği + bulunma eki + "de/da" bağlacı, ünlü uyumuyla: "s. 12'de de", "s. 3'te de", "s. 40'ta da";
    numarasız kimlikte "<ad> sayfasında da"."""
    if not (m := re.search(r"\d+$", pid)):
        return f"{pid} sayfasında da"
    digits = m.group()
    if digits[-1] != "0":
        suffix = _LOCATIVE_UNITS[digits[-1]]
    elif len(digits) > 1 and digits[-2] != "0":
        suffix = _LOCATIVE_TENS[digits[-2]]
    else:
        suffix = "de"                                       # yüz, bin
    return f"{pid}'{suffix} d{suffix[1]}"


def _header_text(page: Page) -> str | None:
    return " ".join(b.text for b in page.blocks if b.category == "Page-header") or None


def job_page_id(src: SourcePage, multi: bool) -> str:
    return f"{src.source.name} {page_id(src)}" if multi and _is_pdf(src) else page_id(src)


def process_job(job: DocJob, opts: Options, run, tsv: Callable[[Path], str] | None = None,
                 progress: Callable[[str], None] = print, *, profile: dict | None = None) -> list[Page]:
    """Belgenin sayfalarını işler; hatalı sayfa uyarıyla boş geçilir, sonunda kitap numaraları tahmin edilir.

    Önbellekte olmayan sayfalardan motorun `parallel` kadarı aynı anda okunur (dots.mocr: 8, sunucu toplu işler);
    öteki motorlarda tek tek. İlerleme, sayfa yapısı ve uyarılar yine sayfa sırasıyla: "(i/n) okunuyor…" satırı
    i'den önceki sayfalar bitince yazılır.
    Uyarı ve ilerleme kimliği page_id; birden fazla PDF'li işte PDF sayfasının önüne dosya adı gelir ("k.pdf s. 3").
    """
    words = _word_list() if opts.apostrophes else None
    profile = profile if profile is not None else identity.runtime(opts.engine).profile(opts.mode)
    multi = len({p.source for p in job.pages if _is_pdf(p)}) > 1
    pool = ThreadPoolExecutor(_workers(opts), thread_name_prefix="ocr")
    try:
        pages, read, sides, headers = _collect(job, opts, run, tsv, words, multi, pool, progress, profile)
    finally:            # hata ya da Ctrl-C: sıradaki sayfalar başlamaz, sürenler beklenmez (sunucu kapanınca düşer)
        pool.shutdown(wait=False, cancel_futures=True)
    return _finish(job, opts, multi, pages, read, sides, headers)


def _collect(job: DocJob, opts: Options, run, tsv, words, multi: bool, pool: ThreadPoolExecutor,
              progress: Callable[[str], None], profile: dict) -> tuple[list[Page], list, list, list]:
    """Sayfaları sırayla toplar ve kurar: (sayfalar, okunan numaralar, taraflar, başlıklar)."""
    pages: list[Page] = []
    read: list[str | None] = []
    sides: list[str | None] = []
    headers: list[str | None] = []
    n = len(job.pages)
    sources: dict[Path, SourceFingerprint | InputError] = {}
    for src in job.pages:
        if src.source not in sources:  # kitabın tamamını sayfa başına değil, iş başına bir kez özetle
            try:
                sources[src.source] = fingerprint_source(src.source)
            except InputError as e:
                sources[src.source] = e
    scheduled = [_schedule(src, sources[src.source], opts, run, tsv, pool, profile) for src in job.pages]
    for i, (src, (state, get, store)) in enumerate(zip(job.pages, scheduled), 1):
        pid = job_page_id(src, multi)
        head = f"{job.name}: {pid} ({i}/{n})"
        try:
            if state:
                progress(f"{head} {state}")
            ocr = get()                      # i bitene kadar beklenir
            fingerprint = sources[src.source]
            if isinstance(fingerprint, SourceFingerprint):
                fingerprint.check(src.source)
            page = build_page(ocr, src, opts, words=words)
        except engines.ServerError:
            raise                 # sunucu açılamadı: sonraki sayfalar da okunamaz, koşu durur
        except Exception as e:  # sayfa düzeyinde hata: iş devam eder
            progress(f"{head} {READ_FAILED}: {_error_text(e)}")
            page = Page(source=str(src.source), index=src.index, width=0, height=0, engine=opts.engine,
                        mode=opts.mode, blocks=[], warnings=[f"{pid}: {READ_FAILED}: {_error_text(e)}"],
                        failed=True)
            read.append(None)
            sides.append(None)
            headers.append(None)
        else:
            if pid != page_id(src):
                page.warnings = [f"{src.source.name} {w}" for w in page.warnings]
            if page.needs_review or page.truncated:
                progress(f"{head} kontrol gerekli")
            elif store is not None:
                try:
                    _save(store, ocr, opts, profile)
                except (OSError, UnicodeError) as e:
                    page.warnings.append(f"{pid}: önbelleğe yazılamadı ({e})")
            read.append(page.book_page)
            sides.append(page.book_page_side)
            headers.append(_header_text(page))
        pages.append(page)
    return pages, read, sides, headers


def _finish(job: DocJob, opts: Options, multi: bool, pages: list[Page], read: list, sides: list,
            headers: list) -> list[Page]:
    """Kitap sayfa numaraları ve numara uyarıları (bütün sayfalar okunduktan sonra)."""
    if not opts.detect_pagenum and opts.first_page is None:
        return pages
    labels = _book_pages(job.pages, read, sides, headers, opts.first_page)
    duplicates = _duplicates(job.pages, read, labels, opts.first_page)
    for pos, (page, src, value, (label, inferred)) in enumerate(zip(pages, job.pages, read, labels)):
        pid = job_page_id(src, multi)
        if value is not None and value.isdigit() and label != value:   # okunan numara düzeltildi (R21)
            page.warnings.append(f"{pid}: okunan {value} → [{label}]")
        elif value is not None and value.isdigit() and inferred:
            # okunduğu gibi ama tahmin işaretli: kitabın taraf–çiftlik kuralına aykırı (R25, pagenum.infer)
            side = "sol" if page.book_page_side == "sol" else "sağ"
            parity = "çift" if int(value) % 2 == 0 else "tek"
            page.warnings.append(f"{pid}: okunan {value} {SIDE_WARNING} ({side} sayfada {parity} numara), kontrol edin")
            page.book_page_check = True
        if pos in duplicates:
            ids = [job_page_id(job.pages[j], multi) for j in duplicates[pos]]
            where = ", ".join(ids[:-1] + [_also_in(ids[-1])])
            page.warnings.append(f"{pid}: {value} numarası {where} okundu ({DUPLICATE_WARNING})")
        if label.startswith(("PDF s. ", "görsel ")):
            page.warnings.append(f"{pid}: {PDF_ORDER_WARNING if _is_pdf(src) else IMAGE_ORDER_WARNING}")
        page.book_page, page.book_page_inferred = label, inferred
    return pages
