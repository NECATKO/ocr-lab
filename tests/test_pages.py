import pypdfium2 as pdfium
import pytest
from PIL import Image

from ocrnow import paths
from ocrnow.pages import (DocJob, InputError, SourcePage, collect_jobs, make_names_unique, parse_pages,
                          pdf_page_count, render)


def make_pdf(path, n, size=(200, 300)):
    pdf = pdfium.PdfDocument.new()
    for _ in range(n):
        pdf.new_page(*size)
    pdf.save(str(path))
    pdf.close()
    return path


@pytest.mark.parametrize("spec,n,exp", [
    (None, 3, [0, 1, 2]), ("hepsi", 2, [0, 1]), ("5", 9, [4]), ("1,3,7", 9, [0, 2, 6]),
    ("10-12", 20, [9, 10, 11]), ("ilk2", 9, [0, 1]), ("1-2,5", 9, [0, 1, 4]),
    (" 1 - 2 , 5 ", 9, [0, 1, 4]), ("Hepsi", 2, [0, 1]), ("3,1,3", 9, [0, 2])])
def test_parse_pages(spec, n, exp):
    assert parse_pages(spec, n)[0] == exp


def test_out_of_range_whole_is_error():
    with pytest.raises(InputError, match="20 sayfa"):
        parse_pages("50-60", 20)


def test_partial_overflow_clipped_with_warning():
    pages, warns = parse_pages("18-25", 20)
    assert pages == [17, 18, 19] and "kırpıldı" in warns[0]


def test_ilk_more_than_available_clipped():
    pages, warns = parse_pages("ilk10", 4)
    assert pages == [0, 1, 2, 3] and "kırpıldı" in warns[0]


@pytest.mark.parametrize("spec", ["üç", "", "1,,2", "0", "5-3", "ilk", "ilk0", "1-", "a-b"])
def test_garbage_spec(spec):
    with pytest.raises(InputError):
        parse_pages(spec, 5)


def test_image_folder_natural_order(tmp_path):
    for n in ["10.jpg", "2.jpg", "1.jpg"]:
        (tmp_path / n).write_bytes(b"")
    jobs, _ = collect_jobs([tmp_path], None)
    assert [p.source.name for p in jobs[0].pages] == ["1.jpg", "2.jpg", "10.jpg"] and jobs[0].name == tmp_path.name
    assert all(p.index == 1 for p in jobs[0].pages)


def test_folder_ignores_other_files(tmp_path):
    (tmp_path / "a.PNG").write_bytes(b"")
    (tmp_path / "notlar.txt").write_text("x")
    jobs, _ = collect_jobs([tmp_path], None)
    assert [p.source.name for p in jobs[0].pages] == ["a.PNG"]


def test_folder_with_pdfs_and_images(tmp_path):
    make_pdf(tmp_path / "b.pdf", 2)
    make_pdf(tmp_path / "a.pdf", 1)
    (tmp_path / "x.png").write_bytes(b"")
    jobs, _ = collect_jobs([tmp_path], None)
    assert [(j.name, len(j.pages)) for j in jobs] == [("a", 1), ("b", 2), (tmp_path.name, 1)]


def test_empty_folder_is_error(tmp_path):
    (tmp_path / "notlar.txt").write_text("x")
    with pytest.raises(InputError, match="görüntü"):
        collect_jobs([tmp_path], None)


def test_loose_images_make_one_job_named_after_first(tmp_path):
    for n in ["b.png", "a.png"]:
        (tmp_path / n).write_bytes(b"")
    jobs, _ = collect_jobs([tmp_path / "b.png", tmp_path / "a.png"], "3")  # seçim görüntülere uygulanmaz
    assert len(jobs) == 1 and jobs[0].name == "b"
    assert [p.source.name for p in jobs[0].pages] == ["b.png", "a.png"]


def test_pdf_per_job_with_spec_applied_to_each(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 5)
    b = make_pdf(tmp_path / "b.pdf", 3)
    jobs, warns = collect_jobs([a, b], "2-4")
    assert [j.name for j in jobs] == ["a_s2-4", "b_s2-3"]       # R23: seçim adda (b'de kırpılmış hâli)
    assert [p.index for p in jobs[0].pages] == [2, 3, 4]
    assert [p.index for p in jobs[1].pages] == [2, 3]
    assert len(warns) == 1 and "b.pdf" in warns[0] and "kırpıldı" in warns[0]


def test_spec_out_of_range_names_file(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 3)
    with pytest.raises(InputError, match="a.pdf"):
        collect_jobs([a], "50-60")


def test_broken_pdf_message(tmp_path):
    bad = tmp_path / "bozuk.pdf"
    bad.write_bytes(b"degil")
    with pytest.raises(InputError, match="açılamadı"):
        collect_jobs([bad], None)


def test_missing_and_unsupported_input(tmp_path):
    with pytest.raises(InputError, match="yok.pdf"):
        collect_jobs([tmp_path / "yok.pdf"], None)
    txt = tmp_path / "not.txt"
    txt.write_text("x")
    with pytest.raises(InputError, match="not.txt"):
        collect_jobs([txt], None)


def test_merge_two_pdfs_into_one_job(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", 2)
    b = make_pdf(tmp_path / "b.pdf", 2)
    jobs, _ = collect_jobs([a, b], None, merge=True)
    assert len(jobs) == 1 and len(jobs[0].pages) == 4 and jobs[0].name == "a_birlesik"
    assert [p.source.name for p in jobs[0].pages] == ["a.pdf", "a.pdf", "b.pdf", "b.pdf"]


def test_no_inputs_is_error():
    with pytest.raises(InputError):
        collect_jobs([], None)


def test_render_pdf_page(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    pdf = make_pdf(tmp_path / "kitap.pdf", 2, size=(72, 144))
    png = render(SourcePage(pdf, 2))
    assert png == paths.PAGES / f"kitap-{pdf.stat().st_size}" / "syf2.png"
    assert Image.open(png).size == (200, 400)  # 72 pt = 1 inç, 200 dpi
    mtime = png.stat().st_mtime_ns
    assert render(SourcePage(pdf, 2)) == png and png.stat().st_mtime_ns == mtime  # yeniden üretilmez


def test_render_other_dpi_gets_own_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    pdf = make_pdf(tmp_path / "kitap.pdf", 1, size=(72, 72))
    p200, p100 = render(SourcePage(pdf, 1)), render(SourcePage(pdf, 1), dpi=100)
    assert p100.name == "syf1_100dpi.png" and p200 != p100
    assert Image.open(p100).size == (100, 100)


def test_render_same_name_different_files_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    a = make_pdf(tmp_path / "x" / "k.pdf", 1)
    b = make_pdf(tmp_path / "y" / "k.pdf", 3)
    assert render(SourcePage(a, 1)) != render(SourcePage(b, 1))


def test_render_image_converted_to_png(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    src = tmp_path / "tara.jpg"
    Image.new("RGB", (30, 20), "white").save(src)
    png = render(SourcePage(src, 1))
    assert png.suffix == ".png" and Image.open(png).size == (30, 20)


def test_render_cmyk_image_to_rgb(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    src = tmp_path / "cmyk.tif"
    Image.new("CMYK", (10, 10)).save(src)
    assert Image.open(render(SourcePage(src, 1))).mode == "RGB"


def test_render_page_out_of_range_and_broken_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    pdf = make_pdf(tmp_path / "k.pdf", 1)
    with pytest.raises(InputError, match="1 sayfa"):
        render(SourcePage(pdf, 5))
    bad = tmp_path / "bozuk.pdf"
    bad.write_bytes(b"degil")
    with pytest.raises(InputError, match="açılamadı"):
        render(SourcePage(bad, 1))


def test_docjob_is_plain_dataclass(tmp_path):
    job = DocJob("a", [SourcePage(tmp_path / "a.pdf", 1)])
    assert job.name == "a" and job.pages[0].index == 1


def test_render_applies_exif_orientation(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    src = tmp_path / "telefon.jpg"
    exif = Image.Exif()
    exif[0x0112] = 6  # 90 derece saat yönünde döndürülerek gösterilmeli
    Image.new("RGB", (30, 20), "white").save(src, exif=exif)
    assert Image.open(render(SourcePage(src, 1))).size == (20, 30)


@pytest.mark.parametrize("mode", ["I;16", "I"])
def test_render_16bit_gray_not_all_white(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    src = tmp_path / "gri.tif"
    im = Image.new(mode, (4, 4), 0)
    im.putpixel((0, 0), 65535)
    im.putpixel((1, 0), 32768)
    im.save(src)
    out = Image.open(render(SourcePage(src, 1)))
    assert out.getpixel((0, 0)) == 255 and out.getpixel((1, 0)) == 128 and out.getpixel((3, 3)) == 0


def test_natural_sort_tolerates_unicode_digits(tmp_path):
    for n in ["1²2.jpg", "10.jpg", "2.jpg"]:
        (tmp_path / n).write_bytes(b"")
    jobs, _ = collect_jobs([tmp_path], None)
    assert [p.source.name for p in jobs[0].pages][-2:] == ["2.jpg", "10.jpg"]


@pytest.mark.parametrize("spec,name", [(None, "k"), ("hepsi", "k"), ("1-30", "k"), ("1-50", "k"),
                                       ("11-20", "k_s11-20"), ("5", "k_s5"), ("1,3,7", "k_s1,3,7"),
                                       ("ilk3", "k_s1-3"), ("7,1-3", "k_s1-3,7"), ("1,2,4,5", "k_s1-2,4-5")])
def test_partial_selection_in_job_name(tmp_path, spec, name):
    """R23: bütün sayfalar seçilmediyse çıktı adı seçimi taşır: -p 1-10 ile -p 11-20 aynı belgeyi ezmez."""
    jobs, _ = collect_jobs([make_pdf(tmp_path / "k.pdf", 30)], spec)
    assert jobs[0].name == name


def test_partial_selection_in_merged_name(tmp_path):
    a, b = make_pdf(tmp_path / "a.pdf", 2), make_pdf(tmp_path / "b.pdf", 2)
    assert collect_jobs([a, b], "1", merge=True)[0][0].name == "a_s1_birlesik"


def test_duplicate_job_names_made_unique(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    pa = make_pdf(tmp_path / "a" / "book.pdf", 1)
    pb = make_pdf(tmp_path / "b" / "book.pdf", 1)
    pc = make_pdf(tmp_path / "Book.pdf", 1)
    jobs, _ = collect_jobs([pa, pb, pc], None)
    assert [j.name for j in jobs] == ["book", "book_2", "Book_3"]


def test_folder_next_to_same_named_pdf(tmp_path):
    make_pdf(tmp_path / "x.pdf", 1)
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "1.png").write_bytes(b"")
    jobs, _ = collect_jobs([tmp_path / "x.pdf", tmp_path / "x"], None)
    assert [j.name for j in jobs] == ["x", "x_2"]


def test_spec_with_images_warns(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"")
    _, warns = collect_jobs([img], "3")
    assert len(warns) == 1 and "görüntülere uygulanmaz" in warns[0]
    assert collect_jobs([img], None)[1] == [] and collect_jobs([img], "hepsi")[1] == []
    pdf = make_pdf(tmp_path / "k.pdf", 3)
    assert collect_jobs([pdf], "2")[1] == []  # yalnız PDF: uyarı yok


def test_zero_page_pdf_is_error(tmp_path):
    pdf = make_pdf(tmp_path / "bos.pdf", 0)
    with pytest.raises(InputError):  # pdfium sayfasız PDF'i açmaz; açılırsa da "hiç sayfa yok" denir
        collect_jobs([pdf], None)


def test_unreadable_folder_and_vanished_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "PAGES", tmp_path / "pages")
    def boom(self):
        raise PermissionError("izin yok")
    monkeypatch.setattr(type(tmp_path), "iterdir", boom)
    with pytest.raises(InputError, match="okunamadı"):
        collect_jobs([tmp_path], None)
    with pytest.raises(InputError, match="yok.png"):
        render(SourcePage(tmp_path / "yok.png", 1))


def test_merge_single_job_keeps_name(tmp_path):
    jobs, _ = collect_jobs([make_pdf(tmp_path / "a.pdf", 1)], None, merge=True)
    assert jobs[0].name == "a"


def test_pdf_page_count(tmp_path):
    assert pdf_page_count(make_pdf(tmp_path / "k.pdf", 4)) == 4


def test_pdf_page_count_broken_pdf_is_input_error(tmp_path):
    bad = tmp_path / "bozuk.pdf"
    bad.write_bytes(b"degil")
    with pytest.raises(InputError, match="açılamadı"):
        pdf_page_count(bad)


def test_make_names_unique_across_separately_collected_jobs(tmp_path):
    a = collect_jobs([make_pdf(tmp_path / "k.pdf", 1)], None)[0]
    other = tmp_path / "alt"
    other.mkdir()
    b = collect_jobs([make_pdf(other / "K.pdf", 1)], None)[0]
    jobs = a + b
    make_names_unique(jobs)
    assert [j.name for j in jobs] == ["k", "K_2"]
