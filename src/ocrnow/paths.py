"""Bütün yollar ve alt süreç ortamı burada durur; başka modül Path(__file__) ya da sabit klasör adı kullanmaz.

Kullanım: `from ocrnow import paths` ve `paths.X` çağrı anında okunur (`from ocrnow.paths import X` yok).
"""
import os
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


def subprocess_env() -> dict[str, str]:
    """VLM/kraken alt süreçleri için ortam: PATH başında vllm ve kraken ortamları, FlashInfer örnekleyici kapalı."""
    env = dict(os.environ)
    front = [str(VENV_VLLM / "bin"), str(VENV_KRAKEN / "bin")]
    env["PATH"] = ":".join([*front, env["PATH"]] if env.get("PATH") else front)
    env["VLLM_USE_FLASHINFER_SAMPLER"] = "0"  # FlashInfer örnekleyici nvcc ister; CUDA toolkit yok
    return env
