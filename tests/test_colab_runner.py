import ast
import json
import stat
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from colab import runner  # noqa: E402


def test_colab_notebooks_are_valid_python():
    notebooks = sorted((ROOT / "colab").glob("*.ipynb"))
    assert {p.name for p in notebooks} >= {"ocr_now.ipynb", "telefon.ipynb"}
    for nb_path in notebooks:
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        for i, cell in enumerate(nb["cells"]):
            if cell["cell_type"] == "code":
                ast.parse("".join(cell["source"]), filename=f"{nb_path.name}:{i}")


def test_phone_notebook_is_one_cell_on_l4_and_calls_phone_flow():
    nb = json.loads((ROOT / "colab/telefon.ipynb").read_text(encoding="utf-8"))
    code = [c for c in nb["cells"] if c["cell_type"] == "code"]
    assert len(code) == 1
    assert "runner.telefon_calistir(" in "".join(code[0]["source"])
    assert nb["metadata"]["accelerator"] == "GPU" and nb["metadata"]["colab"]["gpuType"] == "L4"


def test_notification_topic_is_created_once_and_kept_out_of_repo(tmp_path):
    dp = runner.drive_paths(tmp_path)
    konu = runner.bildirim_konusu(dp)
    assert konu.startswith("ocr-lab-") and len(konu) >= 20
    assert set(konu) <= set("abcdefghijklmnopqrstuvwxyz0123456789-")
    assert runner.bildirim_konusu(dp) == konu                      # ikinci çağrı aynı konuyu okur
    text = dp["bildirim"].read_text(encoding="utf-8")
    assert text.startswith("#") and konu in text
    dp["bildirim"].write_text("# yorum\n\n  benim-konum  \n", encoding="utf-8")
    assert runner.bildirim_konusu(dp) == "benim-konum"
    dp["bildirim"].write_text("# benim-konum\n", encoding="utf-8")   # yorum satırı = bildirim kapalı
    assert runner.bildirim_konusu(dp) == ""
    assert dp["bildirim"].read_text(encoding="utf-8") == "# benim-konum\n"   # üzerine yazılmaz


def test_notify_posts_utf8_json_and_never_raises():
    sent = []
    assert runner.bildir("k", "OCR bitti ✓", "tarama1: 40 sayfa · ğüşıöç", tiklama="https://x", etiketler=["ok"],
                         oncelik=4, _post=lambda body: sent.append(json.loads(body.decode("utf-8"))))
    assert sent == [{"topic": "k", "title": "OCR bitti ✓", "message": "tarama1: 40 sayfa · ğüşıöç",
                     "tags": ["ok"], "priority": 4, "click": "https://x"}]

    def boom(_):
        raise OSError("ağ yok")
    assert runner.bildir("k", "t", "m", _post=boom) is False
    assert runner.bildir("", "t", "m", _post=boom) is False          # konu yoksa sessizce atlanır


def test_status_file(tmp_path):
    dp = runner.drive_paths(tmp_path)
    runner.durum_yaz(dp, "İşleniyor 1/2: a.pdf")
    text = dp["durum"].read_text(encoding="utf-8")
    assert text.endswith("İşleniyor 1/2: a.pdf\n") and text[:4].isdigit()


def test_folder_link_falls_back_without_drive_id(tmp_path):
    assert runner.klasor_baglantisi(tmp_path) == "https://drive.google.com/drive/my-drive"


def test_wait_for_inputs_polls_until_a_file_appears(tmp_path):
    dp = runner.drive_paths(tmp_path)
    dp["input"].mkdir(parents=True)
    naps = []

    def nap(s):
        naps.append(s)
        if len(naps) == 2:
            (dp["input"] / "a.pdf").write_bytes(b"%PDF")
    assert [p.name for p in runner.girdi_bekle(dp, sure=60, aralik=10, _sleep=nap)] == ["a.pdf"]
    assert naps == [10, 10]
    (dp["input"] / "a.pdf").unlink()
    naps.clear()
    assert runner.girdi_bekle(dp, sure=30, aralik=10, _sleep=naps.append) == []
    assert naps == [10, 10, 10]


def job(**kw):
    base = {"ad": "a", "sayfa": 40, "yazildi": True, "tahmin": [], "kontrol": [], "metin_kontrol": [],
            "kesilen": [], "yazma_hatalari": [], "uyarilar": []}
    return {**base, **kw}


def sonuc(rc=0, isler=None, okunamayan=False, yazilanlar=("x.docx",)):
    rapor = None if isler is None else {"belgeler": [], "isler": isler, "okunamayan": okunamayan}
    return runner.OcrSonucu(rc=rc, rapor=rapor, yazilanlar=[Path(y) for y in yazilanlar], hedefler=[])


def test_job_summary_clean_and_problem_cases():
    assert runner.is_ozeti("a.pdf", sonuc(isler=[job()])) == ("a.pdf: 40 sayfa", False)
    assert runner.is_ozeti("a.pdf", sonuc(isler=[job(uyarilar=["u1", "u2"], tahmin=["s. 3"])])) == (
        "a.pdf: 40 sayfa · 2 uyarı", False)
    line, bad = runner.is_ozeti("a.pdf", sonuc(rc=1, isler=[job(
        metin_kontrol=["s. 2", "s. 5"], kesilen=["s. 9"], kontrol=["12"],
        yazma_hatalari=["a.docx yazılamadı"])]))
    assert bad
    assert line == ("a.pdf: 40 sayfa · metin kontrolü: s. 2, s. 5 · kesilen: s. 9 · sayfa no kontrolü: 12"
                    " · yazma hatası: 1")
    many = [f"s. {i}" for i in range(1, 9)]
    assert "s. 1, s. 2, s. 3, s. 4, s. 5 …(+3)" in runner.is_ozeti("a.pdf", sonuc(isler=[job(metin_kontrol=many)]))[0]
    assert runner.is_ozeti("a.pdf", sonuc(rc=2, isler=None, yazilanlar=())) == (
        "a.pdf: OCR çalışmadı, belge yazılmadı (çıkış kodu 2)", True)
    assert runner.is_ozeti("a.pdf", sonuc(isler=[job(yazildi=False)], okunamayan=True, yazilanlar=()))[1]


class PhoneWorld:
    def __init__(self, tmp_path, files=("a.pdf", "b.pdf")):
        self.dp = runner.drive_paths(tmp_path / "drive")
        self.dp["input"].mkdir(parents=True)
        for f in files:
            (self.dp["input"] / f).write_bytes(b"%PDF")
        self.notes, self.calls, self.released, self.setups = [], [], [], []
        self.results = {}

    def ocr(self, drive_root, *, dosya, model):
        self.calls.append(dosya)
        r = self.results.get(dosya, sonuc(isler=[job()]))
        if isinstance(r, Exception):
            raise r
        return r

    def kwargs(self):
        return dict(_kur=lambda root, model: self.setups.append(model), _ocr=self.ocr,
                    _bildir=lambda konu, baslik, mesaj, **kw: self.notes.append((baslik, mesaj, kw)),
                    _birak=lambda: self.released.append(True), _bekle=lambda dp: runner.list_inputs(dp["input"]))


def test_telefon_default_ocr_asks_pdf(tmp_path, monkeypatch):
    assert runner.TELEFON_FORMATLARI == "docx pdf txt"
    w = PhoneWorld(tmp_path, files=("a.pdf",))
    istenen = []

    def sahte(root, *, dosya, model, formatlar="docx txt"):
        istenen.append(formatlar)
        return sonuc(isler=[job()])
    monkeypatch.setattr(runner, "ocr_calistir", sahte)
    kw = w.kwargs()
    del kw["_ocr"]
    assert runner.telefon_calistir(w.dp["root"], **kw) == 0
    assert istenen == ["docx pdf txt"]


def test_colab_is_requests_pdf():
    kaynak = (ROOT / "vps/colab_is.py").read_text(encoding="utf-8")
    assert "formatlar=runner.TELEFON_FORMATLARI" in kaynak


def test_phone_flow_processes_each_file_moves_it_notifies_and_releases_gpu(tmp_path):
    w = PhoneWorld(tmp_path)
    w.results["b.pdf"] = sonuc(rc=1, isler=[job(metin_kontrol=["s. 3"])])
    assert runner.telefon_calistir(w.dp["root"], **w.kwargs()) == 1
    assert w.setups == ["dots.mocr"] and w.calls == ["a.pdf", "b.pdf"] and w.released == [True]
    assert runner.list_inputs(w.dp["input"]) == []
    assert sorted(p.name for p in (w.dp["input"] / "islenenler").iterdir()) == ["a.pdf", "b.pdf"]
    assert [n[0] for n in w.notes] == ["OCR başladı: 2 dosya", "OCR bitti — kontrol gerekli"]
    assert w.notes[1][1] == "a.pdf: 40 sayfa\nb.pdf: 40 sayfa · metin kontrolü: s. 3"
    assert w.notes[1][2]["tiklama"].startswith("https://drive.google.com/")
    assert "OCR bitti — kontrol gerekli" in w.dp["durum"].read_text(encoding="utf-8")


def test_phone_flow_keeps_unwritten_or_crashed_files_in_inbox(tmp_path):
    w = PhoneWorld(tmp_path, files=("a.pdf", "b.pdf", "c.pdf"))
    w.results["a.pdf"] = sonuc(rc=2, isler=None, yazilanlar=())
    w.results["b.pdf"] = RuntimeError("vLLM açılmadı")
    assert runner.telefon_calistir(w.dp["root"], **w.kwargs()) == 1
    assert [p.name for p in runner.list_inputs(w.dp["input"])] == ["a.pdf", "b.pdf"]
    assert "b.pdf: HATA — vLLM açılmadı" in w.notes[-1][1]
    assert w.released == [True]


def test_phone_flow_empty_inbox_skips_setup(tmp_path):
    w = PhoneWorld(tmp_path, files=())
    assert runner.telefon_calistir(w.dp["root"], **w.kwargs()) == 0
    assert w.setups == [] and w.released == [True]
    assert w.notes[0][0] == "OCR: işlenecek dosya yok"


def test_phone_flow_setup_crash_is_reported_and_gpu_released(tmp_path):
    w = PhoneWorld(tmp_path)
    kw = w.kwargs()

    def bad_setup(root, model):
        raise RuntimeError("model indirilemedi")
    kw["_kur"] = bad_setup
    assert runner.telefon_calistir(w.dp["root"], **kw) == 2
    assert w.notes[-1][0] == "OCR durdu (hata)" and "model indirilemedi" in w.notes[-1][1]
    assert "model indirilemedi" in w.dp["durum"].read_text(encoding="utf-8")
    assert w.released == [True] and len(runner.list_inputs(w.dp["input"])) == 2


def test_phone_flow_can_keep_gpu(tmp_path):
    w = PhoneWorld(tmp_path)
    runner.telefon_calistir(w.dp["root"], gpu_birak=False, **w.kwargs())
    assert w.released == []


@pytest.fixture
def fake_local(tmp_path):
    """Sahte ocr-now: rapor, belge ve önbellek dosyası yazar."""
    local = tmp_path / "local"
    local.mkdir()
    (local / "ayarlar").mkdir()
    script = local / "ocr-now"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, sys
        from pathlib import Path
        args = sys.argv[1:]
        root = Path({str(local)!r})
        rapor = Path(args[args.index("--rapor") + 1])
        girdi = Path(args[-1])
        (root / "output/belgeler" / (girdi.stem + ".docx")).write_text("belge")
        (root / "output/_cache/sayfa1.json").write_text("{{}}")
        (root / "output/_cache/.ocr-yarim.json").write_text("yarım")
        rapor.write_text(json.dumps({{"belgeler": [], "okunamayan": False, "isler": [
            {{"ad": girdi.stem, "sayfa": 3, "yazildi": True, "tahmin": [], "kontrol": [], "metin_kontrol": [],
              "kesilen": [], "yazma_hatalari": [], "uyarilar": ["u"]}}]}}))
        print("bitti")
        """), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return local


def test_ocr_run_returns_report_outputs_and_syncs_cache(tmp_path, fake_local, monkeypatch):
    monkeypatch.setattr(runner, "_gpu_memory_gb", lambda: None)
    dp = runner.drive_paths(tmp_path / "drive")
    dp["input"].mkdir(parents=True)
    (dp["input"] / "a.pdf").write_bytes(b"%PDF")
    s = runner.ocr_calistir(dp["root"], dosya="a.pdf", local_root=fake_local, work_dir=tmp_path / "work")
    assert s.rc == 0 and s.rapor["isler"][0]["sayfa"] == 3
    assert [p.name for p in s.yazilanlar] == ["a.docx"] and (dp["output"] / "a.docx").exists()
    assert (dp["cache"] / "sayfa1.json").exists()
    assert not (dp["cache"] / ".ocr-yarim.json").exists()          # yarım yazılmış önbellek Drive'a gitmez
    assert runner.run_ocr(dp["root"], dosya="a.pdf", local_root=fake_local, work_dir=tmp_path / "work") == 0
    assert (dp["output"] / "a_2.docx").exists()


def test_phone_flow_only_processes_listed_files(tmp_path):
    w = PhoneWorld(tmp_path, files=("a.pdf", "b.pdf"))
    runner.telefon_calistir(w.dp["root"], yalniz=["b.pdf"], **w.kwargs())
    assert w.calls == ["b.pdf"] and [p.name for p in runner.list_inputs(w.dp["input"])] == ["a.pdf"]
    w2 = PhoneWorld(tmp_path / "2", files=("a.pdf",))
    runner.telefon_calistir(w2.dp["root"], yalniz=[], **w2.kwargs())
    assert w2.setups == [] and w2.notes[0][0] == "OCR: işlenecek dosya yok"


def test_topic_parser():
    assert runner.konu_ayikla("# a\n\n  k-1 \nk-2") == "k-1"
    assert runner.konu_ayikla("# kapalı\n") == ""


def _setup_env_commands(tmp_path, monkeypatch, *, venv_exists):
    """setup_env'i sahte komutlarla çalıştırır, çalıştırılan komutları döndürür."""
    root = tmp_path / "local"
    (root / ".venv/bin").mkdir(parents=True)
    (root / "ayarlar").mkdir()
    if venv_exists:
        (root / ".venv/bin/ocr-now").write_text("")
    uv = tmp_path / "uv"
    uv.write_text("")
    ran = []

    def fake_run(cmd, *a, **k):
        ran.append(cmd)
        return runner.subprocess.CompletedProcess(cmd, 0, stdout="NVIDIA L4, 23034 MiB\n", stderr="")
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.shutil, "which", lambda name: str(uv))
    monkeypatch.setattr(runner, "_has_tesseract_tur", lambda: True)
    runner.setup_env(tmp_path / "drive", model="tesseract", local_root=root, local_hf=tmp_path / "hf")
    return ran, root


def _pip_installs(ran, root):
    return [c for c in ran if isinstance(c, list) and c[:3] == ["uv", "pip", "install"] and c[-1] == str(root)]


def test_setup_env_refreshes_dependencies_of_existing_venv(tmp_path, monkeypatch):
    """git pull yeni bağımlılık (ör. pikepdf) getirebilir: .venv varken de proje yeniden kurulur."""
    ran, root = _setup_env_commands(tmp_path, monkeypatch, venv_exists=True)
    assert not [c for c in ran if isinstance(c, list) and c[:2] == ["uv", "venv"]]
    assert len(_pip_installs(ran, root)) == 1


def test_setup_env_creates_missing_venv(tmp_path, monkeypatch):
    ran, root = _setup_env_commands(tmp_path, monkeypatch, venv_exists=False)
    venvs = [i for i, c in enumerate(ran) if isinstance(c, list) and c[:2] == ["uv", "venv"]]
    installs = [i for i, c in enumerate(ran) if c in _pip_installs(ran, root)]
    assert len(venvs) == 1 and len(installs) == 1 and venvs[0] < installs[0]
