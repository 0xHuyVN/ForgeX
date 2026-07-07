$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv-voiceclone"
$Python = Join-Path $Venv "Scripts\python.exe"

if (!(Test-Path $Python)) {
  python -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Python -m pip install -r (Join-Path $Root "requirements-voiceclone.txt")

Write-Host "Voice clone Python:"
Write-Host $Python
Write-Host ""
Write-Host "Add this to .env:"
Write-Host "VOICE_CLONE_PYTHON=$Python"
