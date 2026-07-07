$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Get-Item $ScriptDir).Parent.FullName
Set-Location $RootDir

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " ForgeX - CAI DAT TU DONG BANG POWERSHELL + UV" -ForegroundColor Cyan
Write-Host " Tốc độ cài đặt x10-x100 lần so với pip" -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
Write-Host "[1/6] Kiểm tra Python..." -ForegroundColor Yellow
try {
    $pyVerRaw = python --version 2>&1
    $pyVer = [string]$pyVerRaw
    if (-not ($pyVer -match "Python 3\.(1[0-9])")) {
        Write-Host "  [X] Yêu cầu Python 3.10 trở lên. Hệ thống đang dùng: $pyVer" -ForegroundColor Red
        Pause; exit 1
    }
    Write-Host "  [OK] Đã tìm thấy $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  [X] Không tìm thấy lệnh 'python'. Vui lòng cài đặt Python và tích 'Add to PATH'." -ForegroundColor Red
    Pause; exit 1
}

# 2. Install UV
Write-Host "`n[2/6] Cài đặt uv (Trình quản lý gói siêu tốc)..." -ForegroundColor Yellow
python -m pip install uv --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [X] Lỗi khi cài đặt uv. Kiểm tra kết nối mạng." -ForegroundColor Red
    Pause; exit 1
}
Write-Host "  [OK] Đã cài đặt uv thành công." -ForegroundColor Green

# 3. Create VENV
$VenvDir = Join-Path $RootDir ".venv"
Write-Host "`n[3/6] Tạo môi trường ảo (venv)..." -ForegroundColor Yellow
if (-not (Test-Path "$VenvDir\Scripts\python.exe")) {
    python -m uv venv $VenvDir
    Write-Host "  [OK] Đã tạo thư mục .venv" -ForegroundColor Green
} else {
    Write-Host "  [OK] Môi trường ảo đã tồn tại." -ForegroundColor DarkGreen
}

# 4. Download FFmpeg
$FfmpegDir = Join-Path $RootDir "tools\ffmpeg"
Write-Host "`n[4/6] Kiểm tra FFmpeg..." -ForegroundColor Yellow
if (-not (Test-Path "$FfmpegDir\ffmpeg.exe")) {
    Write-Host "  Đang tải FFmpeg (~90MB)... Vui lòng chờ..." -ForegroundColor Cyan
    if (-not (Test-Path $FfmpegDir)) { New-Item -ItemType Directory -Force -Path $FfmpegDir | Out-Null }
    
    $ProgressPreference = 'Continue'
    $zipPath = "$env:TEMP\ffmpeg_forgex.zip"
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile $zipPath
    $ProgressPreference = 'SilentlyContinue'
    
    Write-Host "  Giải nén FFmpeg..." -ForegroundColor Cyan
    Expand-Archive -Path $zipPath -DestinationPath $FfmpegDir -Force
    $bin = Get-ChildItem $FfmpegDir -Recurse -Filter "bin" | Select-Object -First 1
    Copy-Item "$($bin.FullName)\ffmpeg.exe" "$FfmpegDir\ffmpeg.exe" -Force
    Copy-Item "$($bin.FullName)\ffprobe.exe" "$FfmpegDir\ffprobe.exe" -Force
    Remove-Item $zipPath -Force
    Write-Host "  [OK] Đã cài đặt FFmpeg" -ForegroundColor Green
} else {
    Write-Host "  [OK] FFmpeg đã sẵn sàng." -ForegroundColor DarkGreen
}

# 5. Install Packages via UV
Write-Host "`n[5/6] Cài đặt thư viện bằng uv (Cực nhanh)..." -ForegroundColor Yellow

$uvExe = "$VenvDir\Scripts\uv.exe"
if (-not (Test-Path $uvExe)) {
    # Fallback to system uv if not in venv
    $uvExe = "uv"
}

Write-Host "  5.1/ Cài đặt Core Framework & Utilities..." -ForegroundColor Cyan
& $uvExe pip install fastapi "uvicorn[standard]" pydantic python-multipart requests cryptography aiosqlite yt-dlp pydub python-dotenv tqdm psutil edge-tts gtts pillow "qrcode[pil]" fonttools google-api-python-client google-auth

Write-Host "  5.2/ Cài đặt PyTorch (Đang tìm GPU)..." -ForegroundColor Cyan
$torchIndex = @()
$nvidiaSmi = Get-Command "nvidia-smi" -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    Write-Host "       Phát hiện GPU NVIDIA. Đang tải PyTorch CUDA (Khoảng 2.5GB)..." -ForegroundColor Green
    $torchIndex = "--index-url", "https://download.pytorch.org/whl/cu121"
} else {
    Write-Host "       Không có GPU NVIDIA. Đang tải PyTorch CPU..." -ForegroundColor DarkCyan
}
& $uvExe pip install torch numpy $torchIndex

Write-Host "  5.3/ Cài đặt AI Models (Whisper, TTS, Vision)..." -ForegroundColor Cyan
& $uvExe pip install faster-whisper whisperx demucs transformers sentencepiece rapidocr_onnxruntime azure-cognitiveservices-speech elevenlabs f5-tts scenedetect opencv-python mediapipe insightface pyannote-audio pywebview

Write-Host "  [OK] Đã cài đặt xong tất cả thư viện." -ForegroundColor Green

# 6. Setup & Whisper Model
Write-Host "`n[6/6] Cấu hình & tải trước Whisper Model..." -ForegroundColor Yellow

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
    Write-Host "  [WARN] Không thể cập nhật đường dẫn FFmpeg trong .env" -ForegroundColor Yellow
}

Write-Host "  Đang tải model Whisper (145MB)..." -ForegroundColor Cyan
& "$VenvDir\Scripts\python.exe" -c "import warnings; warnings.filterwarnings('ignore'); from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')"

Write-Host "`n=========================================================" -ForegroundColor Cyan
Write-Host " HOÀN TẤT CÀI ĐẶT! MỌI THỨ ĐÃ SẴN SÀNG." -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host " Để chạy tool, hãy mở file run.bat ở thư mục gốc." -ForegroundColor Yellow
Write-Host ""
Pause
