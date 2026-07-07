@echo off
setlocal enabledelayedexpansion
title ForgeX Git Auto-Updater

echo ====================================================
echo         ForgeX - HE THONG DAY CODE TU DONG
echo ====================================================
echo.

:: Dam bao dung remote origin cua ForgeX
echo [*] Dang cau hinh remote URL cho ForgeX...
git remote set-url origin https://github.com/0xHuyVN/ForgeX.git
if %errorlevel% neq 0 (
    echo [x] Loi: Khong the thiet lap remote URL.
    goto error
)

:: Kiem tra xem co file nao thay doi khong (ghi ra file temp ngoai repo)
git status --porcelain > "%temp%\git_status.txt"
for %%I in ("%temp%\git_status.txt") do (
    set file_size=%%~zI
)
del "%temp%\git_status.txt"

if "%file_size%"=="0" (
    echo [i] Working tree sach, khong co thay doi moi de commit.
    set has_changes=0
) else (
    echo [i] Phat hien co thay doi can duoc commit.
    set has_changes=1
)

if "%has_changes%"=="1" (
    echo.
    echo ====================================================
    echo NHAP COMMIT MESSAGE
    echo ====================================================
    set /p commit_msg="> Nhap mo ta thay doi: "
    if "!commit_msg!"=="" (
        set commit_msg=Auto update: %date% %time%
    )
    
    echo.
    echo [1/3] Dang gom cac file thay doi...
    git add .
    if !errorlevel! neq 0 (
        echo [x] Loi: Khong the add file vao vung cho.
        goto error
    )
    
    echo [2/3] Dang tao Commit: "!commit_msg!"...
    git commit -m "!commit_msg!"
    if !errorlevel! neq 0 (
        echo [x] Loi: Khong the tao commit.
        goto error
    )
) else (
    echo.
    echo [1/2] Bo qua buoc commit vi khong co thay doi moi.
)

:: Dam bao luon o nhanh main
git branch -M main

:: Khuyen cao nen pull truoc khi push de tranh conflict
echo.
echo [*] Dang dong bo code moi tu GitHub ve...
git pull origin main --rebase
if %errorlevel% neq 0 (
    echo.
    echo ====================================================
    echo   [x] CANH BAO: Loi khi lay code moi tu GitHub ve.
    echo   Co the co xung dot can xu ly thu cong.
    echo ====================================================
    goto error
)

:: Day code len
echo.
echo [*] Dang push code len GitHub...
git push -u origin main
if %errorlevel% neq 0 (
    echo.
    echo ====================================================
    echo   [x] LOI: KHONG THE PUSH CODE LEN GITHUB.
    echo   Vui long kiem tra lai mang hoac quyen ghi.
    echo ====================================================
    goto error
)

echo.
echo ====================================================
echo   [V] UP CODE LEN GITHUB THANH CONG.
echo ====================================================
goto end

:error
echo.
echo ====================================================
echo   [X] QUA TRÌNH CAP NHAT CO LOI XAY RA.
echo ====================================================

:end
echo.
pause
