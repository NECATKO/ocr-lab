"""ocr-gui iş yürütücüsü: ocr-now alt sürecinin çıktısından ilerleme, kalan süre ve sonuç."""
import sys
import textwrap

import pytest

from ocrnow.gui.job import Job


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def fed(lines_with_times):
    clock = Clock()
    job = Job(["x.pdf"], clock=clock)
    for t, line in lines_with_times:
        clock.t = t
        job.feed(line)
    return job, clock


def test_progress_and_phase_from_page_lines():
    job, _ = fed([(0, "kitap: s. 1 (1/4) okunuyor…"), (5, "kitap: s. 2 (2/4) önbellekten")])
    snap = job.snapshot()
    assert snap["ilerleme"] == {"sayfa": 2, "toplam": 4}
    assert snap["asama"] == "Sayfa 2/4 önbellekten alındı"


def test_server_start_phase():
    job, _ = fed([(0, "kitap: s. 1 (1/4) okunuyor…"),
                  (1, "[dots.mocr] sunucu başlatılıyor (log: /x/dots.mocr.server.log)")])
    assert job.snapshot()["asama"].startswith("Model açılıyor")


def test_remaining_time_excludes_server_start():
    """Model açılışı (dakikalar) sayfa süresine katılmaz; kalan = ortalama sayfa süresi × kalan sayfa."""
    job, clock = fed([(0, "k: s. 1 (1/10) okunuyor…"), (1, "[dots.mocr] sunucu başlatılıyor (log: l)"),
                      (200, "k: s. 2 (2/10) okunuyor…"), (230, "k: s. 3 (3/10) okunuyor…"),
                      (260, "k: s. 4 (4/10) okunuyor…")])
    clock.t = 270
    # ortalama 30 sn; 4. sayfanın 10 sn'si geçti → 20 + 6 × 30
    assert job.snapshot()["kalan"] == 200


def test_remaining_time_unknown_until_a_clean_interval():
    job, _ = fed([(0, "k: s. 1 (1/10) okunuyor…"), (1, "[dots.mocr] sunucu başlatılıyor (log: l)"),
                  (200, "k: s. 2 (2/10) okunuyor…")])
    assert job.snapshot()["kalan"] is None


def test_log_keeps_recent_lines():
    job, _ = fed([(i, f"satır {i}") for i in range(500)])
    lines = job.snapshot()["satirlar"]
    assert lines[-1] == "satır 499" and len(lines) <= 300


FAKE = textwrap.dedent('''
    import json, signal, sys, time
    args = sys.argv[1:]
    rapor = args[args.index("--rapor") + 1]
    mode = args[0]
    signal.signal(signal.SIGINT, lambda *a: (print("İptal edildi.", flush=True), sys.exit(130)))
    print("k: s. 1 (1/2) okunuyor…", flush=True)
    if mode == "uzun":
        time.sleep(30)
    if mode == "cokus":
        print("Hata: model başlatılamadı", flush=True)
        sys.exit(1)
    print("k: s. 2 (2/2) okunuyor…", flush=True)
    json.dump({"belgeler": ["/b/k.docx"], "isler": [], "okunamayan": False, "args": args}, open(rapor, "w"))
''')


@pytest.fixture
def fake_cmd(tmp_path):
    script = tmp_path / "fake_ocr_now.py"
    script.write_text(FAKE)
    return [sys.executable, str(script)]


def test_run_to_completion_reads_report(fake_cmd):
    job = Job(["iyi", "-f", "docx"], cmd=fake_cmd)
    job.start()
    assert job.wait(20)
    snap = job.snapshot()
    assert snap["durum"] == "bitti" and snap["cikis"] == 0
    assert snap["sonuc"]["belgeler"] == ["/b/k.docx"]
    assert snap["sonuc"]["args"][:3] == ["iyi", "-f", "docx"]     # --rapor sona eklenir
    assert snap["ilerleme"] == {"sayfa": 2, "toplam": 2}


def test_failure_without_report_is_error(fake_cmd):
    job = Job(["cokus"], cmd=fake_cmd)
    job.start()
    assert job.wait(20)
    snap = job.snapshot()
    assert snap["durum"] == "hata" and snap["sonuc"] is None
    assert "Hata: model başlatılamadı" in snap["satirlar"]


def test_cancel_sends_interrupt(fake_cmd):
    job = Job(["uzun"], cmd=fake_cmd)
    job.start()
    for _ in range(100):                          # alt süreç sinyal işleyicisini kursun
        if job.snapshot()["ilerleme"]:
            break
        __import__("time").sleep(0.05)
    job.cancel()
    assert job.wait(20)
    snap = job.snapshot()
    assert snap["durum"] == "iptal" and snap["cikis"] == 130
