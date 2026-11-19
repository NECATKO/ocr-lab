"""Tek bir ocr-now koşusu: alt süreci başlatır, çıktısından ilerlemeyi ve kalan süreyi çıkarır, sonda raporu okur.

ocr-now'ın kendisi ayrı bir süreçte çalışır (GPU kilidi, model sunucusu ve Ctrl-C temizliği olduğu gibi kalır).
İptal SIGINT'tir: ocr-now bunu Ctrl-C gibi karşılar ve model sunucusunu kapatıp 130 ile çıkar.
"""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

LOG_LINES = 300
_PAGE = re.compile(r"\((\d+)/(\d+)\) (okunuyor…|önbellekten|okunamadı|kontrol gerekli)")
_SERVER = "sunucu başlatılıyor"
_STATES = {"okunuyor…": "okunuyor", "önbellekten": "önbellekten alındı", "okunamadı": "okunamadı",
           "kontrol gerekli": "kontrol gerekli"}


def default_cmd() -> list[str]:
    return [sys.executable, "-m", "ocrnow.cli"]


class Job:
    """durum: "bekliyor" → "calisiyor" → "bitti" | "kismi" | "hata" | "iptal"."""

    def __init__(self, args: list[str], *, name: str = "", cmd: list[str] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.args = list(args)
        self.name = name
        self.cmd = cmd or default_cmd()
        self.clock = clock
        self.state = "bekliyor"
        self.phase = "Başlatılıyor"
        self.progress: dict | None = None
        self.lines: deque[str] = deque(maxlen=LOG_LINES)
        self.code: int | None = None
        self.result: dict | None = None
        self.started = clock()
        self.finished: float | None = None
        self._last_page: float | None = None   # son sayfa satırının zamanı
        self._dirty = False                    # son aralıkta model açıldı: ortalamaya katılmaz
        self._intervals: list[float] = []
        self._cancelled = False
        self._lock = threading.Lock()
        self._done = threading.Event()
        self._proc: subprocess.Popen | None = None
        fd, report = tempfile.mkstemp(prefix="ocr-gui-", suffix=".json")
        os.close(fd)                           # ocr-now dosyayı adıyla yazar
        self._report = Path(report)

    # ---- çıktı çözümleme ----

    def feed(self, line: str) -> None:
        line = line.rstrip("\n")
        now = self.clock()
        with self._lock:
            self.lines.append(line)
            if self._cancelled:
                return  # geç gelen bir ilerleme satırı iptal durumunu gizlemesin
            if _SERVER in line:
                self.phase = "Model açılıyor (ilk açılış birkaç dakika sürer)"
                self._dirty = True
            elif m := _PAGE.search(line):
                page, total = int(m.group(1)), int(m.group(2))
                current = {"sayfa": page, "toplam": total}
                if current != self.progress:
                    if self._last_page is not None and not self._dirty:
                        self._intervals.append(now - self._last_page)
                    self._last_page, self._dirty = now, False
                    self.progress = current
                self.phase = f"Sayfa {page}/{total} {_STATES[m.group(3)]}"

    def _remaining(self) -> int | None:
        if self._cancelled or not self._intervals or not self.progress or self.state not in ("bekliyor", "calisiyor"):
            return None
        avg = sum(self._intervals[-8:]) / len(self._intervals[-8:])    # son sayfalar: önbellek/okuma karışımı
        left = self.progress["toplam"] - self.progress["sayfa"]
        current = max(0.0, avg - (self.clock() - self._last_page))
        return round(current + left * avg)

    def snapshot(self) -> dict:
        with self._lock:
            end = self.finished if self.finished is not None else self.clock()
            return {"durum": self.state, "ad": self.name, "asama": self.phase, "ilerleme": self.progress,
                    "kalan": self._remaining(), "gecen": round(end - self.started),
                    "satirlar": list(self.lines), "cikis": self.code, "sonuc": self.result}

    # ---- süreç ----

    def start(self) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self._proc = subprocess.Popen([*self.cmd, *self.args, "--rapor", str(self._report)],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                      text=True, encoding="utf-8", errors="replace", bufsize=1, env=env,
                                      start_new_session=True)   # pencere kapanınca gelen SIGHUP'ı almaz; GUI iptal eder
        self.state = "calisiyor"
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        for line in self._proc.stdout:
            self.feed(line)
        code = self._proc.wait()
        result = None
        try:
            if self._report.stat().st_size:
                data = json.loads(self._report.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("belgeler"), list) and isinstance(data.get("isler"), list):
                    result = data
        except (OSError, ValueError):
            result = None
        finally:
            self._report.unlink(missing_ok=True)
        with self._lock:
            self.code, self.result, self.finished = code, result, self.clock()
            if self._cancelled:
                self.state, self.phase = "iptal", "İptal edildi"
            elif code == 0 and result is not None:
                self.state, self.phase = "bitti", "Bitti"
            elif result and result["belgeler"]:
                self.state, self.phase = "kismi", "Kısmen tamamlandı"
            else:
                self.state, self.phase = "hata", "Hata ile durdu"
        self._done.set()

    def cancel(self) -> None:
        with self._lock:
            if not self._cancelled and self._proc and self._proc.poll() is None:
                self._proc.send_signal(signal.SIGINT)
                self._cancelled = True
                self.phase = "İptal ediliyor (model kapatılıyor)…"

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    @property
    def running(self) -> bool:
        return self.state == "calisiyor"
