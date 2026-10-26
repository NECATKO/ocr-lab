import json
import os
import re
import subprocess
import sys
import types

import pytest
from PIL import Image

from ocrnow import paths
from ocrnow.lab import notebook
from ocrnow.lab.notebook import (best_cells, find_page_image, image_html, metrics_table, nice_max, load_runs, record_text, rapor, scatter_svg,
                                 summarize)


def rec(**kw):
    base = {"pdf": "k", "page": 1, "engine": "glm-ocr", "mode": "duz", "seconds": 2.0, "chars": 10, "raw_path": None,
            "cer_raw": 0.1, "cer_norm": 0.05, "cer_body": None, "cer_base": 0.02, "wer": 0.2, "line_end": 0.9,
            "apostrophe": None, "apostrophe_n": None, "apostrophe_flagged": 2, "word_errors": [["a", "b"]],
            "lines": [], "warnings": [], "error": None}
    return {**base, **kw}


def write_run(lab, name, records):
    (lab / name).mkdir(parents=True, exist_ok=True)
    (lab / name / "results.json").write_text(json.dumps(records), encoding="utf-8")


def test_load_runs(tmp_path):
    (tmp_path / "20261025-1200").mkdir()
    (tmp_path / "20261025-1200" / "results.json").write_text(json.dumps([{"engine": "glm-ocr", "mode": "duz", "cer_norm": 0.01}]))
    assert load_runs(tmp_path)[0]["run"] == "20261025-1200"


def test_load_runs_missing_dir_empty_dir_and_bad_files(tmp_path):
    assert load_runs(tmp_path / "yok") == []
    assert load_runs(tmp_path) == []
    write_run(tmp_path, "20261025-1300", [rec()])
    write_run(tmp_path, "20261025-1100", [rec(), rec(page=2)])
    (tmp_path / "20261025-1400").mkdir()                      # results.json yok
    (tmp_path / "20261025-1500").mkdir()
    (tmp_path / "20261025-1500" / "results.json").write_text("{bozuk")   # okunamayan dosya atlanır
    (tmp_path / "summary.csv").write_text("x")
    runs = load_runs(tmp_path)
    assert [r["run"] for r in runs] == ["20261025-1100", "20261025-1100", "20261025-1300"]   # eskiden yeniye


def test_summarize_means_ignore_none_and_errors():
    rows = summarize([rec(cer_norm=0.1, seconds=1.0), rec(cer_norm=None, seconds=3.0), rec(cer_norm=0.3, seconds=2.0),
                      rec(error="x", cer_norm=0.9), rec(engine="dots.mocr", mode="duzen", cer_norm=0.02)])
    by = {r["key"]: r for r in rows}
    assert set(by) == {"glm-ocr/duz", "dots.mocr/duzen"}
    assert by["glm-ocr/duz"]["cer_norm"] == pytest.approx(0.2)
    assert by["glm-ocr/duz"]["seconds"] == pytest.approx(2.0)
    assert by["glm-ocr/duz"]["n"] == 3 and by["glm-ocr/duz"]["errors"] == 1
    assert by["glm-ocr/duz"]["cer_body"] is None


def test_best_cells_lower_is_better_except_line_end_and_apostrophe():
    rows = summarize([rec(), rec(engine="b", cer_norm=0.01, line_end=0.5, apostrophe=0.8),
                      rec(engine="c", cer_norm=0.01, line_end=0.95, apostrophe=0.7)])
    best = best_cells(rows)
    assert best["cer_norm"] == {"b/duz", "c/duz"}           # eşitlik: ikisi de vurgulu
    assert best["line_end"] == {"c/duz"} and best["apostrophe"] == {"b/duz"}
    assert best["cer_body"] == set()                         # hiç ölçü yok


def test_metrics_table_highlights_best_escapes_and_dashes_for_none():
    rows = summarize([rec(engine="a<b", cer_norm=0.01), rec(engine="c", cer_norm=0.2, cer_body=None)])
    html = metrics_table(rows)
    assert "a&lt;b/duz" in html and "a<b" not in html
    assert html.count("font-weight:700") >= 2                       # en iyi hücreler
    assert '<td style="font-weight:700;background:rgba(34,160,90,.28)">1.00%</td>' in html
    assert "<td>20.00%</td>" in html                               # en iyi olmayan: işaretsiz
    assert "<td>—</td>" in html                                    # cer_body ölçüsü yok


@pytest.mark.parametrize("value,expected", [(0, 1.0), (1e-300, 1.0), (5e-324, 1.0), (0.004, 0.005), (7, 10), (35, 50)])
def test_nice_max_handles_tiny_values(value, expected):
    assert nice_max(value) == expected


def test_image_html_escapes_alt(tmp_path):
    png = tmp_path / "a.png"
    Image.new("L", (2, 2)).save(png)
    out = image_html(png, "Mes'ele\" onerror=\"x", 520)
    assert "alt=\"Mes&#x27;ele&quot; onerror=&quot;x\"" in out and out.count("<img") == 1
    assert out.startswith('<img src="data:image/png;base64,')


def test_scatter_svg_skips_missing_points_and_escapes():
    rows = summarize([rec(engine="a<b"), rec(engine="x", cer_norm=None)])
    svg = scatter_svg(rows, "cer_norm")
    assert svg.startswith("<svg") and "a&lt;b" in svg and "a<b" not in svg
    assert "x/duz" not in svg
    assert scatter_svg(summarize([rec(cer_norm=None)]), "cer_norm") == ""


def test_find_page_image_picks_newest_and_ignores_other_pdfs(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path)
    assert find_page_image("k", 1) is None
    old, new, other = (tmp_path / "k-100" / "syf1.png", tmp_path / "k-200" / "syf1.png", tmp_path / "k-2-300" / "syf1.png")
    for p, t in ((old, 1000), (new, 2000), (other, 3000)):
        p.parent.mkdir()
        Image.new("L", (4, 6)).save(p)
        os.utime(p, (t, t))
    assert find_page_image("k", 1) == new
    assert find_page_image("k", 2) is None


def test_record_text_plain_layout_and_missing(tmp_path):
    plain = tmp_path / "a.txt"
    plain.write_text("a\nb", encoding="utf-8")
    assert record_text(rec(raw_path=str(plain)), None) == "a\nb"
    lay = tmp_path / "b.txt"
    lay.write_text(json.dumps([{"bbox": [0, 0, 5, 5], "category": "Text", "text": "senâ-\nkâr"},
                               {"bbox": [0, 6, 5, 9], "category": "Picture"}]), encoding="utf-8")
    assert record_text(rec(engine="dots.mocr", mode="duzen", raw_path=str(lay)), (100, 100)) == "senâ-\nkâr"
    assert record_text(rec(raw_path=None), None) is None
    assert record_text(rec(raw_path=str(tmp_path / "yok.txt")), None) is None


def test_rapor_runs_marimo_with_venv_python(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(paths, "LAB_OUT", tmp_path / "lab")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or types.SimpleNamespace(returncode=3))
    assert rapor(False) == 3 and rapor(True) == 3
    edit, export = calls
    assert edit == [sys.executable, "-m", "marimo", "edit", notebook.__file__]
    assert export[:6] == [sys.executable, "-m", "marimo", "export", "html", notebook.__file__]
    assert export[export.index("-o") + 1] == str(tmp_path / "lab" / "rapor.html") and "-f" in export
    assert (tmp_path / "lab").is_dir()


def export_text(path):
    """Dışa aktarılan sayfada çıktılar JSON içinde \\uXXXX kaçışlıdır; okunur hâle getirir."""
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), path.read_text(encoding="utf-8"))


def export_html(root, out):
    env = {**os.environ, "OCRNOW_ROOT": str(root)}
    return subprocess.run([sys.executable, "-m", "marimo", "export", "html", notebook.__file__, "-o", str(out)],
                          capture_output=True, text=True, env=env)


def test_notebook_runs_headless():
    r = subprocess.run([sys.executable, "-m", "marimo", "export", "html", notebook.__file__, "-o", os.devnull])
    assert r.returncode == 0


def test_notebook_headless_without_results_shows_message(tmp_path):
    out = tmp_path / "bos.html"
    r = export_html(tmp_path, out)
    assert r.returncode == 0, r.stderr
    assert "<strong>Henüz sonuç yok.</strong>" in export_text(out)


def test_notebook_headless_with_one_synthetic_run(tmp_path):
    raw = tmp_path / "output" / "lab" / "20261025-1200" / "glm-ocr" / "k_syf1.duz.txt"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps([{"bbox": [0, 0, 5, 5], "category": "Text", "text": "sentetik çıktı"}]), encoding="utf-8")
    # açılışta ilk (alfabetik) motor/mod seçilir: dots.mocr/duzen
    write_run(tmp_path / "output" / "lab", "20261025-1200",
              [rec(engine="dots.mocr", mode="duzen", seconds=5.0, cer_norm=0.01, raw_path=str(raw),
                   word_errors=[["gtkelime", "ciktikelime"]]),
               rec(),
               rec(page=2, error="patladı", seconds=None, cer_norm=None)])
    Image.new("RGB", (30, 40), "white").save(_page_png(tmp_path))
    out = tmp_path / "rapor.html"
    r = export_html(tmp_path, out)
    assert r.returncode == 0, r.stderr
    html = export_text(out)
    assert "Henüz sonuç yok" not in html.replace("**Henüz sonuç yok.**", "")   # kaynak kodundaki metin hariç
    for beklenen in ("glm-ocr/duz", "dots.mocr/duzen", "<svg", "gtkelime", "ciktikelime", "sentetik çıktı",
                     "data:image/png"):
        assert beklenen in html, beklenen


def test_notebook_headless_hostile_pdf_name_cannot_inject_attributes(tmp_path):
    name = "Mes'ele\" onerror=\"alert(1)"
    write_run(tmp_path / "output" / "lab", "20261025-1200", [rec(pdf=name)])
    png = tmp_path / "output" / "_pages" / f"{name}-123" / "syf1.png"
    png.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8)).save(png)
    out = tmp_path / "rapor.html"
    r = export_html(tmp_path, out)
    assert r.returncode == 0, r.stderr
    html = export_text(out)
    # çıktılar JSON içinde olduğundan öznitelik tırnakları \" olarak kaçışlıdır
    assert 'alt=\\"Mes&#x27;ele&quot; onerror=&quot;alert(1) sayfa 1\\"' in html
    assert "alt=\\\"Mes'" not in html and "alt=\\\"Mes\\\"" not in html


def _page_png(root):
    p = root / "output" / "_pages" / "k-123" / "syf1.png"
    p.parent.mkdir(parents=True)
    return p
