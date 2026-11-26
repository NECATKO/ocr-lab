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
                           colab=["colab"], durum_dizini=tmp_path / "durum", hermes="")


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

    def __call__(self, colab, betik, *, oturum, gpu, log, satir_isle=None):
        metin = betik.read_text(encoding="utf-8")
        assert oct(betik.stat().st_mode & 0o777) == "0o600"
        self.calls.append(json.loads(metin.splitlines()[0].removeprefix("AYAR = "))["dosyalar"])
        for ad in self.isle:                       # VM'de işlenen dosya islenenler/'e taşınır
            self.drive.files.pop(ad)
        if satir_isle is not None:
            for line in self.cikti.splitlines(keepends=True):
                satir_isle(line)
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


KURULUM_HATASI = ("[colab] Session READY (x).\nOCR-VPS-BASLADI\nTraceback ...\nRuntimeError: rclone mount açılamadı\n"
                  "OCR-VPS-HAZIRLIK-HATASI\nOCR-VPS-SONUC 3\n")


def test_setup_failure_keeps_unstarted_files_and_retries_after_backoff(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1"), "b.pdf": ("20", "t1")})
    colab = Colab(d, cikti=KURULUM_HATASI, rc=0)
    now = [1000.0]
    a = args(tmp_path)
    kw = dict(_run=d.run, _colab=colab, _bildir=None, _now=lambda: now[0])
    assert izleyici.bir_tur(a, **kw) == 1
    durum = izleyici.durum_oku(a.durum_dizini / "izleyici.json")
    assert durum["basarisiz"] == {}                                   # dosyaların suçu yok
    now[0] += 60
    izleyici.bir_tur(a, **kw)
    assert len(colab.calls) == 1                                      # altyapı beklemesi uygulanır
    now[0] += izleyici.GERI_CEKILME
    colab.cikti, colab.isle = OK, ["a.pdf", "b.pdf"]
    assert izleyici.bir_tur(a, **kw) == 0
    assert colab.calls == [["a.pdf", "b.pdf"], ["a.pdf", "b.pdf"]]   # aynı iki dosya yeniden denendi


def test_repeated_setup_failure_gives_up_and_notifies(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1")})
    colab = Colab(d, cikti=KURULUM_HATASI)
    notes, now = [], [1000.0]
    a = args(tmp_path)
    kw = dict(_run=d.run, _colab=colab, _now=lambda: now[0],
              _bildir=lambda konu, baslik, mesaj, **k: notes.append((baslik, mesaj)))
    izleyici.bir_tur(a, **kw)
    assert notes == []
    now[0] += izleyici.GERI_CEKILME + 1
    izleyici.bir_tur(a, **kw)
    assert len(colab.calls) == 2 and len(notes) == 1 and "rclone mount" in notes[0][1]
    now[0] += izleyici.GERI_CEKILME + 1
    izleyici.bir_tur(a, **kw)
    assert len(colab.calls) == 2                                      # birim yakmaya devam etmez


def test_result_code_is_read_from_its_own_line_only(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1")})
    cikti = "[colab] Session READY\n$ grep OCR-VPS-SONUC 0 günlük\nConnection was lost\n"
    a = args(tmp_path)
    izleyici.bir_tur(a, _run=d.run, _colab=Colab(d, cikti=cikti, rc=1), _bildir=None)
    durum = izleyici.durum_oku(a.durum_dizini / "izleyici.json")
    assert durum["basarisiz"] == {} and durum["kopmalar"] == {"a.pdf": 1}   # yarıda kalmış sayılır
    assert izleyici.sonuc_kodu("x\nOCR-VPS-SONUC 1\n") == 1
    assert izleyici.sonuc_kodu("OCR-VPS-SONUC bozuk\n") is None


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


def sessiz_colab(tmp_path, govde: str) -> tuple[Path, Path]:
    """`run` çağrısında govde'yi çalıştıran, `stop -s AD` çağrısını dosyaya yazan sahte colab."""
    durdur = tmp_path / "durdurulan"
    fake = tmp_path / "colab"
    fake.write_text(f"""#!{sys.executable}
import sys, time
from pathlib import Path
if sys.argv[1] == "stop":
    Path({str(durdur)!r}).write_text(sys.argv[3]); sys.exit(0)
{govde}
""")
    fake.chmod(0o755)
    return fake, durdur


def calistir_suresiz(tmp_path, fake, en_uzun):
    import time
    betik = tmp_path / "b.py"
    betik.write_text("")
    bas = time.monotonic()
    rc, cikti = izleyici.colab_calistir([str(fake)], betik, oturum="ocr-vps-1", gpu="L4", log=tmp_path / "x.log",
                                        en_uzun=en_uzun)
    return rc, cikti, time.monotonic() - bas


def test_silent_job_is_stopped_when_time_runs_out(tmp_path):
    fake, durdur = sessiz_colab(tmp_path, "print('[colab] Session READY', flush=True); time.sleep(60)")
    rc, cikti, sure = calistir_suresiz(tmp_path, fake, en_uzun=1)
    assert sure < 20 and "Session READY" in cikti
    assert durdur.read_text() == "ocr-vps-1"


def test_job_stuck_mid_line_is_stopped_when_time_runs_out(tmp_path):
    fake, durdur = sessiz_colab(tmp_path, "print('Session READY'); print('yarım', end='', flush=True); time.sleep(60)")
    rc, cikti, sure = calistir_suresiz(tmp_path, fake, en_uzun=1)
    assert sure < 20 and durdur.read_text() == "ocr-vps-1"


def test_job_finishing_in_time_is_not_stopped(tmp_path):
    fake, durdur = sessiz_colab(tmp_path, "print('Session READY'); time.sleep(0.3); print('OCR-VPS-SONUC 0')")
    rc, cikti, sure = calistir_suresiz(tmp_path, fake, en_uzun=10)
    assert rc == 0 and cikti.endswith("OCR-VPS-SONUC 0\n") and not durdur.exists()
    assert (tmp_path / "x.log").read_text() == cikti


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


# --- Hermes (Telegram) ---

class Hermes:
    def __init__(self):
        self.mesajlar = []
        self.ekler = []

    def __call__(self, hedef, metin):
        self.mesajlar.append((hedef, metin))
        for line in metin.splitlines():            # hermes send dosyayı göndermeden döner; şimdi oku
            if line.startswith("MEDIA:"):
                yol = Path(line.removeprefix("MEDIA:"))
                self.ekler.append((yol.name, yol.read_bytes()))
        return True


def drive_with_output(tmp_path, files, output):
    d = Drive(tmp_path, files)
    eski = d.run

    def run(cmd, **kw):
        if cmd[:2] == ["rclone", "copyto"]:
            ad = cmd[2].rsplit("/output/", 1)[1]
            if ad not in output:
                return done(rc=1, stderr="not found")
            Path(cmd[3]).write_bytes(output[ad])
            return done()
        return eski(cmd, **kw)
    d.run = run
    return d


def isaret(ad, veri):
    return f"{ad} {json.dumps(veri, ensure_ascii=False)}\n"


def test_notifications_and_documents_go_to_hermes(tmp_path):
    d = drive_with_output(tmp_path, {"kitap 1.pdf": ("10", "t1")},
                          {"kitap 1.docx": b"DOCX", "kitap 1.txt": b"TXT"})
    cikti = ("[colab] Session READY\n"
             + isaret("OCR-VPS-BILDIRIM", {"baslik": "OCR başladı: 1 dosya", "mesaj": "kitap 1.pdf"})
             + isaret("OCR-VPS-BELGE", {"girdi": "kitap 1.pdf", "belgeler": ["kitap 1.docx", "kitap 1.txt"]})
             + isaret("OCR-VPS-BILDIRIM", {"baslik": "OCR bitti", "mesaj": "kitap 1.pdf: 3 sayfa"})
             + "OCR-VPS-BELGE {bozuk\nOCR-VPS-SONUC 0\n")
    h = Hermes()
    a = args(tmp_path)
    a.hermes = "telegram"
    assert izleyici.bir_tur(a, _run=d.run, _colab=Colab(d, cikti=cikti, isle=["kitap 1.pdf"]),
                            _bildir=None, _hermes=h) == 0
    metinler = [m for _, m in h.mesajlar]
    assert metinler[0] == "OCR başladı: 1 dosya\nkitap 1.pdf"
    assert metinler[1] == "OCR bitti\nkitap 1.pdf: 3 sayfa"
    assert metinler[2].startswith("📄 kitap 1.pdf\nMEDIA:") and len(h.mesajlar) == 3
    assert h.ekler == [("kitap_1.docx", b"DOCX")]          # .docx varken ham .txt gönderilmez
    assert {hedef for hedef, _ in h.mesajlar} == {"telegram"}


def test_belgeleri_ilet_sends_pdf(tmp_path):
    d = drive_with_output(tmp_path, {}, {"a.docx": b"DOCX", "a.pdf": b"%PDF", "a.txt": b"TXT"})
    h = Hermes()
    izleyici.belgeleri_ilet(isaret("OCR-VPS-BELGE", {"girdi": "a.pdf", "belgeler": ["a.docx", "a.pdf", "a.txt"]}),
                            uzak="gdrive:ocr-lab", hedef="telegram", _run=d.run, _gonder=h)
    medya = [s for s in h.mesajlar[0][1].splitlines() if s.startswith("MEDIA:")]
    assert [Path(m).suffix for m in medya] == [".docx", ".pdf"]      # ham .txt gönderilmez
    assert h.ekler == [("a.docx", b"DOCX"), ("a.pdf", b"%PDF")]


def test_belgeleri_ilet_skips_oversized_pdf(tmp_path, monkeypatch):
    assert izleyici.TELEGRAM_SINIRI == 50 * 1024 * 1024
    monkeypatch.setattr(izleyici, "TELEGRAM_SINIRI", 1000)           # testte 50 MB yazmamak için
    d = drive_with_output(tmp_path, {}, {"a.docx": b"DOCX", "a.pdf": b"x" * 1001})
    indirilen = []
    eski = d.run

    def run(cmd, **kw):
        if cmd[:2] == ["rclone", "copyto"]:
            indirilen.append(Path(cmd[3]))
        return eski(cmd, **kw)
    h = Hermes()
    gonderimde_var = []

    def gonder(hedef, metin):
        gonderimde_var.append(indirilen[1].exists())          # büyük dosya gönderimden önce silinmiş olmalı
        return h(hedef, metin)
    izleyici.belgeleri_ilet(isaret("OCR-VPS-BELGE", {"girdi": "a.pdf", "belgeler": ["a.docx", "a.pdf"]}),
                            uzak="gdrive:ocr-lab", hedef="telegram", _run=run, _gonder=gonder)
    metin = h.mesajlar[0][1]
    assert [s for s in metin.splitlines() if s.startswith("MEDIA:")] == [f"MEDIA:{Path(indirilen[0]).parent / 'a.docx'}"]
    assert "Telegram için çok büyük" in metin and "output/" in metin and "a.pdf" in metin
    assert h.ekler == [("a.docx", b"DOCX")]
    assert gonderimde_var == [False]


def test_missing_document_is_reported_in_text(tmp_path):
    d = drive_with_output(tmp_path, {}, {})
    h = Hermes()
    izleyici.belgeleri_ilet(isaret("OCR-VPS-BELGE", {"girdi": "a.pdf", "belgeler": ["a.docx"]}),
                            uzak="gdrive:ocr-lab", hedef="telegram", _run=d.run, _gonder=h)
    assert h.ekler == [] and "indirilemedi" in h.mesajlar[0][1] and "a.docx" in h.mesajlar[0][1]


def test_watcher_notifications_also_go_to_hermes(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1")})
    notes, h = [], Hermes()
    a = args(tmp_path)
    a.hermes = "telegram"
    izleyici.bir_tur(a, _run=d.run, _colab=Colab(d, cikti="Error: no L4 capacity\n", rc=1),
                     _bildir=lambda konu, baslik, *x, **k: notes.append(baslik), _hermes=h)
    assert notes == ["OCR: Colab L4 açılamadı"]
    assert h.mesajlar[0][1].startswith("OCR: Colab L4 açılamadı\n")


def test_hermes_disabled_sends_nothing(tmp_path):
    d = drive_with_output(tmp_path, {"a.pdf": ("1", "t")}, {"a.docx": b"x"})
    cikti = ("Session READY\n" + isaret("OCR-VPS-BILDIRIM", {"baslik": "b", "mesaj": "m"})
             + isaret("OCR-VPS-BELGE", {"girdi": "a.pdf", "belgeler": ["a.docx"]}) + "OCR-VPS-SONUC 0\n")
    h = Hermes()
    izleyici.bir_tur(args(tmp_path), _run=d.run, _colab=Colab(d, cikti=cikti, isle=["a.pdf"]), _bildir=None,
                     _hermes=h)
    assert h.mesajlar == []


def test_hermes_send_uses_stdin_and_survives_failure():
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw.get("input")))
        return done(rc=1, stderr="gateway down")
    assert izleyici.hermes_gonder("telegram", "-baslik\nMEDIA:/x", run) is False
    assert calls == [(["hermes", "send", "--to", "telegram", "--file", "-"], "-baslik\nMEDIA:/x")]
    assert izleyici.hermes_gonder("", "x", run) is False and len(calls) == 1


def test_documents_to_send():
    assert izleyici.gonderilecek_belgeler(["a.docx", "a.txt", "b.txt", "c.pdf"]) == ["a.docx", "b.txt", "c.pdf"]
    assert izleyici.guvenli_ad("kitap 1 / x.docx") == "kitap_1_x.docx"


def test_colab_runner_feeds_lines_live(tmp_path):
    fake = tmp_path / "colab"
    fake.write_text(f"#!{sys.executable}\nprint('a');print('b')\n")
    fake.chmod(0o755)
    betik = tmp_path / "b.py"
    betik.write_text("")
    gelen = []
    izleyici.colab_calistir([str(fake)], betik, oturum="s", gpu="L4", log=tmp_path / "x.log",
                            satir_isle=lambda l: gelen.append(l.strip()) or 1 / 0)   # hata işi bozmaz
    assert gelen[-2:] == ["a", "b"]


# --- ocr_ekle.py ---

import ocr_ekle  # noqa: E402


class Yukleme:
    def __init__(self, mevcut=()):
        self.mevcut = set(mevcut)
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["rclone", "lsf"]:
            return done("".join(f"{a}\n" for a in self.mevcut) if cmd[-1].endswith("/input") else "")
        if cmd[:2] == ["rclone", "copyto"]:
            return done()
        if cmd[0] == "systemctl":
            assert kw["env"]["XDG_RUNTIME_DIR"]
            return done()
        raise AssertionError(cmd)


def test_add_strips_hermes_prefix_avoids_collisions_and_kicks_watcher(tmp_path, capsys):
    f = tmp_path / "doc_0123456789ab_Kitap s.10.pdf"
    f.write_bytes(b"%PDF")
    y = Yukleme(mevcut=["Kitap s.10.pdf"])
    assert ocr_ekle.ekle([f], uzak="gdrive:ocr-lab", _run=y) == 0
    assert ["rclone", "copyto", str(f), "gdrive:ocr-lab/input/Kitap s.10_2.pdf"] in y.calls
    assert y.calls[-1][:4] == ["systemctl", "--user", "start", "--no-block"]
    out = capsys.readouterr().out
    assert "input/Kitap s.10_2.pdf" in out
    assert "Word dosyası ve aranabilir PDF Telegram'a" in out


def test_add_rejects_non_scans_without_uploading(tmp_path):
    f = tmp_path / "notlar.txt"
    f.write_text("x")
    y = Yukleme()
    assert ocr_ekle.ekle([f, tmp_path / "yok.pdf"], uzak="gdrive:ocr-lab", _run=y) == 1
    assert not any(c[:2] == ["rclone", "copyto"] or c[0] == "systemctl" for c in y.calls)


def test_hidden_names_are_made_visible():
    assert ocr_ekle.hedef_ad(Path("doc_0123456789ab_.gizli.pdf")) == "_.gizli.pdf"
    assert ocr_ekle.bos_ad("a.pdf", {"a.pdf", "a_2.pdf"}) == "a_3.pdf"


def test_template_markers_are_parsed_by_watcher(capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("colab_is_sablon", izleyici.SABLON)
    sablon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sablon)
    assert sablon.BELGE_ISARETI == izleyici.BELGE_ISARETI and sablon.BILDIRIM_ISARETI == izleyici.BILDIRIM_ISARETI
    sablon.isaret_bas(sablon.BELGE_ISARETI, {"girdi": "ğ ü.pdf", "belgeler": ["ğ ü.docx"]})
    assert izleyici.isaretli_satirlar(capsys.readouterr().out, izleyici.BELGE_ISARETI) == [
        {"girdi": "ğ ü.pdf", "belgeler": ["ğ ü.docx"]}]


def sablon_modulu():
    import importlib.util
    spec = importlib.util.spec_from_file_location("colab_is_sablon", izleyici.SABLON)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_upload_query_failure_is_unknown_not_zero(monkeypatch):
    m = sablon_modulu()
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: done(rc=1))
    assert m.bekleyen_yukleme() is None
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: done("bozuk"))
    assert m.bekleyen_yukleme() is None
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: done('{"diskCache": {"uploadsQueued": 2}}'))
    assert m.bekleyen_yukleme() == 2


class SahteMount:
    def wait(self, timeout=None):
        return 0


def test_unmount_waits_while_upload_state_is_unknown(monkeypatch):
    m = sablon_modulu()
    durumlar = iter([None, None, 1, 0])
    monkeypatch.setattr(m, "bekleyen_yukleme", lambda: next(durumlar))
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: done())
    assert m.ayir(SahteMount(), en_fazla=60) is True


def test_unmount_reports_unconfirmed_upload(monkeypatch):
    m = sablon_modulu()
    saat = [0.0]
    monkeypatch.setattr(m, "bekleyen_yukleme", lambda: None)
    monkeypatch.setattr(m.time, "time", lambda: saat[0])
    monkeypatch.setattr(m.time, "sleep", lambda s: saat.__setitem__(0, saat[0] + s))
    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: done())
    assert m.ayir(SahteMount(), en_fazla=60) is False


def test_job_prints_upload_warning_before_result(monkeypatch, capsys):
    m = sablon_modulu()
    m.AYAR.update(dosyalar=["a.pdf"], model="dots.mocr")
    from colab import runner as gercek
    for ad in ("rclone_kur", "repo_hazirla"):
        monkeypatch.setattr(m, ad, lambda: None)
    monkeypatch.setattr(m, "bagla", lambda: SahteMount())
    monkeypatch.setattr(m, "ayir", lambda proc: False)
    monkeypatch.setattr(gercek, "telefon_calistir", lambda *a, **k: 0)
    assert m.calis() == 0
    cikti = capsys.readouterr().out.splitlines()
    assert cikti[-2:] == [m.YUKLEME_DOGRULANAMADI, "OCR-VPS-SONUC 0"]
    assert m.YUKLEME_DOGRULANAMADI == izleyici.YUKLEME_DOGRULANAMADI


def test_watcher_warns_when_upload_was_not_confirmed(tmp_path):
    d = Drive(tmp_path, {"a.pdf": ("10", "t1")})
    cikti = OK.replace("OCR-VPS-SONUC", f"{izleyici.YUKLEME_DOGRULANAMADI}\nOCR-VPS-SONUC")
    notes = []
    izleyici.bir_tur(args(tmp_path), _run=d.run, _colab=Colab(d, cikti=cikti, isle=["a.pdf"]),
                     _bildir=lambda konu, baslik, mesaj, **k: notes.append(baslik))
    assert notes == ["OCR: Drive'a yükleme doğrulanamadı"]
