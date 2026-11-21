#!/usr/bin/env bash
# OCR Lab (ocr-now + ocr-gui + ocr-lab) kurulum betiği — Linux ve Windows (WSL2).
# Gereksinimleri (curl, tar, tesseract + Türkçe dil paketi, uv, Python 3.12, GPU, disk) kontrol eder,
# eksikleri kurar ve OCR motorlarını hazırlar. Tekrar çalıştırmak güvenlidir; kurulu adımlar atlanır.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$ROOT"

MODE="default"      # default | all | minimal | check | models
SKIP_SHORTCUT=0
SELECTED_MODELS=()
IS_WSL=0
ARCH=""
GPU_NAME=""
GPU_MIB=0
GPU_GB=""
UV_INSTALLED_NOW=0

usage() {
    cat <<'EOF'
Kullanım: ./kurulum.sh [seçenekler] [model ...]

Seçenekler:
  (varsayılan)     Gereksinimleri ve ana ortamı kurar. NVIDIA GPU varsa önerilen
                   motorları (dots.mocr, glm-ocr, lightonocr, tesseract), GPU
                   yoksa yalnızca tesseract + harici API hazır kurulumu yapar.
  --hepsi          Tüm OCR motorlarını kurar (ocr-lab benchmark motorları dahil).
  --temel          Yalnızca ana ortam + tesseract kurar (GPU modeli indirmez;
                   --gpu-yok / --sadece-api ile aynıdır).
  --kontrol        Hiçbir şey kurmadan sistem gereksinimlerini ve durumu denetler.
  --kisayol-yok    Masaüstü / uygulama menüsü kısayolu oluşturmaz.
  -h, --help       Bu yardımı gösterir.

Örnekler:
  ./kurulum.sh                    # günlük kullanım için önerilen kurulum
  ./kurulum.sh --hepsi            # tüm yerel motorlar (~30+ GB disk)
  ./kurulum.sh --temel            # GPU'suz / yalnızca harici API veya tesseract
  ./kurulum.sh dots.mocr          # yalnızca belirli motor(lar)
  ./kurulum.sh --kontrol          # kurulum yapmadan sistem kontrolü
EOF
}

info() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
warn() { printf 'Uyarı: %s\n' "$*" >&2; }
die()  { printf 'Hata: %s\n' "$*" >&2; exit 1; }

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                usage
                exit 0
                ;;
            --hepsi)
                [[ "$MODE" == "default" || "$MODE" == "all" ]] || die "--hepsi diğer kip seçenekleriyle birlikte verilemez"
                MODE="all"
                ;;
            --temel|--gpu-yok|--sadece-api)
                [[ "$MODE" == "default" || "$MODE" == "minimal" ]] || die "$1 diğer kip seçenekleriyle birlikte verilemez"
                MODE="minimal"
                ;;
            --kontrol)
                [[ "$MODE" == "default" || "$MODE" == "check" ]] || die "--kontrol diğer kip seçenekleriyle birlikte verilemez"
                MODE="check"
                ;;
            --kisayol-yok)
                SKIP_SHORTCUT=1
                ;;
            --)
                shift
                while [[ $# -gt 0 ]]; do
                    SELECTED_MODELS+=("$1")
                    shift
                done
                break
                ;;
            -*)
                die "bilinmeyen seçenek: $1 (yardım için: ./kurulum.sh --help)"
                ;;
            *)
                SELECTED_MODELS+=("$1")
                ;;
        esac
        shift
    done

    if [[ ${#SELECTED_MODELS[@]} -gt 0 ]]; then
        [[ "$MODE" == "default" ]] || die "model adları --hepsi, --temel veya --kontrol ile birlikte verilemez"
        MODE="models"
    fi
}

check_platform() {
    local os
    os="$(uname -s 2>/dev/null || echo unknown)"
    if [[ "$os" != "Linux" ]]; then
        die "Bu betik Linux ve Windows (WSL2) içindir (algılanan: $os). Windows'ta kurulum.bat dosyasını çalıştırın."
    fi
    ARCH="$(uname -m 2>/dev/null || echo unknown)"
    if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qiE 'microsoft|wsl' /proc/version 2>/dev/null; then
        IS_WSL=1
    fi
}

add_user_bins_to_path() {
    local dir
    for dir in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        if [[ -d "$dir" ]] && [[ ":$PATH:" != *":$dir:"* ]]; then
            PATH="$dir:$PATH"
        fi
    done
    export PATH
}

has_tesseract_tur() {
    command -v tesseract >/dev/null 2>&1 || return 1
    tesseract --list-langs 2>/dev/null | tr -d '\r' | grep -qx 'tur'
}

detect_gpu() {
    GPU_NAME=""
    GPU_MIB=0
    GPU_GB=""
    if ! command -v nvidia-smi >/dev/null 2>&1; then
        return 0
    fi
    local line name mib
    line="$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -n 1 || true)"
    [[ -n "$line" ]] || return 0
    name="${line%,*}"
    mib="${line##*,}"
    mib="${mib//[[:space:]]/}"
    if [[ "$mib" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
        GPU_NAME="${name#"${name%%[![:space:]]*}"}"
        GPU_NAME="${GPU_NAME%"${GPU_NAME##*[![:space:]]}"}"
        GPU_MIB="${mib%%.*}"
        GPU_GB="$(awk -v m="$mib" 'BEGIN { printf "%.1f", m / 1024.0 }')"
    fi
}

hf_cache_dir() {
    if [[ -n "${HF_HUB_CACHE:-}" ]]; then
        printf '%s' "$HF_HUB_CACHE"
    elif [[ -n "${HUGGINGFACE_HUB_CACHE:-}" ]]; then
        printf '%s' "$HUGGINGFACE_HUB_CACHE"
    elif [[ -n "${HF_HOME:-}" ]]; then
        printf '%s/hub' "$HF_HOME"
    else
        printf '%s/huggingface/hub' "${XDG_CACHE_HOME:-$HOME/.cache}"
    fi
}

free_gb_for() {
    local target="$1"
    while [[ ! -e "$target" && "$target" != "/" ]]; do
        target="$(dirname "$target")"
    done
    df -Pk "$target" 2>/dev/null | awk 'NR==2 { printf "%.1f", $4 / 1048576.0 }'
}

check_disk_space() {
    local need_gb="$1"
    local root_free hf_dir hf_free
    root_free="$(free_gb_for "$ROOT")"
    hf_dir="$(hf_cache_dir)"
    hf_free="$(free_gb_for "$hf_dir")"
    if [[ -n "$root_free" ]] && awk -v f="$root_free" -v n="$need_gb" 'BEGIN { exit !(f < n) }'; then
        warn "Proje diskinde boş alan düşük (${root_free} GB; tahmini ihtiyaç ~${need_gb} GB)."
    fi
    if [[ -n "$hf_free" && "$hf_free" != "$root_free" ]] && awk -v f="$hf_free" -v n="$need_gb" 'BEGIN { exit !(f < n) }'; then
        warn "Hugging Face önbellek diskinde ($hf_dir) boş alan düşük (${hf_free} GB; tahmini ihtiyaç ~${need_gb} GB)."
    fi
}

run_root() {
    if [[ "$(id -u)" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        die "Yönetici yetkisi gerekiyor ancak 'sudo' bulunamadı. Şu komutu root olarak çalıştırın: $*"
    fi
}

detect_pkg_mgr() {
    if command -v apt-get >/dev/null 2>&1; then
        echo "apt"
    elif command -v dnf >/dev/null 2>&1; then
        echo "dnf"
    elif command -v pacman >/dev/null 2>&1; then
        echo "pacman"
    elif command -v zypper >/dev/null 2>&1; then
        echo "zypper"
    else
        echo ""
    fi
}

install_system_reqs() {
    local need_curl=0 need_tar=0 need_tess=0 need_tur=0
    command -v curl >/dev/null 2>&1 || need_curl=1
    command -v tar >/dev/null 2>&1 || need_tar=1
    command -v tesseract >/dev/null 2>&1 || need_tess=1
    has_tesseract_tur || need_tur=1

    if [[ $need_curl -eq 0 && $need_tar -eq 0 && $need_tess -eq 0 && $need_tur -eq 0 ]]; then
        info "Sistem paketleri hazır (curl, tar, tesseract + Türkçe dil paketi)."
        return 0
    fi

    local mgr pkgs=()
    mgr="$(detect_pkg_mgr)"
    case "$mgr" in
        apt)
            [[ $need_curl -eq 1 ]] && pkgs+=(curl ca-certificates)
            [[ $need_tar -eq 1 ]] && pkgs+=(tar)
            [[ $need_tess -eq 1 ]] && pkgs+=(tesseract-ocr)
            [[ $need_tur -eq 1 ]] && pkgs+=(tesseract-ocr-tur)
            step "Sistem gereksinimleri kuruluyor (apt: ${pkgs[*]})"
            run_root apt-get update -qq
            run_root apt-get install -y "${pkgs[@]}"
            ;;
        dnf)
            [[ $need_curl -eq 1 ]] && pkgs+=(curl ca-certificates)
            [[ $need_tar -eq 1 ]] && pkgs+=(tar)
            [[ $need_tess -eq 1 ]] && pkgs+=(tesseract)
            [[ $need_tur -eq 1 ]] && pkgs+=(tesseract-langpack-tur)
            step "Sistem gereksinimleri kuruluyor (dnf: ${pkgs[*]})"
            run_root dnf install -y "${pkgs[@]}"
            ;;
        pacman)
            [[ $need_curl -eq 1 ]] && pkgs+=(curl ca-certificates)
            [[ $need_tar -eq 1 ]] && pkgs+=(tar)
            [[ $need_tess -eq 1 ]] && pkgs+=(tesseract)
            [[ $need_tur -eq 1 ]] && pkgs+=(tesseract-data-tur)
            step "Sistem gereksinimleri kuruluyor (pacman: ${pkgs[*]})"
            run_root pacman -Sy --noconfirm --needed "${pkgs[@]}"
            ;;
        zypper)
            [[ $need_curl -eq 1 ]] && pkgs+=(curl ca-certificates)
            [[ $need_tar -eq 1 ]] && pkgs+=(tar)
            [[ $need_tess -eq 1 ]] && pkgs+=(tesseract-ocr)
            [[ $need_tur -eq 1 ]] && pkgs+=(tesseract-ocr-traineddata-turkish)
            step "Sistem gereksinimleri kuruluyor (zypper: ${pkgs[*]})"
            run_root zypper --non-interactive install "${pkgs[@]}"
            ;;
        *)
            die "Desteklenen paket yöneticisi (apt/dnf/pacman/zypper) bulunamadı. Lütfen curl, tar, tesseract ve Türkçe dil paketini (tur) elle kurup ./kurulum.sh'ı tekrar çalıştırın."
            ;;
    esac

    command -v tesseract >/dev/null 2>&1 || die "tesseract kurulumundan sonra komut bulunamadı"
    has_tesseract_tur || warn "tesseract Türkçe dil paketi (tur) doğrulanamadı; gerekirse tesseract-ocr-tur paketini kontrol edin."
}

ensure_uv() {
    add_user_bins_to_path
    if command -v uv >/dev/null 2>&1; then
        info "uv hazır: $(uv --version)"
        return 0
    fi
    command -v curl >/dev/null 2>&1 || die "uv kurulumu için curl gerekiyor"
    step "uv kuruluyor"
    local url="${OCR_UV_INSTALL_URL:-https://astral.sh/uv/install.sh}"
    curl -LsSf "$url" | sh
    UV_INSTALLED_NOW=1
    add_user_bins_to_path
    command -v uv >/dev/null 2>&1 || die "uv kuruldu ancak PATH içinde bulunamadı ($HOME/.local/bin)"
    info "uv kuruldu: $(uv --version)"
}

install_linux_desktop_entry() {
    [[ $IS_WSL -eq 0 ]] || return 0
    [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] || return 0
    local apps_dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$apps_dir"
    local desktop_file="$apps_dir/ocr-now.desktop"
    cat >"$desktop_file" <<EOF
[Desktop Entry]
Type=Application
Name=OCR-Now
Comment=Taranmış sayfaları seçilebilir metne ve Word belgesine çevirir
Exec="$ROOT/ocr-gui" --tarayici
Path=$ROOT
Terminal=false
Categories=Office;Utility;
EOF
    info "Uygulama menüsü kısayolu oluşturuldu: $desktop_file"
}

run_check() {
    local missing=0
    step "Sistem kontrolü"
    if [[ $IS_WSL -eq 1 ]]; then
        info "  Platform       : Windows WSL2 (${WSL_DISTRO_NAME:-bilinmiyor}, $ARCH)"
    else
        info "  Platform       : Linux ($ARCH)"
    fi

    if [[ $GPU_MIB -gt 0 ]]; then
        info "  GPU            : [HAZIR] $GPU_NAME (${GPU_GB} GB)"
    else
        if [[ $IS_WSL -eq 1 ]]; then
            info "  GPU            : [YOK]   nvidia-smi bulunamadı (Windows'ta NVIDIA sürücüsü gerekir; GPU yoksa API/tesseract kullanılabilir)"
        else
            info "  GPU            : [YOK]   nvidia-smi bulunamadı (GPU yoksa API/tesseract kullanılabilir)"
        fi
    fi

    local root_free hf_free
    root_free="$(free_gb_for "$ROOT")"
    hf_free="$(free_gb_for "$(hf_cache_dir)")"
    info "  Boş disk       : proje ${root_free:-?} GB | HF önbelleği ${hf_free:-?} GB"

    if command -v curl >/dev/null 2>&1; then
        info "  curl           : [HAZIR]"
    else
        info "  curl           : [EKSİK]"
        missing=1
    fi

    if command -v tar >/dev/null 2>&1; then
        info "  tar            : [HAZIR]"
    else
        info "  tar            : [EKSİK]"
        missing=1
    fi

    if command -v tesseract >/dev/null 2>&1; then
        if has_tesseract_tur; then
            info "  tesseract      : [HAZIR] (Türkçe 'tur' dil paketi mevcut)"
        else
            info "  tesseract      : [EKSİK] tesseract var ama Türkçe 'tur' dil paketi eksik"
            missing=1
        fi
    else
        info "  tesseract      : [EKSİK]"
        missing=1
    fi

    if command -v uv >/dev/null 2>&1; then
        info "  uv             : [HAZIR] ($(uv --version 2>/dev/null || echo kurulu))"
    else
        info "  uv             : [EKSİK]"
        missing=1
    fi

    if [[ -x "$ROOT/.venv/bin/ocr-now" ]]; then
        info "  ana ortam      : [HAZIR] (.venv)"
        step "Motor durumları"
        "$ROOT/ocr-now" models || true
    else
        info "  ana ortam      : [EKSİK] (.venv henüz kurulmadı)"
        missing=1
    fi

    if [[ $missing -eq 0 ]]; then
        info ""
        info "Temel gereksinimler hazır."
        return 0
    fi
    info ""
    info "Eksikleri otomatik kurmak için: ./kurulum.sh"
    return 1
}

main() {
    parse_args "$@"
    check_platform
    add_user_bins_to_path
    detect_gpu

    if [[ "$MODE" == "check" ]]; then
        run_check
        exit $?
    fi

    step "Ortam özeti"
    if [[ $IS_WSL -eq 1 ]]; then
        info "Platform: Windows WSL2 (${WSL_DISTRO_NAME:-Linux}, $ARCH)"
    else
        info "Platform: Linux ($ARCH)"
    fi
    if [[ $GPU_MIB -gt 0 ]]; then
        info "GPU     : $GPU_NAME (${GPU_GB} GB)"
    else
        if [[ $IS_WSL -eq 1 ]]; then
            warn "NVIDIA GPU algılanmadı. NVIDIA ekran kartınız varsa Windows tarafında güncel NVIDIA sürücüsünü kurun (WSL içine ayrı Linux sürücüsü kurulmaz)."
        else
            warn "NVIDIA GPU algılanmadı (nvidia-smi yok veya çalışmıyor)."
        fi
    fi

    if [[ "$ARCH" != "x86_64" && "$MODE" == "all" ]]; then
        warn "Mimari $ARCH; paddleocr-vl için llama.cpp paketi x86_64 gerektirir."
    fi

    case "$MODE" in
        all)     check_disk_space 35 ;;
        minimal) check_disk_space 2 ;;
        models)  check_disk_space 12 ;;
        default)
            if [[ $GPU_MIB -gt 0 ]]; then
                check_disk_space 20
            else
                check_disk_space 2
            fi
            ;;
    esac

    step "Sistem gereksinimleri kontrol ediliyor"
    install_system_reqs
    ensure_uv

    # Git/ZIP sonrası çalıştırma izinlerini garantiye al
    chmod +x "$ROOT/kurulum.sh" "$ROOT/ocr-now" "$ROOT/ocr-lab" "$ROOT/ocr-gui" 2>/dev/null || true

    step "Ana ortam (.venv)"
    if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
        uv venv "$ROOT/.venv" --python 3.12 -q
    fi
    VIRTUAL_ENV="$ROOT/.venv" uv pip install -q -e ".[dev,lab]"

    step "OCR motorları"
    case "$MODE" in
        all)
            "$ROOT/ocr-now" models install --hepsi
            ;;
        minimal)
            info "Temel kip: yerel GPU modelleri indirilmedi (tesseract ve harici API kullanıma hazır)."
            ;;
        models)
            "$ROOT/ocr-now" models install "${SELECTED_MODELS[@]}"
            ;;
        default)
            if [[ $GPU_MIB -gt 0 ]]; then
                "$ROOT/ocr-now" models install
            else
                info "NVIDIA GPU bulunmadığı için yerel GPU modelleri atlandı; tesseract ve harici API kullanıma hazır."
                info "GPU eklediğinizde önerilen modelleri kurmak için: ./ocr-now models install"
            fi
            ;;
    esac

    step "Kurulu motorlar"
    "$ROOT/ocr-now" models

    if [[ $SKIP_SHORTCUT -eq 0 ]]; then
        if [[ $IS_WSL -eq 1 && -n "${WSL_DISTRO_NAME:-}" ]]; then
            step "Masaüstü kısayolu"
            "$ROOT/ocr-gui" --masaustu-kisayolu || true
        else
            install_linux_desktop_entry || true
        fi
    fi

    step "Kurulum tamamlandı"
    info "Tarayıcı arayüzü : ./ocr-gui --tarayici"
    info "Komut satırı     : ./ocr-now"
    if [[ $IS_WSL -eq 1 ]]; then
        info "Windows masaüstü : ocr-now-windows\\OCR-Now kısayoluna çift tıklayabilirsiniz."
    fi
    if [[ $GPU_MIB -eq 0 ]]; then
        info "Harici API için  : ./ocr-gui --tarayici → Gelişmiş ayarlar → Harici API"
    fi
    if [[ $UV_INSTALLED_NOW -eq 1 ]]; then
        info "Not: Yeni terminalde 'uv' komutunu doğrudan kullanmak için kabuğu yeniden açın (~/.local/bin)."
    fi
}

main "$@"
