import json
from pathlib import Path

import pytest
from PIL import Image

from ocrnow import engines, paths, pipeline
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
    assert pages[0].failed is True and pages[1].failed is False
    assert "s. 1: okunamadı: sunucu yanıt vermedi" in pages[0].warnings
    assert not any("Traceback" in w for w in pages[0].warnings)
    assert not any("okunamadı" in w for w in pages[1].warnings)
    assert not cache_path(job.pages[0], Options()).exists()   # hatalı sayfa önbelleğe girmez


def test_server_error_is_not_a_page_error(tmp_path):
    """Sunucu açılamadıysa (engines.ServerError) iş sayfa sayfa sürmez: hata çağırana gider."""
    job = DocJob("k", [SourcePage(source(tmp_path), 1), SourcePage(source(tmp_path), 2)])
    calls = []

    def run(img, mode):
        calls.append(img)
        raise engines.ServerError("'dots.mocr' modeli başlatılamadı: sunucu ayağa kalkmadı")
    with pytest.raises(engines.ServerError):
        process_job(job, Options(), run, lambda img: "", progress=lambda m: None)
    assert len(calls) == 1


def test_tesseract_runs_before_model():
    """R22: tesseract yoksa ya da çökerse sayfa VLM'i (~25 sn) boşuna beklemeden düşer."""
    order = []
    process_page(Path("x.png"), SourcePage(Path("k.pdf"), 1), Options(),
                 lambda img, m: order.append("model") or "[]", lambda img: order.append("tesseract") or "", (100, 100))
    assert order == ["tesseract", "model"]


def test_truncated_model_output_warns():
    raw = engines.Truncated(blocks_json(("Text", [0, 0, 500, 500], "metin")))
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 5), Options(mode="duz"), lambda img, m: raw,
                     lambda img: "", (1000, 1000))
    assert "s. 5: model çıktısı uzunluk sınırında kesildi, sayfa sonu eksik olabilir" in p.warnings
    plain = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 5), Options(mode="duz"), lambda img, m: "metin",
                         lambda img: "", (1000, 1000))
    assert not any("kesildi" in w for w in plain.warnings)


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


def test_apostrophes_restored_in_header_and_caption_only():
    """M4: kesme geri getirme Page-header ve Caption'a da uygulanır; satır yapısı ve belirsiz işaret uygulanmaz."""
    raw = blocks_json(("Page-header", [0, 0, 980, 40], "mes'ele"), ("Caption", [0, 100, 980, 140], "ba'zı resim"),
                      ("Page-footer", [0, 900, 980, 940], "mes'ele 12"))
    tsv = tsv_rows(((10, 10, 900, 40), "başka"), ((10, 110, 900, 140), "farklı okuma"))
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 1), Options(), lambda img, m: raw, lambda img: tsv,
                     (1000, 1000), words={"mes'ele": "mes’ele"})
    header, caption, footer = p.blocks
    assert header.text == "mes’ele" and caption.text == "ba‘zı resim"
    assert footer.text == "mes'ele 12"                                  # altlık yalnız numara için okunur
    assert [m.kind for m in caption.marks] == ["kesme"] and header.marks == []
    assert header.lines == caption.lines == [] and p.warnings == []


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
    assert cache_path(src, Options(apostrophes=False)) == base / "syf5.dots.mocr.duzen.json"   # R27: ek yok
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
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == 2   # yeniden yazıldı


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

    # görüntülerde index hep 1: numarasız görüntünün etiketi iş içindeki sıradan gelir (R17)
    imgs = [SourcePage(source(tmp_path, f"t{k}.png"), 1) for k in (1, 2)]
    shots = process_job(DocJob("t", imgs), Options(), lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert [p.book_page for p in shots] == ["görsel 1", "görsel 2"]
    assert shots[1].warnings == ["t2.png: kitap sayfa numarası bulunamadı, görsel sırası kullanıldı"]


def test_job_first_page_option(tmp_path):
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (1, 2)])
    pages = process_job(job, Options(first_page=9), lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert [p.book_page for p in pages] == ["9", "10"]
    assert not any("PDF sırası" in w for p in pages for w in p.warnings)


def test_job_progress_messages(tmp_path):
    job = DocJob("kitap", [SourcePage(source(tmp_path), i) for i in (1, 2)])
    msgs: list[str] = []
    process_job(job, Options(), lambda img, m: "[]", lambda img: "", progress=msgs.append)
    assert msgs == ["kitap: s. 1 (1/2) okunuyor…", "kitap: s. 2 (2/2) okunuyor…"]
    msgs.clear()
    process_job(job, Options(), lambda img, m: "[]", lambda img: "", progress=msgs.append)
    assert len(msgs) == 2 and all("önbellek" in m for m in msgs)


def test_job_builds_word_list_once(tmp_path, monkeypatch):
    built = []
    monkeypatch.setattr(pipeline, "build_word_list", lambda gt, user: built.append((gt, user)) or {})
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (1, 2, 3)])
    process_job(job, Options(), lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert built == [(paths.GT, paths.SETTINGS / "kesme.txt")]


def with_footer(n: int | None) -> str:
    """Tek Text bloğu ve (n verilirse) altlıkta n numarası olan sentetik sayfa."""
    foot = [("Page-footer", [100, 950, 200, 980], str(n))] if n is not None else []
    return blocks_json(("Text", [0, 0, 500, 500], "metin"), *foot)


def footer_run(numbers: dict[int, int | None]):
    """syf<N>.png → altlığında numbers[N] olan sayfa (None: numarasız)."""
    return lambda img, mode: with_footer(numbers[int(img.stem[3:])])


def book(pages):
    return [(p.book_page, p.book_page_inferred) for p in pages]


def test_gapped_selection_uses_pdf_index(tmp_path):
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (1, 3, 7)])
    quiet = dict(tsv=lambda img: "", progress=lambda m: None)
    pages = process_job(job, Options(), footer_run({1: 412, 3: 414, 7: 418}), **quiet)
    assert book(pages) == [("412", False), ("414", False), ("418", False)]
    other = DocJob("k", [SourcePage(source(tmp_path, "b.pdf"), i) for i in (1, 3, 7)])
    pages = process_job(other, Options(), footer_run({1: 412, 3: None, 7: None}), **quiet)
    assert book(pages) == [("412", False), ("414", True), ("418", True)]


def test_overridden_reading_warns_and_real_break_is_kept(tmp_path):
    quiet = dict(tsv=lambda img: "", progress=lambda m: None)
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (1, 2, 3)])
    pages = process_job(job, Options(), footer_run({1: 412, 2: 431, 3: 414}), **quiet)
    assert book(pages) == [("412", False), ("413", True), ("414", False)]
    assert "s. 2: okunan 431 → [413]" in pages[1].warnings      # R21: düzeltilen okuma görünür
    # C1: 412–415, sonra 418–425 (iki tarama sayfası yok): doğru okunan numaralar ezilmez
    numbers = dict(enumerate([*range(412, 416), *range(418, 426)], 1))
    other = DocJob("b", [SourcePage(source(tmp_path, "b.pdf"), i) for i in numbers])
    pages = process_job(other, Options(), footer_run(numbers), **quiet)
    assert book(pages) == [(str(n), False) for n in numbers.values()]
    assert not any("okunan" in w for p in pages for w in p.warnings)


def test_disagreeing_readings_are_trusted(tmp_path):
    # R25: iki sayfalık alıntıda birbirini tutmayan okumalar okunduğu gibi kalır: köşeli parantez ve uyarı yok
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (10, 11)])
    pages = process_job(job, Options(), footer_run({10: 412, 11: 431}), lambda img: "", progress=lambda m: None)
    assert book(pages) == [("412", False), ("431", False)]
    assert all(p.warnings == ["s. %d, blok 1: satır yapısı kurulamadı: Text bloğu, tesseract satırı yok" % p.index]
               for p in pages)


def numbered(n: int | None, side: str = "sol", header: str | None = None) -> str:
    """Altlığında n numarası (side tarafında) ve isteğe bağlı başlığı olan sentetik sayfa."""
    x = 100 if side == "sol" else 800
    head = [("Page-header", [300, 20, 700, 50], header)] if header else []
    foot = [("Page-footer", [x, 950, x + 100, 980], str(n))] if n is not None else []
    return blocks_json(*head, ("Text", [0, 100, 500, 500], "metin"), *foot)


def replies_run(replies: dict[int, str]):
    return lambda img, mode: replies[int(img.stem[3:])]


def test_reading_contradicting_side_rule_is_flagged(tmp_path):
    # R25: okumalardan çift=sol öğrenilir; sağ sayfada okunan 416 okunduğu gibi kalır ama işaretlenir ve uyarılır
    replies = {1: numbered(412, "sol"), 2: numbered(413, "sag"), 3: numbered(414, "sol"), 4: numbered(416, "sag")}
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in replies])
    pages = process_job(job, Options(), replies_run(replies), lambda img: "", progress=lambda m: None)
    assert book(pages) == [("412", False), ("413", False), ("414", False), ("416", True)]
    assert "s. 4: okunan 416 sayfanın tarafıyla çelişiyor (sağ sayfada çift numara), kontrol edin" in pages[3].warnings
    assert not any("çelişiyor" in w for p in pages[:3] for w in p.warnings)


def test_guess_contradicting_header_is_not_made(tmp_path):
    # ters taranmış koşu 412, 411, 410; s. 4 numarasız, başlığı yazar adı (hep çift sayfada): 409 yazılmaz
    replies = {1: numbered(412, "sol", "YAZAR ADI"), 2: numbered(411, "sag", "KİTAP ADI"),
               3: numbered(410, "sol", "Yazar Âdı"), 4: numbered(None, header="YAZAR ADI")}
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in replies])
    pages = process_job(job, Options(), replies_run(replies), lambda img: "", progress=lambda m: None)
    assert book(pages) == [("412", False), ("411", False), ("410", False), ("PDF s. 4", True)]
    assert "s. 4: kitap sayfa numarası bulunamadı, PDF sırası kullanıldı" in pages[3].warnings
    replies[4] = numbered(None, header="KİTAP ADI")
    other = DocJob("b", [SourcePage(source(tmp_path, "b.pdf"), i) for i in replies])
    assert book(process_job(other, Options(), replies_run(replies), lambda img: "", progress=lambda m: None))[3] \
        == ("409", True)


def test_same_number_read_twice_warns_each_page(tmp_path):
    replies = {1: numbered(285, "sag"), 2: numbered(199, "sag"), 3: numbered(285, "sag")}
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in replies])
    pages = process_job(job, Options(), replies_run(replies), lambda img: "", progress=lambda m: None)
    assert book(pages) == [("285", False), ("199", False), ("285", False)]
    assert "s. 1: 285 numarası s. 3'te de okundu (aynı sayfa iki kez taranmış olabilir)" in pages[0].warnings
    assert "s. 3: 285 numarası s. 1'de de okundu (aynı sayfa iki kez taranmış olabilir)" in pages[2].warnings
    assert not any("okundu" in w for w in pages[1].warnings)
    # --ilk-sayfa verilince eski davranış: numaralar sıradan, tekrar uyarısı yok
    again = process_job(job, Options(first_page=1), replies_run(replies), lambda img: "", progress=lambda m: None)
    assert not any("okundu" in w for p in again for w in p.warnings)


@pytest.mark.parametrize("pid,exp", [
    ("s. 12", "s. 12'de de"), ("s. 3", "s. 3'te de"), ("s. 6", "s. 6'da da"), ("s. 10", "s. 10'da da"),
    ("s. 40", "s. 40'ta da"), ("s. 70", "s. 70'te de"), ("s. 100", "s. 100'de de"), ("s. 2000", "s. 2000'de de"),
    ("k.pdf s. 9", "k.pdf s. 9'da da"), ("tarama.png", "tarama.png sayfasında da")])
def test_also_in(pid, exp):
    # bulunma eki ve "de/da" bağlacı ünlü uyumuyla
    assert pipeline._also_in(pid) == exp


def test_duplicate_warning_harmony_and_corrected_readings_ignored(tmp_path):
    quiet = dict(tsv=lambda img: "", progress=lambda m: None)
    replies = {1: numbered(285, "sag"), 6: numbered(285, "sag")}
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in replies])
    pages = process_job(job, Options(), replies_run(replies), **quiet)
    assert "s. 1: 285 numarası s. 6'da da okundu (aynı sayfa iki kez taranmış olabilir)" in pages[0].warnings
    # s. 2'deki 431 koşudan 413'e düzeltildi: s. 4'teki 431 ile tekrar sayılmaz
    numbers = {1: 412, 2: 431, 3: 414, 4: 431}
    other = DocJob("b", [SourcePage(source(tmp_path, "b.pdf"), i) for i in numbers])
    pages = process_job(other, Options(), footer_run(numbers), **quiet)
    assert book(pages)[1] == ("413", True) and not any("okundu" in w for p in pages for w in p.warnings)


def test_side_flagged_reading_is_marked_for_check(tmp_path):
    replies = {1: numbered(412, "sol"), 2: numbered(413, "sag"), 3: numbered(414, "sol"), 4: numbered(416, "sag"),
               5: numbered(None)}
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in replies])
    pages = process_job(job, Options(), replies_run(replies), lambda img: "", progress=lambda m: None)
    assert [p.book_page_check for p in pages] == [False, False, False, True, False]


def test_chapter_opening_without_number_is_guessed(tmp_path):
    numbers = {5: None, 6: 402, 7: 403, 8: 404}
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in numbers])
    pages = process_job(job, Options(), footer_run(numbers), lambda img: "", progress=lambda m: None)
    assert book(pages) == [("401", True), ("402", False), ("403", False), ("404", False)]


def test_first_page_with_gap(tmp_path):
    job = DocJob("k", [SourcePage(source(tmp_path), i) for i in (5, 6, 9)])
    pages = process_job(job, Options(first_page=100), footer_run({5: None, 6: None, 9: None}),
                        lambda img: "", progress=lambda m: None)
    assert [p.book_page for p in pages] == ["100", "101", "104"]


def test_merged_pdfs_keep_own_numbers_and_prefix(tmp_path):
    a, b = source(tmp_path, "a.pdf"), source(tmp_path, "b.pdf", b"%PDF-baska")
    job = DocJob("a_birlesik", [SourcePage(a, i) for i in (1, 2)] + [SourcePage(b, i) for i in (1, 2)])
    replies = [with_footer(n) for n in (10, 11, 1, None)]   # sahte render iki PDF'te de syf1.png verir: sırayla
    run = lambda img, mode: replies.pop(0)  # noqa: E731
    msgs: list[str] = []
    pages = process_job(job, Options(first_page=10), run, lambda img: "", progress=msgs.append)
    assert book(pages) == [("10", False), ("11", False), ("1", False), ("2", True)]
    assert msgs[2] == "a_birlesik: b.pdf s. 1 (3/4) okunuyor…"
    # bloğun satır uyarısı birleşik işte dosya adıyla başlar; aynı önbellekten tek kaynaklı işte öneksiz
    assert pages[2].warnings == ["b.pdf s. 1, blok 1: satır yapısı kurulamadı: Text bloğu, tesseract satırı yok"]
    again = process_job(job, Options(), lambda img, m: pytest.fail("önbellek"), lambda img: "",
                        progress=lambda m: None)
    assert again[2].warnings == pages[2].warnings
    alone = process_job(DocJob("b", [job.pages[2]]), Options(), lambda img, m: pytest.fail("önbellek"),
                        lambda img: "", progress=lambda m: None)
    assert alone[0].warnings[0] == "s. 1, blok 1: satır yapısı kurulamadı: Text bloğu, tesseract satırı yok"


def test_image_job_warning_prefix(tmp_path):
    imgs = [SourcePage(source(tmp_path, f"tarama{k}.png"), 1) for k in (1, 2, 3)]
    raw = blocks_json(("Text", [0, 0, 500, 500], "metin"), ("Text", [0, 600, 500, 900], "öteki"))
    msgs: list[str] = []
    pages = process_job(DocJob("tarama", imgs), Options(), lambda img, m: raw, lambda img: "", progress=msgs.append)
    assert "tarama3.png, blok 2: satır yapısı kurulamadı: Text bloğu, tesseract satırı yok" in pages[2].warnings
    assert msgs[2] == "tarama: tarama3.png (3/3) okunuyor…"


def test_failed_page_prefix_in_merged_job(tmp_path):
    a, b = source(tmp_path, "a.pdf"), source(tmp_path, "b.pdf", b"%PDF-baska")
    job = DocJob("m", [SourcePage(a, 1), SourcePage(b, 4)])

    def run(img, mode):
        raise RuntimeError("çöktü")
    pages = process_job(job, Options(), run, lambda img: "", progress=lambda m: None)
    assert pages[1].warnings[0] == "b.pdf s. 4: okunamadı: çöktü"
    assert pages[1].warnings[1] == "b.pdf s. 4: kitap sayfa numarası bulunamadı, PDF sırası kullanıldı"
    assert pages[1].book_page == "PDF s. 4"


def test_cache_write_failure_leaves_no_tmp(tmp_path, monkeypatch):
    job = DocJob("k", [SourcePage(source(tmp_path), 1)])
    target = cache_path(job.pages[0], Options())

    def broken(self, *a, **k):
        raise OSError("disk dolu")
    monkeypatch.setattr(Path, "replace", broken)
    pages = process_job(job, Options(), lambda img, m: "[]", lambda img: "", progress=lambda m: None)
    assert "s. 1: önbelleğe yazılamadı (disk dolu)" in pages[0].warnings
    assert list(target.parent.iterdir()) == []


def test_markdown_emphasis_kept_as_marks_and_stripped_before_rules():
    # R26: yıldızlar parse'ta kalkar; kesme ve sarı işaret yıldızsız metinde çalışır, biçim işaretleri korunur
    raw = blocks_json(("Text", [0, 0, 980, 980], "bu *mes'ele* ve **şu** ba'zı"),
                      ("Page-header", [0, 0, 980, 40], "*Kitab'ın adı*"))
    tsv = tsv_rows(((10, 10, 900, 40), "bu mes'ele ve şu ba'zı"))
    p = process_page(Path("x.png"), SourcePage(Path("k.pdf"), 1), Options(), lambda img, m: raw, lambda img: tsv,
                     (1000, 1000), words={"mes'ele": "mes’ele"})
    body, header = p.blocks
    assert body.text == "bu mes’ele ve şu ba‘zı"
    assert [(m.start, m.end, m.kind) for m in body.marks] == [(3, 10, "italik"), (14, 16, "kalin"), (19, 20, "kesme")]
    assert header.text == "Kitab’ın adı" and [m.kind for m in header.marks] == ["italik"]


def test_cache_stores_raw_model_output_and_tsv(tmp_path):
    # R27: önbellek işlenmiş sayfa değil, ham model çıktısı + tesseract TSV + görüntü boyutu (+ motor, mod) saklar
    raw = blocks_json(("Text", [0, 0, 980, 980], "bu *mes'ele*"))
    tsv = tsv_rows(((10, 10, 900, 40), "bu mes'ele"))
    job = DocJob("k", [SourcePage(source(tmp_path), 1)])
    process_job(job, Options(), lambda img, m: raw, lambda img: tsv, progress=lambda m: None)
    cached = json.loads(cache_path(job.pages[0], Options()).read_text(encoding="utf-8"))
    assert cached == {"version": 2, "engine": "dots.mocr", "mode": "duzen", "width": 1000, "height": 1000,
                      "truncated": False, "raw": raw, "tsv": tsv}


def test_cache_hit_rebuilds_page_with_current_rules(tmp_path):
    # önbellek isabetinde sayfa ham çıktıdan yeniden kurulur: kelime listesi ve --kesme-yok GPU'suz uygulanır
    raw = blocks_json(("Text", [0, 0, 980, 980], "bu mes'ele"))
    tsv = tsv_rows(((10, 10, 900, 40), "bu mes'ele"))
    job = DocJob("k", [SourcePage(source(tmp_path), 1)])
    first = process_job(job, Options(), lambda img, m: raw, lambda img: tsv, progress=lambda m: None)
    assert first[0].blocks[0].text == "bu mes‘ele" and [m.kind for m in first[0].blocks[0].marks] == ["kesme"]
    (tmp_path / "ayarlar").mkdir()
    (tmp_path / "ayarlar" / "kesme.txt").write_text("mes’ele\n", encoding="utf-8")
    cached = dict(run=lambda img, m: pytest.fail("önbellek"), tsv=lambda img: pytest.fail("önbellek"),
                  progress=lambda m: None)
    second = process_job(job, Options(), **cached)
    assert second[0].blocks[0].text == "bu mes’ele" and second[0].blocks[0].marks == []
    plain = process_job(job, Options(apostrophes=False), **cached)
    assert plain[0].blocks[0].text == "bu mes'ele"
    assert second[0].tess_tsv == tsv and (second[0].width, second[0].height) == (1000, 1000)


def test_truncated_flag_survives_cache(tmp_path):
    raw = engines.Truncated(blocks_json(("Text", [0, 0, 500, 500], "metin")))
    job = DocJob("k", [SourcePage(source(tmp_path), 5)])
    process_job(job, Options(), lambda img, m: raw, lambda img: "", progress=lambda m: None)
    again = process_job(job, Options(), lambda img, m: pytest.fail("önbellek"), lambda img: "",
                        progress=lambda m: None)
    assert "s. 5: model çıktısı uzunluk sınırında kesildi, sayfa sonu eksik olabilir" in again[0].warnings


def test_old_format_cache_is_miss(tmp_path):
    # sürüm 1 (işlenmiş Page) ve motor/modu tutmayan dosya ıska sayılır: bir kez yeniden OCR
    job = DocJob("k", [SourcePage(source(tmp_path), 1)])
    target = cache_path(job.pages[0], Options())
    target.parent.mkdir(parents=True)
    run, calls = fake_run("[]")
    old = process_page(Path("x.png"), job.pages[0], Options(), run, lambda img: "", (100, 100))
    for stale in (page_to_json(old),
                  json.dumps({"version": 2, "engine": "glm-ocr", "mode": "duzen", "width": 1, "height": 1,
                              "truncated": False, "raw": "[]", "tsv": ""})):
        target.write_text(stale, encoding="utf-8")
        calls.clear()
        process_job(job, Options(), run, lambda img: "", progress=lambda m: None)
        assert len(calls) == 1
        assert json.loads(target.read_text(encoding="utf-8"))["engine"] == "dots.mocr"
