"""`ocr-lab` komutu: motor/mod çiftlerini doğru metinle karşılaştırır; `ocr-lab rapor` sonuçları gösterir."""
import argparse
import importlib
import json
import sys
from pathlib import Path

from ocrnow import engines, pages, paths, ui
from ocrnow.lab import bench
from ocrnow.pages import DEFAULT_DPI, InputError

DEFAULT_PAGES = "1"

EPILOG = """alt komut:
  ocr-lab rapor [--html]   önceki koşuların sonuçlarını notebook'ta gösterir (--html: statik sayfaya çevirir)
  (ilk argüman olarak 'rapor' alt komut sayılır; 'rapor' adlı bir dosya için ./rapor yazın)

-e'den sonra PDF yazılırsa motor sanılır: PDF'leri -e'den önce yazın."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: hata: {message}\n")


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"tam sayı olmalı: '{text}'") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"1 ya da daha büyük olmalı: {value}")
    return value


def _build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="ocr-lab", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
                description="Motorları ve okuma modlarını gt/<pdf>_syf<N>.txt doğru metinleriyle karşılaştırır. "
                            "Argümansız çalıştırılırsa adım adım sorar.")
    p.add_argument("pdfler", nargs="*", type=Path, help="PDF'ler (varsayılan: input/*.pdf)")
    p.add_argument("-e", "--motorlar", nargs="+", metavar="MOTOR[:MOD]",
                   help="test edilecek motorlar, ör. dots.mocr:duzen glm-ocr; mod yazılmazsa motorun bütün modları "
                        f"(varsayılan: hazır olan bütün motorlar; olanlar: {', '.join(engines.ENGINES)})")
    p.add_argument("-p", "--sayfalar", default=DEFAULT_PAGES,
                   help="sayfa seçimi: hepsi, 5, 1,3,7, 10-25, ilk10 (varsayılan: %(default)s)")
    p.add_argument("--dpi", type=_positive_int, default=DEFAULT_DPI, help="PDF görüntüleme çözünürlüğü (varsayılan: %(default)s)")
    return p


def parse_targets(specs: list[str]) -> list[tuple[str, str]]:
    """'dots.mocr:duzen glm-ocr' → [(dots.mocr, duzen), (glm-ocr, duz)]; mod yoksa motorun bütün modları."""
    targets: list[tuple[str, str]] = []
    for spec in specs:
        name, _, mode = spec.partition(":")
        if name not in engines.ENGINES:
            raise InputError(f"bilinmeyen motor '{name}' (olanlar: {', '.join(engines.ENGINES)})")
        modes = engines.ENGINES[name].modes
        if mode and mode not in modes:
            raise InputError(f"{name} motorunda '{mode}' modu yok (olanlar: {', '.join(modes)})")
        targets += [(name, m) for m in ([mode] if mode else modes)]
    return list(dict.fromkeys(targets))


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def _ready() -> dict[str, str | None]:
    """Her motorun durumu (None → hazır); satırlar ekrana yazılır."""
    status = {name: engines.missing(name) for name in engines.ENGINES}
    for name, why in status.items():
        print(f"  {'✓' if not why else '✗'} {name:14} {why or 'hazır'}")
    return status


def _select_targets(args: argparse.Namespace, status: dict[str, str | None]) -> list[tuple[str, str]]:
    ready = [name for name, why in status.items() if not why]
    if args.motorlar:
        targets = parse_targets(args.motorlar)
        for name in dict.fromkeys(n for n, _ in targets):
            if status[name]:
                raise InputError(f"'{name}' motoru kullanılamıyor: {status[name]}")
        return targets
    if not ready:
        raise InputError("Hazır motor yok; kurulum için: ocr-now models install")
    return parse_targets(ready)


# ---- etkileşimli başlangıç ----

def _ask_start(args: argparse.Namespace, status: dict[str, str | None]) -> None:
    """PDF'leri, sayfaları ve motor/mod çiftlerini sorar; seçimi args'a yazar."""
    entries = sorted(paths.INPUT.glob("*.pdf")) if paths.INPUT.is_dir() else []
    if not entries:
        raise InputError(f"{paths.INPUT} içinde PDF yok; dosyalarınızı oraya koyun ya da komuta yol verin.")
    chosen = entries if len(entries) == 1 else \
        [entries[i] for i in ui.choose("PDF:", [e.name for e in entries], "hepsi", multi=True)]
    counts = ", ".join(f"{p.stem}: {pages.pdf_page_count(p)} syf" for p in chosen)
    while True:
        spec = ui.ask(f"\nSayfalar — ör. 1-3,7 ya da hepsi ({counts})", DEFAULT_PAGES)
        try:
            pages.collect_jobs(chosen, spec)
            break
        except InputError as e:
            print(f"  {e}")
    ready = [name for name, why in status.items() if not why]
    if not ready:
        raise InputError("Hazır motor yok; kurulum için: ocr-now models install")
    options = [f"{n}:{m}" for n in ready for m in engines.ENGINES[n].modes]
    args.pdfler, args.sayfalar = chosen, spec
    args.motorlar = [options[i] for i in ui.choose("Motor ve modlar:", options, "hepsi", multi=True)]


# ---- çalıştırma ve özet ----

def _num(value, digits: int = 3) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def print_table(records: list[dict]) -> None:
    """Başarılı kayıtlar tablo olur (ölçü yoksa —); hatalılar tablonun altında tek satır."""
    head = ["motor/mod", "sayfa", "süre", "kr", "cer", "norm", "gövde", "harf", "wer", "satır", "kesme", "sarı"]
    rows = [[f"{r['engine']}/{r['mode']}", f"{r['pdf']} s.{r['page']}", _num(r["seconds"], 1), str(r["chars"]),
             *(_num(r[k]) for k in ("cer_raw", "cer_norm", "cer_body", "cer_base", "wer", "line_end", "apostrophe")),
             "—" if r["apostrophe_flagged"] is None else str(r["apostrophe_flagged"])]
            for r in records if not r["error"]]
    widths = [max(len(row[i]) for row in [head, *rows]) for i in range(len(head))]
    print()
    if rows:
        for row in [head, *rows]:
            print("  " + "  ".join(c.ljust(w) if i < 2 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths))))
    for r in records:
        if r["error"]:
            print(f"  HATA  {r['engine']}/{r['mode']}  {r['pdf']} s.{r['page']}: {r['error']}")


def _run(args: argparse.Namespace, ask_start: bool) -> int:
    status = _ready()
    if status["tesseract"]:   # hat her sayfada tesseract satırlarını kullanır (satır yapısı, sarı işaret)
        raise InputError(f"tesseract kullanılamıyor: {status['tesseract']} (satır yapısı ölçüsü için gerekli). "
                         "Kurulum için: ocr-now models install tesseract")
    if ask_start:
        _ask_start(args, status)
    targets = _select_targets(args, status)
    inputs = args.pdfler or sorted(paths.INPUT.glob("*.pdf"))
    if not inputs:
        raise InputError(f"{paths.INPUT} içinde PDF yok.")
    jobs, warnings = pages.collect_jobs(list(inputs), args.sayfalar)
    for w in warnings:
        print(f"Uyarı: {w}")
    source_pages = [p for job in jobs for p in job.pages]
    print(f"\n▶ {', '.join(j.name for j in jobs)} · sayfa {args.sayfalar} · "
          f"{', '.join(f'{e}/{m}' for e, m in targets)}")

    out_dir = bench.new_run_dir(paths.LAB_OUT)
    results = bench.run_bench(source_pages, targets, paths.GT, out_dir, dpi=args.dpi)
    records = json.loads(results.read_text(encoding="utf-8"))
    print_table(records)
    print(f"\nSonuçlar: {results}")
    for md in sorted(out_dir.glob("*_karsilastirma.md")):
        print(f"Karşılaştırma: {md}")
    print(f"Özet: {paths.LAB_OUT / 'summary.csv'}")
    return 1 if any(r["error"] for r in records) else 0


def rapor(html: bool) -> int:
    """Önceki koşuların sonuçlarını notebook'ta açar (html: statik sayfaya çevirir). notebook ilk kullanımda yüklenir."""
    try:
        notebook = importlib.import_module("ocrnow.lab.notebook")
    except ImportError as e:
        _err(f"Hata: rapor açılamadı ({e}). Kurulum: uv pip install -e '.[lab]'")
        return 2
    return notebook.rapor(html)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "rapor":   # argparse'tan önce: alt komutun kendi seçeneği var
        sub = _Parser(prog="ocr-lab rapor", description="Önceki koşuların sonuçlarını notebook'ta gösterir.")
        sub.add_argument("--html", action="store_true", help="statik HTML sayfasına çevir")
        return rapor(sub.parse_args(argv[1:]).html)
    args = _build_parser().parse_args(argv)
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
