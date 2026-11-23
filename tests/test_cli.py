import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from ocrnow import cli, engines, export_docx, export_md, export_pdf, export_structured, export_txt, pages, paths, pipeline
from ocrnow.cli import main, parse_args
from ocrnow.layout import Page
from ocrnow.pages import DocJob, InputError, SourcePage


def fake_page(index, warnings=(), label="413", inferred=True, failed=False, check=False):
    return Page(source="k.pdf", index=index, width=10, height=10, engine="dots.mocr", mode="duzen",
                blocks=[], book_page=label, book_page_inferred=inferred, warnings=list(warnings), failed=failed,
                book_page_check=check)


def failed_page(index):
    return fake_page(index, [f"s. {index}: {pipeline.READ_FAILED}: boom"], failed=True)


class Env:
    """Sahte bileşenlerin kayıtları."""

    def __init__(self):
        self.written = {}
        self.opts = []
        self.collect_calls = []
        self.servers = []   # ("giriş" | "çıkış", motor)
        self.missing = {}   # motor → neden
        self.jobs = [DocJob("k", [SourcePage(Path("k.pdf"), 1), SourcePage(Path("k.pdf"), 2)])]
        self.pages = [fake_page(1), fake_page(2)]
        self.process_error = None
        self.cached = False   # True: process_job modeli hiç çağırmaz (bütün sayfalar önbellekte)
        self.export_warning = None   # sahte write_docx'in eklediği dışa aktarma uyarısı


@pytest.fixture
def fake_env(tmp_path, monkeypatch, known_ocr):
    env = Env()
    monkeypatch.setattr(paths, "DOCS_OUT", tmp_path / "belgeler")
    monkeypatch.setattr(paths, "INPUT", tmp_path / "input")

    def collect_jobs(inputs, spec, merge=False):
        env.collect_calls.append((list(inputs), spec, merge))
        return list(env.jobs), []

    @contextmanager
    def server(name, **options):
        env.servers.append(("giriş", name))
        try:
            yield lambda img, mode: "anlamsız"
        finally:
            env.servers.append(("çıkış", name))

    def process_job(job, opts, run, *args, **kwargs):
        env.opts.append(opts)
        if not env.cached:
            run(Path("x.png"), opts.mode)
        if env.process_error:
            raise env.process_error
        return list(env.pages)

    def write(fmt):
        def fn(pages_, out, **kwargs):
            env.written[fmt] = {"pages": pages_, "out": out, **kwargs}
            if fmt == "docx" and env.export_warning:
                kwargs["warnings"].append(env.export_warning)
            return out
        return fn

    monkeypatch.setattr(pages, "collect_jobs", collect_jobs)
    monkeypatch.setattr(engines, "server", server)
    monkeypatch.setattr(engines, "missing", lambda name: env.missing.get(name))
    monkeypatch.setattr(pipeline, "process_job", process_job)
    monkeypatch.setattr(export_docx, "write_docx", write("docx"))
    monkeypatch.setattr(export_txt, "write_txt", write("txt"))
    monkeypatch.setattr(export_md, "write_md", write("md"))
    monkeypatch.setattr(export_structured, "write_json", write("json"))
    monkeypatch.setattr(export_structured, "write_csv", write("csv"))
    monkeypatch.setattr(export_pdf, "write_pdf", write("pdf"))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    return env


def tty(monkeypatch, answers):
    answers = iter(answers)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: next(answers))


def no_questions(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("soru sorulmamalı"))


def make_pdf(path, n):
    pdf = pdfium.PdfDocument.new()
    for _ in range(n):
        pdf.new_page(200, 300)
    pdf.save(str(path))
    pdf.close()
    return path


def test_defaults():
    a = parse_args(["k.pdf"])
    assert (a.model, a.okuma, a.isaretle, a.format) == ("dots.mocr", "duzen", None, None)
    assert (a.dpi, a.birlestir, a.kesme_yok, a.ilk_sayfa, a.sayfalar) == (200, False, False, None, None)
    assert a.kalin_kalip == export_docx.BOLD_DEFAULT


def test_option_spellings():
    a = parse_args(["a.pdf", "b.pdf", "-p", "1-3", "-m", "glm-ocr", "-f", "docx", "txt", "--isaretleme",
                    "--kesme-yok", "--ilk-sayfa", "5", "--birlestir", "--dpi", "300"])
    assert a.girdiler == ["a.pdf", "b.pdf"] and a.sayfalar == "1-3" and a.model == "glm-ocr"
    assert a.format == ["docx", "txt"] and a.isaretle is False and a.kesme_yok and a.ilk_sayfa == 5
    assert a.birlestir and a.dpi == 300
    assert parse_args(["k.pdf", "--isaretle"]).isaretle is True


@pytest.mark.parametrize("argv", [
    ["k.pdf", "-m", "yok-model"],
    ["k.pdf", "-m", "glm-ocr", "-o", "duzen"],   # model bu modu bilmiyor
    ["k.pdf", "-o", "baska"],
    ["k.pdf", "-f", "rtf"],
    ["k.pdf", "--ilk-sayfa", "0"],
    ["k.pdf", "--dpi", "0"],
])
def test_bad_options_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as e:
        parse_args(argv)
    assert e.value.code == 2
    assert "hata" in capsys.readouterr().err.lower()


def test_invalid_bold_pattern_rejected_before_ocr(fake_env, capsys):
    """M1: geçersiz düzenli ifade OCR'dan sonra iz kaydıyla değil, baştan açık mesajla reddedilir."""
    with pytest.raises(SystemExit) as e:
        main(["k.pdf", "--kalin-kalip", "[106b"])
    assert e.value.code == 2
    assert "--kalin-kalip" in capsys.readouterr().err and fake_env.opts == []


def test_mode_falls_back_for_single_mode_model(fake_env):
    assert main(["k.pdf", "-m", "glm-ocr", "-f", "txt"]) == 0
    assert (fake_env.opts[0].engine, fake_env.opts[0].mode) == ("glm-ocr", "duz")


def test_default_mode_for_multi_mode_model(fake_env):
    main(["k.pdf", "-f", "txt"])
    assert (fake_env.opts[0].engine, fake_env.opts[0].mode) == ("dots.mocr", "duzen")


def test_options_reach_pipeline_and_collect_jobs(fake_env):
    main(["a.pdf", "b.pdf", "-p", "2-4", "-f", "txt", "--kesme-yok", "--ilk-sayfa", "7", "--dpi", "300",
          "--birlestir"])
    o = fake_env.opts[0]
    assert (o.apostrophes, o.first_page, o.dpi) == (False, 7, 300)
    assert fake_env.collect_calls == [([Path("a.pdf"), Path("b.pdf")], "2-4", True)]


def test_no_inputs_non_tty_uses_input_folder(fake_env):
    main(["-f", "txt"])
    assert fake_env.collect_calls[0][0] == [paths.INPUT]


def test_asks_format_at_end(fake_env, monkeypatch):
    tty(monkeypatch, ["7", "h"])               # hepsi (docx, txt, md, json, csv, pdf), işaretleme hayır
    assert main(["k.pdf"]) == 0                # girdi verildi → baştaki sorular yok, sondaki sorular var
    assert fake_env.written["docx"]["highlight"] is False and "txt" in fake_env.written and "md" in fake_env.written
    assert fake_env.written["docx"]["out"] == paths.DOCS_OUT / "k.docx"
    assert fake_env.written["txt"]["out"] == paths.DOCS_OUT / "k.txt"
    assert fake_env.written["md"]["out"] == paths.DOCS_OUT / "k.md"


def test_existing_documents_are_not_overwritten(fake_env):
    """R23: eski belge (belki elle düzeltilmiş) ezilmez; yeni ad _2, _3 ... ve biçimler aynı eki paylaşır."""
    paths.DOCS_OUT.mkdir()
    (paths.DOCS_OUT / "k.docx").write_bytes(b"eski")
    main(["k.pdf", "-f", "docx"])
    assert fake_env.written["docx"]["out"] == paths.DOCS_OUT / "k_2.docx"
    (paths.DOCS_OUT / "k_2.txt").write_text("eski")
    main(["k.pdf", "-f", "docx", "txt"])
    assert fake_env.written["docx"]["out"] == paths.DOCS_OUT / "k_3.docx"
    assert fake_env.written["txt"]["out"] == paths.DOCS_OUT / "k_3.txt"
    main(["k.pdf", "-f", "txt"])                   # k.txt yok: ad olduğu gibi
    assert fake_env.written["txt"]["out"] == paths.DOCS_OUT / "k.txt"
    assert (paths.DOCS_OUT / "k.docx").read_bytes() == b"eski"


def test_concurrent_runs_keep_both_formats_and_existing_documents(fake_env, monkeypatch):
    fake_env.cached = True
    paths.DOCS_OUT.mkdir()
    (paths.DOCS_OUT / "k.docx").write_bytes(b"eski")
    first_writing = threading.Event()
    second_writing = threading.Event()
    release = threading.Event()

    def write(out, label):
        with paths.atomic_output(out) as tmp:
            tmp.write_text(label)

    def word(pages_, out, **kwargs):
        label = threading.current_thread().name
        if label == "ilk_0":
            first_writing.set()
            assert release.wait(10)
        else:
            second_writing.set()
        write(out, label)

    monkeypatch.setattr(export_docx, "write_docx", word)
    monkeypatch.setattr(export_txt, "write_txt", lambda pages_, out: write(out, threading.current_thread().name))
    args = ["k.pdf", "-f", "docx", "txt"]
    with (ThreadPoolExecutor(1, thread_name_prefix="ilk") as first,
          ThreadPoolExecutor(1, thread_name_prefix="ikinci") as second):
        earliest = first.submit(main, args)
        try:
            assert first_writing.wait(10)
            latest = second.submit(main, args)
            second_writing.wait(0.5)  # kilit yoksa ikinci iş ilk işin ayırdığı boş adı da seçebilir
        finally:
            release.set()
        assert earliest.result(10) == latest.result(10) == 0
    assert (paths.DOCS_OUT / "k.docx").read_bytes() == b"eski"
    for ext in ("docx", "txt"):
        assert (paths.DOCS_OUT / f"k_2.{ext}").read_text() == "ilk_0"
        assert (paths.DOCS_OUT / f"k_3.{ext}").read_text() == "ikinci_0"


def test_existing_dangling_output_link_is_not_taken(fake_env):
    paths.DOCS_OUT.mkdir()
    link = paths.DOCS_OUT / "k.docx"
    link.symlink_to("bulunmayan.docx")
    assert main(["k.pdf", "-f", "docx"]) == 0
    assert fake_env.written["docx"]["out"].name == "k_2.docx"
    assert link.is_symlink()


@pytest.mark.parametrize("answer,formats", [("1", {"docx"}), ("docx", {"docx"}), ("2", {"txt"}), ("txt", {"txt"}),
                                           ("3", {"md"}), ("md", {"md"}), ("4", {"json"}), ("5", {"csv"}),
                                           ("6", {"pdf"}),
                                           ("7", {"docx", "txt", "md", "json", "csv", "pdf"}),
                                           ("hepsi", {"docx", "txt", "md", "json", "csv", "pdf"}), ("", {"docx"})])
def test_format_answers(fake_env, monkeypatch, answer, formats):
    tty(monkeypatch, [answer, "e"])            # txt/md/json/csv/pdf tek başına seçilirse işaretleme sorulmaz
    main(["k.pdf"])
    assert set(fake_env.written) == formats


def test_new_exporter_needs_only_registry_entry(fake_env, monkeypatch):
    """M5: yeni dışa aktarıcı tek yerde (EXPORTERS) eklenir: -f, sondaki soru ve yazma döngüsü onu tanır."""
    got = []
    monkeypatch.setitem(cli.EXPORTERS, "rst", lambda doc_pages, out, settings: got.append(out))
    assert main(["k.pdf", "-f", "rst"]) == 0 and got == [paths.DOCS_OUT / "k.rst"]
    tty(monkeypatch, ["7"])                         # 1) docx 2) txt 3) md 4) json 5) csv 6) pdf 7) rst 8) hepsi
    main(["k.pdf"])
    assert got[-1] == paths.DOCS_OUT / "k.rst" and fake_env.written == {}
    tty(monkeypatch, ["hepsi", "e"])
    main(["k.pdf"])
    assert set(fake_env.written) == {"docx", "txt", "md", "json", "csv", "pdf"} and len(got) == 3


def test_invalid_format_answer_is_asked_again(fake_env, monkeypatch):
    tty(monkeypatch, ["9", "2"])
    main(["k.pdf"])
    assert set(fake_env.written) == {"txt"}


def test_highlight_default_yes_and_answers(fake_env, monkeypatch):
    tty(monkeypatch, ["1", ""])
    main(["k.pdf"])
    assert fake_env.written["docx"]["highlight"] is True
    tty(monkeypatch, ["1", "evet"])
    main(["k.pdf"])
    assert fake_env.written["docx"]["highlight"] is True
    tty(monkeypatch, ["1", "x", "hayır"])
    main(["k.pdf"])
    assert fake_env.written["docx"]["highlight"] is False


def test_txt_only_does_not_ask_highlight(fake_env, monkeypatch):
    tty(monkeypatch, ["2"])                    # ikinci soru sorulursa StopIteration
    main(["k.pdf"])
    assert set(fake_env.written) == {"txt"}


def test_given_options_are_not_asked(fake_env, monkeypatch):
    no_questions(monkeypatch)
    assert main(["k.pdf", "-f", "docx", "--isaretleme"]) == 0
    assert fake_env.written["docx"]["highlight"] is False


def test_only_highlight_asked_when_format_given(fake_env, monkeypatch):
    tty(monkeypatch, ["h"])
    main(["k.pdf", "-f", "docx", "txt"])
    assert fake_env.written["docx"]["highlight"] is False and "txt" in fake_env.written


def test_non_tty_no_questions(fake_env, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("soru sorulmamalı"))
    assert main(["k.pdf"]) == 0 and "docx" in fake_env.written
    assert fake_env.written["docx"]["highlight"] is True and "txt" not in fake_env.written


def test_bold_pattern_default_and_empty(fake_env):
    main(["k.pdf", "-f", "docx"])
    assert fake_env.written["docx"]["bold_pattern"] == export_docx.BOLD_DEFAULT
    main(["k.pdf", "-f", "docx", "--kalin-kalip", ""])
    assert fake_env.written["docx"]["bold_pattern"] is None
    main(["k.pdf", "-f", "docx", "--kalin-kalip", r"\[x\]"])
    assert fake_env.written["docx"]["bold_pattern"] == r"\[x\]"


def test_missing_engine_exit_2(fake_env, capsys):
    fake_env.missing["dots.mocr"] = "vllm kurulu değil"
    assert main(["k.pdf", "-f", "txt"]) == 2
    err = capsys.readouterr().err
    assert "vllm kurulu değil" in err and "ocr-now models install" in err
    assert fake_env.opts == [] and fake_env.servers == [] and fake_env.written == {}


def test_missing_tesseract_exit_2_before_anything(fake_env, monkeypatch, capsys):
    """R22: tesseract her sayfada gerekir; yoksa model hiç açılmadan, sorular sorulmadan durulur."""
    fake_env.missing["tesseract"] = "tesseract kurulu değil"
    assert main(["k.pdf", "-f", "txt"]) == 2
    err = capsys.readouterr().err
    assert "tesseract kurulu değil" in err and "ocr-now models install tesseract" in err
    assert fake_env.opts == [] and fake_env.servers == [] and fake_env.written == {}
    no_questions(monkeypatch)
    assert main([]) == 2 and fake_env.collect_calls == []


def test_input_error_exit_2(fake_env, monkeypatch, capsys):
    def boom(*a, **k):
        raise InputError("Dosya ya da klasör bulunamadı: x.pdf")
    monkeypatch.setattr(pages, "collect_jobs", boom)
    assert main(["x.pdf"]) == 2
    assert "Dosya ya da klasör bulunamadı: x.pdf" in capsys.readouterr().err


def test_failed_page_exit_1_but_documents_written(fake_env, capsys):
    fake_env.pages = [fake_page(1), failed_page(2)]
    assert main(["k.pdf", "-f", "txt"]) == 1
    assert "txt" in fake_env.written
    assert f"s. 2: {pipeline.READ_FAILED}: boom" in capsys.readouterr().out


def test_all_pages_failed_job_is_not_written(fake_env, monkeypatch, capsys):
    """R22: hiçbir sayfası okunamayan iş boş belge yazmaz (eski aynı adlı belgeyi de ezmez)."""
    fake_env.pages = [failed_page(1), failed_page(2)]
    assert main(["k.pdf", "-f", "docx", "txt"]) == 1
    assert fake_env.written == {}
    out = capsys.readouterr().out
    assert "k: hiçbir sayfa okunamadı, belge yazılmadı" in out
    assert str(paths.DOCS_OUT / "k.docx") not in out
    no_questions(monkeypatch)                    # yazılacak belge yoksa sondaki sorular da sorulmaz
    assert main(["k.pdf"]) == 1 and fake_env.written == {}


def test_server_start_failure_stops_whole_run(fake_env, monkeypatch, capsys):
    """R22: model sunucusu açılamazsa koşu tek açık mesajla durur; her sayfa ayrı ayrı 'okunamadı' olmaz."""
    fake_env.jobs = [DocJob("a", [SourcePage(Path("a.pdf"), 1)]), DocJob("b", [SourcePage(Path("b.pdf"), 1)])]

    @contextmanager
    def broken(name, **options):
        raise RuntimeError("sunucu ayağa kalkmadı, bkz. dots.mocr.server.log")
        yield

    monkeypatch.setattr(engines, "server", broken)
    assert main(["a.pdf", "b.pdf", "-f", "txt"]) == 1
    err = capsys.readouterr().err
    assert err.count("Hata:") == 1 and "dots.mocr" in err and "ayağa kalkmadı" in err and "yazılmadı" in err
    assert len(fake_env.opts) == 1 and fake_env.written == {}


def test_ordinary_warnings_do_not_fail(fake_env):
    fake_env.pages = [fake_page(1, ["s. 1, blok 2: satır yapısı kurulamadı"]), fake_page(2)]
    assert main(["k.pdf", "-f", "txt"]) == 0


def test_summary_lists_paths_labels_and_warnings_per_document(fake_env, capsys):
    fake_env.jobs = [DocJob("k", [SourcePage(Path("k.pdf"), 1)]), DocJob("m", [SourcePage(Path("m.pdf"), 1)])]
    fake_env.pages = [fake_page(1, ["s. 1: uyarı-bir"], "413", True), fake_page(2, [], "PDF s. 2", True),
                      fake_page(3, [], "7", False)]
    main(["k.pdf", "m.pdf", "-f", "docx", "txt"])
    out = capsys.readouterr().out
    assert str(paths.DOCS_OUT / "k.docx") in out and str(paths.DOCS_OUT / "m.txt") in out
    assert "tahmin edilen sayfa numaraları" in out and "[413], [PDF s. 2]" in out
    assert "[7]" not in out and "uyarı-bir" in out
    assert out.index("k:") < out.index("uyarı-bir") < out.index("m:")


def test_export_warnings_listed_under_their_document(fake_env, capsys):
    fake_env.jobs = [DocJob("k", [SourcePage(Path("k.pdf"), 1)])]
    fake_env.export_warning = "docx sayfa 413, blok 2: satır 8 pt'de de sığmıyor"
    assert main(["k.pdf", "-f", "docx"]) == 0           # uyarı, çıkış kodunu bozmaz
    out = capsys.readouterr().out
    assert out.index("k: uyarılar:") < out.index("docx sayfa 413, blok 2: satır 8 pt'de de sığmıyor")


def test_one_server_for_all_jobs(fake_env):
    fake_env.jobs = [DocJob("a", [SourcePage(Path("a.pdf"), 1)]), DocJob("b", [SourcePage(Path("b.pdf"), 1)])]
    main(["a.pdf", "b.pdf", "-f", "txt"])
    assert fake_env.servers == [("giriş", "dots.mocr"), ("çıkış", "dots.mocr")]
    assert len(fake_env.opts) == 2
    assert set(fake_env.written) == {"txt"}


def test_all_cached_does_not_start_server(fake_env):
    fake_env.cached = True
    assert main(["k.pdf", "-f", "txt"]) == 0
    assert fake_env.servers == [] and set(fake_env.written) == {"txt"}


def test_ctrl_c_exit_130_and_server_closed(fake_env, capsys):
    fake_env.process_error = KeyboardInterrupt()
    assert main(["k.pdf", "-f", "txt"]) == 130
    assert fake_env.servers[-1] == ("çıkış", "dots.mocr") and fake_env.written == {}
    assert "İptal" in capsys.readouterr().err


def test_models_subcommand_delegates_before_argparse(monkeypatch):
    got = []
    fake = types.ModuleType("ocrnow.models")
    fake.main = lambda argv: got.append(argv) or 7
    monkeypatch.setitem(sys.modules, "ocrnow.models", fake)
    assert main(["models", "install", "--yok-boyle-secenek"]) == 7
    assert got == [["install", "--yok-boyle-secenek"]]
    assert main(["models"]) == 7 and got[-1] == []


def test_models_only_as_first_argument(fake_env):
    assert main(["./models", "-f", "txt"]) == 0       # dosya adı olarak, delegasyon yok
    assert fake_env.collect_calls[0][0] == [Path("./models")]


def test_help_mentions_models_subcommand(capsys):
    with pytest.raises(SystemExit) as e:
        parse_args(["--help"])
    assert e.value.code == 0
    assert "ocr-now models" in capsys.readouterr().out


def test_interactive_start(fake_env, monkeypatch, capsys):
    paths.INPUT.mkdir()
    make_pdf(paths.INPUT / "a.pdf", 5)
    make_pdf(paths.INPUT / "b.pdf", 3)
    (paths.INPUT / "notlar.txt").write_text("x")
    (paths.INPUT / "tarama").mkdir()
    fake_env.missing["glm-ocr"] = "kurulu değil"
    # girdiler "1 3" → a.pdf ve tarama/ (notlar.txt listelenmez); sayfalar yalnız a.pdf için sorulur
    answers = iter(["1 3", "2-4", None, None, "1", "e"])
    printed = []

    def fake_input(prompt=""):
        printed.extend([capsys.readouterr().out, prompt])
        a = next(answers)
        if a is None:       # model / mod sorusu: listedeki ★'lı varsayılana bırak
            return ""
        return a

    tty(monkeypatch, [])
    monkeypatch.setattr("builtins.input", fake_input)
    assert main([]) == 0
    text = "".join(printed)
    assert "a.pdf" in text and "b.pdf" in text and "tarama" in text and "notlar.txt" not in text
    assert "5 sayfa" in text
    assert "★" in text and "glm-ocr" not in text      # yalnız hazır motorlar listelenir
    assert "duzen" in text                            # dots.mocr birden fazla moda sahip → mod sorusu
    specs = {tuple(c[0]): c[1] for c in fake_env.collect_calls}
    assert specs == {(paths.INPUT / "a.pdf",): "2-4", (paths.INPUT / "tarama",): None}
    assert (fake_env.opts[0].engine, fake_env.opts[0].mode) == ("dots.mocr", "duzen")
    assert "docx" in fake_env.written


def test_interactive_start_single_mode_model_skips_mode_question(fake_env, monkeypatch, capsys):
    paths.INPUT.mkdir()
    make_pdf(paths.INPUT / "a.pdf", 2)
    seen = []
    ready = [n for n in engines.ENGINES if n not in fake_env.missing]
    glm = ready.index("glm-ocr") + 1
    answers = iter(["1", "", str(glm), "1", "e"])   # girdi, sayfalar (hepsi), model, format, işaretleme

    def fake_input(prompt=""):
        seen.append(prompt)
        return next(answers)

    tty(monkeypatch, [])
    monkeypatch.setattr("builtins.input", fake_input)
    main([])
    assert fake_env.opts[0].engine == "glm-ocr" and fake_env.opts[0].mode == "duz"
    assert fake_env.collect_calls[0][1] is None
    assert len(seen) == 5     # mod sorusu yok


def test_interactive_start_empty_input_folder_exit_2(fake_env, monkeypatch, capsys):
    paths.INPUT.mkdir()
    tty(monkeypatch, [])
    assert main([]) == 2
    assert "input" in capsys.readouterr().err


def test_summary_lists_side_flagged_readings_separately(fake_env, capsys):
    fake_env.pages = [fake_page(1, [], "413", True), fake_page(2, [], "416", True, check=True)]
    main(["k.pdf", "-f", "txt"])
    out = capsys.readouterr().out
    assert "tahmin edilen sayfa numaraları:\n  [413]\n" in out
    assert "kontrol edilmesi gereken okunan numaralar:\n  [416]\n" in out


def test_report_json_has_documents_labels_and_warnings(fake_env, tmp_path):
    """GUI için: --rapor iş sonu özetini (belgeler, numaralar, uyarılar, okunamayan) JSON olarak yazar."""
    import json
    fake_env.pages = [fake_page(1, ["s. 1: uyarı-bir"], "413", True), fake_page(2, [], "416", True, check=True),
                      fake_page(3, [f"s. 3: {pipeline.READ_FAILED}: boom"], "PDF s. 3", False, failed=True)]
    fake_env.export_warning = "docx uyarısı"
    report = tmp_path / "rapor.json"
    assert main(["k.pdf", "-f", "docx", "txt", "--rapor", str(report)]) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["belgeler"] == [str(paths.DOCS_OUT / "k.docx"), str(paths.DOCS_OUT / "k.txt")]
    assert data["okunamayan"] is True
    [job] = data["isler"]
    assert job["ad"] == "k" and job["yazildi"] is True
    assert job["tahmin"] == ["[413]"] and job["kontrol"] == ["[416]"]
    assert job["uyarilar"] == ["s. 1: uyarı-bir", f"s. 3: {pipeline.READ_FAILED}: boom", "docx uyarısı"]


def test_report_json_marks_unwritten_job(fake_env, tmp_path):
    import json
    fake_env.pages = [failed_page(1)]
    report = tmp_path / "rapor.json"
    main(["k.pdf", "-f", "txt", "--rapor", str(report)])
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["belgeler"] == [] and data["isler"][0]["yazildi"] is False


@pytest.mark.parametrize("formats,written", [(["docx"], []), (["docx", "txt"], ["k.txt"])])
def test_report_tracks_actual_exports_and_write_errors(fake_env, tmp_path, monkeypatch, capsys, formats, written):
    import json

    def broken(*args, **kwargs):
        raise OSError("disk dolu")

    monkeypatch.setattr(export_docx, "write_docx", broken)
    report = tmp_path / "rapor.json"
    assert main(["k.pdf", "-f", *formats, "--rapor", str(report)]) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert [Path(p).name for p in data["belgeler"]] == written
    assert data["isler"][0]["yazildi"] is bool(written)
    assert data["isler"][0]["yazma_hatalari"] == ["k.docx yazılamadı (disk dolu)"]
    assert not data["okunamayan"]
    printed = capsys.readouterr()
    assert "hiçbir sayfa okunamadı" not in printed.out and "disk dolu" in printed.err


def test_report_is_written_if_output_directory_cannot_be_opened(fake_env, tmp_path):
    import json

    paths.DOCS_OUT.write_text("bu yol zaten dosya")
    report = tmp_path / "rapor.json"
    assert main(["k.pdf", "-f", "docx", "txt", "--rapor", str(report)]) == 1
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["belgeler"] == [] and not data["okunamayan"]
    errors = data["isler"][0]["yazma_hatalari"]
    assert [error.split()[0] for error in errors] == ["k.docx", "k.txt"]
    assert paths.DOCS_OUT.read_text() == "bu yol zaten dosya"


def test_missing_pikepdf_only_fails_pdf_format(tmp_path, monkeypatch):
    """pikepdf'siz eski bir .venv'de (git pull, yeniden kurulum yok) CLI açılır; yalnız .pdf yazılamaz."""
    import importlib

    import ocrnow
    for name in ("ocrnow.cli", "ocrnow.export_pdf"):
        monkeypatch.delitem(sys.modules, name)
        monkeypatch.delattr(ocrnow, name.split(".")[1])              # `from ocrnow import x` önce buna bakar
    monkeypatch.setitem(sys.modules, "pikepdf", None)               # import pikepdf → ImportError
    fresh = importlib.import_module("ocrnow.cli")
    assert fresh is not cli and fresh.export_pdf is not export_pdf
    monkeypatch.setattr(paths, "DOCS_OUT", tmp_path / "belgeler")
    page = Page(source=str(tmp_path / "k.png"), index=1, width=10, height=10, engine="dots.mocr", mode="duzen",
                blocks=[])
    settings = {"highlight": False, "bold": None}
    written, errors = fresh._write_job("k", [page], ["docx", "txt", "pdf"], settings, [])
    assert [p.name for p in written] == ["k.docx", "k.txt"] and all(p.is_file() for p in written)
    assert len(errors) == 1 and errors[0].startswith("k.pdf yazılamadı (") and "pikepdf" in errors[0]
    assert not (tmp_path / "belgeler" / "k.pdf").exists()
