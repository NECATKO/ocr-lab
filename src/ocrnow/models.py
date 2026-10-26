"""`ocr-now models`: önerilen modellerin listesi ve tek komutla kurulum.

Kurulum adımları kurulum.sh'tekilerle aynıdır (aynı sürümler, aynı vLLM yaması). Kurulu olan adım listeye girmez,
bu yüzden komutu tekrar çalıştırmak güvenlidir. Yollar `paths.X` ile çağrı anında okunur.
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ocrnow import engines, paths

LLAMA = "b11756"
VLLM = "0.32.0"
KRAKEN = "7.1.1"
KRAKEN_DOI = "10.5281/zenodo.10592716"  # CATMuS-Print Large (çok dilli matbu Latin)
TESSERACT_APT = "sudo apt install -y tesseract-ocr tesseract-ocr-tur"
PADDLE_REPO = "PaddlePaddle/PaddleOCR-VL-1.6-GGUF"
# paddleocr-vl sunucusunun beklediği bağlantılar: models/<ad> → HF anlık görüntüsündeki dosya
PADDLE_LINKS = {"PaddleOCR-VL-1.6.gguf": "PaddleOCR-VL-1.6-GGUF.gguf",
                "PaddleOCR-VL-1.6-mmproj.gguf": "PaddleOCR-VL-1.6-GGUF-mmproj.gguf"}
KRAKEN_MODEL_FILE = "catmus-print-large.mlmodel"
KRAKEN_SRC_NAME = "catmus-print-fondue-large.mlmodel"  # kraken get'in indirdiği dosya adı

# Triton 3.7'de DeepSeek-OCR-2 kodlayıcı çekirdeği derlenmiyor (LOG2E constexpr değil): vLLM 0.32'ye yama
PATCH_FILE = "vllm/model_executor/models/deepencoder.py"
PATCH_IMPORT = "from vllm.triton_utils import LOG2E, tl, triton"
PATCH_MARK = "ocr-lab yaması"
PATCH_LINE = f"LOG2E = tl.constexpr(LOG2E)  # {PATCH_MARK}: Triton 3.7 constexpr olmayan global kabul etmiyor"

# llama.cpp'yi indirir ve açar (kurulum.sh'taki komutun aynısı); $1 hedef klasör, $2 sürüm
LLAMA_SCRIPT = (
    'set -e; mkdir -p "$1" && cd "$1"; '
    'URL=https://github.com/ggml-org/llama.cpp/releases/download/$2; '
    'for f in llama cudart-llama; do curl -fsSL "$URL/$f-$2-bin-ubuntu-cuda-12.8-x64.tar.gz" | tar xz; done; '
    'mv ./*-x64/* llama-$2/* . && rmdir ./*-x64 llama-$2'
)


@dataclass
class Step:
    desc: str
    cmd: list[str] | None = None
    func: Callable[[], None] | None = None


# ---------------------------------------------------------------- liste

def gpu_total_gb() -> float | None:
    """GPU'nun toplam belleği (GB); nvidia-smi yoksa ya da çalışmazsa None."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, check=True).stdout
        return float(out.strip().splitlines()[0]) / 1024  # MiB → GB
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        return None


def list_lines(gpu_gb: float | None) -> list[str]:
    """Başlık + motor başına bir satır; GPU belleği sığmayanlara uyarı konur."""
    lines = [f"  {'Model':<14}  {'GPU belleği':>11}  {'Disk':>8}  Durum"]
    for e in engines.ENGINES.values():
        vram = f"{e.vram_gb:.1f} GB" if e.vram_gb is not None else "CPU"
        disk = f"{e.disk_gb:.1f} GB" if e.disk_gb is not None else "—"
        status = engines.missing(e.name) or "hazır"
        warn = "  ⚠ GPU'ya sığmayabilir" if gpu_gb is not None and e.vram_gb is not None and e.vram_gb > gpu_gb else ""
        lines.append(f"{'★' if e.recommended else ' '} {e.name:<14}  {vram:>11}  {disk:>8}  {status}{warn}")
    return lines


# ---------------------------------------------------------------- kurulum adımları

def _venv_ready(venv: Path) -> bool:
    return (venv / "bin" / "python").exists()


def _has_dist(venv: Path, name: str, version: str) -> bool:
    return bool(glob.glob(str(venv / "lib" / "python*" / "site-packages" / f"{name}-{version}.dist-info")))


def _hf_cache() -> Path:
    if env := os.environ.get("HF_HUB_CACHE"):
        return Path(env)
    return Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"


def _repo_dir(repo: str) -> Path:
    return _hf_cache() / ("models--" + repo.replace("/", "--"))


def _snapshots(repo: str) -> list[Path]:
    return sorted(p for p in (_repo_dir(repo) / "snapshots").glob("*") if p.is_dir() and any(p.iterdir()))


def _hf_done(repo: str) -> bool:
    """Önbellekte anlık görüntü var ve yarım kalmış indirme yok."""
    return bool(_snapshots(repo)) and not list((_repo_dir(repo) / "blobs").glob("*.incomplete"))


def _repo_of(name: str) -> str:
    """vLLM motorunun HF deposu `vllm serve <depo>` komutundan okunur (iki yerde yazılmasın)."""
    return engines.ENGINES[name].serve.split()[2]


def _tesseract_missing() -> None:
    raise RuntimeError(f"tesseract kurulu değil; yönetici yetkisi gerekir, şu komutu çalıştırın: {TESSERACT_APT}")


def _patch_file() -> Path | None:
    found = glob.glob(str(paths.VENV_VLLM / "lib" / "python*" / "site-packages" / PATCH_FILE))
    return Path(found[0]) if found else None


def _patch_needed() -> bool:
    f = _patch_file()
    if f is None:
        return True  # vLLM henüz kurulu değil; kurulduktan sonra çalışacak
    text = f.read_text(encoding="utf-8")
    return PATCH_MARK not in text and PATCH_IMPORT in text


def _apply_patch() -> None:
    f = _patch_file()
    if f is None:
        raise RuntimeError(f"{PATCH_FILE} bulunamadı (vLLM kurulumu eksik)")
    text = f.read_text(encoding="utf-8")
    if PATCH_MARK in text:
        return
    f.write_text(text.replace(PATCH_IMPORT + "\n", f"{PATCH_IMPORT}\n{PATCH_LINE}\n", 1), encoding="utf-8")


def _copy_kraken_model() -> None:
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    found = sorted((data / "htrmopo").rglob(KRAKEN_SRC_NAME))
    if not found:
        raise RuntimeError(f"{KRAKEN_SRC_NAME} bulunamadı ({data / 'htrmopo'})")
    paths.MODELS.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(found[0], paths.MODELS / KRAKEN_MODEL_FILE)


def _link_paddle() -> None:
    snaps = [s for s in _snapshots(PADDLE_REPO) if all((s / f).exists() for f in PADDLE_LINKS.values())]
    if not snaps:
        raise RuntimeError(f"{PADDLE_REPO} dosyaları HF önbelleğinde bulunamadı")
    paths.MODELS.mkdir(parents=True, exist_ok=True)
    for link, target in PADDLE_LINKS.items():
        dest = paths.MODELS / link
        if dest.is_symlink() or dest.exists():  # ln -sf gibi
            dest.unlink()
        dest.symlink_to(snaps[-1] / target)


def _venv_steps(venv: Path, label: str, package: str, version: str) -> list[Step]:
    steps = []
    py = venv / "bin" / "python"
    if not _venv_ready(venv):
        steps.append(Step(f"{label} ortamı ({venv.name})", cmd=["uv", "venv", str(venv), "--python", "3.12", "-q"]))
    if not _has_dist(venv, package, version):
        steps.append(Step(f"{label} {version}", cmd=["uv", "pip", "install", "-q", "--python", str(py), f"{package}=={version}"]))
    return steps


def install_steps(names: list[str]) -> list[Step]:
    """Verilen motorlar için eksik adımlar, kurulum sırasıyla; ortak adımlar bir kez. Kurulu adım listeye girmez."""
    steps: list[Step] = []
    vllm_names = [n for n in names if engines.ENGINES[n].serve and engines.ENGINES[n].serve.startswith("vllm ")]

    if "tesseract" in names and not shutil.which("tesseract"):
        steps.append(Step(f"tesseract kurulu değil: {TESSERACT_APT}", func=_tesseract_missing))

    if "paddleocr-vl" in names and not (paths.TOOLS / "llama.cpp" / "llama-server").exists():
        steps.append(Step(f"llama.cpp {LLAMA} (CUDA 12.8)",
                          cmd=["sh", "-c", LLAMA_SCRIPT, "sh", str(paths.TOOLS / "llama.cpp"), LLAMA]))

    if vllm_names:
        steps += _venv_steps(paths.VENV_VLLM, "vLLM", "vllm", VLLM)
        if _patch_needed():
            steps.append(Step("vLLM yaması (DeepSeek-OCR-2 kodlayıcı çekirdeği)", func=_apply_patch))

    if "kraken" in names:
        steps += _venv_steps(paths.VENV_KRAKEN, "kraken", "kraken", KRAKEN)
        if not (paths.MODELS / KRAKEN_MODEL_FILE).exists():
            steps.append(Step(f"kraken modeli indiriliyor ({KRAKEN_DOI})",
                              cmd=[str(paths.VENV_KRAKEN / "bin" / "kraken"), "get", KRAKEN_DOI]))
            steps.append(Step(f"kraken modeli models/{KRAKEN_MODEL_FILE} olarak kopyalanıyor", func=_copy_kraken_model))

    # vLLM ortamı varsa/kurulacaksa hf oradan gelir; yalnız paddleocr-vl isteniyorsa 8 GB'lık vLLM kurmak yerine uvx
    hf = ([str(paths.VENV_VLLM / "bin" / "hf")] if vllm_names or (paths.VENV_VLLM / "bin" / "hf").exists()
          else ["uvx", "--from", "huggingface_hub", "hf"])
    repos = ([PADDLE_REPO] if "paddleocr-vl" in names else []) + [_repo_of(n) for n in vllm_names]
    for repo in repos:
        if not _hf_done(repo):
            steps.append(Step(f"model ağırlıkları: {repo}", cmd=[*hf, "download", repo]))
    if "paddleocr-vl" in names and not all((paths.MODELS / link).exists() for link in PADDLE_LINKS):
        steps.append(Step("PaddleOCR-VL GGUF bağlantıları (models/)", func=_link_paddle))
    return steps


def _run(steps: list[Step]) -> int:
    for step in steps:
        print(f"\n== {step.desc}", flush=True)
        try:
            if step.cmd:
                subprocess.run(step.cmd, env=paths.subprocess_env(), check=True)
            if step.func:
                step.func()
        except subprocess.CalledProcessError as e:
            why = f"komut {e.returncode} koduyla bitti: {' '.join(map(str, e.cmd))}"
        except FileNotFoundError as e:
            why = f"komut bulunamadı: {e.filename}"
        except (OSError, RuntimeError) as e:
            why = str(e)
        else:
            continue
        print(f"Hata: '{step.desc}' adımı başarısız: {why}\n"
              "Sorunu giderip komutu yeniden çalıştırın; kurulu adımlar atlanır.", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------- komut

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: hata: {message}\n")


def _parser() -> argparse.ArgumentParser:
    p = _Parser(prog="ocr-now models", description="Modelleri listeler (★ = önerilen) ve kurar.")
    sub = p.add_subparsers(dest="komut")
    i = sub.add_parser("install", help="modelleri kurar (varsayılan: önerilenler)")
    i.add_argument("adlar", nargs="*", metavar="ad", help="kurulacak motorlar; verilmezse önerilenler")
    i.add_argument("--hepsi", action="store_true", help="bütün motorları kur")
    return p


def main(argv: list[str]) -> int:
    """`ocr-now models` alt komutu; argv, 'models' sözcüğünden sonrasıdır."""
    try:
        args = _parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0)

    if args.komut != "install":
        gpu = gpu_total_gb()
        print(f"GPU: {gpu:.1f} GB" if gpu is not None else "GPU: bulunamadı")
        print("\n".join(list_lines(gpu)))
        print("\n★ önerilen. Disk, vLLM motorlarının ortak ortamını (.venv-vllm, ~8 GB) içermez. "
              "GPU belleği tahmini asgaridir.")
        return 0

    unknown = [a for a in args.adlar if a not in engines.ENGINES]
    if unknown or (args.hepsi and args.adlar):
        why = f"bilinmeyen model: {', '.join(unknown)}" if unknown else "--hepsi ile model adı birlikte verilemez"
        print(f"Hata: {why}\nModeller: {', '.join(engines.ENGINES)}", file=sys.stderr)
        return 2
    names = (list(engines.ENGINES) if args.hepsi else args.adlar
             or [n for n, e in engines.ENGINES.items() if e.recommended])
    gpu = gpu_total_gb()
    for n in names:
        vram = engines.ENGINES[n].vram_gb
        if gpu is not None and vram is not None and vram > gpu:
            print(f"Uyarı: {n} tahminen {vram:.1f} GB GPU belleği ister, GPU'da {gpu:.1f} GB var; çalışmayabilir.")
    steps = install_steps(names)
    if not steps:
        print("Seçilen modellerin hepsi zaten kurulu.")
        return 0
    try:
        code = _run(steps)
    except KeyboardInterrupt:
        print("\nİptal edildi.", file=sys.stderr)
        return 130
    if code == 0:
        print("\nKurulum tamam. Durum için: ocr-now models")
    return code
