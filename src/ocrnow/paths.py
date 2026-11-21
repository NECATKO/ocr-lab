"""Bütün yollar ve alt süreç ortamı burada durur; başka modül Path(__file__) ya da sabit klasör adı kullanmaz.

Kullanım: `from ocrnow import paths` ve `paths.X` çağrı anında okunur (`from ocrnow.paths import X` yok).
"""
import fcntl
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# OCRNOW_ROOT ile değiştirilebilir (testler); yoksa depo kökü
ROOT = Path(os.environ.get("OCRNOW_ROOT") or Path(__file__).resolve().parents[2])

INPUT = ROOT / "input"
OUTPUT = ROOT / "output"
GT = ROOT / "gt"
PAGES = OUTPUT / "_pages"
LOGS = OUTPUT / "_logs"
CACHE = OUTPUT / "_cache"
DOCS_OUT = OUTPUT / "belgeler"
LAB_OUT = OUTPUT / "lab"
MODELS = ROOT / "models"
TOOLS = ROOT / "tools"
SETTINGS = ROOT / "ayarlar"
VENV_VLLM = ROOT / ".venv-vllm"  # vllm ve kraken ayrı ortamlarda (bağımlılıkları çakışıyor)
VENV_KRAKEN = ROOT / ".venv-kraken"


def hf_cache() -> Path:
    """Hugging Face'in yerel model deposu (ağa bağlanmaz)."""
    if env := os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE"):
        return Path(env)
    return Path(os.environ.get("HF_HOME") or
                Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "huggingface") / "hub"


@contextmanager
def export_lock(folder: Path) -> Iterator[None]:
    """Ad seçiminden son biçimin yazımına kadar çıktı klasörünü diğer ocr-now koşularına kilitle."""
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)    # kapatılınca kilit bırakılır; disk üzerinde kilit dosyası gerekmez


@contextmanager
def atomic_output(path: Path) -> Iterator[Path]:
    """Alıcı yalnız tamamlanmış dosyayı görsün; eşzamanlı yazılar farklı geçici yollar kullanır."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".ocr-", suffix=path.suffix)
    tmp = Path(name)
    try:
        os.close(fd)
        yield tmp
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def subprocess_env() -> dict[str, str]:
    """VLM/kraken alt süreçleri için ortam: PATH başında vllm ve kraken ortamları, FlashInfer örnekleyici kapalı."""
    env = dict(os.environ)
    front = [str(VENV_VLLM / "bin"), str(VENV_KRAKEN / "bin")]
    env["PATH"] = ":".join([*front, env["PATH"]] if env.get("PATH") else front)
    env["VLLM_USE_FLASHINFER_SAMPLER"] = "0"  # FlashInfer örnekleyici nvcc ister; CUDA toolkit yok
    return env
