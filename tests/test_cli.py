import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from ocrnow import engines, export_docx, export_txt, pages, paths, pipeline
from ocrnow.cli import main, parse_args
from ocrnow.layout import Page
from ocrnow.pages import DocJob, InputError, SourcePage


def fake_page(index, warnings=(), label="413", inferred=True):
    return Page(source="k.pdf", index=index, width=10, height=10, engine="dots.mocr", mode="duzen",
                blocks=[], book_page=label, book_page_inferred=inferred, warnings=list(warnings))


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


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    env = Env()
    monkeypatch.setattr(paths, "DOCS_OUT", tmp_path / "belgeler")
    monkeypatch.setattr(paths, "INPUT", tmp_path / "input")

    def collect_jobs(inputs, spec, merge=False):
        env.collect_calls.append((list(inputs), spec, merge))
        return list(env.jobs), []

    @contextmanager
    def server(name):
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
            return out
        return fn

    monkeypatch.setattr(pages, "collect_jobs", collect_jobs)
    monkeypatch.setattr(engines, "server", server)
    monkeypatch.setattr(engines, "missing", lambda name: env.missing.get(name))
    monkeypatch.setattr(pipeline, "process_job", process_job)
    monkeypatch.setattr(export_docx, "write_docx", write("docx"))
    monkeypatch.setattr(export_txt, "write_txt", write("txt"))
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
    ["k.pdf", "-f", "pdf"],
    ["k.pdf", "--ilk-sayfa", "0"],
    ["k.pdf", "--dpi", "0"],
])
def test_bad_options_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as e:
        parse_args(argv)
    assert e.value.code == 2
    assert "hata" in capsys.readouterr().err.lower()


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
    tty(monkeypatch, ["3", "h"])               # ikisi, işaretleme hayır
    assert main(["k.pdf"]) == 0                # girdi verildi → baştaki sorular yok, sondaki sorular var
    assert fake_env.written["docx"]["highlight"] is False and "txt" in fake_env.written
    assert fake_env.written["docx"]["out"] == paths.DOCS_OUT / "k.docx"
    assert fake_env.written["txt"]["out"] == paths.DOCS_OUT / "k.txt"


@pytest.mark.parametrize("answer,formats", [("1", {"docx"}), ("docx", {"docx"}), ("2", {"txt"}), ("txt", {"txt"}),
                                           ("3", {"docx", "txt"}), ("ikisi", {"docx", "txt"}), ("", {"docx"})])
def test_format_answers(fake_env, monkeypatch, answer, formats):
    tty(monkeypatch, [answer, "e"])            # txt tek başına seçilirse işaretleme sorulmaz; kalan cevap kullanılmaz
    main(["k.pdf"])
    assert set(fake_env.written) == formats


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


def test_input_error_exit_2(fake_env, monkeypatch, capsys):
    def boom(*a, **k):
        raise InputError("Dosya ya da klasör bulunamadı: x.pdf")
    monkeypatch.setattr(pages, "collect_jobs", boom)
    assert main(["x.pdf"]) == 2
    assert "Dosya ya da klasör bulunamadı: x.pdf" in capsys.readouterr().err


def test_failed_page_exit_1_but_documents_written(fake_env, capsys):
    fake_env.pages = [fake_page(1), fake_page(2, [f"s. 2: {pipeline.READ_FAILED}: boom"])]
    assert main(["k.pdf", "-f", "txt"]) == 1
    assert "txt" in fake_env.written
    assert f"s. 2: {pipeline.READ_FAILED}: boom" in capsys.readouterr().out


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
