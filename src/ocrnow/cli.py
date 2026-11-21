"""`ocr-now` komutu: taranmış sayfaları .docx/.txt'ye çevirir; argümansız açılınca soru sorar."""
import argparse
import importlib
import json
import re
import sys
import textwrap
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

from ocrnow import (
    engines, export_docx, export_md, export_structured, export_txt, identity, pages, paths, pipeline, profiles, ui,
)
from ocrnow.layout import Page
from ocrnow.pages import DEFAULT_DPI, InputError

# Dışa aktarıcılar: biçim → yaz(sayfalar, çıktı yolu, ayarlar). Yeni biçim yalnız buraya eklenir; -f, sondaki soru ve
# yazma döngüsü buradan okur. ayarlar: highlight, bold (kalın kalıbı ya da None), warnings (eklenecek uyarı listesi).
EXPORTERS: dict[str, Callable[[list[Page], Path, dict], object]] = {
    "docx": lambda doc_pages, out, settings: export_docx.write_docx(
        doc_pages, out, highlight=settings["highlight"], bold_pattern=settings["bold"],
        warnings=settings["warnings"], flowing=settings.get("flowing", False), rtl=settings.get("rtl", False)),
    "txt": lambda doc_pages, out, settings: export_txt.write_txt(doc_pages, out),
    "md": lambda doc_pages, out, settings: export_md.write_md(doc_pages, out),
    "json": lambda doc_pages, out, settings: export_structured.write_json(doc_pages, out),
    "csv": lambda doc_pages, out, settings: export_structured.write_csv(doc_pages, out),
}
PREVIEW_LIMIT = 32_000
MODE_LABELS = {"duz": "duz — düz metin", "duzen": "duzen — başlık, dipnot ve sütunları ayırarak"}
YES = {"e", "evet", "y", "yes"}
NO = {"h", "hayır", "hayir", "n", "no"}

EPILOG = """alt komut:
  ocr-now models [install]   modelleri listeler / kurar
  (ilk argüman olarak 'models' alt komut sayılır; 'models' adlı bir dosya için ./models yazın)

-f'den sonra girdi yazılırsa biçim sanılır: girdileri -f'den önce yazın ya da araya -- koyun."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: hata: {message}\n")


class _Explicit(argparse.Action):
    """Değeri kaydeder ve seçeneğin açıkça verildiğini not eder (varsayılanla ayırt etmek için)."""

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        setattr(namespace, f"{self.dest}_verildi", True)


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"tam sayı olmalı: '{text}'") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 ya da daha büyük olmalı: {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="ocr-now", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
                description="Taranmış belge ve görselleri Word (.docx), Markdown (.md) ya da düz metne (.txt) çevirir. "
                            "Argümansız çalıştırılırsa adım adım sorar.")
    p.add_argument("girdiler", nargs="*", help="PDF, görüntü ya da klasör (varsayılan: input/ klasörü)")
    p.add_argument("--profil", choices=list(profiles.PROFILES), default=profiles.DEFAULT_PROFILE, action=_Explicit,
                   help=f"belge profili: {', '.join(profiles.PROFILES)} (varsayılan: %(default)s)")
    p.add_argument("-p", "--sayfalar", help="sayfa seçimi: hepsi, 5, 1,3,7, 10-25, ilk10 (varsayılan: hepsi)")
    p.add_argument("-m", "--model", default=engines.DEFAULT_ENGINE, action=_Explicit,
                   help=f"motor: {', '.join(engines.ENGINES)} (varsayılan: profile göre)")
    p.add_argument("-o", "--okuma", default=engines.DEFAULT_MODE, action=_Explicit,
                   help="okuma modu: duz ya da duzen (varsayılan: profile göre; model desteklemiyorsa duz)")
    p.add_argument("-f", "--format", nargs="+",
                   help=f"çıktı biçimi: {', '.join(EXPORTERS)} ya da birkaçı (verilmezse sonda sorulur)")
    p.add_argument("--isaretle", dest="isaretle", action="store_true", default=None,
                   help="şüpheli kelimeleri sarı işaretle (verilmezse profile göre / sonda sorulur)")
    p.add_argument("--isaretleme", dest="isaretle", action="store_false", default=None,
                   help="sarı işaretleme yapma")
    p.add_argument("--kesme", dest="kesme", action="store_true", default=None,
                   help="ayın/hemze (kesme işareti) adımını aç")
    p.add_argument("--kesme-yok", dest="kesme", action="store_false", default=None,
                   help="ayın/hemze (kesme işareti) adımını kapat")
    p.add_argument("--kalin-kalip", default=export_docx.BOLD_DEFAULT, action=_Explicit, metavar="KALIP",
                   help="kalın yazılacak metnin düzenli ifadesi; boş ('') kalın yapmaz (varsayılan: profile göre)")
    p.add_argument("--ilk-sayfa", type=_positive_int, help="ilk sayfanın kitap sayfa numarası")
    p.add_argument("--birlestir", action="store_true", help="bütün girdileri tek belgede birleştir")
    p.add_argument("--dpi", type=_positive_int, default=DEFAULT_DPI, help="PDF görüntüleme çözünürlüğü (varsayılan: %(default)s)")
    p.add_argument("--dil", help="Tesseract dili (ör. tur, tur+eng, eng, ara; varsayılan: TESS_LANG/tur)")
    p.add_argument("--kraken-model", dest="kraken_model", metavar="DOSYA",
                   help="Kraken .mlmodel dosyası (models/ içindeki ad ya da tam yol; yazma HTR modelleri için)")
    p.add_argument("--rapor", type=Path, metavar="DOSYA", help="iş sonu özetini JSON olarak bu dosyaya da yaz (ocr-gui için)")
    p.set_defaults(okuma_verildi=False, model_verildi=False, profil_verildi=False, kalin_kalip_verildi=False)
    return p


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)
    prof = profiles.get(args.profil)
    if not args.model_verildi:
        args.model = prof.engine
    if not args.okuma_verildi:
        args.okuma = prof.mode
    if not args.kalin_kalip_verildi:
        args.kalin_kalip = prof.bold_pattern or ""
    if args.model not in engines.ENGINES:
        parser.error(f"bilinmeyen model '{args.model}' (olanlar: {', '.join(engines.ENGINES)})")
    modes = engines.ENGINES[args.model].modes
    if args.okuma not in modes:
        if args.okuma_verildi:
            parser.error(f"{args.model} modelinde '{args.okuma}' okuma modu yok (olanlar: {', '.join(modes)})")
        args.okuma = "duz"
    if args.kalin_kalip:
        try:
            re.compile(args.kalin_kalip)
        except re.error as e:
            parser.error(f"--kalin-kalip geçersiz düzenli ifade: '{args.kalin_kalip}' ({e})")
    if args.format is not None:
        bad = [f for f in args.format if f not in EXPORTERS]
        if bad:
            parser.error(f"bilinmeyen biçim '{bad[0]}' (olanlar: {', '.join(EXPORTERS)}; girdileri -f'den önce yazın)")
        args.format = list(dict.fromkeys(args.format))
    args.kesme_yok = args.kesme is False
    return args


def _err(message: str) -> None:
    print(message, file=sys.stderr)


# ---- etkileşimli başlangıç ----

def _input_entries() -> list[Path]:
    """input/ içindeki PDF'ler, görüntüler ve alt klasörler."""
    root = paths.INPUT
    if not root.is_dir():
        return []
    return sorted((f for f in root.iterdir()
                   if not f.name.startswith(".")
                   and (f.is_dir() or f.suffix.lower() == ".pdf" or f.suffix.lower() in pages.IMAGE_EXTS)),
                  key=lambda f: f.name.lower())


def _ask_pages(pdf: Path) -> str | None:
    n = pages.pdf_page_count(pdf)
    while True:
        spec = ui.ask(f"\n{pdf.name}: kaç sayfa okunsun? ({n} sayfa var; ör. 1-3,7 ya da ilk10 ya da hepsi)", "hepsi")
        if spec.lower() == "hepsi":
            return None
        try:
            pages.parse_pages(spec, n)
        except InputError as e:
            print(f"  {e}")
            continue
        return spec


def _ask_start(args: argparse.Namespace) -> tuple[list[pages.DocJob], list[str]]:
    """Baştaki sorular: girdiler, PDF başına sayfalar, model, okuma modu. Seçimi args'a yazar."""
    entries = _input_entries()
    if not entries:
        raise InputError(f"{paths.INPUT} içinde PDF, görüntü ya da klasör yok; dosyalarınızı oraya koyun "
                         "ya da komuta yol verin.")
    labels = [f"{e.name}/ (klasör)" if e.is_dir() else e.name for e in entries]
    chosen = [entries[i] for i in ui.choose("Okunacak girdiler:", labels, "hepsi", multi=True)]

    jobs: list[pages.DocJob] = []
    warnings: list[str] = []
    images = [e for e in chosen if e.is_file() and e.suffix.lower() != ".pdf"]
    for entry in (e for e in chosen if e not in images):
        spec = _ask_pages(entry) if entry.is_file() else None
        j, w = pages.collect_jobs([entry], spec)
        jobs += j
        warnings += w
    if images:   # tek tek görüntüler komut satırındaki gibi tek belge olur
        j, w = pages.collect_jobs(images, None)
        jobs += j
        warnings += w
    pages.make_names_unique(jobs)

    ready = [name for name in engines.ENGINES if engines.missing(name) is None]
    if not ready:
        raise InputError("Hazır model yok; durumu görmek için: ocr-now models")
    options = [f"{n} ★" if engines.ENGINES[n].recommended else n for n in ready]
    default = str(ready.index(engines.DEFAULT_ENGINE) + 1) if engines.DEFAULT_ENGINE in ready else "1"
    args.model = ready[ui.choose("Model (★ önerilen):", options, default)[0]]
    modes = list(engines.ENGINES[args.model].modes)
    if len(modes) > 1:
        default = str(modes.index(engines.DEFAULT_MODE) + 1) if engines.DEFAULT_MODE in modes else "1"
        args.okuma = modes[ui.choose("Okuma modu:", [MODE_LABELS.get(m, m) for m in modes], default)[0]]
    else:
        args.okuma = modes[0]
    return jobs, warnings


# ---- sondaki sorular ----

def _ask_formats() -> list[str]:
    """Her biçim tek başına, sonra hepsi birden (iki biçimde "ikisi"); numarayla ya da adıyla seçilir."""
    names = list(EXPORTERS)
    choices = [(n, [n]) for n in names] + [("ikisi" if len(names) == 2 else "hepsi", names)]
    answers = {"hepsi": names} | {label: fmts for label, fmts in choices} \
        | {str(k): fmts for k, (_, fmts) in enumerate(choices, 1)}
    print("\nÇıktı biçimi:\n" + "\n".join(f"  {k}) {label}" for k, (label, _) in enumerate(choices, 1)))
    while True:
        answer = ui.ask("Seçim", "1").lower()
        if answer in answers:
            return answers[answer]
        print("  Geçersiz seçim, tekrar dene.")


def _ask_highlight() -> bool:
    while True:
        answer = ui.ask("Şüpheli kelimeler Word'de sarı işaretlensin mi? (e/h)", "e").lower()
        if answer in YES | NO:
            return answer in YES
        print("  Geçersiz cevap, e ya da h yaz.")


# ---- çalıştırma ----

def _free_stem(name: str, formats: list[str]) -> str:
    """Belgeler klasöründe bu biçimlerin hiçbiri yoksa name; varsa name_2, name_3 ... (eski belge ezilmez, biçimler
    aynı eki paylaşır)."""
    stem, k = name, 2
    while any((paths.DOCS_OUT / f"{stem}.{fmt}").exists(follow_symlinks=False) for fmt in formats):
        stem, k = f"{name}_{k}", k + 1
    return stem


def _write_job(name: str, doc_pages: list[Page], formats: list[str], settings: dict,
               warnings: list[str]) -> tuple[list[Path], list[str]]:
    written, errors = [], []

    def error(filename: str, exc: Exception) -> None:
        message = f"{filename} yazılamadı ({str(exc) or type(exc).__name__})"
        errors.append(message)
        _err(f"Hata: {message}")

    with ExitStack() as stack:
        try:
            stack.enter_context(paths.export_lock(paths.DOCS_OUT))
            stem = _free_stem(name, formats)
        except OSError as e:  # klasör açılamadı, kilit alınamadı veya ad seçilemedi
            for fmt in formats:
                error(f"{name}.{fmt}", e)
            return written, errors
        for fmt in formats:
            out = paths.DOCS_OUT / f"{stem}.{fmt}"
            try:
                EXPORTERS[fmt](doc_pages, out, {**settings, "warnings": warnings})
            except Exception as e:  # bir biçim ötekileri durdurmasın; Ctrl-C yine dışarı çıkar
                error(out.name, e)
            else:
                written.append(out)
    return written, errors


def _preview_text(doc_pages: list[Page], formats: list[str]) -> str:
    text = (export_md.render_md(doc_pages) if "md" in formats
            else export_txt.render_txt(doc_pages, page_breaks=len(doc_pages) > 1)).strip()
    if len(text) > PREVIEW_LIMIT:
        return text[:PREVIEW_LIMIT] + "\n…"
    return text


def _report(results: list[tuple[pages.DocJob, list[Page]]], written: dict[str, list[Path]],
            export_warnings: dict[str, list[str]], export_errors: dict[str, list[str]],
            formats: list[str] | None = None, *, rtl: bool = False) -> dict:
    """İş sonu özeti: gerçek çıktılar, sayfa denetimleri ve dışa aktarma hataları ayrı tutulur."""
    formats = formats or []
    jobs = []
    for job, doc_pages in results:
        multi = len({src.source for src in job.pages if src.source.suffix.lower() == ".pdf"}) > 1
        jobs.append({
            "ad": job.name,
            "yazildi": bool(written.get(job.name)),
            "tahmin": [export_txt.page_label(p) for p in doc_pages if p.book_page_inferred and not p.book_page_check],
            "kontrol": [export_txt.page_label(p) for p in doc_pages if p.book_page_check],
            "metin_kontrol": [pipeline.job_page_id(src, multi)
                              for src, p in zip(job.pages, doc_pages) if p.needs_review and not p.truncated],
            "kesilen": [pipeline.job_page_id(src, multi)
                        for src, p in zip(job.pages, doc_pages) if p.truncated],
            "yazma_hatalari": export_errors.get(job.name, []),
            "uyarilar": [w for p in doc_pages for w in p.warnings] + export_warnings.get(job.name, []),
            "onizleme": _preview_text(doc_pages, formats),
            "rtl": rtl,
        })
    return {"belgeler": [str(path) for files in written.values() for path in files], "isler": jobs,
            "okunamayan": any(p.failed for _, doc_pages in results for p in doc_pages)}


def _summary(report: dict) -> None:
    """Bitiş özetini (_report) terminale yazar."""
    print("\nYazılan belgeler:" if report["belgeler"] else "\nYazılan belge yok.")
    for path in report["belgeler"]:
        print(f"  {path}")
    for job in report["isler"]:
        if not job["yazildi"] and not job["yazma_hatalari"]:
            print(f"  {job['ad']}: hiçbir sayfa okunamadı, belge yazılmadı")
    for job in report["isler"]:
        for title, labels in (("metni kontrol edilmesi gereken sayfalar", job["metin_kontrol"]),
                              ("model çıktısı kesilen sayfalar", job["kesilen"]),
                              ("tahmin edilen sayfa numaraları", job["tahmin"]),
                              ("kontrol edilmesi gereken okunan numaralar", job["kontrol"])):
            if labels:
                print(f"\n{job['ad']}: {title}:")
                print(textwrap.fill(", ".join(labels), width=88, initial_indent="  ", subsequent_indent="  "))
        if job["uyarilar"]:
            print(f"\n{job['ad']}: uyarılar:")
            for w in job["uyarilar"]:
                print(f"  - {w}")


def _run(args: argparse.Namespace, ask_start: bool) -> int:
    interactive = sys.stdin.isatty()
    prof = profiles.get(args.profil)
    use_tesseract = prof.use_tesseract
    if (use_tesseract or args.model == "tesseract") and (reason := engines.missing("tesseract")):
        _err(f"Hata: tesseract kullanılamıyor: {reason} (satır yapısı ve sarı işaretleme için gerekli)\n"
             "Kurulum için: ocr-now models install tesseract")
        return 2
    if ask_start:
        jobs, warnings = _ask_start(args)
    else:
        inputs = [Path(x) for x in args.girdiler] or [paths.INPUT]
        jobs, warnings = pages.collect_jobs(inputs, args.sayfalar, args.birlestir)
    for w in warnings:
        print(f"Uyarı: {w}")
    if reason := engines.missing(args.model):
        _err(f"Hata: '{args.model}' modeli kullanılamıyor: {reason}\nKurulum için: ocr-now models install")
        return 2

    apostrophes = prof.apostrophes if args.kesme is None else args.kesme
    opts = pipeline.Options(engine=args.model, mode=args.okuma, apostrophes=apostrophes,
                            first_page=args.ilk_sayfa, dpi=args.dpi, use_tesseract=use_tesseract,
                            detect_pagenum=prof.detect_pagenum, cjk_check=prof.cjk_check, lang=args.dil)
    rt_kwargs = {}
    if args.dil:
        rt_kwargs["lang"] = args.dil
    if args.kraken_model:
        rt_kwargs["kraken_model"] = args.kraken_model
    runtime = identity.runtime(args.model, **rt_kwargs)
    profile = runtime.profile(args.okuma, prompt_override=prof.prompt)
    if not profile["cacheable"]:
        print("Uyarı: OCR ayar kimliği doğrulanamadı; ham OCR önbelleği kullanılmayacak.")
    server_opts = dict(runtime.server_options)
    if engines.ENGINES[args.model].kind == "api" and prof.prompt:
        server_opts["prompt_override"] = prof.prompt
    results = []
    try:
        with engines.lazy_server(args.model, **server_opts) as run:
            for job in jobs:
                results.append((job, pipeline.process_job(job, opts, run, progress=print, profile=profile)))
    except engines.ServerError as e:
        _err(f"Hata: {e}\nHiçbir belge yazılmadı.")
        return 1

    writable = [(job, doc_pages) for job, doc_pages in results   # boş belge yazılmaz, aynı adlı eski belge ezilmez
                if not all(p.failed for p in doc_pages)]
    formats = args.format or (_ask_formats() if interactive and writable else list(prof.formats))
    highlight = args.isaretle
    if highlight is None:
        highlight = _ask_highlight() if interactive and writable and "docx" in formats and prof.highlight else prof.highlight
    settings = {"highlight": highlight, "bold": args.kalin_kalip or None,
                "flowing": prof.flowing_docx, "rtl": prof.rtl}

    written: dict[str, list[Path]] = {}
    export_warnings: dict[str, list[str]] = {}
    export_errors: dict[str, list[str]] = {}
    for job, doc_pages in writable:
        files, errors = _write_job(job.name, doc_pages, formats, settings, export_warnings.setdefault(job.name, []))
        if files:
            written[job.name] = files
        if errors:
            export_errors[job.name] = errors
    report = _report(results, written, export_warnings, export_errors, formats, rtl=prof.rtl)
    _summary(report)
    incomplete = (report["okunamayan"] or bool(export_errors)
                  or any(j["metin_kontrol"] or j["kesilen"] for j in report["isler"]))
    if args.rapor:
        try:
            with paths.atomic_output(args.rapor) as tmp:
                tmp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError as e:
            _err(f"Hata: {args.rapor} yazılamadı ({e})")
            incomplete = True
    return 1 if incomplete else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "models":   # argparse'tan önce: alt komutun kendi seçenekleri var
        return importlib.import_module("ocrnow.models").main(argv[1:])
    args = parse_args(argv)
    try:
        return _run(args, ask_start=not argv and sys.stdin.isatty())
    except InputError as e:
        _err(f"Hata: {e}")
        return 2
    except (KeyboardInterrupt, EOFError):
        _err("\nİptal edildi.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
