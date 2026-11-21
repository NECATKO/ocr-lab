import subprocess

import pytest

from ocrnow import engines, identity, paths


def test_cached_hf_revision_is_pinned_at_launch(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    revision = "a" * 40
    model = paths.hf_cache() / "models--dots-studio--dots.mocr"
    (model / "refs").mkdir(parents=True)
    (model / "refs" / "main").write_text(revision)
    (model / "snapshots" / revision).mkdir(parents=True)
    (model / "snapshots" / revision / "config.json").write_text("{}")
    monkeypatch.setenv("VLLM_API_KEY", "not-for-results")
    seen = []
    served = iter([None, None, "dots-studio/dots.mocr"])
    monkeypatch.setattr(engines, "served_model", lambda _: next(served))
    monkeypatch.setattr(engines.subprocess, "Popen", lambda cmd, **_: seen.append(cmd) or object())
    monkeypatch.setattr(paths, "LOGS", tmp_path / "logs")
    runtime = identity.runtime("dots.mocr", tess={"version": "5.test", "language": "tur"})
    profile = runtime.profile("duzen")
    assert profile["model_revision"] == revision and profile["cacheable"]
    assert "VLLM_API_KEY" not in profile["environment"]
    _, model_id, _ = engines._start_server(engines.ENGINES["dots.mocr"], **runtime.server_options)
    assert model_id == "dots-studio/dots.mocr" and seen[0][-2:] == ["--revision", revision]


def test_tesseract_version_language_and_weight_replacement(monkeypatch, tmp_path):
    tesseract = tmp_path / "tesseract"
    tesseract.touch()
    weights = tmp_path / "tur.traineddata"
    weights.write_bytes(b"original")
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setattr(engines, "_which", lambda _: str(tesseract))
    monkeypatch.setattr(engines, "TESS_LANG", "tur")
    monkeypatch.setattr(subprocess, "check_output", lambda cmd, **_: "tesseract test-version\n")
    profile = identity.runtime("tesseract").profile("duz")
    assert profile["tesseract"]["version"] == "tesseract test-version"
    assert profile["tesseract"]["data_files"]["tur"]["path"] == str(weights)
    weights.write_bytes(b"replaced-weights")
    assert identity.runtime("tesseract").profile("duz")["id"] != profile["id"]


@pytest.mark.parametrize("options,serving", [
    ({"expect_managed": True}, "yeni harici model"),
    ({"external_model": "beklenen model"}, "değiştirilmiş model"),
    ({"external_model": "beklenen model"}, None),
])
def test_server_does_not_run_under_wrong_identity(monkeypatch, options, serving):
    monkeypatch.setattr(engines, "served_model", lambda _: serving)
    monkeypatch.setattr(engines.subprocess, "Popen", lambda *a, **k: pytest.fail("kimlik uyuşmadan sunucu açıldı"))
    with pytest.raises(engines.ServerError, match="yeniden deneyin"):
        engines._start_server(engines.ENGINES["dots.mocr"], **options)


def test_profile_id_ignores_ctime_and_root_move(monkeypatch, tmp_path):
    """chmod/ctime değişimi ya da depo klasörünün taşınması üretim önbelleğini sıfırlamamalı."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    revision = "b" * 40
    model = paths.hf_cache() / "models--dots-studio--dots.mocr"
    (model / "refs").mkdir(parents=True)
    (model / "refs" / "main").write_text(revision)
    (model / "snapshots" / revision).mkdir(parents=True)
    (model / "snapshots" / revision / "config.json").write_text("{}")
    monkeypatch.setattr(engines, "served_model", lambda _: None)
    vllm_a = tmp_path / "a" / "bin" / "vllm"
    vllm_b = tmp_path / "b" / "bin" / "vllm"
    for p in (vllm_a, vllm_b):
        p.parent.mkdir(parents=True)
        p.touch()
        dist = p.parent.parent / "lib" / "python3.12" / "site-packages" / "vllm-0.32.0.dist-info"
        dist.mkdir(parents=True)
    tess_a = {"version": "5.5", "language": "tur", "data_files": {
        "tur": {"path": "/a/tur.traineddata", "size": 100, "mtime_ns": 10, "ctime_ns": 1}}}
    tess_b = {"version": "5.5", "language": "tur", "data_files": {
        "tur": {"path": "/b/tur.traineddata", "size": 100, "mtime_ns": 10, "ctime_ns": 999}}}
    monkeypatch.setattr(engines, "_which", lambda _: str(vllm_a))
    id_a = identity.runtime("dots.mocr", tess=tess_a).profile("duzen")["id"]
    monkeypatch.setattr(engines, "_which", lambda _: str(vllm_b))
    id_b = identity.runtime("dots.mocr", tess=tess_b).profile("duzen")["id"]
    assert id_a == id_b


def test_package_version_ignores_prefixed_plugin_packages(tmp_path):
    bin_vllm = tmp_path / "bin" / "vllm"
    bin_vllm.parent.mkdir(parents=True)
    bin_vllm.touch()
    site = tmp_path / "lib64" / "python3.12" / "site-packages"
    (site / "vllm-0.32.0.dist-info").mkdir(parents=True)
    (site / "vllm-flash-attn-1.0.dist-info").mkdir(parents=True)
    assert identity._package_version(str(bin_vllm), "vllm") == "0.32.0"
