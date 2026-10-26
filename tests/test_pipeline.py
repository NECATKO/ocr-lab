import json
from pathlib import Path

import pytest
from PIL import Image

from ocrnow import paths, pipeline
from ocrnow.layout import page_to_json
from ocrnow.pages import DocJob, SourcePage
from ocrnow.pipeline import Options, cache_path, process_job, process_page

SPIKE_SIZE = (1653, 2339)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Önbellek ve kelime listesi geçici klasörde: gerçek gt/ ve ayarlar/ testlere sızmaz."""
    monkeypatch.setattr(paths, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(paths, "GT", tmp_path / "gt")
    monkeypatch.setattr(paths, "SETTINGS", tmp_path / "ayarlar")
    rendered = []

    def fake_render(page, dpi=200):
        rendered.append((page.index, dpi))
        out = tmp_path / f"syf{page.index}.png"
        Image.new("L", (1000, 1000), 255).save(out)
        return out

    monkeypatch.setattr(pipeline, "render", fake_render)
    return rendered


def fake_run(layout):
    calls = []

    def run(img, mode):
        calls.append(img)
        return layout
    return run, calls


def source(tmp_path, name="k.pdf", data=b"%PDF-sahte") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def blocks_json(*blocks) -> str:
    """Sentetik dots çıktısı: (kategori, bbox, metin) üçlüleri; bbox model (smart_resize) pikselinde."""
    return json.dumps([{"category": c, "bbox": b, "text": t} for c, b, t in blocks], ensure_ascii=False)


def tsv_rows(*lines) -> str:
    """Sentetik tesseract TSV: (kutu, kelimeler) çiftleri."""
    out = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for n, ((x1, y1, x2, y2), words) in enumerate(lines, 1):
        out.append(f"4\t1\t1\t1\t{n}\t0\t{x1}\t{y1}\t{x2 - x1}\t{y2 - y1}\t-1\t")
        for k, w in enumerate(words.split(), 1):
            out.append(f"5\t1\t1\t1\t{n}\t{k}\t{x1}\t{y1}\t10\t10\t90\t{w}")
    return "\n".join(out) + "\n"


def test_page_structure(spike_layout, spike_tsv, tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CACHE", tmp_path)
    run, _ = fake_run(spike_layout)
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 1), Options(), run, lambda img: spike_tsv, SPIKE_SIZE)
    assert p.book_page == "412" and p.book_page_side == "sol"
    assert sum(len(b.lines) for b in p.blocks if b.category == "Text") == 36


def test_page_structure_fields_and_marks(spike_layout, spike_tsv):
    run, _ = fake_run(spike_layout)
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 7), Options(), run, lambda img: spike_tsv, SPIKE_SIZE)
    assert (p.source, p.index, p.width, p.height, p.engine, p.mode) == ("k.pdf", 7, 1653, 2339, "dots.mocr", "duzen")
    assert p.tess_tsv == spike_tsv
    assert any(m.kind == "belirsiz" for b in p.blocks for m in b.marks)
    for b in p.blocks:
        assert [m.start for m in b.marks] == sorted(m.start for m in b.marks)
        if b.category in ("Page-header", "Page-footer", "Picture"):
            assert b.lines == [] and b.marks == []
    assert all(w.startswith("s. 7") for w in p.warnings)


def test_cache_hit_skips_engine(spike_layout, spike_tsv, tmp_path):
    run, calls = fake_run(spike_layout)
    job = DocJob("k", [SourcePage(source(tmp_path), 1)])
    first = process_job(job, Options(), run, lambda img: spike_tsv, progress=lambda m: None)
    n = len(calls)
    second = process_job(job, Options(), run, lambda img: spike_tsv, progress=lambda m: None)
    assert len(calls) == n
    assert page_to_json(first[0]) == page_to_json(second[0])


def test_engine_error_keeps_going(tmp_path):
    def run(img, mode):
        if img.name == "syf1.png":
            raise RuntimeError("sunucu yanıt vermedi")
        return "[]"
    job = DocJob("k", [SourcePage(source(tmp_path), 1), SourcePage(source(tmp_path), 2)])
    pages = process_job(job, Options(), run, lambda img: "", progress=lambda m: None)
    assert len(pages) == 2 and any("okunamadı" in w for w in pages[0].warnings)
    assert pages[0].blocks == [] and pages[0].index == 1
    assert "s. 1: okunamadı: sunucu yanıt vermedi" in pages[0].warnings
    assert not any("Traceback" in w for w in pages[0].warnings)
    assert not any("okunamadı" in w for w in pages[1].warnings)
    assert not cache_path(job.pages[0], Options()).exists()   # hatalı sayfa önbelleğe girmez


def test_blank_page():
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 1), Options(),
                     lambda img, m: "[]", lambda img: "", (100, 100))
    assert p.blocks == [] and not any("Traceback" in w for w in p.warnings)


def test_title_only_page_without_tesseract_lines():
    raw = blocks_json(("Section-header", [100, 100, 600, 160], "BİRİNCİ BÖLÜM"))
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 3), Options(), lambda img, m: raw,
                     lambda img: "", (1000, 1000))
    assert len(p.blocks) == 1 and p.blocks[0].lines and p.blocks[0].marks == []
    assert p.warnings == ["s. 3, blok 1: satır yapısı kurulamadı: Section-header bloğu, tesseract satırı yok"]


def test_parse_warning_gets_page_prefix():
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 4), Options(), lambda img, m: "",
                     lambda img: "", (100, 100))
    assert p.warnings == ["s. 4: model boş yanıt verdi"]


def test_apostrophes_restored_and_switchable(tmp_path):
    (tmp_path / "ayarlar").mkdir()
    (tmp_path / "ayarlar" / "kesme.txt").write_text("mes’ele\n", encoding="utf-8")
    raw = blocks_json(("Text", [0, 0, 980, 980], "bu mes'ele ve şu ba'zı"))
    tsv = tsv_rows(((10, 10, 900, 40), "bu mes'ele ve şu ba'zı"))
    page = SourcePage(Path("k.pdf"), 1)
    on = process_page(Path("x.png"), page, Options(), lambda img, m: raw, lambda img: tsv, (1000, 1000))
    b = on.blocks[0]
    assert b.text == "bu mes’ele ve şu ba‘zı"
    assert [(m.start, m.kind) for m in b.marks] == [(19, "kesme")]   # listede olmayan: sarı
    off = process_page(Path("x.png"), page, Options(apostrophes=False), lambda img, m: raw, lambda img: tsv,
                       (1000, 1000))
    assert off.blocks[0].text == "bu mes'ele ve şu ba'zı" and off.blocks[0].marks == []


def test_words_param_skips_word_list(monkeypatch):
    monkeypatch.setattr(pipeline, "build_word_list", lambda *a: pytest.fail("liste kurulmamalı"))
    raw = blocks_json(("Text", [0, 0, 980, 980], "bu mes'ele"))
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 1), Options(), lambda img, m: raw,
                     lambda img: "", (1000, 1000), words={"mes'ele": "mes’ele"})
    assert p.blocks[0].text == "bu mes’ele"


def test_size_read_from_image(tmp_path):
    img = tmp_path / "x.png"
    Image.new("L", (120, 80), 255).save(img)
    p = process_page(img, SourcePage(Path("k.pdf"), 1), Options(), lambda i, m: "[]", lambda i: "")
    assert (p.width, p.height) == (120, 80)


def test_cache_path_naming(tmp_path):
    src = SourcePage(source(tmp_path, "Kitap.pdf", b"x" * 1234), 5)
    base = paths.CACHE / "Kitap-1234"
    assert cache_path(src, Options()) == base / "syf5.dots.mocr.duzen.json"
    assert cache_path(src, Options(apostrophes=False)) == base / "syf5.dots.mocr.duzen.kesmesiz.json"
    assert cache_path(src, Options(dpi=300)) == base / "syf5_300dpi.dots.mocr.duzen.json"
    assert cache_path(src, Options(engine="glm-ocr", mode="duz")) == base / "syf5.glm-ocr.duz.json"
    img = SourcePage(source(tmp_path, "tarama.png", b"y" * 10), 1)
    assert cache_path(img, Options(dpi=300)) == paths.CACHE / "tarama-10" / "syf1.dots.mocr.duzen.json"


def test_unreadable_cache_is_miss(tmp_path):
    job = DocJob("k", [SourcePage(source(tmp_path), 1)])
    target = cache_path(job.pages[0], Options())
    target.parent.mkdir(parents=True)
    target.write_text('{"eski": "şema"}', encoding="utf-8")
    run, calls = fake_run("[]")
    pages = process_job(job, Options(), run, lambda img: "", progress=lambda m: None)
    assert len(calls) == 1 and pages[0].blocks == []
    assert json.loads(target.read_text(encoding="utf-8"))["index"] == 1   # yeniden yazıldı


def test_job_passes_dpi_and_skips_render_on_hit(tmp_path, isolated):
    run, _ = fake_run("[]")
    job = DocJob("k", [SourcePage(source(tmp_path), 1)])
    process_job(job, Options(dpi=300), run, lambda img: "", progress=lambda m: None)
    process_job(job, Options(dpi=300), run, lambda img: "", progress=lambda m: None)
    assert isolated == [(1, 300)]


def test_job_infers_book_pages_and_warns_pdf_order(tmp_path):
    footer = lambda n: blocks_json(("Text", [0, 0, 500, 500], "metin"), ("Page-footer", [100, 950, 200, 980], str(n)))
    outputs = {"syf1.png": footer(412), "syf2.png": "[]", "syf3.png": footer(414)}
    src = source(tmp_path)
    job = DocJob("k", [SourcePage(src, i) for i in (1, 2, 3)])
    pages = process_job(job, Options(), lambda img, m: outputs[img.name], lambda img: "", progress=lambda m: None)
    assert [(p.book_page, p.book_page_inferred) for p in pages] == [("412", False), ("413", True), ("414", False)]
    # önbellekte okunan numara durur, tahmin değil: ikinci çalıştırma aynı sonucu verir
    again = process_job(job, Options(), lambda img, m: pytest.fail("önbellek"), lambda img: "", progress=lambda m: None)
    assert [p.book_page for p in again] == ["412", "413", "414"]

    blank = process_job(DocJob("b", [SourcePage(src, 50), SourcePage(src, 51)]), Options(),
                        lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert [(p.book_page, p.book_page_inferred) for p in blank] == [("PDF s. 50", True), ("PDF s. 51", True)]
    assert "s. 50: kitap sayfa numarası bulunamadı, PDF sırası kullanıldı" in blank[0].warnings

    # görüntülerde index hep 1: etiket iş içindeki sıradan gelir
    imgs = [SourcePage(source(tmp_path, f"t{k}.png"), 1) for k in (1, 2)]
    shots = process_job(DocJob("t", imgs), Options(), lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert [p.book_page for p in shots] == ["PDF s. 1", "PDF s. 2"]


def test_job_first_page_option(tmp_path):
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (1, 2)])
    pages = process_job(job, Options(first_page=9), lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert [p.book_page for p in pages] == ["9", "10"]
    assert not any("PDF sırası" in w for p in pages for w in p.warnings)


def test_job_progress_messages(tmp_path):
    job = DocJob("kitap", [SourcePage(source(tmp_path), i) for i in (1, 2)])
    msgs: list[str] = []
    process_job(job, Options(), lambda img, m: "[]", lambda img: "", progress=msgs.append)
    assert len(msgs) == 2 and msgs[0].startswith("kitap s. 1/2") and msgs[1].startswith("kitap s. 2/2")
    msgs.clear()
    process_job(job, Options(), lambda img, m: "[]", lambda img: "", progress=msgs.append)
    assert len(msgs) == 2 and all("önbellek" in m for m in msgs)


def test_job_builds_word_list_once(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(pipeline, "build_word_list", lambda gt, user: built.append((gt, user)) or {})
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (1, 2, 3)])
    process_job(job, Options(), lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert built == [(paths.GT, paths.SETTINGS / "kesme.txt")]
