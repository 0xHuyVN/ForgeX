param(
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $rootDir

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Tool Review Master V2.1.1" -ForegroundColor Cyan
Write-Host "  Tool Review Master" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

if ($Build) {
    Write-Host "[BUILD] Dang build file exe..." -ForegroundColor Yellow
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) {
        Write-Host "[LOI] Khong tim thay Python" -ForegroundColor Red
        exit 1
    }
    pip install pyinstaller -q
    pyinstaller RichReviewTool.spec --clean
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Da build xong: dist\0xForge.exe" -ForegroundColor Green
    } else {
        Write-Host "[LOI] Build that bai" -ForegroundColor Red
    }
    return
}

# Kiem tra Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "[LOI] Khong tim thay Python." -ForegroundColor Red
    Write-Host "      Cai tai: https://www.python.org/downloads/" -ForegroundColor Yellow
    Read-Host "Nhan Enter de thoat"
    exit 1
}
Write-Host "[OK] Python $(python --version 2>&1)" -ForegroundColor Green

# Tao .env
if (-not (Test-Path ".env")) {
    Write-Host "[INFO] Tao file .env tu .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "[INFO] Da tao .env. Sua API key trong file .env neu can." -ForegroundColor Yellow
}

# Kiem tra thu vien
Write-Host "[INFO] Kiem tra thu vien..." -ForegroundColor Yellow
try {
    python -c "import fastapi, uvicorn, pydub" -ErrorAction Stop | Out-Null
    Write-Host "[OK] Thu vien da duoc cai dat." -ForegroundColor Green
} catch {
    Write-Host "[INFO] Dang cai dat thu vien (lan dau se lau)..." -ForegroundColor Yellow
    pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[LOI] Cai dat thu vien that bai." -ForegroundColor Red
        Read-Host "Nhan Enter de thoat"
        exit 1
    }
    Write-Host "[OK] Da cai dat xong." -ForegroundColor Green
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Dang khoi dong server..." -ForegroundColor Cyan
Write-Host "  Mo trinh duyet: http://127.0.0.1:7860" -ForegroundColor White
Write-Host "  Ctrl+C de dung server" -ForegroundColor Gray
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Start-Process "http://127.0.0.1:7860"

try {
    python -m backend.main
} catch {
    Write-Host "[LOI] Server that bai." -ForegroundColor Red
    Read-Host "Nhan Enter de thoat"
}
