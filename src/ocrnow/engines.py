"""Motor kaydı: hangi motor hangi modda çalışır, VLM sunucusu nasıl açılıp kapanır, ham metin nasıl alınır.

Yollar `paths.X` ile çağrı anında okunur. Sunucu komutlarındaki $TOOLS ve $MODELS yer tutucuları
`paths.TOOLS` / `paths.MODELS` ile, $VLLM ortak vLLM bayraklarıyla değişir.
"""
import base64
import json
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from ocrnow import paths

TESS_LANG = os.environ.get("TESS_LANG", "tur")
MAX_TOKENS = 4096
VLLM_FLAGS = "--max-model-len 8192 --gpu-memory-utilization 0.85 --mm-processor-cache-gb 0 --no-enable-prefix-caching"  # 8 GB GPU için

# dots.mocr'un yerleşim (bbox + kategori + metin) istemi; ekler/layout_try.py'deki PROMPT, kelimesi kelimesine
LAYOUT_PROMPT = """Please output the layout information from the PDF image, including each layout element's bbox, its category, and the corresponding text content within the bbox.

1. Bbox format: [x1, y1, x2, y2]

2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].

3. Text Extraction & Formatting Rules:
    - Picture: For the 'Picture' category, the text field should be omitted.
    - Formula: Format its text as LaTeX.
    - Table: Format its text as HTML.
    - All Others (Text, Title, etc.): Format their text as Markdown.

4. Constraints:
    - The output text must be the original text from the image, with no translation.
    - All layout elements must be sorted according to human reading order.

5. Final Output: The entire output must be a single JSON object.
"""


class ServerError(RuntimeError):
    """Motor sunucusu açılamadı: hiçbir sayfa okunamaz, koşu durmalı (sayfa düzeyinde hata değil)."""


@dataclass(frozen=True)
class Mode:
    prompt: str | None
    layout: bool  # True → model blok JSON'u döner


@dataclass(frozen=True)
class Engine:
    name: str
    kind: str  # "local" | "vlm"
    modes: dict[str, Mode]  # anahtar: "duz" | "duzen"
    port: int | None = None
    serve: str | None = None  # $TOOLS, $MODELS ve $VLLM yer tutucuları kullanılabilir
    recommended: bool = False
    vram_gb: float | None = None  # tahmini asgari GPU belleği (GB); None → işlemci (aşağıdaki nota bkz.)
    disk_gb: float | None = None  # motora özgü diskte yer (GB); ortak .venv-vllm (~8 GB) dahil değil


def _duz(prompt: str | None) -> dict[str, Mode]:
    return {"duz": Mode(prompt, False)}


# vram_gb: sunucu açıkken nvidia-smi ile ÖLÇÜLEMEZ, çünkü vLLM `--gpu-memory-utilization 0.85` ile belleğin
# neredeyse hepsini baştan ayırır (kullanılan bellek ihtiyacı değil, ayrılan paydır). Bu yüzden tahmini asgari
# kullanılır: yüklenen dtype'taki ağırlık boyutu + 1.5 GB (etkinleştirme/KV önbelleği/CUDA bağlamı payı).
# Ağırlık boyutu: fp8 modellerde parametre sayısı × 1 bayt, bf16'da × 2 bayt, GGUF'ta dosya boyutu
# (parametre sayıları HF önbelleğindeki safetensors başlıklarından; GB = 2^30 bayt):
#   paddleocr-vl GGUF 0.94+0.88 GB → 1.7+1.5=3.2 | glm-ocr 1.33 G param bf16 → 2.5+1.5=4.0
#   lightonocr 1.01 G bf16 → 1.9+1.5=3.4 | deepseek-ocr2 3.39 G fp8 → 3.2+1.5=4.7 | dots.mocr 3.04 G fp8 → 2.8+1.5=4.3
# disk_gb: HF önbelleği anlık görüntüsünün `du -sL` boyutu (GGUF için llama.cpp 1.0 GB dahil; kraken için
# .venv-kraken 3.7 GB dahil); 0.1 GB'a yuvarlandı. tesseract sistem paketidir, bu tabloda yer tutmaz.
# VLM'ler OpenAI uyumlu sunucu üzerinden çağrılır. Sunucuyu kendin açtıysan (docker, ollama vs.)
# sadece portu eşle; kayıt ona bağlanır, kapatmaz.
ENGINES: dict[str, Engine] = {e.name: e for e in [
    Engine("tesseract", "local", _duz(None), recommended=True),
    Engine("kraken", "local", _duz(None), disk_gb=3.7),
    Engine("paddleocr-vl", "vlm", _duz("OCR:"), port=8001, vram_gb=3.2, disk_gb=2.7,
           serve="$TOOLS/llama.cpp/llama-server -m $MODELS/PaddleOCR-VL-1.6.gguf --mmproj $MODELS/PaddleOCR-VL-1.6-mmproj.gguf --temp 0 -ngl 99 -c 8192 --port 8001"),
    Engine("glm-ocr", "vlm", _duz("Text Recognition:"), port=8002, recommended=True, vram_gb=4.0, disk_gb=2.5,
           serve="vllm serve zai-org/GLM-OCR --port 8002 $VLLM"),
    Engine("lightonocr", "vlm", _duz(None), port=8003, recommended=True, vram_gb=3.4, disk_gb=1.9,  # LightOnOCR metin istemez, sadece görsel
           serve="""vllm serve lightonai/LightOnOCR-2-1B --port 8003 --limit-mm-per-prompt '{"image": 1}' $VLLM"""),
    Engine("deepseek-ocr2", "vlm", _duz("Free OCR."), port=8004, vram_gb=4.7, disk_gb=6.3,  # bf16 hâli 8 GB'a sığmıyor → fp8
           serve="vllm serve deepseek-ai/DeepSeek-OCR-2 --port 8004 --quantization fp8 --enforce-eager $VLLM"),
    Engine("dots.mocr", "vlm",
           {"duz": Mode("Extract the text content from this image.", False), "duzen": Mode(LAYOUT_PROMPT, True)},
           port=8005, recommended=True, vram_gb=4.3, disk_gb=5.7,  # 6 GB → fp8
           serve="""vllm serve dots-studio/dots.mocr --port 8005 --trust-remote-code --chat-template-content-format string --quantization fp8 --enforce-eager --mm-processor-kwargs '{"max_pixels": 4000000}' $VLLM"""),
]}
DEFAULT_ENGINE = "dots.mocr"
DEFAULT_MODE = "duzen"


def _tesseract_text(img: Path) -> str:
    return subprocess.run(["tesseract", str(img), "stdout", "-l", TESS_LANG],
                          capture_output=True, text=True, check=True, env=paths.subprocess_env()).stdout


def tesseract_tsv(img: Path) -> str:
    """Kelime kutularıyla birlikte tesseract TSV çıktısı (satır yapısı buradan gelir)."""
    return subprocess.run(["tesseract", str(img), "stdout", "-l", TESS_LANG, "tsv"],
                          capture_output=True, text=True, check=True, env=paths.subprocess_env()).stdout


def _kraken_model() -> Path:
    env = os.environ.get("KRAKEN_MODEL")
    return paths.ROOT / env if env else paths.MODELS / "catmus-print-large.mlmodel"  # göreli yol köke göre


def _kraken_text(img: Path) -> str:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "o.txt"
        subprocess.run(["kraken", "-i", str(img), str(out), "segment", "-bl", "ocr", "-m", str(_kraken_model())],
                       capture_output=True, text=True, check=True, env=paths.subprocess_env())
        return out.read_text()


def _local_text(name: str, img: Path) -> str:
    return _tesseract_text(img) if name == "tesseract" else _kraken_text(img)


def _which(cmd: str) -> str | None:
    """Komutu önce vllm/kraken ortamlarında, sonra PATH'te arar (alt süreçlerin PATH'iyle aynı sıra)."""
    for venv in (paths.VENV_VLLM, paths.VENV_KRAKEN):
        found = shutil.which(str(venv / "bin" / cmd))
        if found:
            return found
    return shutil.which(cmd)


def _serve_cmd(eng: Engine) -> list[str]:
    # önce bölünür, yollar sonra parça parça konur: kök klasör adında boşluk olsa da komut bozulmaz
    return [t.replace("$TOOLS", str(paths.TOOLS)).replace("$MODELS", str(paths.MODELS))
            for t in shlex.split(eng.serve.replace("$VLLM", VLLM_FLAGS))]


def _base(eng: Engine) -> str:
    return f"http://127.0.0.1:{eng.port}/v1"


def missing(name: str) -> str | None:
    """Motor çalışamayacaksa nedenini döndürür, hazırsa None."""
    if name == "tesseract":
        return None if _which("tesseract") else "tesseract kurulu değil"
    if name == "kraken":
        if not _which("kraken"):
            return "kraken kurulu değil"
        return None if _kraken_model().exists() else f"model yok: {_kraken_model()}"
    eng = ENGINES[name]
    if served_model(_base(eng)):
        return None
    cmd = _serve_cmd(eng)
    if not _which(cmd[0]):
        return f"{cmd[0]} kurulu değil"
    lost = [a for a in cmd if a.endswith(".gguf") and not Path(a).exists()]
    return f"dosya yok: {', '.join(lost)}" if lost else None


def served_model(base: str) -> str | None:
    try:
        with urllib.request.urlopen(base + "/models", timeout=3) as r:
            return json.load(r)["data"][0]["id"]
    except Exception:
        return None


def _start_server(eng: Engine):
    """(base, model, proc) döndürür; sunucu zaten açıksa proc None olur ve kapatılmaz.

    Beklerken hata ya da Ctrl-C gelirse açılan süreç kapatılıp istisna yeniden fırlatılır
    (sunucu ayrı oturumda çalıştığı için Ctrl-C ona ulaşmaz).
    """
    base = _base(eng)
    proc = None
    if not served_model(base):
        paths.LOGS.mkdir(parents=True, exist_ok=True)
        with open(paths.LOGS / f"{eng.name}.server.log", "w") as log:
            print(f"[{eng.name}] sunucu başlatılıyor (log: {log.name})")
            proc = subprocess.Popen(_serve_cmd(eng), stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, env=paths.subprocess_env())
        try:
            deadline = time.time() + 1200
            while not served_model(base):
                if proc.poll() is not None or time.time() > deadline:
                    raise RuntimeError(f"sunucu ayağa kalkmadı, bkz. {log.name}")
                time.sleep(3)
        except BaseException:
            _stop_server(proc)
            raise
    return base, served_model(base), proc


def _stop_server(proc) -> None:
    if proc and proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(60)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)


def _vlm(img: Path, base: str, model: str, prompt: str | None) -> str:
    content = [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(img.read_bytes()).decode()}}]
    if prompt:
        content.append({"type": "text", "text": prompt})
    body = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0, "max_tokens": MAX_TOKENS}
    req = urllib.request.Request(base + "/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.load(r)["choices"][0]["message"]["content"]


def _mode(eng: Engine, mode: str) -> Mode:
    if mode not in eng.modes:
        raise ValueError(f"{eng.name} motorunda '{mode}' modu yok (olanlar: {', '.join(eng.modes)})")
    return eng.modes[mode]


@contextmanager
def server(name: str) -> Iterator[Callable[[Path, str], str]]:
    """Motoru hazırlar ve run(img, mode) -> ham metin verir. VLM sunucusu çıkışta her durumda kapatılır."""
    eng = ENGINES[name]
    if eng.kind == "local":
        yield lambda img, mode: (_mode(eng, mode), _local_text(name, img))[1]
        return
    base, model, proc = _start_server(eng)
    try:
        yield lambda img, mode: _vlm(img, base, model, _mode(eng, mode).prompt)
    finally:
        _stop_server(proc)


@contextmanager
def lazy_server(name: str) -> Iterator[Callable[[Path, str], str]]:
    """server() gibi, ama motor ilk run çağrısında açılır: bütün sayfalar önbellekteyse GPU hiç kullanılmaz.

    Açılış hata verirse ServerError fırlatılır; sonraki çağrılarda da aynısı (her sayfada yeniden beklenmez).
    """
    with ExitStack() as stack:
        state: dict = {}

        def run(img: Path, mode: str) -> str:
            if "error" in state:
                raise state["error"]
            if "run" not in state:
                try:
                    state["run"] = stack.enter_context(server(name))
                except Exception as e:
                    state["error"] = ServerError(f"'{name}' modeli başlatılamadı: {e}")
                    raise state["error"] from e
            return state["run"](img, mode)
        yield run
