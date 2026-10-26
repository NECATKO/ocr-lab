import subprocess

import pytest

from ocrnow import engines, models, paths
from ocrnow.models import Step, gpu_total_gb, install_steps, list_lines


def _text(steps):
    return [" ".join(s.cmd) if s.cmd else s.desc for s in steps]


@pytest.fixture(autouse=True)
def no_gpu_query(monkeypatch):
    """main() gerçek nvidia-smi çağırmasın; GPU isteyen test kendi değerini koyar."""
    monkeypatch.setattr(models, "gpu_total_gb", lambda: None)


@pytest.fixture
def bos_kok(monkeypatch, tmp_path):
    """Hiçbir şeyin kurulu olmadığı sahte kök; HF önbelleği de boş."""
    for ad, alt in [("MODELS", "models"), ("TOOLS", "tools"), ("VENV_VLLM", ".venv-vllm"), ("VENV_KRAKEN", ".venv-kraken")]:
        monkeypatch.setattr(paths, ad, tmp_path / alt)
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))
    monkeypatch.setattr(models.shutil, "which", lambda cmd, **kw: None)
    return tmp_path


def test_list_marks_recommended_and_warns():
    lines = "\n".join(list_lines(gpu_gb=4.0))
    assert "★ dots.mocr" in lines and "sığmayabilir" in lines


def test_list_has_row_per_engine_and_cpu_label(monkeypatch):
    monkeypatch.setattr(engines, "missing", lambda name: None)
    lines = list_lines(gpu_gb=8.0)
    rows = [l for l in lines if any(l[2:].startswith(n + " ") for n in engines.ENGINES)]
    assert len(rows) == len(engines.ENGINES) == 7
    assert sum(l.startswith("★") for l in rows) == 4
    assert "CPU" in next(l for l in rows if "tesseract" in l)
    assert not any("sığmayabilir" in l for l in lines)


def test_list_without_gpu_does_not_warn(monkeypatch):
    monkeypatch.setattr(engines, "missing", lambda name: None)
    assert not any("sığmayabilir" in l for l in list_lines(gpu_gb=None))


def test_list_shows_status(monkeypatch):
    monkeypatch.setattr(engines, "missing", lambda name: "kraken kurulu değil" if name == "kraken" else None)
    kraken = next(l for l in list_lines(gpu_gb=8.0) if "kraken" in l)
    assert "kraken kurulu değil" in kraken


def test_every_engine_has_measured_sizes():
    for e in engines.ENGINES.values():
        assert e.vram_gb is None if e.kind == "local" else e.vram_gb > 0
        if e.name != "tesseract":
            assert e.disk_gb > 0


def test_fp8_vram_from_measured_load():
    """M7: fp8 motorlarda vLLM logundaki yüklenen bellek (dots 3.44 GiB, deepseek 3.91 GiB) + 1.5 GB."""
    assert engines.ENGINES["dots.mocr"].vram_gb == 4.9
    assert engines.ENGINES["deepseek-ocr2"].vram_gb == 5.4


def test_install_warns_for_engines_too_big_for_gpu(monkeypatch, capsys):
    monkeypatch.setattr(models, "install_steps", lambda names: [])
    monkeypatch.setattr(models, "gpu_total_gb", lambda: 4.5)
    assert models.main(["install", "dots.mocr", "glm-ocr"]) == 0
    out = capsys.readouterr().out
    assert "Uyarı: dots.mocr tahminen 4.9 GB GPU belleği ister, GPU'da 4.5 GB var" in out
    assert "glm-ocr tahminen" not in out
    monkeypatch.setattr(models, "gpu_total_gb", lambda: None)            # GPU bilinmiyorsa uyarı yok
    models.main(["install", "dots.mocr"])
    assert "Uyarı" not in capsys.readouterr().out


def _fake_smi(monkeypatch, out=None, exc=None):
    def run(cmd, **kw):
        assert cmd[0] == "nvidia-smi" and "--query-gpu=memory.total" in cmd
        if exc:
            raise exc
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")
    monkeypatch.setattr(models.subprocess, "run", run)


def test_gpu_total_converts_mib_to_gb(monkeypatch):
    _fake_smi(monkeypatch, "8151\n")
    assert gpu_total_gb() == pytest.approx(7.96, abs=0.01)


@pytest.mark.parametrize("exc", [FileNotFoundError(), subprocess.CalledProcessError(9, "nvidia-smi")])
def test_gpu_total_none_when_smi_missing_or_fails(monkeypatch, exc):
    _fake_smi(monkeypatch, exc=exc)
    assert gpu_total_gb() is None


def test_gpu_total_none_on_garbage(monkeypatch):
    _fake_smi(monkeypatch, "[N/A]\n")
    assert gpu_total_gb() is None


def test_install_steps_skip_done(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hf"))   # gerçek önbellek GLM-OCR'yi içerebilir
    monkeypatch.setattr(paths, "VENV_VLLM", tmp_path)          # bin/python var gibi
    (tmp_path / "bin").mkdir(); (tmp_path / "bin" / "python").write_text("")
    steps = install_steps(["glm-ocr"])
    assert not any("uv venv" in " ".join(s.cmd) for s in steps if s.cmd)
    assert any("zai-org/GLM-OCR" in " ".join(s.cmd) for s in steps if s.cmd)


def test_vllm_engines_share_one_environment(bos_kok):
    text = _text(install_steps(["glm-ocr", "lightonocr", "deepseek-ocr2", "dots.mocr"]))
    assert sum("uv venv" in t and ".venv-vllm" in t for t in text) == 1
    assert sum("vllm==0.32.0" in t for t in text) == 1
    assert sum("patch" in s.desc.lower() or "yama" in s.desc.lower() for s in install_steps(["glm-ocr", "dots.mocr"])) == 1
    for repo in ["zai-org/GLM-OCR", "lightonai/LightOnOCR-2-1B", "deepseek-ai/DeepSeek-OCR-2", "dots-studio/dots.mocr"]:
        assert sum(t.endswith("download " + repo) for t in text) == 1
    # sıra: ortam, vllm, yama, indirmeler
    assert text.index(next(t for t in text if "uv venv" in t)) < text.index(next(t for t in text if "vllm==" in t)) \
        < text.index(next(t for t in text if t.endswith("download zai-org/GLM-OCR")))


def test_kraken_steps(bos_kok):
    steps = install_steps(["kraken"])
    text = _text(steps)
    assert any("uv venv" in t and ".venv-kraken" in t for t in text)
    assert any("kraken==7.1.1" in t for t in text)
    assert any(t.endswith("kraken get 10.5281/zenodo.10592716") for t in text)
    assert any(s.func and "catmus" in s.desc for s in steps)
    assert not any("vllm" in t for t in text)


def test_paddle_steps(bos_kok):
    text = _text(install_steps(["paddleocr-vl"]))
    joined = "\n".join(text)
    assert "b11756" in joined and "ubuntu-cuda-12.8-x64" in joined
    assert "PaddlePaddle/PaddleOCR-VL-1.6-GGUF" in joined
    assert "vllm==" not in joined          # vLLM ortamı gerekmez; hf uvx ile gelir
    assert "uvx" in joined


def test_kraken_and_paddle_skipped_when_done(bos_kok):
    k = paths.VENV_KRAKEN
    (k / "bin").mkdir(parents=True); (k / "bin" / "python").write_text("")
    (k / "lib" / "python3.12" / "site-packages" / "kraken-7.1.1.dist-info").mkdir(parents=True)
    paths.MODELS.mkdir(); (paths.MODELS / "catmus-print-large.mlmodel").write_text("x")
    assert install_steps(["kraken"]) == []
    (paths.TOOLS / "llama.cpp").mkdir(parents=True); (paths.TOOLS / "llama.cpp" / "llama-server").write_text("")
    snap = paths_hf(bos_kok, "PaddlePaddle/PaddleOCR-VL-1.6-GGUF")
    (snap / "PaddleOCR-VL-1.6-GGUF.gguf").write_text("g"); (snap / "PaddleOCR-VL-1.6-GGUF-mmproj.gguf").write_text("m")
    for n in ("PaddleOCR-VL-1.6.gguf", "PaddleOCR-VL-1.6-mmproj.gguf"):
        (paths.MODELS / n).write_text("")
    assert install_steps(["paddleocr-vl"]) == []


def paths_hf(root, repo):
    snap = root / "hf" / ("models--" + repo.replace("/", "--")) / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")
    return snap


def test_hf_cache_snapshot_skips_download(bos_kok):
    paths_hf(bos_kok, "zai-org/GLM-OCR")
    text = _text(install_steps(["glm-ocr"]))
    assert not any("zai-org/GLM-OCR" in t for t in text)


def test_hf_incomplete_download_is_not_done(bos_kok):
    snap = paths_hf(bos_kok, "zai-org/GLM-OCR")
    (snap.parents[1] / "blobs").mkdir()
    (snap.parents[1] / "blobs" / "abc.incomplete").write_text("")
    assert any("zai-org/GLM-OCR" in t for t in _text(install_steps(["glm-ocr"])))


def test_empty_snapshot_dir_is_not_done(bos_kok):
    (bos_kok / "hf" / "models--zai-org--GLM-OCR" / "snapshots").mkdir(parents=True)
    assert any("zai-org/GLM-OCR" in t for t in _text(install_steps(["glm-ocr"])))


def test_tesseract_missing_shows_apt_command(bos_kok):
    (step,) = install_steps(["tesseract"])
    assert "sudo apt install -y tesseract-ocr tesseract-ocr-tur" in step.desc
    with pytest.raises(RuntimeError, match="sudo apt install -y tesseract-ocr tesseract-ocr-tur"):
        step.func()


def test_tesseract_present_is_no_step(bos_kok, monkeypatch):
    monkeypatch.setattr(models.shutil, "which", lambda cmd, **kw: "/usr/bin/" + cmd)
    assert install_steps(["tesseract"]) == []


DEEP = "vllm/model_executor/models/deepencoder.py"
IMPORT = "from vllm.triton_utils import LOG2E, tl, triton"


def _fake_vllm(root, body):
    (root / ".venv-vllm" / "bin").mkdir(parents=True); (root / ".venv-vllm" / "bin" / "python").write_text("")
    (root / ".venv-vllm" / "bin" / "hf").write_text("")
    site = root / ".venv-vllm" / "lib" / "python3.12" / "site-packages"
    (site / "vllm-0.32.0.dist-info").mkdir(parents=True)
    f = site / DEEP
    f.parent.mkdir(parents=True)
    f.write_text(body)
    return f


def test_vllm_patch_applies_once(bos_kok):
    f = _fake_vllm(bos_kok, f"a = 1\n{IMPORT}\nb = 2\n")
    steps = install_steps(["deepseek-ocr2"])
    patch = next(s for s in steps if s.func)
    patch.func()
    patched = f.read_text()
    assert patched == (f"a = 1\n{IMPORT}\nLOG2E = tl.constexpr(LOG2E)  "
                       "# ocr-lab yaması: Triton 3.7 constexpr olmayan global kabul etmiyor\nb = 2\n")
    patch.func()                                                    # ikinci kez dokunmaz
    assert f.read_text() == patched
    assert not any(s.func for s in install_steps(["deepseek-ocr2"]))


def test_vllm_patch_missing_file_fails_loudly(bos_kok):
    f = _fake_vllm(bos_kok, "")
    f.unlink()
    patch = next(s for s in install_steps(["glm-ocr"]) if s.func)
    with pytest.raises(RuntimeError, match="deepencoder"):
        patch.func()


def test_paddle_symlink_step(bos_kok):
    snap = paths_hf(bos_kok, "PaddlePaddle/PaddleOCR-VL-1.6-GGUF")
    (snap / "PaddleOCR-VL-1.6-GGUF.gguf").write_text("g"); (snap / "PaddleOCR-VL-1.6-GGUF-mmproj.gguf").write_text("m")
    (paths.TOOLS / "llama.cpp").mkdir(parents=True); (paths.TOOLS / "llama.cpp" / "llama-server").write_text("")
    (link,) = [s for s in install_steps(["paddleocr-vl"]) if s.func]
    link.func()
    assert (paths.MODELS / "PaddleOCR-VL-1.6.gguf").read_text() == "g"
    assert (paths.MODELS / "PaddleOCR-VL-1.6-mmproj.gguf").read_text() == "m"
    link.func()                                                     # ln -sf gibi: tekrar çalışır


def test_unknown_engine_is_rejected(capsys):
    assert models.main(["install", "yok-motor"]) == 2
    assert "yok-motor" in capsys.readouterr().err


def test_install_default_is_recommended_and_hepsi_is_all(monkeypatch):
    seen = []
    monkeypatch.setattr(models, "install_steps", lambda names: seen.append(names) or [])
    assert models.main(["install"]) == 0
    assert models.main(["install", "--hepsi"]) == 0
    assert models.main(["install", "kraken", "glm-ocr"]) == 0
    assert seen == [["tesseract", "glm-ocr", "lightonocr", "dots.mocr"], list(engines.ENGINES), ["kraken", "glm-ocr"]]


def test_hepsi_with_names_is_an_error(capsys):
    assert models.main(["install", "--hepsi", "kraken"]) == 2


def test_install_runs_steps_in_order_and_stops_at_first_failure(monkeypatch, capsys):
    calls = []
    runs = []

    def fake_run(cmd, **kw):
        runs.append((cmd, kw["env"]))
        if cmd == ["bad"]:
            raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(models.subprocess, "run", fake_run)
    steps = [Step("bir", cmd=["ok"]), Step("iki", func=lambda: calls.append("f")),
             Step("üç bozuk", cmd=["bad"]), Step("dört", func=lambda: calls.append("asla"))]
    monkeypatch.setattr(models, "install_steps", lambda names: steps)
    assert models.main(["install"]) == 1
    out = capsys.readouterr()
    assert calls == ["f"]
    assert "üç bozuk" in out.err and "dört" not in out.err
    assert runs[0][1]["VLLM_USE_FLASHINFER_SAMPLER"] == "0"        # env=paths.subprocess_env()


def test_install_func_failure_is_reported(monkeypatch, capsys):
    def boom():
        raise RuntimeError("sebep burada")
    monkeypatch.setattr(models, "install_steps", lambda names: [Step("kırık adım", func=boom)])
    assert models.main(["install"]) == 1
    err = capsys.readouterr().err
    assert "kırık adım" in err and "sebep burada" in err


def test_nothing_to_install_message(monkeypatch, capsys):
    monkeypatch.setattr(models, "install_steps", lambda names: [])
    assert models.main(["install"]) == 0
    assert "kurulu" in capsys.readouterr().out


def test_main_lists_with_gpu_line(monkeypatch, capsys):
    monkeypatch.setattr(models, "gpu_total_gb", lambda: 8.0)
    monkeypatch.setattr(engines, "missing", lambda name: None)
    assert models.main([]) == 0
    out = capsys.readouterr().out
    assert "8.0 GB" in out and "★ dots.mocr" in out


def test_main_lists_without_gpu(monkeypatch, capsys):
    monkeypatch.setattr(models, "gpu_total_gb", lambda: None)
    monkeypatch.setattr(engines, "missing", lambda name: None)
    assert models.main([]) == 0
    assert "GPU: bulunamadı" in capsys.readouterr().out
