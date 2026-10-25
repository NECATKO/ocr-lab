import ast
import shutil

import pytest

from ocrnow import engines, paths
from ocrnow.engines import DEFAULT_ENGINE, DEFAULT_MODE, ENGINES, missing, server


def test_registry_defaults():
    assert ENGINES[DEFAULT_ENGINE].modes[DEFAULT_MODE].layout is True
    assert set(ENGINES["dots.mocr"].modes) == {"duz", "duzen"}
    assert {e.name for e in ENGINES.values() if e.recommended} == {"tesseract", "dots.mocr", "glm-ocr", "lightonocr"}


def test_registry_modes_and_ports():
    assert list(ENGINES) == ["tesseract", "kraken", "paddleocr-vl", "glm-ocr", "lightonocr", "deepseek-ocr2", "dots.mocr"]
    for name, eng in ENGINES.items():
        assert eng.name == name
        if name != "dots.mocr":
            assert set(eng.modes) == {"duz"} and not eng.modes["duz"].layout
        assert (eng.kind == "vlm") == (eng.port is not None) == (eng.serve is not None)
    assert ENGINES["dots.mocr"].modes["duz"].prompt == "Extract the text content from this image."
    assert ENGINES["dots.mocr"].modes["duzen"].prompt == engines.LAYOUT_PROMPT
    assert ENGINES["lightonocr"].modes["duz"].prompt is None
    assert len({e.port for e in ENGINES.values() if e.port}) == 5


def test_layout_prompt_is_verbatim_from_spike_script():
    src = paths.ROOT / "docs/superpowers/specs/2026-10-25-ocr-now-ekler/layout_try.py"
    if not src.exists():
        pytest.skip("layout_try.py yok")
    prompt = next(n.value.value for n in ast.parse(src.read_text(encoding="utf-8")).body
                  if isinstance(n, ast.Assign) and n.targets[0].id == "PROMPT")
    assert engines.LAYOUT_PROMPT == prompt


def test_missing_reports_absent_binary(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(engines, "served_model", lambda base: None)
    assert "kurulu değil" in missing("glm-ocr")
    assert "kurulu değil" in missing("tesseract")


def test_missing_none_when_server_already_up(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(engines, "served_model", lambda base: "m")
    assert missing("glm-ocr") is None


def test_missing_reports_absent_model_file(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda cmd: cmd)
    monkeypatch.setattr(engines, "served_model", lambda base: None)
    monkeypatch.setattr(paths, "MODELS", tmp_path)
    assert "dosya yok" in missing("paddleocr-vl")


def test_local_server_runs_without_http(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "_tesseract_text", lambda img: "merhaba")
    monkeypatch.setattr(engines, "_start_server", lambda *a: pytest.fail("yerel motor sunucu açmamalı"))
    with server("tesseract") as run:
        assert run(tmp_path / "x.png", "duz") == "merhaba"


def test_vlm_server_uses_mode_prompt_and_always_stops(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(engines, "_start_server", lambda eng: ("http://x/v1", "mdl", "PROC"))
    monkeypatch.setattr(engines, "_stop_server", lambda proc: calls.append(("stop", proc)))
    monkeypatch.setattr(engines, "_vlm", lambda img, base, model, prompt: calls.append((base, model, prompt)) or "ham")
    with pytest.raises(RuntimeError):
        with server("dots.mocr") as run:
            assert run(tmp_path / "x.png", "duzen") == "ham"
            assert run(tmp_path / "x.png", "duz") == "ham"
            raise RuntimeError("sayfa patladı")
    assert calls == [("http://x/v1", "mdl", engines.LAYOUT_PROMPT),
                     ("http://x/v1", "mdl", "Extract the text content from this image."),
                     ("stop", "PROC")]


def test_unknown_mode_gives_turkish_error(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "_tesseract_text", lambda img: "x")
    with server("tesseract") as run:
        with pytest.raises(ValueError, match="modu yok"):
            run(tmp_path / "x.png", "duzen")


def test_tesseract_tsv_command(monkeypatch, tmp_path):
    seen = {}

    class R:
        stdout = "level\ttext\n"

    def fake_run(cmd, **kw):
        seen["cmd"], seen["kw"] = cmd, kw
        return R()

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    assert engines.tesseract_tsv(tmp_path / "a.png") == "level\ttext\n"
    assert seen["cmd"] == ["tesseract", str(tmp_path / "a.png"), "stdout", "-l", "tur", "tsv"]
    assert seen["kw"]["env"]["VLLM_USE_FLASHINFER_SAMPLER"] == "0"
