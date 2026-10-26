"""`ocr-now` komutu: taranmış sayfaları .docx/.txt'ye çevirir; argümansız açılınca soru sorar."""
import argparse
import importlib
import re
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path

from ocrnow import engines, export_docx, export_txt, pages, paths, pipeline, ui
from ocrnow.layout import Page
from ocrnow.pages import DEFAULT_DPI, InputError

# Dışa aktarıcılar: biçim → yaz(sayfalar, çıktı yolu, ayarlar). Yeni biçim yalnız buraya eklenir; -f, sondaki soru ve
# yazma döngüsü buradan okur. ayarlar: highlight, bold (kalın kalıbı ya da None), warnings (eklenecek uyarı listesi).
EXPORTERS: dict[str, Callable[[list[Page], Path, dict], object]] = {
    "docx": lambda doc_pages, out, settings: export_docx.write_docx(
        doc_pages, out, highlight=settings["highlight"], bold_pattern=settings["bold"], warnings=settings["warnings"]),
    "txt": lambda doc_pages, out, settings: export_txt.write_txt(doc_pages, out),
}
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
                description="Taranmış kitap sayfalarını Word (.docx) ya da düz metne (.txt) çevirir. "
                            "Argümansız çalıştırılırsa adım adım sorar.")
    p.add_argument("girdiler", nargs="*", help="PDF, görüntü ya da klasör (varsayılan: input/ klasörü)")
    p.add_argument("-p", "--sayfalar", help="sayfa seçimi: hepsi, 5, 1,3,7, 10-25, ilk10 (varsayılan: hepsi)")
    p.add_argument("-m", "--model", default=engines.DEFAULT_ENGINE,
                   help=f"motor: {', '.join(engines.ENGINES)} (varsayılan: %(default)s)")
    p.add_argument("-o", "--okuma", default=engines.DEFAULT_MODE, action=_Explicit,
                   help="okuma modu: duz ya da duzen (varsayılan: %(default)s; model desteklemiyorsa duz)")
    p.add_argument("-f", "--format", nargs="+",
                   help=f"çıktı biçimi: {', '.join(EXPORTERS)} ya da birkaçı (verilmezse sonda sorulur)")
    p.add_argument("--isaretle", dest="isaretle", action="store_true", default=None,
                   help="şüpheli kelimeleri sarı işaretle (verilmezse sonda sorulur)")
    p.add_argument("--isaretleme", dest="isaretle", action="store_false", default=None,
                   help="sarı işaretleme yapma")
    p.add_argument("--kesme-yok", action="store_true", help="ayın/hemze (kesme işareti) adımını kapat")
    p.add_argument("--kalin-kalip", default=export_docx.BOLD_DEFAULT, metavar="KALIP",
                   help="kalın yazılacak metnin düzenli ifadesi; boş ('') kalın yapmaz (varsayılan: %(default)s)")
    p.add_argument("--ilk-sayfa", type=_positive_int, help="ilk sayfanın kitap sayfa numarası")
    p.add_argument("--birlestir", action="store_true", help="bütün girdileri tek belgede birleştir")
    p.add_argument("--dpi", type=_positive_int, default=DEFAULT_DPI, help="PDF görüntüleme çözünürlüğü (varsayılan: %(default)s)")
    p.set_defaults(okuma_verildi=False)
    return p


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)
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
    while any((paths.DOCS_OUT / f"{stem}.{fmt}").exists() for fmt in formats):
        stem, k = f"{name}_{k}", k + 1
    return stem

def _summary(results: list[tuple[pages.DocJob, list]], written: list[Path],
             export_warnings: dict[str, list[str]]) -> bool:
    """Bitiş özetini yazar (uyarılar: sayfalarınki, sonra dışa aktarmanınki); en az bir sayfa okunamadıysa True."""
    print("\nYazılan belgeler:" if written else "\nYazılan belge yok.")
    for path in written:
        print(f"  {path}")
    for job, doc_pages in results:
        if all(p.failed for p in doc_pages):
            print(f"  {job.name}: hiçbir sayfa okunamadı, belge yazılmadı")
    failed = False
    for job, doc_pages in results:
        guessed = [export_txt.page_label(p) for p in doc_pages if p.book_page_inferred]
        if guessed:
            print(f"\n{job.name}: tahmin edilen sayfa numaraları:")
            print(textwrap.fill(", ".join(guessed), width=88, initial_indent="  ", subsequent_indent="  "))
        warns = [w for p in doc_pages for w in p.warnings] + export_warnings.get(job.name, [])
        failed = failed or any(p.failed for p in doc_pages)
        if warns:
            print(f"\n{job.name}: uyarılar:")
            for w in warns:
                print(f"  - {w}")
    return failed


def _run(args: argparse.Namespace, ask_start: bool) -> int:
    interactive = sys.stdin.isatty()
    if reason := engines.missing("tesseract"):   # her sayfada satır yapısı ve sarı işaret için gerekir
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

    opts = pipeline.Options(engine=args.model, mode=args.okuma, apostrophes=not args.kesme_yok,
                            first_page=args.ilk_sayfa, dpi=args.dpi)
    results = []
    try:
        with engines.lazy_server(args.model) as run:   # model ilk okunan sayfada bir kez açılır; hepsi önbellekteyse hiç
            for job in jobs:
                results.append((job, pipeline.process_job(job, opts, run, progress=print)))
    except engines.ServerError as e:
        _err(f"Hata: {e}\nHiçbir belge yazılmadı.")
        return 1

    writable = [(job, doc_pages) for job, doc_pages in results   # boş belge yazılmaz, aynı adlı eski belge ezilmez
                if not all(p.failed for p in doc_pages)]
    formats = args.format or (_ask_formats() if interactive and writable else ["docx"])
    highlight = args.isaretle
    if highlight is None:
        highlight = _ask_highlight() if interactive and writable and "docx" in formats else True
    settings = {"highlight": highlight, "bold": args.kalin_kalip or None}   # boş kalıp kalın işaretlemeyi kapatır

    written: list[Path] = []
    export_warnings: dict[str, list[str]] = {}
    write_failed = False
    for job, doc_pages in writable:
        stem = _free_stem(job.name, formats)
        for fmt in formats:
            out = paths.DOCS_OUT / f"{stem}.{fmt}"
            try:
                EXPORTERS[fmt](doc_pages, out, {**settings, "warnings": export_warnings.setdefault(job.name, [])})
            except OSError as e:
                _err(f"Hata: {out} yazılamadı ({e})")
                write_failed = True
            else:
                written.append(out)
    read_failed = _summary(results, written, export_warnings)
    return 1 if read_failed or write_failed else 0


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
