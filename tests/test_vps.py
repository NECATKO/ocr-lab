import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vps"))
import izleyici  # noqa: E402


def done(stdout="", rc=0, stderr=""):
    return subprocess.CompletedProcess([], rc, stdout, stderr)


class Drive:
    """Sahte rclone: input/ içeriği, bildirim.txt ve ayar dosyası."""

    def __init__(self, tmp_path, files):
        self.files = dict(files)            # ad -> (boyut, zaman)
        self.conf = tmp_path / "rclone.conf"
        self.conf.write_text("[gdrive]\ntype = drive\ntoken = {\"x\": 1}\n")
        self.bildirim = "# yorum\nkonu-123\n"

    def run(self, cmd, **kw):
        if cmd[:2] == ["rclone", "lsf"]:
            return done("".join(f"{a}\t{b}\t{z}\n" for a, (b, z) in self.files.items()))
        if cmd[:3] == ["rclone", "config", "file"]:
            return done(f"Configuration file is stored at:\n{self.conf}\n")
        if cmd[:2] == ["rclone", "cat"]:
            return done(self.bildirim)
        raise AssertionError(cmd)


def args(tmp_path):
    return SimpleNamespace(uzak="gdrive:ocr-lab", gpu="L4", model="dots.mocr", repo="R", dal="main",
                           colab=["colab"], durum_dizini=tmp_path / "durum")


def test_listing_filters_non_inputs(tmp_path):
    d = Drive(tmp_path, {"b.pdf": ("10", "2026-11-22 10:00:00"), "a.JPG": ("5", "t"), "not.txt": ("1", "t"),
                         ".gizli.pdf": ("1", "t")})
    assert [g.ad for g in izleyici.girdileri_listele("gdrive:ocr-lab", d.run)] == ["a.JPG", "b.pdf"]


def test_job_script_embeds_settings_and_is_valid_python():
    sablon = izleyici.SABLON.read_text(encoding="utf-8")
    metin = izleyici.is_betigi(sablon, uzak="gdrive:ocr-lab", rclone_conf="[gdrive]\ntoken = ğ\n",
                               dosyalar=["a b.pdf"], repo="R", dal="main", model="dots.mocr")
    ast.parse(metin)
    ayar = json.loads(metin.splitlines()[0].removeprefix("AYAR = "))
    assert ayar["dosyalar"] == ["a b.pdf"] and ayar["uzak"] == "gdrive:ocr-lab"
    import base64
    assert base64.b64decode(ayar["rclone_conf_b64"]).decode() == "[gdrive]\ntoken = ğ\n"


def test_template_alone_does_nothing_when_run():
    res = subprocess.run([sys.executable, str(izleyici.SABLON)], capture_output=True, text=True)
    assert res.returncode == 0 and res.stdout == ""


class Colab:
    def __init__(self, drive, *, cikti, rc=0, isle=()):
        self.drive, self.cikti, self.rc, self.isle = drive, cikti, rc, isle
        self.calls = []

    def __call__(self, colab, betik, *, oturum, gpu, log):
        metin = betik.read_text(encoding="utf-8")
        assert oct(betik.stat().st_mode & 0o777) == "0o600"
        self.calls.append(json.loads(metin.splitlines()[0].removeprefix("AYAR = "))["dosyalar"])
        for ad in self.isle:                       # VM'de işlenen dosya islenenler/'e taşınır
            self.drive.files.pop(ad)
        return self.rc, self.cikti


OK = "[colab] Session READY (x).\n...\nOCR-VPS-SONUC 0\n"


def test_nothing_new_does_not_start_gpu(tmp_path):
    d = Drive(tmp_path, {})
    colab = Colab(d, cikti=OK)
    assert izleyici.bir_tur(args(tmp_path), _run=d.run, _colab=colab, _bildir=None) == 0
    assert colab.calls == []


def test_failed_file_is_not_retried_until_it_changes(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1"), "b.pdf": ("20", "t1")})
    colab = Colab(d, cikti=OK, rc=1, isle=["a.pdf"])          # b.pdf okunamadı, input/'ta kaldı
    notes = []
    a = args(tmp_path)
    assert izleyici.bir_tur(a, _run=d.run, _colab=colab, _bildir=lambda *x, **k: notes.append(x)) == 0
    assert colab.calls == [["a.pdf", "b.pdf"]] and notes == []    # bildirimi VM'deki akış zaten gönderdi
    assert izleyici.bir_tur(a, _run=d.run, _colab=colab, _bildir=None) == 0
    assert len(colab.calls) == 1                                   # b.pdf yeniden denenmedi
    d.files["b.pdf"] = ("21", "t2")                                # kullanıcı dosyayı yeniden yükledi
    colab.isle = ["b.pdf"]
    izleyici.bir_tur(a, _run=d.run, _colab=colab, _bildir=None)
    assert colab.calls[-1] == ["b.pdf"]
    assert izleyici.durum_oku(a.durum_dizini / "izleyici.json")["basarisiz"] == {}


def test_gpu_unavailable_backs_off_and_notifies_once(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1")})
    colab = Colab(d, cikti="[colab] Creating session...\nError: no L4 capacity\n", rc=1)
    notes, now = [], [1000.0]
    a = args(tmp_path)
    kw = dict(_run=d.run, _colab=colab, _bildir=lambda konu, baslik, *x, **k: notes.append((konu, baslik)),
              _now=lambda: now[0])
    assert izleyici.bir_tur(a, **kw) == 1
    assert notes == [("konu-123", "OCR: Colab L4 açılamadı")]
    now[0] += 60
    assert izleyici.bir_tur(a, **kw) == 0 and len(colab.calls) == 1   # geri çekilme süresinde denenmez
    now[0] += izleyici.GERI_CEKILME
    izleyici.bir_tur(a, **kw)
    assert len(colab.calls) == 2 and len(notes) == 1                 # ikinci başarısızlıkta yeniden bildirmez
    assert izleyici.durum_oku(a.durum_dizini / "izleyici.json")["basarisiz"] == {}   # dosya suçlu sayılmaz


def test_interrupted_job_is_retried_once_then_given_up_and_notified(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1")})
    colab = Colab(d, cikti="[colab] Session READY (x).\nRuntimeError: Connection was lost.\n", rc=1)
    notes = []
    a = args(tmp_path)
    kw = dict(_run=d.run, _colab=colab, _bildir=lambda konu, baslik, mesaj, **k: notes.append((baslik, mesaj)))
    izleyici.bir_tur(a, **kw)
    assert notes == [] and izleyici.durum_oku(a.durum_dizini / "izleyici.json")["basarisiz"] == {}
    izleyici.bir_tur(a, **kw)                                          # ikinci kopma: vazgeç ve bildir
    assert len(colab.calls) == 2
    assert notes[0][0] == "OCR: Colab işi yarıda kaldı" and "Connection was lost" in notes[0][1]
    izleyici.bir_tur(a, **kw)
    assert len(colab.calls) == 2                                       # artık denenmez


def test_successful_run_clears_interrupt_count(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1")})
    a = args(tmp_path)
    izleyici.bir_tur(a, _run=d.run, _colab=Colab(d, cikti="[colab] Session READY\nkopma\n", rc=1), _bildir=None)
    izleyici.bir_tur(a, _run=d.run, _colab=Colab(d, cikti=OK, isle=["a.pdf"]), _bildir=None)
    assert izleyici.durum_oku(a.durum_dizini / "izleyici.json")["kopmalar"] == {}


def test_colab_runner_streams_output_to_log(tmp_path):
    fake = tmp_path / "colab"
    fake.write_text(f"#!{sys.executable}\nimport sys\nprint('[colab] Session READY');print(' '.join(sys.argv[1:]))\n")
    fake.chmod(0o755)
    betik = tmp_path / "b.py"
    betik.write_text("")
    rc, cikti = izleyici.colab_calistir([str(fake)], betik, oturum="s1", gpu="L4", log=tmp_path / "x.log")
    assert rc == 0 and "Session READY" in cikti
    assert f"run -s s1 --gpu L4 --timeout 0 {betik}" in cikti
    assert (tmp_path / "x.log").read_text() == cikti


def test_colab_runner_retries_when_connection_drops_before_job_starts(tmp_path):
    sayac = tmp_path / "sayac"
    fake = tmp_path / "colab"
    fake.write_text(f"""#!{sys.executable}
import sys
from pathlib import Path
p = Path({str(sayac)!r}); n = int(p.read_text()) + 1 if p.exists() else 1; p.write_text(str(n))
print("[colab] Session READY")
if n == 1:
    print("RuntimeError: Connection was lost."); sys.exit(1)
print("OCR-VPS-BASLADI"); print("OCR-VPS-SONUC 0")
""")
    fake.chmod(0o755)
    betik = tmp_path / "b.py"
    betik.write_text("")
    rc, cikti = izleyici.colab_calistir([str(fake)], betik, oturum="s", gpu="L4", log=tmp_path / "x.log")
    assert rc == 0 and sayac.read_text() == "2" and "OCR-VPS-SONUC 0" in cikti
    assert (tmp_path / "x-2.log").exists()


def test_colab_runner_does_not_retry_once_job_started(tmp_path):
    fake = tmp_path / "colab"
    fake.write_text(f"#!{sys.executable}\nimport sys\nprint('OCR-VPS-BASLADI');print('Connection was lost.');sys.exit(1)\n")
    fake.chmod(0o755)
    betik = tmp_path / "b.py"
    betik.write_text("")
    rc, cikti = izleyici.colab_calistir([str(fake)], betik, oturum="s", gpu="L4", log=tmp_path / "x.log")
    assert rc == 1 and cikti.count("OCR-VPS-BASLADI") == 1


def test_old_logs_are_pruned(tmp_path):
    for i in range(25):
        (tmp_path / f"2026{i:04d}.log").write_text("x")
    izleyici.eski_kayitlari_sil(tmp_path, sakla=20)
    assert len(list(tmp_path.glob("*.log"))) == 20 and not (tmp_path / "20260000.log").exists()


def test_lock_prevents_parallel_runs(tmp_path, monkeypatch):
    import fcntl
    d = tmp_path / "durum"
    d.mkdir()
    with open(d / "kilit", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        monkeypatch.setattr(izleyici, "bir_tur", lambda a: (_ for _ in ()).throw(AssertionError("çalışmamalı")))
        assert izleyici.main(["--durum-dizini", str(d)]) == 0
