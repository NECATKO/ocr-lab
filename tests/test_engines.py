import ast
import io
import json
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


def test_serve_cmd_keeps_paths_with_spaces_in_one_token(monkeypatch, tmp_path):
    root = tmp_path / "kök klasör"
    monkeypatch.setattr(paths, "TOOLS", root / "tools")
    monkeypatch.setattr(paths, "MODELS", root / "models")
    cmd = engines._serve_cmd(ENGINES["paddleocr-vl"])
    assert cmd[0] == f"{root}/tools/llama.cpp/llama-server"
    assert cmd[cmd.index("-m") + 1] == f"{root}/models/PaddleOCR-VL-1.6.gguf"
    assert cmd[cmd.index("--mmproj") + 1] == f"{root}/models/PaddleOCR-VL-1.6-mmproj.gguf"
    dots = engines._serve_cmd(ENGINES["dots.mocr"])
    assert dots[:3] == ["vllm", "serve", "dots-studio/dots.mocr"]
    assert dots[dots.index("--mm-processor-kwargs") + 1] == '{"max_pixels": 4000000}'
    assert dots[dots.index("--max-model-len") + 1] == "8192"  # $VLLM bayrakları ayrı parçalar


def test_missing_message_shows_full_path(monkeypatch, tmp_path):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setattr(engines, "served_model", lambda base: None)
    monkeypatch.setattr(paths, "TOOLS", tmp_path / "tools")
    assert missing("paddleocr-vl") == f"{tmp_path}/tools/llama.cpp/llama-server kurulu değil"


def test_relative_kraken_model_resolves_against_root(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "ROOT", tmp_path)
    monkeypatch.setenv("KRAKEN_MODEL", "models/x.mlmodel")
    assert engines._kraken_model() == tmp_path / "models/x.mlmodel"
    monkeypatch.setenv("KRAKEN_MODEL", str(tmp_path / "abs.mlmodel"))
    assert engines._kraken_model() == tmp_path / "abs.mlmodel"


class _FakeProc:
    pid = 4242

    def poll(self):
        return None


@pytest.mark.parametrize("exc", [KeyboardInterrupt, RuntimeError])
def test_start_server_stops_process_on_interrupt_during_wait(monkeypatch, tmp_path, exc):
    proc, stopped = _FakeProc(), []
    monkeypatch.setattr(paths, "LOGS", tmp_path)
    monkeypatch.setattr(engines, "served_model", lambda base: None)
    monkeypatch.setattr(engines.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(engines, "_stop_server", stopped.append)

    def interrupted(_):
        raise exc

    monkeypatch.setattr(engines.time, "sleep", interrupted)
    with pytest.raises(exc):
        engines._start_server(ENGINES["glm-ocr"])
    assert stopped == [proc]


def test_start_server_closes_log_and_stops_on_timeout(monkeypatch, tmp_path):
    proc, stopped, logs = _FakeProc(), [], []
    monkeypatch.setattr(paths, "LOGS", tmp_path)
    monkeypatch.setattr(engines, "served_model", lambda base: None)

    def popen(cmd, stdout, **k):
        logs.append(stdout)
        return proc

    monkeypatch.setattr(engines.subprocess, "Popen", popen)
    monkeypatch.setattr(engines, "_stop_server", stopped.append)
    monkeypatch.setattr(engines.time, "time", iter([0, 10**6]).__next__)
    with pytest.raises(RuntimeError, match="ayağa kalkmadı"):
        engines._start_server(ENGINES["glm-ocr"])
    assert stopped == [proc] and logs[0].closed


def test_lazy_server_starts_on_first_call_only(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(engines, "_start_server", lambda eng: calls.append("start") or ("http://x/v1", "mdl", "P"))
    monkeypatch.setattr(engines, "_stop_server", lambda proc: calls.append(("stop", proc)))
    monkeypatch.setattr(engines, "_vlm", lambda img, base, model, prompt: calls.append("vlm") or "ham")
    with engines.lazy_server("dots.mocr") as run:
        assert calls == []                      # önbellekten gelen sayfalar için GPU açılmaz
        assert run(tmp_path / "x.png", "duzen") == "ham"
        assert run(tmp_path / "x.png", "duz") == "ham"
    assert calls == ["start", "vlm", "vlm", ("stop", "P")]


def test_lazy_server_never_called_never_starts(monkeypatch):
    monkeypatch.setattr(engines, "_start_server", lambda eng: pytest.fail("çağrı yokken sunucu açılmamalı"))
    with engines.lazy_server("dots.mocr"):
        pass


def test_lazy_server_start_failure_not_retried(monkeypatch, tmp_path):
    starts = []

    def start(eng):
        starts.append(eng.name)
        raise RuntimeError("sunucu ayağa kalkmadı")
    monkeypatch.setattr(engines, "_start_server", start)
    with engines.lazy_server("dots.mocr") as run:
        for _ in range(2):   # her sayfa için yeniden 20 dk beklenmez
            with pytest.raises(engines.ServerError, match="'dots.mocr' modeli başlatılamadı: sunucu ayağa kalkmadı"):
                run(tmp_path / "x.png", "duzen")
    assert starts == ["dots.mocr"]


def test_lazy_server_page_error_is_not_server_error(monkeypatch, tmp_path):
    monkeypatch.setattr(engines, "_start_server", lambda eng: ("http://x/v1", "mdl", "P"))
    monkeypatch.setattr(engines, "_stop_server", lambda proc: None)

    def vlm(*a):
        raise RuntimeError("zaman aşımı")
    monkeypatch.setattr(engines, "_vlm", vlm)
    with engines.lazy_server("dots.mocr") as run:
        with pytest.raises(RuntimeError) as e:
            run(tmp_path / "x.png", "duzen")
    assert not isinstance(e.value, engines.ServerError)


@pytest.mark.parametrize("reason,truncated", [("length", True), ("stop", False), (None, False)])
def test_vlm_marks_truncated_output(monkeypatch, tmp_path, reason, truncated):
    """R22: finish_reason == "length" → metin Truncated (str alt sınıfı) olarak döner."""
    img = tmp_path / "x.png"
    img.write_bytes(b"png")
    choice = {"message": {"content": "metin"}, **({"finish_reason": reason} if reason else {})}
    monkeypatch.setattr(engines.urllib.request, "urlopen",
                        lambda req, timeout: io.BytesIO(json.dumps({"choices": [choice]}).encode()))
    out = engines._vlm(img, "http://x/v1", "mdl", "istem")
    assert out == "metin" and isinstance(out, engines.Truncated) is truncated
