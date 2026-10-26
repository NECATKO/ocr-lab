"""marimo analiz notebook'u: paths.LAB_OUT/<koşu>/results.json dosyalarını okur.

Çalıştırma: `ocr-lab rapor` (marimo edit) ya da `ocr-lab rapor --html` (statik sayfa).
Dosyanın üst düzey işlevleri (`load_runs`, `summarize`, ...) hücrelerden bağımsız olarak testlenir.
"""
import marimo

__generated_with = "0.25.2"
app = marimo.App(width="medium")

with app.setup:
    import html
    import json
    import re
    import subprocess
    import sys
    from pathlib import Path

    import marimo as mo

    from ocrnow import paths
    from ocrnow.lab import bench

    # (alan, başlık, yüksek olan iyi mi, yüzde mi)
    COLUMNS = [("seconds", "Süre (sn)", False, False),
               ("cer_raw", "Ham CER", False, True), ("cer_norm", "Normalize CER", False, True),
               ("cer_body", "Gövde CER", False, True), ("cer_base", "Harf CER", False, True),
               ("wer", "WER", False, True), ("line_end", "Satır sonu doğr.", True, True),
               ("apostrophe", "Ayın/hemze doğr.", True, True)]
    AXES = {field: title for field, title, _, _ in COLUMNS}

    def fmt(value: float | None, percent: bool) -> str:
        if value is None:
            return "—"
        return f"{value * 100:.2f}%" if percent else f"{value:.1f}"

    PALETTE = ["#2563eb", "#dc2626", "#16a34a", "#d97706", "#7c3aed", "#0891b2", "#db2777", "#6b7280"]

    def nice_max(value: float) -> float:
        """Eksen üst sınırı: 1, 2, 5 × 10^k."""
        if value <= 0:
            return 1.0
        scale = 10 ** (len(str(int(value))) - 1) if value >= 1 else 10 ** -(len(str(int(1 / value))))
        for step in (1, 2, 5, 10):
            if value <= step * scale:
                return step * scale
        return 10 * scale


@app.function
def load_runs(lab_dir: Path) -> list[dict]:
    """lab_dir/*/results.json kayıtları düz liste olarak, eskiden yeniye; her kayda `run` (klasör adı) eklenir.

    Klasör yoksa ya da results.json okunamıyorsa o koşu atlanır (notebook boş sonuçla da açılabilmeli)."""
    records: list[dict] = []
    if not lab_dir.is_dir():
        return records
    for run_dir in sorted(p for p in lab_dir.iterdir() if p.is_dir()):
        try:
            data = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            records += [{**r, "run": run_dir.name} for r in data if isinstance(r, dict)]
    return records


@app.function
def summarize(records: list[dict]) -> list[dict]:
    """Motor/mod başına satır: hatasız kayıtların ölçü ortalamaları (None olanlar ortalamaya girmez),
    n = hatasız kayıt sayısı, errors = hatalı kayıt sayısı."""
    groups: dict[str, list[dict]] = {}
    for r in records:
        groups.setdefault(f"{r['engine']}/{r['mode']}", []).append(r)
    rows = []
    for key, group in sorted(groups.items()):
        ok = [r for r in group if not r.get("error")]
        row = {"key": key, "engine": group[0]["engine"], "mode": group[0]["mode"],
               "n": len(ok), "errors": len(group) - len(ok)}
        for field, *_ in COLUMNS:
            values = [r[field] for r in ok if r.get(field) is not None]
            row[field] = sum(values) / len(values) if values else None
        rows.append(row)
    return rows


@app.function
def best_cells(rows: list[dict]) -> dict[str, set[str]]:
    """Her ölçü sütununun en iyi satır anahtarları (eşitlikte hepsi); ölçüsü olmayan sütun boş küme."""
    best: dict[str, set[str]] = {}
    for field, _, higher_better, _ in COLUMNS:
        known = [(r[field], r["key"]) for r in rows if r[field] is not None]
        top = (max if higher_better else min)((v for v, _ in known), default=None)
        best[field] = {k for v, k in known if v == top}
    return best


@app.function
def metrics_table(rows: list[dict]) -> str:
    """Ölçü tablosu (HTML): en iyi hücre kalın ve yeşil zeminli. Açık/koyu temada okunur (renk devralınır)."""
    best = best_cells(rows)
    head = "".join(f"<th>{html.escape(t)}</th>" for t in ["Motor/mod", "Sayfa", "Hata"] + [c[1] for c in COLUMNS])
    body = []
    for r in rows:
        cells = [f"<td>{html.escape(r['key'])}</td>", f"<td>{r['n']}</td>", f"<td>{r['errors'] or ''}</td>"]
        for field, _, _, percent in COLUMNS:
            mark = ' style="font-weight:700;background:rgba(34,160,90,.28)"' if r["key"] in best[field] else ""
            cells.append(f"<td{mark}>{fmt(r[field], percent)}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    return ('<table class="ocr-ozet" style="border-collapse:collapse;font-size:.9em;font-variant-numeric:tabular-nums">'
            f"<thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"
            "<style>.ocr-ozet th,.ocr-ozet td{padding:.25em .7em;text-align:right;border-bottom:1px solid rgba(128,128,128,.3)}"
            ".ocr-ozet th:first-child,.ocr-ozet td:first-child{text-align:left}</style>")


@app.function
def scatter_svg(rows: list[dict], field: str) -> str:
    """Süre (x) ve seçilen ölçü (y) dağılım grafiği, SVG metni; çizilecek nokta yoksa ''.

    Renk motoru, işaret (daire/kare/üçgen) modu gösterir; metin renkleri temadan gelir (currentColor)."""
    points = [r for r in rows if r["seconds"] is not None and r[field] is not None]
    if not points:
        return ""
    percent = dict((f, p) for f, _, _, p in COLUMNS)[field]
    w, h, left, right, top, bottom = 680, 380, 64, 150, 16, 48
    xmax, ymax = nice_max(max(r["seconds"] for r in points)), nice_max(max(r[field] for r in points))
    px = lambda v: left + v / xmax * (w - left - right)
    py = lambda v: top + (1 - v / ymax) * (h - top - bottom)
    engines_ = list(dict.fromkeys(r["engine"] for r in rows))
    modes = list(dict.fromkeys(r["mode"] for r in rows))
    out = [f'<svg viewBox="0 0 {w} {h}" width="100%" style="max-width:{w}px" role="img" '
           f'aria-label="{html.escape(AXES[field])} ve süre dağılımı" font-size="12" font-family="inherit">']
    for i in range(5):   # ızgara ve eksen yazıları
        gx, gy = xmax * i / 4, ymax * i / 4
        ylabel = f"{gy * 100:g}%" if percent else f"{gy:g}"
        out.append(f'<line x1="{px(gx):.1f}" x2="{px(gx):.1f}" y1="{top}" y2="{h - bottom}" stroke="currentColor" opacity=".12"/>'
                   f'<line x1="{left}" x2="{w - right}" y1="{py(gy):.1f}" y2="{py(gy):.1f}" stroke="currentColor" opacity=".12"/>'
                   f'<text x="{px(gx):.1f}" y="{h - bottom + 16}" text-anchor="middle" fill="currentColor">{gx:g}</text>'
                   f'<text x="{left - 8}" y="{py(gy) + 4:.1f}" text-anchor="end" fill="currentColor">{ylabel}</text>')
    out.append(f'<text x="{(left + w - right) / 2}" y="{h - 8}" text-anchor="middle" fill="currentColor">Süre (sn / sayfa)</text>'
               f'<text transform="translate(14 {(top + h - bottom) / 2}) rotate(-90)" text-anchor="middle" '
               f'fill="currentColor">{html.escape(AXES[field])}</text>')
    for r in points:
        color = PALETTE[engines_.index(r["engine"]) % len(PALETTE)]
        x, y, shape = px(r["seconds"]), py(r[field]), modes.index(r["mode"]) % 3
        mark = (f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6"' if shape == 0 else
                f'<rect x="{x - 6:.1f}" y="{y - 6:.1f}" width="12" height="12"' if shape == 1 else
                f'<path d="M{x:.1f} {y - 7:.1f} L{x + 7:.1f} {y + 6:.1f} L{x - 7:.1f} {y + 6:.1f} Z"')
        tip = f"{r['key']}: {fmt(r[field], percent)}, {r['seconds']:.1f} sn"
        out.append(f'<g>{mark} fill="{color}" stroke="canvas" stroke-width="1.5"><title>{html.escape(tip)}</title>'
                   f'{"</circle>" if shape == 0 else "</rect>" if shape == 1 else "</path>"}'
                   f'<text x="{x + 10:.1f}" y="{y + 4:.1f}" fill="currentColor">{html.escape(r["key"])}</text></g>')
    out.append("</svg>")
    return "".join(out)


@app.function
def find_page_image(pdf: str, page: int) -> Path | None:
    """paths.PAGES/<pdf>-<bayt>/syf<N>.png (dpi 200 dışında syf<N>_<dpi>dpi.png); birden çoksa en yenisi.

    Kayıtlar kaynak yolunu tutmaz; klasör adı <pdf>-<sayı> kalıbıyla eşleşir (başka bir '<pdf>-2' PDF'i karışmaz)."""
    if not paths.PAGES.is_dir():
        return None
    folder = re.compile(re.escape(pdf) + r"-\d+")
    found = [f for d in paths.PAGES.iterdir() if d.is_dir() and folder.fullmatch(d.name)
             for f in d.glob(f"syf{page}*.png") if re.fullmatch(rf"syf{page}(_\d+dpi)?\.png", f.name)]
    return max(found, key=lambda f: f.stat().st_mtime, default=None)


@app.function
def record_text(rec: dict, size: tuple[int, int] | None) -> str | None:
    """Kaydın ölçülen model metni (bench.model_text); ham dosya yoksa/okunamazsa None.

    size sayfa görüntüsünün pikseli; yalnız kutu ölçeklemesinde kullanılır, metni değiştirmez."""
    if not rec.get("raw_path"):
        return None
    try:
        raw = Path(rec["raw_path"]).read_text(encoding="utf-8")
    except OSError:
        return None
    return bench.model_text(raw, rec["engine"], rec["mode"], size or (1, 1))


@app.function
def pick_record(records: list[dict], key: str, pdf: str, page: int) -> dict | None:
    """Seçili koşulların en yenisindeki kayıt (records eskiden yeniye sıralı)."""
    match = [r for r in records if f"{r['engine']}/{r['mode']}" == key and r["pdf"] == pdf and r["page"] == page]
    return match[-1] if match else None


@app.function
def rapor(html_out: bool) -> int:
    """`marimo edit` ya da `marimo export html` çalıştırır (venv'in marimo'su); çıkış kodunu döndürür."""
    cmd = [sys.executable, "-m", "marimo"]
    if html_out:
        paths.LAB_OUT.mkdir(parents=True, exist_ok=True)
        cmd += ["export", "html", __file__, "-o", str(paths.LAB_OUT / "rapor.html"), "-f"]
    else:
        cmd += ["edit", __file__]
    try:
        return subprocess.run(cmd).returncode
    except KeyboardInterrupt:
        return 130


@app.cell
def _():
    runs = load_runs(paths.LAB_OUT)
    mo.stop(not runs, mo.md(
        f"# OCR benchmark raporu\n\n**Henüz sonuç yok.** `{paths.LAB_OUT}` altında `results.json` bulunamadı. "
        "Önce `ocr-lab` ile bir benchmark çalıştırın."))
    run_names = list(dict.fromkeys(r["run"] for r in runs))
    run_select = mo.ui.multiselect(options=run_names, value=run_names[-1:], label="Çalışmalar")
    mo.vstack([mo.md("# OCR benchmark raporu"), run_select])
    return runs, run_select


@app.cell
def _(run_select, runs):
    selected = [r for r in runs if r["run"] in run_select.value]
    mo.stop(not selected, mo.md("Yukarıdan en az bir çalışma seçin."))
    rows = summarize(selected)
    mo.vstack([mo.md("## Ölçüler\n\nMotor/mod başına sayfa ortalaması; en iyi değer vurgulu. "
                     "Hatalı sayfalar ortalamaya girmez, 'Hata' sütununda sayılır."),
               mo.Html(metrics_table(rows))])
    return rows, selected


@app.cell
def _(rows):
    axis_select = mo.ui.dropdown(options={t: f for f, t in AXES.items() if f != "seconds"}, value="Normalize CER",
                                 label="Dikey eksen")
    axis_select
    return (axis_select,)


@app.cell
def _(axis_select, rows):
    svg = scatter_svg(rows, axis_select.value)
    mo.vstack([mo.md("## Doğruluk ve süre"), mo.Html(svg) if svg else
               mo.md("Çizilecek nokta yok: seçili ölçü ya da süre hiçbir motor/modda yok.")])
    return


@app.cell
def _(rows, selected):
    key_select = mo.ui.dropdown(options=[r["key"] for r in rows], value=rows[0]["key"], label="Motor/mod")
    pages_of = {k: sorted({(r["pdf"], r["page"]) for r in selected if f"{r['engine']}/{r['mode']}" == k})
                for k in (r["key"] for r in rows)}
    return key_select, pages_of


@app.cell
def _(key_select, pages_of):
    page_options = [f"{pdf} s.{page}" for pdf, page in pages_of[key_select.value]]
    page_select = mo.ui.dropdown(options=page_options, value=page_options[0], label="Sayfa")
    mo.vstack([mo.md("## Kelime hataları ve sayfa"), mo.hstack([key_select, page_select], justify="start")])
    return page_options, page_select


@app.cell
def _(key_select, page_options, page_select, pages_of, selected):
    pdf, page = pages_of[key_select.value][page_options.index(page_select.value)]
    rec = pick_record(selected, key_select.value, pdf, page)
    image_path = find_page_image(pdf, page)
    parts = [mo.md(f"Koşu: `{rec['run']}`")]
    if rec["error"]:
        parts.append(mo.callout(f"Bu sayfa hata verdi: {rec['error']}", kind="danger"))
    parts += [mo.callout(w, kind="warn") for w in rec["warnings"]]
    pairs = rec["word_errors"]
    if pairs:
        parts.append(mo.ui.table([{"doğru metin (gt)": gt, "çıktı": out} for gt, out in pairs], selection=None,
                                 label=f"{len(pairs)} kelime hatası"))
    else:
        parts.append(mo.md("Kelime hatası yok ya da bu sayfa için doğru metin (`gt/`) bulunamadı."))
    size = None
    if image_path:
        from PIL import Image
        with Image.open(image_path) as im:
            size = im.size
    text = record_text(rec, size)
    if text is not None:
        parts.append(mo.accordion({"Model çıktısı": mo.Html(
            f'<pre style="white-space:pre-wrap;font-family:inherit">{html.escape(text)}</pre>')}))
    errors_view = mo.vstack(parts)
    image_view = (mo.image(src=image_path.read_bytes(), alt=f"{pdf} sayfa {page}", width=520) if image_path else
                  mo.md(f"Sayfa görüntüsü bulunamadı (`{paths.PAGES}/{pdf}-*/syf{page}.png`)."))
    return errors_view, image_view


@app.cell
def _(errors_view, image_view):
    mo.hstack([errors_view, image_view], widths=[1, 1], align="start", gap=1.5)
    return


if __name__ == "__main__":
    app.run()
