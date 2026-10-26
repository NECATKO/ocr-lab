import csv
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from ocrnow import engines, pages, paths
from ocrnow.lab import bench
from ocrnow.lab.bench import new_run_dir, run_bench
from ocrnow.pages import SourcePage

LAYOUT = json.dumps([
    {"category": "Page-header", "bbox": [10, 10, 90, 20], "text": "BAŞLIK"},
    {"category": "Text", "bbox": [10, 30, 90, 70], "text": "bir iki üç\ndört beş"},
    {"category": "Page-footer", "bbox": [40, 90, 60, 99], "text": "12"},
])
TSV = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
       "4\t1\t1\t1\t1\t0\t10\t30\t80\t10\t-1\t\n5\t1\t1\t1\t1\t1\t10\t30\t80\t10\t90\tbir\n"
       "5\t1\t1\t1\t1\t2\t20\t30\t10\t10\t90\tiki\n5\t1\t1\t1\t1\t3\t30\t30\t10\t10\t90\tüç\n"
       "4\t1\t1\t1\t2\t0\t10\t50\t80\t10\t-1\t\n5\t1\t1\t1\t2\t1\t10\t50\t10\t10\t90\tdört\n"
       "5\t1\t1\t1\t2\t2\t30\t50\t10\t10\t90\tbeş\n")


class Fake:
    """Sahte motorlar: server() girişi/çıkışı kaydedilir, run ham metni döndürür."""

    def __init__(self):
        self.log = []
        self.outputs = {("dots.mocr", "duzen"): LAYOUT, ("glm-ocr", "duz"): "bir iki üç\ndört beş"}
        self.fail_start = set()
        self.fail_page = set()
        self.interrupt = set()
        self.tsv_calls = 0


@pytest.fixture
def env(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(paths, "LAB_OUT", tmp_path / "lab")
    monkeypatch.setattr(paths, "SETTINGS", tmp_path / "ayarlar")

    def render(src, dpi=200):
        out = tmp_path / "pages" / f"{src.source.stem}_syf{src.index}.png"
        out.parent.mkdir(exist_ok=True)
        Image.new("L", (100, 100), 255).save(out)
        return out

    @contextmanager
    def server(name):
        fake.log.append(("giriş", name))
        if name in fake.fail_start:
            raise RuntimeError("sunucu ayağa kalkmadı")
        try:
            def run(img, mode):
                if name in fake.interrupt:
                    raise KeyboardInterrupt
                if (name, img.name) in fake.fail_page:
                    raise RuntimeError("model çöktü")
                return fake.outputs[name, mode]
            yield run
        finally:
            fake.log.append(("çıkış", name))

    def tsv(img):
        fake.tsv_calls += 1
        return TSV

    monkeypatch.setattr(pages, "render", render)
    monkeypatch.setattr(engines, "server", server)
    monkeypatch.setattr(engines, "tesseract_tsv", tsv)
    (tmp_path / "gt").mkdir()
    return fake


def src(tmp_path, index=1, stem="kitap"):
    return SourcePage(tmp_path / f"{stem}.pdf", index)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_two_targets_without_gt(tmp_path, env):
    out = tmp_path / "run"
    results = run_bench([src(tmp_path)], [("dots.mocr", "duzen"), ("glm-ocr", "duz")], tmp_path / "gt", out)
    assert results == out / "results.json"
    recs = load(results)
    assert [(r["engine"], r["mode"], r["pdf"], r["page"]) for r in recs] == \
        [("dots.mocr", "duzen", "kitap", 1), ("glm-ocr", "duz", "kitap", 1)]
    for r in recs:
        assert r["error"] is None and r["seconds"] >= 0 and r["chars"] > 0
        assert all(r[k] is None for k in
                   ("cer_raw", "cer_norm", "cer_body", "cer_base", "wer", "line_end", "apostrophe", "apostrophe_n"))
        assert r["word_errors"] == []
        assert Path(r["raw_path"]).read_text(encoding="utf-8") == env.outputs[r["engine"], r["mode"]]
    rows = list(csv.reader((paths.LAB_OUT / "summary.csv").open(encoding="utf-8")))
    assert rows[0] == ["engine", "pdf", "page", "seconds", "chars", "cer", "mode", "cer_norm", "cer_body",
                       "cer_base", "wer", "line_end", "apostrophe"]
    assert len(rows) == 3 and rows[1][0] == "dots.mocr" and rows[1][5] == "" and rows[2][6] == "duz"


def test_raw_path_layout(tmp_path, env):
    results = run_bench([src(tmp_path, 3)], [("glm-ocr", "duz")], tmp_path / "gt", tmp_path / "run")
    assert load(results)[0]["raw_path"] == str(tmp_path / "run" / "glm-ocr" / "kitap_syf3.duz.txt")


def test_metrics_with_gt(tmp_path, env):
    (tmp_path / "gt" / "kitap_syf1.txt").write_text("BAŞLIK\n\nbir iki üç\ndört beş\n\n12\n", encoding="utf-8")
    recs = load(run_bench([src(tmp_path)], [("dots.mocr", "duzen"), ("glm-ocr", "duz")], tmp_path / "gt",
                          tmp_path / "run"))
    layout, plain = recs
    assert layout["cer_norm"] == 0 and layout["wer"] == 0 and layout["cer_body"] == 0 and layout["cer_base"] == 0
    assert layout["word_errors"] == [] and layout["line_end"] == 1.0
    assert layout["lines"] == ["BAŞLIK", "bir iki üç", "dört beş", "12"]
    assert plain["cer_raw"] > 0 and plain["cer_body"] == 0     # başlık ve numara yok; gövde aynı
    assert plain["word_errors"] == [["BAŞLIK", ""], ["12", ""]]
    assert layout["apostrophe"] is None and layout["apostrophe_n"] == 0
    row = list(csv.reader((paths.LAB_OUT / "summary.csv").open(encoding="utf-8")))[1]
    assert row[5] == "0.0" and row[7] == "0.0" and row[11] == "1.0"


def test_apostrophe_list_excludes_the_page_being_measured(tmp_path, env):
    env.outputs["glm-ocr", "duz"] = "cür'et"
    gt = tmp_path / "gt"
    (gt / "kitap_syf1.txt").write_text("cür’et", encoding="utf-8")
    rec = load(run_bench([src(tmp_path)], [("glm-ocr", "duz")], gt, tmp_path / "r1"))[0]
    assert rec["apostrophe"] == 0.0 and rec["apostrophe_n"] == 1   # kendi cevabı listede yok → varsayılan ‘
    (gt / "kitap_syf2.txt").write_text("cür’et", encoding="utf-8")
    rec = load(run_bench([src(tmp_path)], [("glm-ocr", "duz")], gt, tmp_path / "r2"))[0]
    assert rec["apostrophe"] == 1.0                                # başka sayfadan öğrenilir


def test_engines_run_one_server_at_a_time_and_pages_in_each_mode(tmp_path, env):
    env.outputs["dots.mocr", "duz"] = "x"
    run_bench([src(tmp_path, 1), src(tmp_path, 2)],
              [("dots.mocr", "duzen"), ("glm-ocr", "duz"), ("dots.mocr", "duz")], tmp_path / "gt", tmp_path / "run")
    assert env.log == [("giriş", "dots.mocr"), ("çıkış", "dots.mocr"), ("giriş", "glm-ocr"), ("çıkış", "glm-ocr")]
    recs = load(tmp_path / "run" / "results.json")
    assert [(r["engine"], r["mode"], r["page"]) for r in recs] == [
        ("dots.mocr", "duzen", 1), ("dots.mocr", "duzen", 2), ("dots.mocr", "duz", 1), ("dots.mocr", "duz", 2),
        ("glm-ocr", "duz", 1), ("glm-ocr", "duz", 2)]
    assert env.tsv_calls == 2   # sayfa başına bir kez


def test_failed_server_start_marks_its_records_and_continues(tmp_path, env):
    env.fail_start.add("dots.mocr")
    recs = load(run_bench([src(tmp_path)], [("dots.mocr", "duzen"), ("glm-ocr", "duz")], tmp_path / "gt",
                          tmp_path / "run"))
    assert recs[0]["error"] == "sunucu ayağa kalkmadı" and recs[0]["seconds"] is None and recs[0]["cer_norm"] is None
    assert recs[1]["error"] is None
    rows = list(csv.reader((paths.LAB_OUT / "summary.csv").open(encoding="utf-8")))
    assert len(rows) == 2   # hatalı kayıt özet satırı almaz


def test_failed_page_marks_one_record_and_continues(tmp_path, env):
    env.fail_page.add(("glm-ocr", "kitap_syf1.png"))
    (tmp_path / "gt" / "kitap_syf1.txt").write_text("bir", encoding="utf-8")
    recs = load(run_bench([src(tmp_path, 1), src(tmp_path, 2)], [("glm-ocr", "duz")], tmp_path / "gt",
                          tmp_path / "run"))
    assert recs[0]["error"] == "model çöktü" and recs[0]["cer_raw"] is None and recs[0]["raw_path"] is None
    assert recs[1]["error"] is None and recs[1]["chars"] > 0


def test_render_error_is_recorded(tmp_path, env, monkeypatch):
    def broken(src, dpi=200):
        raise pages.InputError("PDF açılamadı")
    monkeypatch.setattr(pages, "render", broken)
    rec = load(run_bench([src(tmp_path)], [("glm-ocr", "duz")], tmp_path / "gt", tmp_path / "run"))[0]
    assert rec["error"] == "PDF açılamadı"


def test_summary_appends_across_runs_with_one_header(tmp_path, env):
    for name in ("r1", "r2"):
        run_bench([src(tmp_path)], [("glm-ocr", "duz")], tmp_path / "gt", tmp_path / name)
    rows = list(csv.reader((paths.LAB_OUT / "summary.csv").open(encoding="utf-8")))
    assert [r[0] for r in rows] == ["engine", "glm-ocr", "glm-ocr"]


def test_comparison_markdown(tmp_path, env):
    (tmp_path / "gt" / "kitap_syf1.txt").write_text("bir iki üç dört beş", encoding="utf-8")
    env.fail_page.add(("glm-ocr", "kitap_syf1.png"))
    out = tmp_path / "run"
    run_bench([src(tmp_path)], [("dots.mocr", "duzen"), ("glm-ocr", "duz")], tmp_path / "gt", out)
    md = (out / "kitap_karsilastirma.md").read_text(encoding="utf-8")
    assert md.startswith("# kitap — OCR karşılaştırması")
    assert "## Sayfa 1" in md and "![sayfa 1](../pages/kitap_syf1.png)" in md
    assert "Model süresi (sn)" in md and "yalnız model çağrısıdır" in md and "Sarı kesme" in md
    assert "| dots.mocr/duzen |" in md and "| glm-ocr/duz | — | — | HATA |" in md
    assert "### dots.mocr/duzen" in md and "BAŞLIK\n\nbir iki üç\ndört beş\n\n12" in md and "HATA: model çöktü" in md


def test_results_written_even_when_interrupted(tmp_path, env):
    env.interrupt.add("glm-ocr")
    out = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        run_bench([src(tmp_path)], [("dots.mocr", "duzen"), ("glm-ocr", "duz")], tmp_path / "gt", out)
    assert [r["engine"] for r in load(out / "results.json")] == ["dots.mocr"]   # yarım sayfa kayda girmez
    assert env.log[-1] == ("çıkış", "glm-ocr")                                  # sunucu yine kapanır


def test_unknown_target_fails_before_starting_anything(tmp_path, env):
    with pytest.raises(ValueError, match="bilinmeyen motor"):
        run_bench([src(tmp_path)], [("yok", "duz")], tmp_path / "gt", tmp_path / "run")
    with pytest.raises(ValueError, match="modu yok"):
        run_bench([src(tmp_path)], [("glm-ocr", "duzen")], tmp_path / "gt", tmp_path / "run")
    assert env.log == []


def test_new_run_dir_adds_suffix_when_taken(tmp_path):
    now = datetime(2026, 10, 25, 12, 0)
    assert new_run_dir(tmp_path, now).name == "20261025-1200"
    assert new_run_dir(tmp_path, now).name == "20261025-1200-2"
    assert new_run_dir(tmp_path, now).name == "20261025-1200-3"


def layout(*items):
    return json.dumps([{"category": c, "bbox": [0, 0, 10, 10], **({"text": t} if t is not None else {})} for c, t in items],
                      ensure_ascii=False)


SIZE = (100, 100)


def test_model_text_matches_manual_join_on_spike(spike_layout):
    manual = "\n\n".join(b["text"] for b in json.loads(spike_layout))
    assert bench.model_text(spike_layout, "dots.mocr", "duzen", (1653, 2339)) == manual


def test_model_text_skips_pictures_flattens_tables_keeps_line_breaks():
    raw = layout(("Text", "senâ-\nkâr"), ("Picture", None), ("Table", "<table><tr><td>a</td><td>b</td></tr></table>"),
                 ("Text", "son"))
    assert bench.model_text(raw, "dots.mocr", "duzen", SIZE) == "senâ-\nkâr\n\na b\n\nson"
    assert metrics_norm_equal("senâkâr", bench.model_text(layout(("Text", "senâ-\nkâr")), "dots.mocr", "duzen", SIZE))


def metrics_norm_equal(a, b):
    from ocrnow.lab import metrics
    return metrics.cer_norm(a, b) == 0


def test_model_text_falls_back_to_block_text_on_broken_json():
    raw = layout(("Text", "ilk"), ("Text", "ikinci"))[:-3]   # kesik JSON: kurtarma yolu
    assert bench.model_text(raw, "dots.mocr", "duzen", SIZE) == "ilk\n\nikinci"


def test_model_text_plain_mode_is_raw():
    assert bench.model_text("a\nb", "glm-ocr", "duz", SIZE) == "a\nb"


def test_apostrophe_flagged_counts_undecided_marks(tmp_path, env):
    env.outputs["glm-ocr", "duz"] = "cür'et ve ba'z"
    rec = load(run_bench([src(tmp_path)], [("glm-ocr", "duz")], tmp_path / "gt", tmp_path / "run"))[0]
    assert rec["apostrophe_flagged"] == 2     # liste ve kural karar veremedi, ikisi de sarı
    env.fail_page.add(("glm-ocr", "kitap_syf1.png"))
    rec = load(run_bench([src(tmp_path)], [("glm-ocr", "duz")], tmp_path / "gt", tmp_path / "run2"))[0]
    assert rec["apostrophe_flagged"] is None
