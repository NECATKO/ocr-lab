import importlib

from ocrnow import paths


def test_subprocess_env_has_venv_bins_and_vllm_flag():
    env = paths.subprocess_env()
    assert env["PATH"].split(":")[:2] == [str(paths.VENV_VLLM / "bin"), str(paths.VENV_KRAKEN / "bin")]
    assert env["VLLM_USE_FLASHINFER_SAMPLER"] == "0"


def test_root_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OCRNOW_ROOT", str(tmp_path))
    try:
        importlib.reload(paths)
        assert paths.OUTPUT == tmp_path / "output" and paths.GT == tmp_path / "gt"
    finally:
        # Sonraki testler geçici kökü görmesin
        monkeypatch.delenv("OCRNOW_ROOT", raising=False)
        importlib.reload(paths)
