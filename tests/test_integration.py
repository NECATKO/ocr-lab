"""Uçtan uca (M11): ocr-now CLI → gerçek collect_jobs/render/process_job/önbellek → gerçek docx/txt dışa aktarıcıları.

Yalnız model sunucusu (engines.server) ve tesseract sahtedir; GPU ve tesseract gerekmez.
"""
import json
import sys
import textwrap
import types
from contextlib import contextmanager

import pypdfium2 as pdfium
import pytest
from docx import Document
from PIL import Image

from ocrnow import engines, export_docx, paths, pipeline
from ocrnow.cli import main
from ocrnow.export_txt import REVIEW_TEXT
from ocrnow.gui.job import Job
from ocrnow.pages import SourcePage


def make_pdf(path, n):
    pdf = pdfium.PdfDocument.new()
    for _ in range(n):
        pdf.new_page(200, 300)
    pdf.save(str(path))
    pdf.close()
    return path


@pytest.fixture
def world(tmp_path, monkeypatch, known_ocr):
    for name, sub in [("INPUT", "input"), ("CACHE", "output/_cache"), ("PAGES", "output/_pages"),
                      ("DOCS_OUT", "output/belgeler"), ("LOGS", "output/_logs"), ("GT", "gt"),
                      ("SETTINGS", "ayarlar")]:
        monkeypatch.setattr(paths, name, tmp_path / sub)
    monkeypatch.setattr(engines, "missing", lambda name: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    state = types.SimpleNamespace(footers={}, fail=set(), blank=set(), truncated=set(), text={},
                                  starts=0, start_error=None, tsv_calls=0)

    def tsv(img):                     # satır yok: bloklar akan paragraf
        state.tsv_calls += 1
        return ""

    monkeypatch.setattr(engines, "tesseract_tsv", tsv)

    @contextmanager
    def server(name, **options):
        state.starts += 1
        if state.start_error:
            raise state.start_error

        def run(img, mode):
            n = int(img.stem.removeprefix("syf"))                   # render: .../syf<N>.png
            if n in state.fail:
                raise RuntimeError("model yanıt vermedi")
            if n in state.blank:
                return "[]"
            blocks = [{"category": "Text", "bbox": [50, 50, 500, 400], "text": state.text.get(n, f"metin {n}")}]
            if state.footers.get(n) is not None:
                blocks.append({"category": "Page-footer", "bbox": [50, 760, 120, 790], "text": str(state.footers[n])})
            raw = json.dumps(blocks)
            return engines.Truncated(raw[:-3]) if n in state.truncated else raw
        yield run

    monkeypatch.setattr(engines, "server", server)
    state.pdf = make_pdf(tmp_path / "k.pdf", 3)
    return state


def footers(docx_path):
    return [s.footer.paragraphs[0].text for s in Document(docx_path).sections]


def test_numbering_gap_inferred_end_to_end(world, capsys):
    world.footers = {1: 412, 3: 414}
    assert main([str(world.pdf), "-f", "docx", "txt"]) == 0
    assert footers(paths.DOCS_OUT / "k.docx") == ["412", "[413]", "414"]
    txt = (paths.DOCS_OUT / "k.txt").read_text(encoding="utf-8")
    assert txt == "--- s. 412 ---\nmetin 1\n\n--- s. [413] ---\nmetin 2\n\n--- s. 414 ---\nmetin 3\n"
    assert "tahmin edilen sayfa numaraları:\n  [413]" in capsys.readouterr().out
    assert world.tsv_calls == 3                                # gerçek tesseract çağrılmadı


def test_failed_page_has_placeholder_end_to_end(world, capsys):
    world.footers, world.fail = {1: 412, 2: 413, 3: 414}, {2}
    assert main([str(world.pdf), "-f", "docx", "txt"]) == 1
    paras = [p.text for p in Document(paths.DOCS_OUT / "k.docx").paragraphs]
    assert paras == ["metin 1", "[Bu sayfa okunamadı]", "metin 3"]
    assert footers(paths.DOCS_OUT / "k.docx") == ["412", "[413]", "414"]
    assert "--- s. [413] ---\n[Bu sayfa okunamadı]" in (paths.DOCS_OUT / "k.txt").read_text(encoding="utf-8")
    assert "s. 2: okunamadı: model yanıt vermedi" in capsys.readouterr().out


def test_all_pages_failed_writes_nothing_end_to_end(world, capsys):
    world.fail = {1, 2, 3}
    assert main([str(world.pdf), "-f", "docx", "txt"]) == 1
    assert not paths.DOCS_OUT.exists() or list(paths.DOCS_OUT.iterdir()) == []
    assert "k: hiçbir sayfa okunamadı, belge yazılmadı" in capsys.readouterr().out


def test_server_start_failure_end_to_end(world, capsys):
    world.start_error = RuntimeError("sunucu ayağa kalkmadı")
    assert main([str(world.pdf), "-f", "txt"]) == 1
    err = capsys.readouterr().err
    assert "'dots.mocr' modeli başlatılamadı: sunucu ayağa kalkmadı" in err and err.count("Hata:") == 1
    assert world.starts == 1 and not paths.DOCS_OUT.exists()


def test_all_cached_rerun_end_to_end(world):
    world.footers = {1: 412, 2: 413, 3: 414}
    assert main([str(world.pdf), "-p", "2-3", "-f", "docx"]) == 0
    first = paths.DOCS_OUT / "k_s2-3.docx"
    assert footers(first) == ["413", "414"] and world.starts == 1
    world.start_error = AssertionError("hepsi önbellekteyken sunucu açılmamalı")
    assert main([str(world.pdf), "-p", "2-3", "-f", "docx", "txt"]) == 0
    assert world.starts == 1                                   # model hiç çağrılmadı
    again = paths.DOCS_OUT / "k_s2-3_2.docx"                    # eski belge ezilmedi
    assert [p.text for p in Document(again).paragraphs] == [p.text for p in Document(first).paragraphs]
    assert (paths.DOCS_OUT / "k_s2-3_2.txt").exists()


def test_blank_page_marked_in_both_formats_and_only_it_is_retried(world, tmp_path):
    world.blank = {2}
    world.footers = {1: 412, 3: 414}
    report = tmp_path / "is.json"
    args = [str(world.pdf), "-f", "docx", "txt", "--rapor", str(report)]
    assert main(args) == 1
    docx = Document(paths.DOCS_OUT / "k.docx")
    assert [p.text for p in docx.paragraphs] == ["metin 1", REVIEW_TEXT, "metin 3"]
    assert REVIEW_TEXT in (paths.DOCS_OUT / "k.txt").read_text(encoding="utf-8")
    assert footers(paths.DOCS_OUT / "k.docx") == ["412", "[413]", "414"]
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["isler"][0]["metin_kontrol"] == ["s. 2"] and not data["okunamayan"]
    assert not pipeline.cache_path(SourcePage(world.pdf, 2), pipeline.Options()).exists()
    world.blank.clear()
    assert main(args) == 0
    assert world.tsv_calls == 4  # ilk koşuda 3 sayfa; yeniden yalnız kontrol gerekli olan çalıştı
    assert REVIEW_TEXT not in (paths.DOCS_OUT / "k_2.txt").read_text(encoding="utf-8")


def test_truncated_page_preserves_text_but_reports_incomplete_and_is_retried(world, tmp_path):
    world.truncated = {2}
    report = tmp_path / "is.json"
    args = [str(world.pdf), "-f", "docx", "txt", "--rapor", str(report)]
    assert main(args) == 1
    paragraphs = [p.text for p in Document(paths.DOCS_OUT / "k.docx").paragraphs]
    assert paragraphs[0] == "metin 1" and paragraphs[2:] == ["metin 2", "metin 3"]
    assert "model çıktısı kesildi" in paragraphs[1].lower()
    assert f"{paragraphs[1]}\n\nmetin 2" in (paths.DOCS_OUT / "k.txt").read_text(encoding="utf-8")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["isler"][0]["kesilen"] == ["s. 2"] and not data["okunamayan"]
    assert data["isler"][0]["metin_kontrol"] == []
    assert not pipeline.cache_path(SourcePage(world.pdf, 2), pipeline.Options()).exists()
    assert world.tsv_calls == 3

    world.truncated.clear()
    assert main(args) == 0
    assert world.tsv_calls == 4
    assert paragraphs[1] not in (paths.DOCS_OUT / "k_2.txt").read_text(encoding="utf-8")


def test_word_marks_xml_incompatible_character_and_txt_preserves_it(world, tmp_path):
    world.text[2] = "önce\x00sonra"
    world.footers = {2: 412}
    report = tmp_path / "is.json"
    args = [str(world.pdf), "-f", "docx", "txt", "--rapor", str(report)]
    assert main(args) == 0
    paragraphs = [p.text for p in Document(paths.DOCS_OUT / "k.docx").paragraphs]
    assert paragraphs == ["metin 1", "önce�sonra", "metin 3"]
    assert "önce\x00sonra" in (paths.DOCS_OUT / "k.txt").read_text(encoding="utf-8")
    warnings = json.loads(report.read_text(encoding="utf-8"))["isler"][0]["uyarilar"]
    assert any("docx sayfa 412" in w and "1 karakter" in w for w in warnings)


def test_word_value_error_does_not_block_txt_or_report(world, tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("Word verisi geçersiz")

    monkeypatch.setattr(export_docx, "write_docx", broken)
    report = tmp_path / "is.json"
    assert main([str(world.pdf), "-f", "docx", "txt", "--rapor", str(report)]) == 1
    assert not (paths.DOCS_OUT / "k.docx").exists()
    txt = paths.DOCS_OUT / "k.txt"
    assert "metin 2" in txt.read_text(encoding="utf-8")
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["belgeler"] == [str(txt)]
    assert data["isler"][0]["yazma_hatalari"] == ["k.docx yazılamadı (Word verisi geçersiz)"]


@pytest.mark.parametrize("case", ["kesik", "word_hatasi"])
def test_gui_job_is_partial_and_keeps_downloads(world, tmp_path, monkeypatch, case):
    """GUI'nin gerçek alt süreci CLI'ı ve rapor şemasını kullanır."""
    runner = tmp_path / "run_ocr.py"
    runner.write_text(textwrap.dedent("""\
        from contextlib import contextmanager
        from ocrnow import cli, engines, export_docx
        import os

        @contextmanager
        def server(name, **options):
            yield lambda *_: engines.Truncated("yarım metin") if case == "kesik" else "tam metin"

        def bad_word(*args, **kwargs):
            raise ValueError("Word hatası")

        case = os.environ["OCR_TEST_CASE"]
        engines.server = server
        engines.missing = lambda name: None
        engines.tesseract_tsv = lambda img: ""
        if case == "word_hatasi":
            export_docx.write_docx = bad_word
        raise SystemExit(cli.main())
        """), encoding="utf-8")
    monkeypatch.setenv("OCRNOW_ROOT", str(tmp_path))
    monkeypatch.setenv("OCR_TEST_CASE", case)
    job = Job([str(world.pdf), "-p", "2", "-o", "duz", "-f", "docx", "txt"], cmd=[sys.executable, str(runner)])
    job.start()
    assert job.wait(20)
    snapshot = job.snapshot()
    assert snapshot["durum"] == "kismi" and snapshot["cikis"] == 1
    result = snapshot["sonuc"]
    txt = paths.DOCS_OUT / "k_s2.txt"
    assert str(txt) in result["belgeler"] and txt.is_file()
    details = result["isler"][0]
    if case == "kesik":
        assert details["kesilen"] == ["s. 2"] and not details["yazma_hatalari"]
        assert str(txt.with_suffix(".docx")) in result["belgeler"]
    else:
        assert details["yazma_hatalari"] == ["k_s2.docx yazılamadı (Word hatası)"] and not details["kesilen"]
        assert result["belgeler"] == [str(txt)]


def test_equal_size_equal_name_scans_export_their_own_text(world, tmp_path, monkeypatch):
    scans = [tmp_path / folder / "k.png" for folder in ("a", "b")]
    for scan, color in zip(scans, ("red", "blue")):
        scan.parent.mkdir()
        Image.new("RGB", (24, 24), color).save(scan, compress_level=0)
    assert scans[0].stat().st_size == scans[1].stat().st_size
    reads = []

    @contextmanager
    def server(name, **options):
        def run(img, mode):
            with Image.open(img) as im:
                text = "kırmızı" if im.getpixel((0, 0))[0] else "mavi"
            reads.append(text)
            return json.dumps([{"category": "Text", "bbox": [0, 0, 10, 10], "text": text}])
        yield run

    monkeypatch.setattr(engines, "server", server)
    for scan in [*scans, scans[1]]:
        assert main([str(scan), "-f", "txt"]) == 0
    assert reads == ["kırmızı", "mavi"]
    assert [(paths.DOCS_OUT / f"{name}.txt").read_text(encoding="utf-8").splitlines()[-1]
            for name in ("k", "k_2", "k_3")] == ["kırmızı", "mavi", "mavi"]
