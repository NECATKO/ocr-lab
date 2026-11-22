#!/usr/bin/env bash
# OCR Lab VPS izleyicisi kurulumu (Ubuntu/Debian). Tekrar çalıştırmak güvenlidir.
#
# Kurar: git/python3/rclone, uv ve Google'ın Colab komut satırı aracı (google-colab-cli),
# repo (~/ocr-lab) ve birkaç dakikada bir izleyiciyi çalıştıran systemd kullanıcı zamanlayıcısı.
# VPS'e model, vLLM ya da GPU yazılımı KURULMAZ; OCR Colab'ın GPU'sunda çalışır.
set -euo pipefail

REPO_URL=${OCR_VPS_REPO:-https://github.com/NECATKO/ocr-lab.git}
DIZIN=${OCR_VPS_DIZIN:-$HOME/ocr-lab}
ARALIK=${OCR_VPS_ARALIK:-3min}
UZAK=${OCR_VPS_UZAK:-gdrive:ocr-lab}

kok() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }

echo "== Sistem paketleri"
eksik=()
for p in git curl python3 unzip; do command -v "$p" >/dev/null || eksik+=("$p"); done
if [ ${#eksik[@]} -gt 0 ]; then
  kok apt-get update -qq
  kok apt-get install -y -qq "${eksik[@]}"
fi
command -v rclone >/dev/null || curl -fsSL https://rclone.org/install.sh | kok bash

echo "== uv ve Colab komut satırı aracı"
export PATH="$HOME/.local/bin:$PATH"
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install -q --with keyrings.alt google-colab-cli 2>/dev/null || uv tool upgrade -q google-colab-cli

echo "== Repo: $DIZIN"
if [ -d "$DIZIN/.git" ]; then
  git -C "$DIZIN" pull -q --ff-only
else
  git clone -q --depth 1 "$REPO_URL" "$DIZIN"
fi

echo "== systemd zamanlayıcısı (her $ARALIK)"
birim="$HOME/.config/systemd/user"
mkdir -p "$birim"
cat > "$birim/ocr-vps.service" <<EOF
[Unit]
Description=OCR Lab: Drive input/ klasörünü yokla, yeni dosyaları Colab GPU'da okut

[Service]
Type=oneshot
Environment=PATH=$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring
Environment=OCR_VPS_UZAK=$UZAK
ExecStartPre=-/usr/bin/git -C $DIZIN pull -q --ff-only
ExecStart=/usr/bin/python3 $DIZIN/vps/izleyici.py
TimeoutStartSec=5h
EOF
cat > "$birim/ocr-vps.timer" <<EOF
[Unit]
Description=OCR Lab izleyicisini düzenli çalıştır

[Timer]
OnBootSec=2min
OnUnitInactiveSec=$ARALIK

[Install]
WantedBy=timers.target
EOF
kok loginctl enable-linger "$(id -un)"
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
systemctl --user daemon-reload

hazir=1
if ! rclone lsf "$UZAK/" --max-depth 1 >/dev/null 2>&1; then
  hazir=0
  echo
  echo "!! rclone Drive bağlantısı yok. 'gdrive' adlı bir Google Drive uzağı kurun:"
  echo "   rclone config create gdrive drive scope=drive config_is_local=false"
fi
if ! PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring colab usage </dev/null >/dev/null 2>&1; then
  hazir=0
  echo
  echo "!! Colab girişi yok. Bir kez çalıştırın ve verilen bağlantıyı onaylayın:"
  echo "   PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring colab usage"
fi

if [ "$hazir" = 1 ]; then
  systemctl --user enable --now ocr-vps.timer
  echo
  echo "Kurulum tamam. İzleyici her $ARALIK çalışıyor."
  echo "  Durum : systemctl --user list-timers ocr-vps.timer"
  echo "  Kayıt : journalctl --user -u ocr-vps -n 100"
  echo "  Elle  : systemctl --user start ocr-vps"
else
  echo
  echo "Girişleri tamamladıktan sonra bu betiği yeniden çalıştırın; zamanlayıcı o zaman açılır."
fi
