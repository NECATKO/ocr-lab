import pytest

from ocrnow import paths


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
