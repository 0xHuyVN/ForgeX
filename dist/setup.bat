@echo off
chcp 65001 >nul 2>&1
echo Dang khoi dong trinh cai dat sieu toc (PowerShell + UV)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
