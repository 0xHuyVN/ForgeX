param(
    [switch]$Clean,
    [switch]$NoPack,
    [switch]$Open
)

$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $rootDir

$specFile = "RichReviewTool.spec"
$exeName = "Tool_Review_Master_V2.1.1.exe"
$distPath = Join-Path $rootDir "dist\$exeName"
$startTime = Get-Date

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Tool Review Master build" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

if (-not (Get-Command pyinstaller -ErrorAction SilentlyContinue)) {
    Write-Host "[INFO] PyInstaller is missing. Installing..." -ForegroundColor Yellow
    pip install pyinstaller -q
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install PyInstaller"
    }
}

if ($Clean) {
    Write-Host "[INFO] Removing old build and dist folders..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force "build" -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force "dist" -ErrorAction SilentlyContinue
    Remove-Item -Force "*.pkg" -ErrorAction SilentlyContinue
}

Write-Host "[INFO] Running PyInstaller..." -ForegroundColor Yellow
Write-Host "       Spec: $specFile" -ForegroundColor Gray

$pyArgs = @($specFile, "--clean")
if ($NoPack) {
    Write-Host "[INFO] -NoPack ignored for .spec builds on this PyInstaller version." -ForegroundColor DarkYellow
}

& pyinstaller @pyArgs
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller exit code: $LASTEXITCODE"
}

$elapsed = [math]::Round(((Get-Date) - $startTime).TotalSeconds, 1)

if (-not (Test-Path $distPath)) {
    throw "Build finished but exe was not found: $distPath"
}

$sizeMB = [math]::Round((Get-Item $distPath).Length / 1MB, 1)
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  BUILD OK" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host "  File: $distPath" -ForegroundColor White
Write-Host "  Size: $sizeMB MB" -ForegroundColor White
Write-Host "  Time: ${elapsed}s" -ForegroundColor White
Write-Host ""

if ($Open) {
    Start-Process "explorer.exe" (Split-Path $distPath)
}
