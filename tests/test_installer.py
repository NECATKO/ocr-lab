"""Kurulum betikleri (kurulum.sh, kurulum.bat, kurulum.ps1) için testler.

Gerçek sistem paketlerini veya interneti kullanmadan, geçici bir PATH ve kopyalanmış kurulum.sh ile
tüm gereksinim denetimi ve otomatik kurulum yollarını doğrular.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

from ocrnow import paths

ROOT = Path(__file__).resolve().parents[1]


def _make_exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _setup_sandbox(tmp_path: Path, *, has_gpu: bool = True, has_uv: bool = True,
                   has_tesseract: bool = True, has_tur: bool = True,
                   has_curl: bool = True, pkg_mgr: str = "apt"):
    """İzole bir sahte proje kökü ve kontrollü PATH oluşturur."""
    proj = tmp_path / "proj"
    proj.mkdir()
    shutil.copy2(ROOT / "kurulum.sh", proj / "kurulum.sh")
    (proj / "kurulum.sh").chmod(0o755)
    log = tmp_path / "calls.log"

    # Sahte ocr-now ve ocr-gui
    _make_exe(proj / "ocr-now", f'echo "ocr-now $*" >> "{log}"\n')
    _make_exe(proj / "ocr-gui", f'echo "ocr-gui $*" >> "{log}"\n')
    _make_exe(proj / "ocr-lab", f'echo "ocr-lab $*" >> "{log}"\n')

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    # Temel POSIX araçlarını gerçek sistemden linkle (kontrollü liste — tesseract/uv/nvidia-smi/apt sızmasın)
    for tool in ("bash", "sh", "uname", "dirname", "pwd", "grep", "tr", "head", "awk", "df",
                 "id", "chmod", "mkdir", "cat", "tar", "env"):
        real = shutil.which(tool)
        if real:
            (bin_dir / tool).symlink_to(real)

    # Sahte sudo: doğrudan komutu çalıştır ve logla
    _make_exe(bin_dir / "sudo", f'echo "sudo $*" >> "{log}"\nexec "$@"\n')

    if has_curl:
        _make_exe(bin_dir / "curl", f'''
echo "curl $*" >> "{log}"
cat <<'EOS'
#!/usr/bin/env bash
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/uv" <<'EUV'
#!/usr/bin/env bash
echo "uv $*" >> "{log}"
if [[ "$1" == "--version" ]]; then echo "uv 0.9.0-test"; exit 0; fi
if [[ "$1" == "venv" ]]; then
  mkdir -p "$2/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$2/bin/python"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$2/bin/ocr-now"
  chmod +x "$2/bin/python" "$2/bin/ocr-now"
fi
EUV
chmod +x "$HOME/.local/bin/uv"
EOS
''')

    if has_uv:
        _make_exe(bin_dir / "uv", f'''
echo "uv $*" >> "{log}"
if [[ "$1" == "--version" ]]; then echo "uv 0.9.0-test"; exit 0; fi
if [[ "$1" == "venv" ]]; then
  mkdir -p "$2/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$2/bin/python"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$2/bin/ocr-now"
  chmod +x "$2/bin/python" "$2/bin/ocr-now"
fi
''')

    if has_tesseract:
        langs = "List of available languages:\neng\ntur\n" if has_tur else "List of available languages:\neng\n"
        _make_exe(bin_dir / "tesseract", f'''
if [[ "$1" == "--list-langs" ]]; then
  printf %b "{langs.replace(chr(10), r"\n")}"
  exit 0
fi
''')

    if has_gpu:
        _make_exe(bin_dir / "nvidia-smi", 'echo "NVIDIA GeForce RTX 4060, 8192"\n')

    # Paket yöneticisi: çağrıldığında sahte curl ve tesseract (+tur) kurar
    mgr_cmd = {"apt": "apt-get", "dnf": "dnf", "pacman": "pacman", "zypper": "zypper"}.get(pkg_mgr)
    if mgr_cmd:
        _make_exe(bin_dir / mgr_cmd, f'''
echo "{mgr_cmd} $*" >> "{log}"
if [[ "$*" == *"install"* || "$*" == *"-Sy"* ]]; then
  cat > "{bin_dir}/tesseract" <<'ET'
#!/usr/bin/env bash
if [[ "$1" == "--list-langs" ]]; then printf "eng\\ntur\\n"; fi
ET
  chmod +x "{bin_dir}/tesseract"
  if [[ "$*" == *"curl"* ]]; then
    cat > "{bin_dir}/curl" <<'EC'
#!/usr/bin/env bash
echo "curl $*" >> "{log}"
cat <<'EOS'
#!/usr/bin/env bash
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/uv" <<'EUV'
#!/usr/bin/env bash
echo "uv $*" >> "{log}"
if [[ "$1" == "--version" ]]; then echo "uv 0.9.0-test"; exit 0; fi
if [[ "$1" == "venv" ]]; then
  mkdir -p "$2/bin"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$2/bin/python"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$2/bin/ocr-now"
  chmod +x "$2/bin/python" "$2/bin/ocr-now"
fi
EUV
chmod +x "$HOME/.local/bin/uv"
EOS
EC
    chmod +x "{bin_dir}/curl"
  fi
fi
''')

    env = {
        "PATH": str(bin_dir),
        "HOME": str(home),
        "LANG": "C.UTF-8",
    }
    return proj, log, env


def _run(proj: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(proj / "kurulum.sh"), *args], env=env, capture_output=True, text=True)


def test_kurulum_sh_syntax_is_valid():
    res = subprocess.run(["bash", "-n", str(ROOT / "kurulum.sh")], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_help_flag(tmp_path):
    proj, _, env = _setup_sandbox(tmp_path)
    res = _run(proj, env, "--help")
    assert res.returncode == 0
    assert "--hepsi" in res.stdout and "--temel" in res.stdout and "--kontrol" in res.stdout


def test_check_mode_reports_missing_without_installing(tmp_path):
    proj, log, env = _setup_sandbox(tmp_path, has_gpu=False, has_uv=False, has_tesseract=False, has_curl=False)
    res = _run(proj, env, "--kontrol")
    assert res.returncode == 1
    assert "[EKSİK]" in res.stdout
    assert not log.exists()  # hiçbir şey kurmaya kalkışmadı


def test_install_automatically_installs_missing_system_pkgs_and_uv(tmp_path):
    proj, log, env = _setup_sandbox(tmp_path, has_gpu=True, has_uv=False, has_tesseract=False, has_curl=False)
    res = _run(proj, env, "--kisayol-yok")
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text(encoding="utf-8")
    assert "apt-get update -qq" in calls
    assert "apt-get install -y curl ca-certificates tesseract-ocr tesseract-ocr-tur" in calls
    assert "curl -LsSf https://astral.sh/uv/install.sh" in calls
    assert "uv venv" in calls
    assert "uv pip install -q -e .[dev,lab]" in calls
    assert "ocr-now models install\n" in calls  # GPU varken varsayılan: önerilenler


def test_install_detects_missing_turkish_tesseract_pack(tmp_path):
    proj, log, env = _setup_sandbox(tmp_path, has_gpu=False, has_uv=True, has_tesseract=True, has_tur=False)
    res = _run(proj, env, "--temel", "--kisayol-yok")
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text(encoding="utf-8")
    assert "apt-get install -y tesseract-ocr-tur" in calls
    assert "ocr-now models install" not in calls  # --temel GPU modeli indirmez


def test_default_without_gpu_skips_vlm_downloads(tmp_path):
    proj, log, env = _setup_sandbox(tmp_path, has_gpu=False, has_uv=True, has_tesseract=True, has_tur=True)
    res = _run(proj, env, "--kisayol-yok")
    assert res.returncode == 0, res.stdout + res.stderr
    calls = log.read_text(encoding="utf-8")
    assert "ocr-now models install" not in calls
    assert "ocr-now models\n" in calls
    assert "GPU bulunmadığı için yerel GPU modelleri atlandı" in res.stdout


def test_hepsi_and_specific_models_flags(tmp_path):
    proj, log, env = _setup_sandbox(tmp_path, has_gpu=True)
    res = _run(proj, env, "--hepsi", "--kisayol-yok")
    assert res.returncode == 0, res.stderr
    assert "ocr-now models install --hepsi" in log.read_text(encoding="utf-8")

    log.unlink()
    res2 = _run(proj, env, "dots.mocr", "glm-ocr", "--kisayol-yok")
    assert res2.returncode == 0, res2.stderr
    assert "ocr-now models install dots.mocr glm-ocr" in log.read_text(encoding="utf-8")


def test_conflicting_flags_are_rejected(tmp_path):
    proj, _, env = _setup_sandbox(tmp_path)
    assert _run(proj, env, "--hepsi", "--temel").returncode != 0
    assert _run(proj, env, "--hepsi", "dots.mocr").returncode != 0
    assert _run(proj, env, "--bilinmeyen-bayrak").returncode != 0


def test_dnf_package_manager_installs_fedora_packages(tmp_path):
    proj, log, env = _setup_sandbox(tmp_path, has_gpu=False, has_tesseract=False, pkg_mgr="dnf")
    res = _run(proj, env, "--temel", "--kisayol-yok")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "dnf install -y tesseract tesseract-langpack-tur" in log.read_text(encoding="utf-8")


def test_wsl_and_linux_desktop_shortcuts(tmp_path):
    # WSL'de --masaustu-kisayolu çağrılır
    proj, log, env = _setup_sandbox(tmp_path, has_gpu=False)
    wsl_env = {**env, "WSL_DISTRO_NAME": "Ubuntu"}
    res = _run(proj, wsl_env, "--temel")
    assert res.returncode == 0, res.stderr
    assert "ocr-gui --masaustu-kisayolu" in log.read_text(encoding="utf-8")

    # Saf Linux masaüstünde .desktop dosyası oluşturulur (bu ortam WSL olduğundan /proc/version'da WSL yoksa)
    if not (Path("/proc/version").exists() and "microsoft" in Path("/proc/version").read_text(errors="ignore").lower()):
        proj2, _, env2 = _setup_sandbox(tmp_path / "lin", has_gpu=False)
        lin_env = {**env2, "DISPLAY": ":0"}
        res2 = _run(proj2, lin_env, "--temel")
        assert res2.returncode == 0, res2.stderr
        desktop = Path(lin_env["HOME"]) / ".local" / "share" / "applications" / "ocr-now.desktop"
        assert desktop.is_file() and f'Exec="{proj2}/ocr-gui" --tarayici' in desktop.read_text(encoding="utf-8")


def test_windows_installer_files_exist_with_proper_encoding_and_eol():
    bat = (ROOT / "kurulum.bat").read_bytes()
    ps1 = (ROOT / "kurulum.ps1").read_bytes()
    sh = (ROOT / "kurulum.sh").read_bytes()

    assert b"\r\n" in bat and b"kurulum.ps1" in bat
    assert ps1.startswith(b"\xef\xbb\xbf")      # PowerShell 5.1 Türkçe karakterler için UTF-8 BOM şart
    assert b"\r\n" in ps1
    assert b"\r\n" not in sh                    # Linux/WSL'de CRLF /bin/sh^M hatası verir
    assert os.access(ROOT / "kurulum.sh", os.X_OK)
    assert (ROOT / ".gitattributes").is_file()
