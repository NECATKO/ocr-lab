import runpy
from pathlib import Path

import pypdfium2 as pdfium
import pytest


def test_reference_pdfs_reproduce_and_have_no_text_layer(tmp_path):
    corpus = Path(__file__).resolve().parents[1] / "benchmark"
    gen = runpy.run_path(str(corpus / "generate.py"))
    if not gen["DEFAULT_FONT"].is_file():
        pytest.skip("DejaVu Serif kurulu değil")
    first = gen["generate"](tmp_path / "first")
    second = gen["generate"](tmp_path / "second")
    assert len(first) == len(second) == 3
    for a, b in zip(first, second):
        assert a.read_bytes() == b.read_bytes()
        with pdfium.PdfDocument(a) as pdf:
            assert len(pdf) == 2
            for i, page in enumerate(pdf, 1):
                assert not page.get_textpage().get_text_range()
                assert (corpus / "referans" / f"{a.stem}_syf{i}.txt").is_file()
