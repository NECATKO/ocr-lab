# OCR Lab Windows kurulum betiği (WSL2 üzerinden kurulum.sh çalıştırır).
# Kullanım: kurulum.bat'a çift tıklayın veya PowerShell'den:
#   .\kurulum.ps1 [--hepsi | --temel | --kontrol | --kisayol-yok | -Distro <ad> | -Hedef <wsl-yolu> | model ...]
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:WSL_UTF8 = "1"

function Show-Usage {
    @"
OCR Lab Windows Kurulumu (WSL2 + Ubuntu)

Kullanım:
  kurulum.bat [seçenekler] [model ...]
  .\kurulum.ps1 [seçenekler] [model ...]

Seçenekler:
  (varsayılan)      WSL2/Ubuntu hazırlar, gereksinimleri kurar. NVIDIA GPU varsa
                    önerilen motorları, yoksa temel kurulumu (tesseract + API) yapar.
  --hepsi           Tüm yerel OCR motorlarını kurar (~30+ GB disk).
  --temel           Yalnızca ana ortam + tesseract kurar (GPU modeli indirmez).
  --kontrol         Hiçbir şey kurmadan Windows + WSL2 durumunu kontrol eder.
  --kisayol-yok     Windows masaüstü kısayolu (ocr-now-windows) oluşturmaz.
  -Distro <ad>      Kullanılacak WSL dağıtımını seçer (ör. Ubuntu).
  -Hedef <yol>      WSL içindeki hedef klasör (varsayılan: ~/ocr-lab).
  -h, --help        Bu yardımı gösterir.
"@ | Write-Host
}

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "== $Text" -ForegroundColor Cyan
}

function Write-Warn([string]$Text) {
    Write-Host "Uyarı: $Text" -ForegroundColor Yellow
}

function Write-Err([string]$Text) {
    Write-Host "Hata: $Text" -ForegroundColor Red
}

function Clean-WslText([object[]]$Lines) {
    $out = @()
    foreach ($line in $Lines) {
        if ($null -eq $line) { continue }
        $clean = ([string]$line) -replace "`0", ""
        $clean = $clean.Trim()
        if ($clean.Length -gt 0) { $out += $clean }
    }
    return $out
}

function Get-WslDistros {
    $raw = & wsl.exe -l -v 2>$null
    if ($LASTEXITCODE -ne 0) {
        $raw = & wsl.exe -l -q 2>$null
        if ($LASTEXITCODE -ne 0) { return @() }
        $names = Clean-WslText $raw | Where-Object { $_ -notmatch '^docker-desktop' }
        return @($names | ForEach-Object { [pscustomobject]@{ Name = $_; Default = $false; Version = 2 } })
    }
    $lines = Clean-WslText $raw
    $result = @()
    foreach ($line in $lines) {
        $isDefault = $line.StartsWith("*")
        $body = $line.TrimStart("*").Trim()
        if ($body -match '^(.+?)\s+\S+\s+(\d+)$') {
            $name = $Matches[1].Trim()
            $ver = [int]$Matches[2]
            if ($name -match '^docker-desktop') { continue }
            $result += [pscustomobject]@{ Name = $name; Default = $isDefault; Version = $ver }
        }
    }
    return $result
}

function Select-Distro([object[]]$Distros, [string]$Requested) {
    if ($Requested) {
        $found = $Distros | Where-Object { $_.Name -ieq $Requested } | Select-Object -First 1
        if (-not $found) {
            Write-Err "'$Requested' adlı WSL dağıtımı bulunamadı. Kurulu dağıtımlar: $(($Distros | ForEach-Object { $_.Name }) -join ', ')"
            exit 1
        }
        return $found
    }
    $ubuntu = $Distros | Where-Object { $_.Name -match '^Ubuntu' -and $_.Version -eq 2 } | Select-Object -First 1
    if ($ubuntu) { return $ubuntu }
    $def = $Distros | Where-Object { $_.Default -and $_.Version -eq 2 } | Select-Object -First 1
    if ($def) { return $def }
    $anyV2 = $Distros | Where-Object { $_.Version -eq 2 } | Select-Object -First 1
    if ($anyV2) { return $anyV2 }
    return ($Distros | Select-Object -First 1)
}

function Check-WindowsGpu {
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($smi) {
        $out = & $smi.Source --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null | Select-Object -First 1
        if ($LASTEXITCODE -eq 0 -and $out -match '^(.+),\s*([0-9.]+)$') {
            $name = $Matches[1].Trim()
            $gb = [math]::Round(([double]$Matches[2]) / 1024.0, 1)
            Write-Host "Windows GPU : [HAZIR] $name ($gb GB)" -ForegroundColor Green
            return
        }
    }
    $nv = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'NVIDIA' } | Select-Object -First 1
    if ($nv) {
        Write-Warn "NVIDIA GPU algılandı ($($nv.Name)) ancak nvidia-smi çalışmıyor. WSL2 GPU desteği için güncel Windows NVIDIA sürücüsünü kurun."
    } else {
        Write-Host "Windows GPU : [YOK] NVIDIA GPU bulunamadı (tesseract ve harici API modu kullanılacak)."
    }
}

# ---- Argüman ayrıştırma (PowerShell'in -- bayraklarını yutmasını önler) ----
$RequestedDistro = ""
$CustomTarget = ""
$CheckOnly = $false
$ForwardArgs = New-Object System.Collections.Generic.List[string]

for ($i = 0; $i -lt $args.Count; $i++) {
    $a = [string]$args[$i]
    switch ($a) {
        "-h"      { Show-Usage; exit 0 }
        "--help"  { Show-Usage; exit 0 }
        "-?"      { Show-Usage; exit 0 }
        "-Distro" {
            if ($i + 1 -ge $args.Count) { Write-Err "-Distro için dağıtım adı gerekli"; exit 2 }
            $RequestedDistro = [string]$args[++$i]
        }
        "--distro" {
            if ($i + 1 -ge $args.Count) { Write-Err "--distro için dağıtım adı gerekli"; exit 2 }
            $RequestedDistro = [string]$args[++$i]
        }
        "-Hedef" {
            if ($i + 1 -ge $args.Count) { Write-Err "-Hedef için WSL yolu gerekli"; exit 2 }
            $CustomTarget = [string]$args[++$i]
        }
        "--hedef" {
            if ($i + 1 -ge $args.Count) { Write-Err "--hedef için WSL yolu gerekli"; exit 2 }
            $CustomTarget = [string]$args[++$i]
        }
        "--kontrol" {
            $CheckOnly = $true
            $ForwardArgs.Add($a)
        }
        default {
            $ForwardArgs.Add($a)
        }
    }
}

Write-Step "Windows ortamı kontrol ediliyor"
Check-WindowsGpu

$wslCmd = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wslCmd) {
    Write-Err "wsl.exe bulunamadı. Windows 10 (2004+) veya Windows 11 üzerinde WSL2 gereklidir."
    Write-Host "Kurulum rehberi: https://learn.microsoft.com/windows/wsl/install"
    exit 1
}

# ---- Proje konumu: zaten WSL içinde mi, Windows sürücüsünde mi? ----
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$InWslUnc = $false
$UncDistro = ""
$UncLinuxPath = ""
if ($ScriptDir -match '^\\\\wsl(?:\.localhost|\$)\\([^\\]+)\\(.*)$') {
    $InWslUnc = $true
    $UncDistro = $Matches[1]
    $UncLinuxPath = "/" + ($Matches[2] -replace '\\', '/')
    if (-not $RequestedDistro) { $RequestedDistro = $UncDistro }
}

$distros = @(Get-WslDistros)
if ($distros.Count -eq 0) {
    if ($CheckOnly) {
        Write-Host "WSL2        : [EKSİK] Kurulu Linux dağıtımı (Ubuntu) yok." -ForegroundColor Yellow
        Write-Host "Kurmak için : kurulum.bat"
        exit 1
    }
    Write-Step "WSL2 ve Ubuntu kuruluyor"
    Write-Host "Windows'ta OCR Lab, WSL2 (Ubuntu) içinde çalışır. Yönetici izni (UAC) istenebilir."
    try {
        $proc = Start-Process -FilePath "wsl.exe" -ArgumentList @("--install", "-d", "Ubuntu") -Verb RunAs -Wait -PassThru
        if ($proc.ExitCode -ne 0) {
            Write-Err "wsl --install komutu $($proc.ExitCode) koduyla bitti."
            exit $proc.ExitCode
        }
    } catch {
        Write-Err "WSL2 kurulumu başlatılamadı: $($_.Exception.Message)"
        Write-Host "Yönetici PowerShell'de şu komutu elle çalıştırabilirsiniz: wsl --install -d Ubuntu"
        exit 1
    }
    Write-Host ""
    Write-Host "WSL2 / Ubuntu kurulumu başlatıldı." -ForegroundColor Green
    Write-Host "1. Gerekirse bilgisayarınızı yeniden başlatın."
    Write-Host "2. Başlat menüsünden 'Ubuntu'yu açıp Linux kullanıcı adı ve şifrenizi oluşturun."
    Write-Host "3. Ardından bu kurulum.bat dosyasını tekrar çalıştırın."
    exit 0
}

$chosen = Select-Distro $distros $RequestedDistro
$DistroName = $chosen.Name
Write-Host "WSL2        : [HAZIR] $DistroName (WSL sürüm $($chosen.Version))" -ForegroundColor Green
if ($chosen.Version -ne 2) {
    Write-Warn "$DistroName dağıtımı WSL 1 sürümünde. NVIDIA GPU ve vLLM için WSL 2 gerekir."
    Write-Warn "Yükseltmek için: wsl --set-version $DistroName 2"
}

# ---- Dağıtım ilk kurulumunu (kullanıcı oluşturma) tamamlamış mı? ----
& wsl.exe -d $DistroName -- true 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Err "$DistroName başlatılamadı. İlk kurulum henüz tamamlanmamış olabilir."
    Write-Host "Başlat menüsünden '$DistroName' uygulamasını açıp kullanıcı adı/şifre belirleyin, sonra kurulum.bat'ı tekrar çalıştırın."
    exit 1
}

# ---- WSL içindeki çalışma klasörünü hazırla ----
$TargetLinuxPath = ""
if ($InWslUnc) {
    $TargetLinuxPath = $UncLinuxPath
} else {
    $winSlash = $ScriptDir -replace '\\', '/'
    $srcLinuxRaw = & wsl.exe -d $DistroName -- wslpath -a "$winSlash" 2>$null
    $srcLinux = (Clean-WslText $srcLinuxRaw | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $srcLinux) {
        Write-Err "Windows yolu WSL yoluna çevrilemedi: $ScriptDir"
        exit 1
    }

    if ($CheckOnly) {
        $TargetLinuxPath = $srcLinux
    } else {
        $homeRaw = & wsl.exe -d $DistroName -- bash -c 'printf "%s" "$HOME"'
        $wslHome = (Clean-WslText $homeRaw | Select-Object -First 1)
        if ($CustomTarget) {
            $TargetLinuxPath = $CustomTarget -replace '^~', $wslHome
        } else {
            $TargetLinuxPath = "$wslHome/ocr-lab"
        }

        Write-Step "Proje dosyaları WSL dosya sistemine aktarılıyor ($TargetLinuxPath)"
        Write-Host "(Windows diski /mnt/c üzerinde Python ortamları ve vLLM çok yavaş çalışır; kullanıcı taramaları/çıktıları korunur.)"
        $syncScript = @'
set -euo pipefail
src="$1"
dst="$2"
mkdir -p "$dst"
tar -C "$src" \
    --exclude='./.venv*' \
    --exclude='./output' \
    --exclude='./models' \
    --exclude='./tools' \
    --exclude='./input' \
    --exclude='./gt' \
    --exclude='./ayarlar/api.json' \
    --exclude='./.pytest_cache' \
    --exclude='./.ruff_cache' \
    --exclude='./__pycache__' \
    -cf - . | tar -C "$dst" -xf -
for f in kurulum.sh ocr-now ocr-lab ocr-gui; do
    if [ -f "$dst/$f" ]; then
        sed -i 's/\r$//' "$dst/$f"
        chmod +x "$dst/$f"
    fi
done
'@
        $syncScriptUnix = ($syncScript -replace "`r`n", "`n")
        $syncScriptUnix | & wsl.exe -d $DistroName -- bash -s -- "$srcLinux" "$TargetLinuxPath"
        if ($LASTEXITCODE -ne 0) {
            Write-Err "Dosyalar WSL klasörüne aktarılamadı."
            exit $LASTEXITCODE
        }
    }
}

# ---- WSL içinde kurulum.sh'ı çalıştır ----
Write-Step "WSL ($DistroName) içinde kurulum başlatılıyor"
$runArgs = @("-d", $DistroName, "--cd", $TargetLinuxPath, "--", "bash", "-c", 'grep -q "$(printf "\r")" kurulum.sh 2>/dev/null && sed -i "s/\r$//" kurulum.sh 2>/dev/null || true; exec bash ./kurulum.sh "$@"', "kurulum")
foreach ($fa in $ForwardArgs) {
    $runArgs += $fa
}
& wsl.exe @runArgs
exit $LASTEXITCODE
