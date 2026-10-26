"""Benchmark: seçili (motor, mod) çiftleri seçili sayfaları okur; ölçüler gt/<ad>_syf<N>.txt ile hesaplanır.

Çıktılar (out_dir altında): results.json, <motor>/<ad>_syf<N>.<mod>.txt (ham model çıktısı), <ad>_karsilastirma.md.
Ayrıca paths.LAB_OUT/summary.csv'ye satır eklenir (koşular arasında birikir).
"""
import csv
import json
import dataclasses
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path


from ocrnow import engines, pages, paths, pipeline
from ocrnow.apostrophe import build_word_list
from ocrnow.export_txt import plain_text
from ocrnow.lab import metrics
from ocrnow.layout import Page, parse_output
from ocrnow.pages import DEFAULT_DPI, SourcePage

SUMMARY_HEADER = ["engine", "pdf", "page", "seconds", "chars", "cer", "mode",
                  "cer_norm", "cer_body", "cer_base", "wer", "line_end", "apostrophe"]
METRICS = ("cer_raw", "cer_norm", "cer_body", "cer_base", "wer", "line_end", "apostrophe")


def new_run_dir(root: Path, now: datetime | None = None) -> Path:
    """root/YYYYmmdd-HHMM; varsa -2, -3 ... eklenir. Klasörü oluşturur."""
    name = (now or datetime.now()).strftime("%Y%m%d-%H%M")
    path, k = root / name, 2
    while True:
        try:
            path.mkdir(parents=True)
            return path
        except FileExistsError:
            path = root / f"{name}-{k}"
            k += 1


class _Capture:
    """run'ı sarar: ham çıktıyı tutar, yalnız model çağrısının süresini ölçer."""

    def __init__(self, run):
        self.run = run
        self.raw: str | None = None
        self.seconds: float | None = None

    def __call__(self, img: Path, mode: str) -> str:
        start = time.perf_counter()
        try:
            self.raw = self.run(img, mode)
        finally:
            self.seconds = time.perf_counter() - start
        return self.raw


def _error_text(error: Exception) -> str:
    return str(error) or type(error).__name__


def _raw_texts(raw: str, n: int) -> list[str | None] | None:
    """Model JSON'u geçerliyse her öğenin ham "text" değeri (blok içi satır sonları korunur); aksi hâlde None.

    layout.parse_output boşlukları tek satıra katlar; bu yüzden satır sonu tireleri (senâ-\nkâr) blok metninden
    birleştirilemez. JSON bozuk/kesikse (kurtarma yolu) ham satır sonları bilinemez: blok metni kullanılır ve
    tire birleştirme bu sayfada çalışmaz (bilinen sınır).
    """
    try:
        items = json.loads(re.sub(r"\s*```$", "", re.sub(r"^```[A-Za-z]*\s*", "", raw.strip())))
    except ValueError:
        return None
    if not (isinstance(items, list) and len(items) == n and all(isinstance(i, dict) for i in items)):
        return None
    return [i.get("text") if isinstance(i.get("text"), str) else None for i in items]


def model_text(raw: str, engine: str, mode: str, size: tuple[int, int]) -> str:
    """Ölçülen metin: düz modda ham çıktı. Düzen modunda blok metinleri boş satırla ayrılır; Picture blokları
    ([Görsel] yer tutucusu modelin metni değil) atlanır, Table düz metne çevrilir, blok içi satır sonları
    mümkünse modelin ham JSON'undan alınır."""
    if not engines.ENGINES[engine].modes[mode].layout:
        return raw
    blocks = parse_output(raw, True, size)[0]
    texts = _raw_texts(raw, len(blocks)) or [None] * len(blocks)
    parts = []
    for block, text in zip(blocks, texts):
        if block.category == "Picture":
            continue
        if text is not None:
            block = dataclasses.replace(block, text=text)
        if part := plain_text(block).strip():
            parts.append(part)
    return "\n\n".join(parts)


def _pipeline_lines(page: Page) -> list[str]:
    """Hattın ürettiği satırlar, eklenecek tireler dahil. Satır yapısı olmayan blok tek satırdır."""
    out = []
    for block in page.blocks:
        if block.lines:
            out += [block.text[ln.start:ln.end].strip() + ("-" if ln.hyphen else "") for ln in block.lines]
        elif text := plain_text(block):
            out.append(text)
    return out


def _words_without(gt_dir: Path, own: Path) -> dict[str, str]:
    """Kesme kelime listesi; ölçülen sayfanın kendi gt'si hariç (liste kendi cevaplarını içermesin)."""
    return build_word_list(gt_dir, paths.SETTINGS / "kesme.txt", exclude=own)


def _measure(gt: str, text: str, lines: list[str], page: Page) -> dict:
    """Ölçüler: CER'ler ve WER modelin metni üzerinde, satır sonu hat satırları üzerinde,
    kesme işareti hattın geri getirme sonrası metni üzerinde."""
    pipeline_text = "\n\n".join(b.text for b in page.blocks)
    accuracy, n = metrics.apostrophe_accuracy(gt, pipeline_text)
    return {"cer_raw": metrics.cer_raw(gt, text), "cer_norm": metrics.cer_norm(gt, text),
            "cer_body": metrics.cer_body(gt, text), "cer_base": metrics.cer_base(gt, text),
            "wer": metrics.wer(gt, text),
            "line_end": metrics.line_end_accuracy(gt, lines),
            "apostrophe": accuracy if n else None, "apostrophe_n": n,
            "word_errors": [list(e) for e in metrics.word_errors(gt, text)]}


def _empty_record(src: SourcePage, engine: str, mode: str) -> dict:
    return {"pdf": src.source.stem, "page": src.index, "engine": engine, "mode": mode, "seconds": None,
            "chars": None, "raw_path": None, **dict.fromkeys(METRICS), "apostrophe_n": None, "apostrophe_flagged": None,
            "word_errors": [], "lines": [], "warnings": [], "error": None}


def _fmt(value, digits: int = 4) -> str:
    return "" if value is None else str(round(value, digits))


def _summary_rows(records: list[dict]) -> list[list[str]]:
    return [[r["engine"], r["pdf"], str(r["page"]), _fmt(r["seconds"], 2), str(r["chars"]), _fmt(r["cer_raw"]),
             r["mode"], *(_fmt(r[k]) for k in ("cer_norm", "cer_body", "cer_base", "wer", "line_end", "apostrophe"))]
            for r in records if not r["error"]]


def _append_summary(records: list[dict]) -> Path:
    path = paths.LAB_OUT / "summary.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new:
            writer.writerow(SUMMARY_HEADER)
        writer.writerows(_summary_rows(records))
    return path


def write_comparison(path: Path, stem: str, targets: list[tuple[str, str]], records: list[dict],
                     texts: dict, images: dict) -> None:
    """Bugünkü karşılaştırma Markdown'u: her sayfa için sayfa görüntüsü, ölçü tablosu ve her çıktının metni."""
    show = lambda v: "—" if v is None else f"{v:.4f}"  # noqa: E731
    lines = [f"# {stem} — OCR karşılaştırması", "",
             f"{time.strftime('%Y-%m-%d %H:%M')} · {', '.join(f'{e}/{m}' for e, m in targets)}", "",
             "Süre yalnız model çağrısıdır (sayfa görüntüsü, tesseract ve hat işleme dahil değil). "
             "Düzen modunda model JSON'u bozuksa blok içi satır sonları korunamaz; satır sonu tireleri "
             "o sayfada CER norm'u etkiler.", ""]
    mine = [r for r in records if r["pdf"] == stem]
    for index in sorted({r["page"] for r in mine}):
        img = images.get((stem, index))
        lines += [f"## Sayfa {index}", ""]
        if img:
            lines += [f"![sayfa {index}]({Path(os.path.relpath(img, path.parent)).as_posix()})", ""]
        lines += ["| Motor/mod | Model süresi (sn) | Karakter | CER | CER norm | WER | Satır sonu | Kesme | Sarı kesme |",
                  "|---|---|---|---|---|---|---|---|---|"]
        rows = [r for r in mine if r["page"] == index]
        for r in rows:
            name = f"{r['engine']}/{r['mode']}"
            if r["error"]:
                lines.append(f"| {name} | — | — | HATA | | | | | |")
            else:
                lines.append(f"| {name} | {r['seconds']:.1f} | {r['chars']} | {show(r['cer_raw'])} | "
                             f"{show(r['cer_norm'])} | {show(r['wer'])} | {show(r['line_end'])} | {show(r['apostrophe'])} | "
                             f"{'—' if r['apostrophe_flagged'] is None else r['apostrophe_flagged']} |")
        for r in rows:
            body = f"HATA: {r['error']}" if r["error"] else texts[stem, index, r["engine"], r["mode"]].strip()
            lines += ["", f"### {r['engine']}/{r['mode']}", "", "````text", body, "````"]
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _check_targets(targets: list[tuple[str, str]]) -> None:
    for engine, mode in targets:
        if engine not in engines.ENGINES:
            raise ValueError(f"bilinmeyen motor: {engine}")
        if mode not in engines.ENGINES[engine].modes:
            raise ValueError(f"{engine} motorunda '{mode}' modu yok (olanlar: {', '.join(engines.ENGINES[engine].modes)})")


def run_bench(source_pages: list[SourcePage], targets: list[tuple[str, str]], gt_dir: Path, out_dir: Path,
              *, dpi: int = DEFAULT_DPI) -> Path:
    """Her motor için tek sunucu açılır (sırayla, tek GPU işi); motorun seçili modları ve sayfaları sırayla okunur.

    Motor açılamazsa ya da bir sayfa hata verirse o kayıt `error` taşır ve iş sürer. results.json yolunu döndürür;
    Ctrl-C gelse de o ana kadarki sonuçlar yazılır.
    """
    _check_targets(targets)
    targets = list(dict.fromkeys(targets))
    gt_dir, out_dir = Path(gt_dir), Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    by_engine: dict[str, list[str]] = {}
    for engine, mode in targets:
        by_engine.setdefault(engine, []).append(mode)

    records: list[dict] = []
    texts: dict = {}
    images: dict = {}
    memo_words: dict = {}
    memo_tsv: dict = {}

    def tsv(img: Path) -> str:   # aynı sayfa her hedefte yeniden taranmasın
        if img not in memo_tsv:
            memo_tsv[img] = engines.tesseract_tsv(img)
        return memo_tsv[img]

    def one(run, engine: str, mode: str, src: SourcePage) -> None:
        rec = _empty_record(src, engine, mode)
        cap = _Capture(run)
        key = (src.source, src.index)
        try:
            if key not in images:
                images[key] = pages.render(src, dpi)
            img = images[key]
            gt_path = gt_dir / f"{rec['pdf']}_syf{src.index}.txt"
            wkey = gt_path.name if gt_path.exists() else None
            if wkey not in memo_words:
                memo_words[wkey] = _words_without(gt_dir, gt_path)
            page = pipeline.process_page(img, src, pipeline.Options(engine, mode, True, dpi=dpi), cap, tsv,
                                         words=memo_words[wkey])
            text = model_text(cap.raw, engine, mode, (page.width, page.height))
            lines = _pipeline_lines(page)
            raw_path = out_dir / engine / f"{rec['pdf']}_syf{src.index}.{mode}.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(cap.raw, encoding="utf-8")
            rec.update(seconds=cap.seconds, chars=len(text), raw_path=str(raw_path), lines=lines,
                       warnings=page.warnings,
                       apostrophe_flagged=sum(m.kind == "kesme" for b in page.blocks for m in b.marks))
            texts[rec["pdf"], src.index, engine, mode] = text
            if gt_path.exists():
                rec.update(_measure(gt_path.read_text(encoding="utf-8"), text, lines, page))
        except Exception as e:
            rec.clear()   # yarım dolmuş alan kalmasın
            rec.update(_empty_record(src, engine, mode), seconds=cap.seconds, error=_error_text(e))
        records.append(rec)   # Ctrl-C ile yarıda kalan sayfa kayda girmez
        print(f"  {rec['pdf']} syf{src.index}  {engine}/{mode}  "
              + (f"HATA: {rec['error']}" if rec["error"] else
                 f"{rec['seconds']:.1f}s  {rec['chars']} kr  cer {_fmt(rec['cer_raw']) or '—'}"), flush=True)

    try:
        for engine, modes in by_engine.items():
            print(f"\n=== {engine} ===", flush=True)
            try:
                with engines.server(engine) as run:
                    for mode in modes:
                        for src in source_pages:
                            one(run, engine, mode, src)
            except Exception as e:   # sunucu açılamadı (ya da kapanırken hata): eksik kayıtlar hata taşır
                print(f"[{engine}] HATA: {_error_text(e)}", file=sys.stderr)
                done = {(r["engine"], r["mode"], r["pdf"], r["page"]) for r in records}
                for mode in modes:
                    for src in source_pages:
                        if (engine, mode, src.source.stem, src.index) not in done:
                            rec = _empty_record(src, engine, mode)
                            rec["error"] = _error_text(e)
                            records.append(rec)
    finally:
        results = out_dir / "results.json"
        results.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
        _append_summary(records)
        for stem in dict.fromkeys(r["pdf"] for r in records):
            write_comparison(out_dir / f"{stem}_karsilastirma.md", stem, targets, records, texts,
                             {(s.source.stem, s.index): images[s.source, s.index] for s in source_pages
                              if (s.source, s.index) in images})
    return results
