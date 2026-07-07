$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Get-Item $ScriptDir).Parent.FullName
Set-Location $RootDir

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " ForgeX - CAI DAT TU DONG BANG POWERSHELL + UV" -ForegroundColor Cyan
Write-Host " Toc do cai dat x10-x100 lan so voi pip" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
Write-Host "[1/6] Kiem tra Python..." -ForegroundColor Yellow
try {
    $pyVerRaw = python --version 2>&1
    $pyVer = [string]$pyVerRaw
    if (-not ($pyVer -match "Python 3\.(1[0-9])")) {
        Write-Host "  [X] Yeu cau Python 3.10 tro len. He thong dang dung: $pyVer" -ForegroundColor Red
        Pause; exit 1
    }
    Write-Host "  [OK] Da tim thay $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  [X] Khong tim thay lenh 'python'. Vui long cai dat Python va tich 'Add to PATH'." -ForegroundColor Red
    Pause; exit 1
}

# 2. Install UV
Write-Host "`n[2/6] Cai dat uv (Trinh quan ly goi sieu toc)..." -ForegroundColor Yellow
python -m pip install uv --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [X] Loi khi cai dat uv. Kiem tra ket noi mang." -ForegroundColor Red
    Pause; exit 1
}
Write-Host "  [OK] Da cai dat uv thanh cong." -ForegroundColor Green

# 3. Create VENV
$VenvDir = Join-Path $RootDir ".venv"
Write-Host "`n[3/6] Tao moi truong ao (venv)..." -ForegroundColor Yellow
if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    python -m uv venv $VenvDir
    Write-Host "  [OK] Da tao thu muc .venv" -ForegroundColor Green
} else {
    Write-Host "  [OK] Moi truong ao da ton tai." -ForegroundColor DarkGreen
}

# 4. Download FFmpeg
$FfmpegDir = Join-Path $RootDir "tools\ffmpeg"
Write-Host "`n[4/6] Kiem tra FFmpeg..." -ForegroundColor Yellow
if (-not (Test-Path "$FfmpegDir\ffmpeg.exe")) {
    Write-Host "  Dang tai FFmpeg (~90MB)... Vui long cho..." -ForegroundColor Cyan
    if (-not (Test-Path $FfmpegDir)) { New-Item -ItemType Directory -Force -Path $FfmpegDir | Out-Null }
    
    $ProgressPreference = 'Continue'
    $zipPath = "$env:TEMP\ffmpeg_forgex.zip"
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zipPath
    $ProgressPreference = 'SilentlyContinue'
    
    Write-Host "  Giai nen FFmpeg..." -ForegroundColor Cyan
    Expand-Archive -Path $zipPath -DestinationPath $FfmpegDir -Force
    $bin = Get-ChildItem $FfmpegDir -Recurse -Filter "bin" | Select-Object -First 1
    Copy-Item "$($bin.FullName)\ffmpeg.exe" "$FfmpegDir\ffmpeg.exe" -Force
    Copy-Item "$($bin.FullName)\ffprobe.exe" "$FfmpegDir\ffprobe.exe" -Force
    Remove-Item $zipPath -Force
    Write-Host "  [OK] Da cai dat FFmpeg" -ForegroundColor Green
} else {
    Write-Host "  [OK] FFmpeg da san sang." -ForegroundColor DarkGreen
}

# 5. Install Packages via UV
Write-Host "`n[5/6] Cai dat thu vien bang uv (Cuc nhanh)..." -ForegroundColor Yellow

$uvExe = "$VenvDir\Scripts\uv.exe"
if (-not (Test-Path $uvExe)) {
    # Fallback to system uv if not in venv
    $uvExe = "uv"
}

Write-Host "  5.1/ Cai dat Core Framework & Utilities..." -ForegroundColor Cyan
& $uvExe pip install fastapi "uvicorn[standard]" pydantic python-multipart requests cryptography aiosqlite yt-dlp pydub python-dotenv tqdm psutil edge-tts gtts pillow "qrcode[pil]" fonttools google-api-python-client google-auth

Write-Host "  5.2/ Cai dat PyTorch (Dang tim GPU)..." -ForegroundColor Cyan
$torchIndex = @()
$nvidiaSmi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    Write-Host "       Phat hien GPU NVIDIA. Dang tai PyTorch CUDA (Khoang 2.5GB)..." -ForegroundColor Green
    $torchIndex = "--index-url", "https://download.pytorch.org/whl/cu121"
} else {
    Write-Host "       Khong co GPU NVIDIA. Dang tai PyTorch CPU..." -ForegroundColor DarkCyan
}
& $uvExe pip install torch numpy $torchIndex

Write-Host "  5.3/ Cai dat AI Models (Whisper, TTS, Vision)..." -ForegroundColor Cyan
& $uvExe pip install faster-whisper whisperx demucs transformers sentencepiece rapidocr_onnxruntime azure-cognitiveservices-speech elevenlabs f5-tts scenedetect opencv-python mediapipe insightface pyannote-audio pywebview

Write-Host "  [OK] Da cai dat xong tat ca thu vien." -ForegroundColor Green

# 6. Setup & Whisper Model
Write-Host "`n[6/6] Cau hinh & tai truoc Whisper Model..." -ForegroundColor Yellow

if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
    } else {
        Set-Content ".env" "HOST=127.0.0.1`nPORT=7860`nWHISPER_MODEL=base`nMAX_QUEUE_WORKERS=1"
    }
}

# Update FFmpeg Path
try {
    $envContent = Get-Content ".env" -Raw
    $escapedFfmpeg = $FfmpegDir.Replace('\','\\')
    $envContent = $envContent -replace "FFMPEG_PATH=.*", "FFMPEG_PATH=$escapedFfmpeg\\ffmpeg.exe"
    $envContent = $envContent -replace "FFPROBE_PATH=.*", "FFPROBE_PATH=$escapedFfmpeg\\ffprobe.exe"
    Set-Content ".env" $envContent -NoNewline
} catch {
    Write-Host "  [WARN] Khong the cap nhat duong dan FFmpeg trong .env" -ForegroundColor Yellow
}

Write-Host "  Dang tai model Whisper (145MB)..." -ForegroundColor Cyan
& "$VenvDir\Scripts\python.exe" -c "import warnings; warnings.filterwarnings('ignore'); from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

Write-Host "`n=========================================================" -ForegroundColor Cyan
Write-Host " HOAN TAT CAI DAT! MOI THU DA SAN SANG." -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " De chay tool, hay mo file run.bat o thu muc goc." -ForegroundColor Yellow
Write-Host ""
Pause
