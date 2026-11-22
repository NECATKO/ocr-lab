"""Google Colab + Google Drive çalışma yardımcıları.

Notebook (`colab/ocr_now.ipynb`) yalnızca form alanlarını taşır; asıl mantık buradadır.
Yerelde düzenleyip `git push` yaptığınızda Colab Hücre 2 `git pull` ile otomatik günceller.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
DEFAULT_LOCAL_ROOT = Path("/content/ocr-lab")
DEFAULT_LOCAL_HF = Path("/content/hf_cache")


def drive_paths(drive_root: str | Path) -> dict[str, Path]:
    root = Path(drive_root)
    return {
        "root": root,
        "input": root / "input",
        "output": root / "output",
        "cache": root / "cache",
        "models": root / "colab-models",
        "kesme": root / "kesme.txt",
        "hf_tar": root / "colab-models" / "hf_cache.tar",
    }


def list_inputs(input_dir: Path) -> list[Path]:
    if not input_dir.is_dir():
        return []
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and not p.name.startswith(".")
        and (p.suffix.lower() == ".pdf" or p.suffix.lower() in IMAGE_EXTS)
    )


def prepare_drive(drive_root: str | Path) -> dict[str, Path]:
    """Drive klasör yapısını oluşturur ve `input/` içindeki bekleyen dosyaları listeler."""
    dp = drive_paths(drive_root)
    for key in ("input", "output", "cache", "models"):
        dp[key].mkdir(parents=True, exist_ok=True)
    files = list_inputs(dp["input"])
    print(f"Drive klasörü hazır: {dp['root']}")
    print(f"Girdi klasörü (input/): {len(files)} dosya")
    for f in files:
        print(f"  - {f.name}")
    if not files:
        print("\nPDF veya görsellerinizi Google Drive'da 'ocr-lab/input/' klasörüne yükleyin.")
    return dp


def unique_dest(folder: Path, name: str) -> Path:
    dest = folder / name
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    i = 2
    while (folder / f"{stem}_{i}{suffix}").exists():
        i += 1
    return folder / f"{stem}_{i}{suffix}"


def _has_tesseract_tur() -> bool:
    if not shutil.which("tesseract"):
        return False
    res = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True)
    return "tur" in res.stdout.splitlines()


def setup_env(
    drive_root: str | Path,
    *,
    model: str = "dots.mocr",
    local_root: str | Path = DEFAULT_LOCAL_ROOT,
    local_hf: str | Path = DEFAULT_LOCAL_HF,
) -> None:
    """Colab ortamını (tesseract, uv, .venv, model ağırlıkları, Drive yedeği) hazırlar."""
    dp = drive_paths(drive_root)
    root = Path(local_root)
    hf_dir = Path(local_hf)

    # GPU denetimi
    gpu_res = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
        capture_output=True, text=True,
    )
    if gpu_res.returncode != 0 or not gpu_res.stdout.strip():
        print("UYARI: GPU bulunamadı! Yalnızca tesseract veya önbellekten dışa aktarma çalışır.")
    else:
        gpu_info = gpu_res.stdout.strip().splitlines()[0]
        print(f"GPU: {gpu_info}")
        if "T4" in gpu_info and model in ("dots.mocr", "deepseek-ocr2", "onerilenler"):
            print("UYARI: T4 GPU fp8 desteklemez! dots.mocr için Çalışma Zamanı -> L4 veya A100 seçin, "
                  "ya da glm-ocr/lightonocr kullanın.")

    # Sistem paketleri
    if not _has_tesseract_tur() or not shutil.which("rsync"):
        print("\n== tesseract-ocr-tur ve rsync kuruluyor...")
        subprocess.run(["apt-get", "update", "-qq"], check=True)
        subprocess.run(["apt-get", "install", "-y", "-qq", "tesseract-ocr", "tesseract-ocr-tur", "rsync"], check=True)

    # uv
    uv_bin = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
    if not Path(uv_bin).exists():
        print("\n== uv kuruluyor...")
        subprocess.run("curl -LsSf https://astral.sh/uv/install.sh | sh", shell=True, check=True)
    local_bin = str(Path.home() / ".local/bin")
    if local_bin not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = f"{local_bin}:{os.environ.get('PATH', '')}"

    # Ana Python ortamı (.venv)
    if not (root / ".venv/bin/ocr-now").exists():
        print("\n== Ana ortam (.venv) kuruluyor...")
        subprocess.run(["uv", "venv", str(root / ".venv"), "--python", "3.12", "-q"], check=True)
        subprocess.run(
            ["uv", "pip", "install", "-q", "--python", str(root / ".venv/bin/python"), "-e", str(root)],
            check=True,
        )

    # Hugging Face önbelleği: Drive'daki .tar arşivinden yerel SSD'ye aç (symlink'leri korur)
    hf_dir.mkdir(parents=True, exist_ok=True)
    os.environ["HF_HOME"] = str(hf_dir)
    hub_dir = hf_dir / "hub"

    had_local_hf = hub_dir.is_dir() and any(hub_dir.glob("models--*"))
    if not had_local_hf and dp["hf_tar"].is_file():
        size_gb = dp["hf_tar"].stat().st_size / 1e9
        print(f"\n== Model ağırlıkları Drive'dan yerel SSD'ye açılıyor ({size_gb:.1f} GB)...")
        subprocess.run(["tar", "-xf", str(dp["hf_tar"]), "-C", str(hf_dir)], check=True)

    before_models = {p.name for p in hub_dir.glob("models--*")} if hub_dir.is_dir() else set()

    # Seçilen model + vLLM kurulumu
    if model != "tesseract":
        install_cmd = [str(root / "ocr-now"), "models", "install"]
        if model != "onerilenler":
            install_cmd.append(model)
        print(f"\n== Model kuruluyor: {' '.join(install_cmd)}")
        subprocess.run(install_cmd, cwd=str(root), check=True)

    # Yeni model indirildiyse Drive arşivini güncelle
    after_models = {p.name for p in hub_dir.glob("models--*")} if hub_dir.is_dir() else set()
    if after_models and (after_models != before_models or not dp["hf_tar"].is_file()):
        print("\n== Model ağırlıkları sonraki oturumlar için Drive'a yedekleniyor...")
        tmp_tar = Path("/content/hf_cache.tar")
        subprocess.run(["tar", "-cf", str(tmp_tar), "-C", str(hf_dir), "hub"], check=True)
        shutil.move(str(tmp_tar), str(dp["hf_tar"]))
        print(f"Yedeklendi: {dp['hf_tar']} ({dp['hf_tar'].stat().st_size / 1e9:.1f} GB)")

    # kesme.txt eşitleme
    local_kesme = root / "ayarlar/kesme.txt"
    if dp["kesme"].is_file():
        shutil.copy2(dp["kesme"], local_kesme)
        print(f"Drive'daki kesme.txt yüklendi: {dp['kesme']}")
    elif local_kesme.is_file():
        shutil.copy2(local_kesme, dp["kesme"])
        print(f"Varsayılan kesme.txt Drive'a kopyalandı: {dp['kesme']}")

    print("\nKurulum tamam!")
    subprocess.run([str(root / "ocr-now"), "models"], cwd=str(root), check=True)


def run_ocr(
    drive_root: str | Path,
    *,
    dosya: str = "HEPSI",
    sayfalar: str = "hepsi",
    profil: str = "kitap",
    model: str = "dots.mocr",
    okuma: str = "duzen",
    formatlar: str = "docx txt",
    ilk_sayfa: int = 0,
    isaretle: bool = True,
    kesme: bool = True,
    dpi: int = 200,
    islenenleri_tasi: bool = False,
    local_root: str | Path = DEFAULT_LOCAL_ROOT,
    local_hf: str | Path = DEFAULT_LOCAL_HF,
) -> int:
    """Drive'daki girdileri yerel SSD'de işler, çıktı ve önbelleği Drive'a geri yazar."""
    dp = drive_paths(drive_root)
    root = Path(local_root)
    local_cache = root / "output/_cache"
    local_docs = root / "output/belgeler"
    os.environ["HF_HOME"] = str(local_hf)

    # 1) Güncel kesme.txt ve OCR önbelleğini Drive'dan al
    if dp["kesme"].is_file():
        shutil.copy2(dp["kesme"], root / "ayarlar/kesme.txt")

    local_cache.mkdir(parents=True, exist_ok=True)
    if dp["cache"].is_dir() and any(dp["cache"].iterdir()):
        subprocess.run(["rsync", "-a", f"{dp['cache']}/", f"{local_cache}/"], check=True)

    # 2) Seçili girdileri yerel SSD'ye kopyala
    if dosya.strip().upper() == "HEPSI":
        hedefler = list_inputs(dp["input"])
    else:
        p = dp["input"] / dosya.strip()
        if not p.exists():
            raise FileNotFoundError(f"Drive'da bulunamadı: {p}")
        hedefler = [p]

    if not hedefler:
        raise RuntimeError(f"İşlenecek dosya yok: {dp['input']}")

    work_in = Path("/content/work_input")
    if work_in.exists():
        shutil.rmtree(work_in)
    work_in.mkdir(parents=True)
    for h in hedefler:
        shutil.copy2(h, work_in / h.name)

    if local_docs.exists():
        shutil.rmtree(local_docs)
    local_docs.mkdir(parents=True)

    # 3) ocr-now çalıştır (canlı çıktı akışıyla)
    cmd = [
        str(root / "ocr-now"),
        "--profil", profil,
        "-m", model,
        "-p", sayfalar.strip() or "hepsi",
        "-f", *formatlar.split(),
        "--dpi", str(dpi),
        "--isaretle" if isaretle else "--isaretleme",
        "--kesme" if kesme else "--kesme-yok",
    ]
    if model == "dots.mocr":
        cmd += ["-o", okuma]
    if ilk_sayfa and ilk_sayfa > 0:
        cmd += ["--ilk-sayfa", str(ilk_sayfa)]
    cmd += ["--", *[str(work_in / h.name) for h in hedefler]]

    print("Komut:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(
        cmd, cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
    rc = proc.wait()

    # 4) Yeni OCR önbelleğini Drive'a geri yaz
    subprocess.run(["rsync", "-a", f"{local_cache}/", f"{dp['cache']}/"], check=True)

    # 5) Üretilen belgeleri Drive'a kopyala (üzerine yazmaz)
    yazilanlar = []
    for out_file in sorted(local_docs.iterdir()):
        if out_file.is_file():
            hedef = unique_dest(dp["output"], out_file.name)
            shutil.copy2(out_file, hedef)
            yazilanlar.append(hedef)

    print(f"\n== Drive'a kaydedilen belgeler ({len(yazilanlar)}):")
    for y in yazilanlar:
        print(f"  - {y}")

    if islenenleri_tasi and rc == 0:
        done_dir = dp["input"] / "islenenler"
        done_dir.mkdir(exist_ok=True)
        for h in hedefler:
            shutil.move(str(h), str(unique_dest(done_dir, h.name)))
        print(f"İşlenen {len(hedefler)} dosya '{done_dir}' klasörüne taşındı.")

    if rc != 0:
        print(f"\nUYARI: ocr-now çıkış kodu {rc} (yukarıdaki uyarıları kontrol edin; okunan sayfalar kaydedildi).")
    return rc
