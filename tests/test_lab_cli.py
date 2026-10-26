import json
import sys
import types

import pypdfium2 as pdfium
import pytest

from ocrnow import engines, paths, ui
from ocrnow.lab import bench, cli
from ocrnow.lab.cli import main, parse_targets
from ocrnow.pages import InputError


def make_pdf(path, n):
    pdf = pdfium.PdfDocument.new()
    for _ in range(n):
        pdf.new_page(200, 300)
    pdf.save(str(path))
    pdf.close()
    return path


def record(error=None, **kw):
    base = {"pdf": "k", "page": 1, "engine": "glm-ocr", "mode": "duz", "seconds": 1.5, "chars": 10,
            "cer_raw": 0.1, "cer_norm": 0.05, "cer_body": None, "cer_base": 0.02, "wer": 0.2, "line_end": 0.9,
            "apostrophe": None, "error": error}
    return {**base, **kw}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "INPUT", tmp_path / "input")
    monkeypatch.setattr(paths, "GT", tmp_path / "gt")
    monkeypatch.setattr(paths, "LAB_OUT", tmp_path / "lab")
    (tmp_path / "input").mkdir()
    state = types.SimpleNamespace(calls=[], records=[record()], missing={"kraken": "kraken kurulu değil",
                                                                          "paddleocr-vl": "dosya yok",
                                                                          "deepseek-ocr2": "dosya yok",
                                                                          "lightonocr": "vllm kurulu değil"})
    monkeypatch.setattr(engines, "missing", lambda name: state.missing.get(name))

    def run_bench(source_pages, targets, gt_dir, out_dir, *, dpi=200):
        state.calls.append(types.SimpleNamespace(pages=source_pages, targets=targets, gt=gt_dir, out=out_dir, dpi=dpi))
        (out_dir / "k_karsilastirma.md").write_text("md")
        (out_dir / "results.json").write_text(json.dumps(state.records))
        return out_dir / "results.json"

    monkeypatch.setattr(bench, "run_bench", run_bench)
    return state


def test_parse_targets():
    assert parse_targets(["dots.mocr:duzen", "glm-ocr"]) == [("dots.mocr", "duzen"), ("glm-ocr", "duz")]
    assert parse_targets(["dots.mocr"]) == [("dots.mocr", "duz"), ("dots.mocr", "duzen")]
    assert parse_targets(["glm-ocr", "glm-ocr:duz"]) == [("glm-ocr", "duz")]


@pytest.mark.parametrize("spec,msg", [("yok", "bilinmeyen motor 'yok'"), ("glm-ocr:duzen", "glm-ocr motorunda 'duzen' modu yok")])
def test_parse_targets_errors(spec, msg):
    with pytest.raises(InputError, match=msg):
        parse_targets([spec])


def test_runs_given_pdf_pages_and_targets(tmp_path, env, capsys):
    pdf = make_pdf(tmp_path / "k.pdf", 3)
    assert main([str(pdf), "-e", "dots.mocr:duzen", "glm-ocr", "-p", "2-3", "--dpi", "150"]) == 0
    call = env.calls[0]
    assert [(p.source, p.index) for p in call.pages] == [(pdf, 2), (pdf, 3)]
    assert call.targets == [("dots.mocr", "duzen"), ("glm-ocr", "duz")]
    assert call.gt == paths.GT and call.dpi == 150
    assert call.out.parent == paths.LAB_OUT and call.out.is_dir()
    out = capsys.readouterr().out
    assert "glm-ocr/duz" in out and "k s.1" in out and "results.json" in out and "k_karsilastirma.md" in out
    assert "summary.csv" in out


def test_defaults_to_input_pdfs_page_one_and_ready_engines(tmp_path, env, monkeypatch):
    pdf = make_pdf(paths.INPUT / "k.pdf", 3)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)   # TTY değil: sormaz
    assert main([]) == 0
    call = env.calls[0]
    assert [(p.source, p.index) for p in call.pages] == [(pdf, 1)]
    ready = {e for e, _ in call.targets}
    assert ready == {"tesseract", "glm-ocr", "dots.mocr"}
    assert ("dots.mocr", "duzen") in call.targets and ("dots.mocr", "duz") in call.targets


def test_unready_engine_requested_explicitly_is_an_error(tmp_path, env, capsys):
    pdf = make_pdf(tmp_path / "k.pdf", 1)
    assert main([str(pdf), "-e", "kraken"]) == 2
    assert "'kraken' motoru kullanılamıyor: kraken kurulu değil" in capsys.readouterr().err
    assert env.calls == []


def test_missing_pdfs_and_bad_pages_are_clear_errors(tmp_path, env, capsys, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert main([]) == 2 and "PDF yok" in capsys.readouterr().err
    pdf = make_pdf(tmp_path / "k.pdf", 2)
    assert main([str(pdf), "-p", "50-60"]) == 2 and "belgede 2 sayfa var" in capsys.readouterr().err


def test_exit_code_one_when_a_record_failed_and_table_shows_it(tmp_path, env, capsys):
    env.records = [record(), record(error="model çöktü", engine="dots.mocr", mode="duzen", seconds=None, chars=None)]
    pdf = make_pdf(tmp_path / "k.pdf", 1)
    assert main([str(pdf), "-e", "glm-ocr"]) == 1
    out = capsys.readouterr().out
    assert "HATA  dots.mocr/duzen  k s.1: model çöktü" in out
    assert " —" in out   # ölçü yoksa —


def test_interactive_flow(tmp_path, env, monkeypatch):
    make_pdf(paths.INPUT / "a.pdf", 2)
    b = make_pdf(paths.INPUT / "b.pdf", 5)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    asked = []

    def choose(title, options, default, multi=False):
        asked.append((title, options))
        if title.startswith("PDF"):
            return [1]
        return [options.index("glm-ocr:duz"), options.index("dots.mocr:duzen")]

    answers = iter(["9", "2-3"])   # önce geçersiz (taşan) seçim, sonra geçerli
    monkeypatch.setattr(ui, "choose", choose)
    monkeypatch.setattr(ui, "ask", lambda q, d: next(answers))
    monkeypatch.setattr(cli.pages, "pdf_page_count", lambda p: 5)
    assert main([]) == 0
    call = env.calls[0]
    assert [(p.source, p.index) for p in call.pages] == [(b, 2), (b, 3)]
    assert call.targets == [("glm-ocr", "duz"), ("dots.mocr", "duzen")]
    engine_options = asked[1][1]
    assert "kraken:duz" not in engine_options and "dots.mocr:duz" in engine_options


def test_rapor_routes_to_notebook(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "ocrnow.lab.notebook", types.SimpleNamespace(rapor=lambda html: calls.append(html) or 0))
    assert main(["rapor"]) == 0 and main(["rapor", "--html"]) == 0
    assert calls == [False, True]


def test_rapor_without_notebook_is_a_clear_error(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ocrnow.lab.notebook", None)   # import ImportError verir
    assert main(["rapor"]) == 2
    assert "rapor açılamadı" in capsys.readouterr().err
