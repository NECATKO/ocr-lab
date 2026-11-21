import pytest

from ocrnow import engines, identity, paths


@pytest.fixture
def known_ocr(monkeypatch):
    monkeypatch.setattr(engines, "served_model", lambda _: None)
    monkeypatch.setattr(identity, "_hf_revision", lambda _: "a" * 40)
    monkeypatch.setattr(identity, "tesseract_profile", lambda: {"version": "test-5", "language": engines.TESS_LANG})


def _kitap_verisi(path) -> str:
    """Telifli kitap verisi git'te değildir; yoksa test atlanır."""
    if not path.exists():
        pytest.skip("kitap verisi yok")
    return path.read_text(encoding="utf-8")


@pytest.fixture
def spike_layout() -> str:
    return _kitap_verisi(paths.OUTPUT / "_spike" / "dots_layout_syf1.json")


@pytest.fixture
def spike_tsv() -> str:
    return _kitap_verisi(paths.OUTPUT / "_spike" / "tess_syf1.tsv")


@pytest.fixture
def gt_syf1() -> str:
    return _kitap_verisi(paths.GT / "tarama5_syf1.txt")
