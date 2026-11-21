"""ocr-gui sunucusu: tarayıcıdaki sayfa için yerel HTTP uç noktaları (yalnız 127.0.0.1).

Güvenlik: yalnız localhost Host başlığı kabul edilir (DNS yeniden bağlama); POST'lar JSON gövde ya da X-Dosya-Adi
başlığı ister. İkisi de tarayıcıda ön kontrol (CORS preflight) gerektirir ve buna cevap verilmediği için başka
sitelerin sayfaları iş başlatamaz, dosya yükleyemez.
"""
import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import urllib.parse
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from ocrnow import engines, pages, paths, profiles
from ocrnow.cli import EXPORTERS
from ocrnow.gui.job import Job
from ocrnow.pages import DEFAULT_DPI, InputError

INPUT_EXTS = {".pdf"} | pages.IMAGE_EXTS
MAX_UPLOAD = 1024 ** 3          # 1 GB
UPLOAD_CHUNK = 64 * 1024        # 64 KiB akış parçası
DPI_RANGE = (72, 600)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

_PAGE_CACHE_LOCK = threading.Lock()
_PAGE_COUNT_CACHE: dict[tuple[str, int, int], int] = {}


class FormError(ValueError):
    """Formdaki değer kabul edilmedi; ileti doğrudan kullanıcıya gösterilir."""


# ---- girdiler ----

def _input_file(name: str) -> Path:
    path = paths.INPUT / Path(str(name)).name
    if not name or Path(str(name)).name != name or not path.is_file() or path.suffix.lower() not in INPUT_EXTS:
        raise FormError(f"Tarama bulunamadı: {name}")
    return path


def _page_count(path: Path) -> int:
    if path.suffix.lower() != ".pdf":
        return 1
    try:
        st = path.stat()
    except OSError as e:
        raise InputError(f"PDF açılamadı: {path} ({e})") from e
    key = (str(path), st.st_size, st.st_mtime_ns)
    with _PAGE_CACHE_LOCK:
        if key in _PAGE_COUNT_CACHE:
            return _PAGE_COUNT_CACHE[key]
    count = pages.pdf_page_count(path)
    with _PAGE_CACHE_LOCK:
        if len(_PAGE_COUNT_CACHE) > 512:
            _PAGE_COUNT_CACHE.clear()
        _PAGE_COUNT_CACHE[key] = count
    return count


def input_files() -> list[dict]:
    if not paths.INPUT.is_dir():
        return []
    out = []
    for f in sorted(paths.INPUT.iterdir(), key=lambda f: f.name.lower()):
        if f.is_file() and not f.name.startswith(".") and f.suffix.lower() in INPUT_EXTS:
            try:
                out.append({"ad": f.name, "sayfa": _page_count(f)})
            except InputError:
                continue                       # açılamayan PDF listede görünmez
    return out


def _sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def save_upload_stream(name: str, stream, length: int, *, validate: bool = False) -> tuple[Path, int | None]:
    """Yüklemeyi bellekte biriktirmeden geçici dosyaya akıtır, SHA-256 ile mevcut kopyayı arar, yoksa atomik taşır."""
    base = Path(str(name).replace("\\", "/")).name
    stem, suffix = Path(base).stem, Path(base).suffix.lower()
    if not stem or base.startswith(".") or suffix not in INPUT_EXTS:
        raise FormError(f"Yalnız PDF ya da görüntü (PNG, JPG, TIFF) yüklenebilir: {name}")
    if length < 0:
        raise FormError("Geçersiz dosya boyutu")
    if length > MAX_UPLOAD:
        raise FormError("Dosya çok büyük (en çok 1 GB)")
    paths.INPUT.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=paths.INPUT, prefix=".upload-", suffix=Path(base).suffix)
    tmp = Path(tmp_name)
    try:
        hasher = hashlib.sha256()
        remaining = length
        written = 0
        with os.fdopen(fd, "wb") as out:
            while remaining > 0:
                chunk = stream.read(min(UPLOAD_CHUNK, remaining))
                if not chunk:
                    break
                out.write(chunk)
                hasher.update(chunk)
                written += len(chunk)
                remaining -= len(chunk)
        digest = hasher.hexdigest()
        count: int | None = None
        if validate:
            try:
                count = pages.pdf_page_count(tmp) if suffix == ".pdf" else 1
            except InputError as e:
                raise FormError(f"Dosya açılamadı: {e}") from None
        with paths.export_lock(paths.INPUT):
            path, k = paths.INPUT / base, 2
            while path.exists():
                try:
                    if path.is_file() and path.stat().st_size == written and _sha256_file(path) == digest:
                        return path, count
                except OSError:
                    pass
                path, k = paths.INPUT / f"{stem}_{k}{Path(base).suffix}", k + 1
            tmp.replace(path)
            return path, count
    finally:
        tmp.unlink(missing_ok=True)


def save_upload(name: str, data: bytes) -> Path:
    """Yüklenen dosyayı input/ altına yazar. Aynı adla aynı içerik varsa o dosya kullanılır (önbellek geçerli
    kalır); farklıysa _2, _3 eklenir."""
    return save_upload_stream(name, io.BytesIO(data), len(data))[0]


# ---- form → ocr-now argümanları ----

def build_args(form: dict) -> list[str]:
    path = _input_file(form.get("dosya", ""))
    args = [str(path)]
    profile_id = form.get("profil") or profiles.DEFAULT_PROFILE
    if profile_id not in profiles.PROFILES:
        raise FormError(f"Bilinmeyen profil: {profile_id}")
    if profile_id != profiles.DEFAULT_PROFILE:
        args += ["--profil", profile_id]
    prof = profiles.PROFILES[profile_id]
    spec = str(form.get("sayfalar") or "").strip()
    if spec and spec.lower() != "hepsi":
        try:
            _, notes = pages.parse_pages(spec, _page_count(path))
        except InputError as e:
            raise FormError(str(e)) from None
        if notes:
            raise FormError(f"{'; '.join(notes)} (taramada {_page_count(path)} sayfa var)")
        args += ["-p", spec]
    model = form.get("model")
    if model not in engines.ENGINES:
        raise FormError(f"Bilinmeyen model: {model}")
    mode = form.get("okuma")
    if mode not in engines.ENGINES[model].modes:
        raise FormError(f"{model} modelinde '{mode}' okuma modu yok")
    args += ["-m", model, "-o", mode]
    try:
        dpi = int(form.get("dpi", DEFAULT_DPI))
    except (TypeError, ValueError):
        dpi = 0
    if not DPI_RANGE[0] <= dpi <= DPI_RANGE[1]:
        raise FormError(f"DPI {DPI_RANGE[0]}–{DPI_RANGE[1]} arasında olmalı")
    args += ["--dpi", str(dpi)]
    lang = str(form.get("dil") or "").strip()
    if lang:
        args += ["--dil", lang]
    kraken_model = str(form.get("kraken_model") or "").strip()
    if kraken_model:
        args += ["--kraken-model", kraken_model]
    first = str(form.get("ilk_sayfa") or "").strip()
    if first:
        if not first.isdigit() or int(first) < 1:
            raise FormError("İlk sayfanın kitap numarası 1 ya da daha büyük bir tam sayı olmalı")
        args += ["--ilk-sayfa", first]
    args.append("--isaretle" if form.get("isaretle", prof.highlight) else "--isaretleme")
    kesme = form.get("kesme", prof.apostrophes)
    if kesme and not prof.apostrophes:
        args.append("--kesme")
    elif not kesme and prof.apostrophes:
        args.append("--kesme-yok")
    formats = form.get("formatlar") or []
    if not formats or any(f not in EXPORTERS for f in formats):
        raise FormError("En az bir çıktı biçimi seçin (Word, Markdown ya da metin)")
    return args + ["-f", *dict.fromkeys(formats)]


# ---- Windows'ta gösterme ----

def show_in_explorer(path: Path) -> None:
    """Dosyayı Windows Gezgini'nde seçili gösterir (WSL); başka sistemde klasörü açar."""
    try:
        win = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=True).stdout.strip()
        subprocess.Popen(["explorer.exe", f"/select,{win}"])
    except (OSError, subprocess.CalledProcessError):
        subprocess.Popen(["xdg-open", str(path.parent)])


# ---- uygulama durumu ----

class App:
    def __init__(self, cmd: list[str] | None = None, opener: Callable[[Path], None] = show_in_explorer,
                 on_close: Callable[[], None] | None = None):
        self.cmd = cmd
        self.opener = opener
        self.on_close = on_close
        self.job: Job | None = None
        self._lock = threading.Lock()

    def state(self) -> dict:
        models = []
        for e in engines.ENGINES.values():
            reason = engines.missing(e.name)
            models.append({"ad": e.name, "hazir": reason is None, "neden": reason, "modlar": list(e.modes),
                           "onerilen": e.recommended and e.name != "tesseract"})
        models.sort(key=lambda m: m["ad"] != engines.DEFAULT_ENGINE)    # varsayılan başta
        profs = [
            {"id": p.id, "ad": p.label, "aciklama": p.description, "model": p.engine, "okuma": p.mode,
             "formatlar": list(p.formats), "isaretle": p.highlight, "kesme": p.apostrophes,
             "sayfa_no": p.detect_pagenum, "rtl": p.rtl}
            for p in profiles.PROFILES.values()
        ]
        cfg = engines.api_config()
        api_state = {"base_url": cfg["base_url"], "model": cfg["model"], "anahtar_var": bool(cfg["api_key"])}
        k_models = [p.name for p in engines.kraken_models()]
        return {"dosyalar": input_files(), "modeller": models, "profiller": profs, "api": api_state,
                "kraken_modelleri": k_models,
                "is": self.job.snapshot() if self.job else None,
                "varsayilan": {"profil": profiles.DEFAULT_PROFILE, "model": engines.DEFAULT_ENGINE,
                               "okuma": engines.DEFAULT_MODE, "dpi": DEFAULT_DPI, "dil": engines.TESS_LANG,
                               "formatlar": ["docx"], "isaretle": True, "kesme": True}}

    def start(self, form: dict) -> Job:
        args = build_args(form)
        with self._lock:
            if self.job is not None and self.job.running:
                raise RuntimeError("Bir okuma zaten sürüyor; bitmesini bekleyin ya da iptal edin")
            self.job = Job(args, name=Path(args[0]).name, cmd=self.cmd)
            self.job.start()
            return self.job

    def stop(self, timeout: float = 90) -> None:
        """Süren işi iptal eder ve bitmesini bekler (model sunucusu kapansın)."""
        job = self.job
        if job is not None and job.running:
            job.cancel()
            job.wait(timeout)


def _target(where: str, name: str) -> Path | None:
    folder = {"belge": paths.DOCS_OUT, "tarama": paths.INPUT}.get(where)
    if folder is None or not name or Path(name).name != name:
        return None
    path = folder / name
    return path if path.is_file() else None


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "ocr-gui"

    def log_message(self, fmt, *args):     # her isteği terminale yazma
        pass

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"hata": message}, status)

    def _local(self) -> bool:
        host = (self.headers.get("Host") or "").strip()
        host = host.rsplit(":", 1)[0] if not host.endswith("]") else host
        if host in LOCAL_HOSTS:
            return True
        self._error(HTTPStatus.FORBIDDEN, "yalnız bu bilgisayardan erişilebilir")
        return False

    def _content_length(self) -> int:
        raw = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw)
        except ValueError:
            raise FormError("Geçersiz Content-Length başlığı") from None
        if length < 0:
            raise FormError("Geçersiz Content-Length başlığı")
        if length > MAX_UPLOAD:
            raise FormError("Dosya çok büyük (en çok 1 GB)")
        return length

    def _body(self) -> bytes:
        return self.rfile.read(self._content_length())

    def do_GET(self):
        if not self._local():
            return
        path = urllib.parse.urlsplit(self.path).path
        if path == "/":
            page = resources.files("ocrnow.gui").joinpath("index.html").read_bytes()
            self._send(200, page, "text/html; charset=utf-8")
        elif path == "/api/saglik":
            self._json({"ocr-gui": True})
        elif path == "/api/durum":
            self._json(self.app.state())
        elif path == "/api/is":
            self._json(self.app.job.snapshot() if self.app.job else None)
        elif path.startswith("/belge/"):
            name = urllib.parse.unquote(path[len("/belge/"):])
            target = _target("belge", name)
            if target is None:
                return self._error(404, "belge bulunamadı")
            quoted = urllib.parse.quote(name)
            self._send(200, target.read_bytes(), "application/octet-stream",
                       {"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"})
        else:
            self._error(404, "bulunamadı")

    def do_POST(self):
        if not self._local():
            return
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/yukle":
                name = self.headers.get("X-Dosya-Adi")
                if not name:
                    return self._error(403, "X-Dosya-Adi başlığı gerekli")
                length = self._content_length()
                saved, count = save_upload_stream(urllib.parse.unquote(name), self.rfile, length, validate=True)
                return self._json({"ad": saved.name, "sayfa": count})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._error(403, "JSON gövde gerekli")
            try:
                form = json.loads(self._body() or b"{}")
            except ValueError:
                return self._error(400, "geçersiz JSON")
            if path == "/api/baslat":
                job = self.app.start(form)
                return self._json(job.snapshot())
            if path == "/api/iptal":
                if self.app.job:
                    self.app.job.cancel()
                return self._json(self.app.job.snapshot() if self.app.job else None)
            if path == "/api/goster":
                target = _target(form.get("yer", ""), form.get("ad", ""))
                if target is None:
                    return self._error(404, "dosya bulunamadı")
                self.app.opener(target)
                return self._json({"tamam": True})
            if path == "/api/api-ayar":
                base_url = str(form.get("base_url") or "").strip()
                model = str(form.get("model") or "").strip()
                if base_url and not base_url.startswith(("http://", "https://")):
                    raise FormError("API adresi http:// veya https:// ile başlamalı")
                raw_key = form.get("api_key")
                api_key = None if raw_key in (None, "") else str(raw_key)
                if form.get("anahtari_sil"):
                    api_key = ""
                engines.save_api_config(base_url, model, api_key)
                return self._json(self.app.state())
            if path == "/api/kapat":
                self._json({"tamam": True})
                if self.app.on_close:
                    threading.Thread(target=self.app.on_close, daemon=True).start()
                return None
            return self._error(404, "bulunamadı")
        except FormError as e:
            return self._error(400, str(e))
        except InputError as e:
            return self._error(400, str(e))
        except RuntimeError as e:
            return self._error(409, str(e))


def make_server(app: App, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    return httpd
