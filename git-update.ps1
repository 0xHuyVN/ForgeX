param(
    [string]$msg = "update"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

git add .
git commit -m "$msg"
git push

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nOK! Da push len GitHub thanh cong." -ForegroundColor Green
} else {
    Write-Host "`nLOI! Kiem tra lai." -ForegroundColor Red
}
