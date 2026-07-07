@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0.."
title ForgeX Setup - Cai Dat Tu Dong

:: =====================================================================
::  ForgeX Setup Script
::  - Cai dat tat ca thanh phan can thiet de chay ForgeX
::  - Hien thi % tien trinh va log loi chi tiet
::  - Tu dong tiep tuc neu bi gian doan (mat mang, thoat dot ngot...)
::  - Dung file .setup_checkpoint de theo doi buoc da hoan thanh
:: =====================================================================

set "CHECKPOINT_FILE=.setup_checkpoint"
set "LOG_FILE=setup_log.txt"
set "FFMPEG_DIR=%~dp0tools\ffmpeg"
set "VENV_DIR=.venv"
set "TOTAL_STEPS=8"
set "ERRORS=0"

:: Ghi header log
echo ============================================== >> "%LOG_FILE%"
echo  ForgeX Setup - %date% %time% >> "%LOG_FILE%"
echo ============================================== >> "%LOG_FILE%"

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║         ForgeX - CAI DAT TU DONG TAT CA             ║
echo  ║   Tool se kiem tra va cai dat cac thanh phan can     ║
echo  ║   thiet. Neu bi gian doan, chay lai file nay de      ║
echo  ║   tiep tuc tu buoc dang do.                          ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: =====================================================================
::  HAM HO TRO
:: =====================================================================

goto :main

:check_done
:: Kiem tra buoc da hoan thanh chua
if exist "%CHECKPOINT_FILE%" (
    findstr /c:"%~1" "%CHECKPOINT_FILE%" >nul 2>&1
    if !errorlevel! equ 0 (
        exit /b 0
    )
)
exit /b 1

:mark_done
:: Danh dau buoc da hoan thanh
echo %~1 >> "%CHECKPOINT_FILE%"
exit /b

:show_progress
:: Hien thi thanh tien trinh
set /a pct=%~1 * 100 / %TOTAL_STEPS%
set "bar="
set /a filled=pct / 5
set /a empty=20 - filled
for /l %%i in (1,1,!filled!) do set "bar=!bar!█"
for /l %%i in (1,1,!empty!) do set "bar=!bar!░"
echo  [!bar!] !pct!%%  -  Buoc %~1/%TOTAL_STEPS%: %~2
exit /b

:: =====================================================================
::  BAT DAU CAI DAT
:: =====================================================================

:main

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 1/8: KIEM TRA PYTHON
:: ─────────────────────────────────────────────────────────────────────
call :check_done "STEP1_PYTHON"
if !errorlevel! equ 0 (
    call :show_progress 1 "Python [DA HOAN THANH]"
    goto step2
)

call :show_progress 1 "Kiem tra Python..."
echo.

where python >nul 2>&1
if !errorlevel! neq 0 (
    echo  [LOI] Khong tim thay Python tren he thong!                  >> "%LOG_FILE%"
    echo.
    echo  ┌─────────────────────────────────────────────────────────┐
    echo  │  [X] KHONG TIM THAY PYTHON                              │
    echo  │                                                         │
    echo  │  Ban can cai dat Python 3.10+ truoc khi chay setup:     │
    echo  │  1. Truy cap: https://www.python.org/downloads/         │
    echo  │  2. Tai ban Python 3.12 hoac moi hon                    │
    echo  │  3. QUAN TRONG: Tick vao o "Add Python to PATH"         │
    echo  │  4. Cai dat xong, KHOI DONG LAI may tinh                │
    echo  │  5. Chay lai file setup.bat nay                         │
    echo  └─────────────────────────────────────────────────────────┘
    echo.
    echo  [LOG] Chi tiet loi xem tai: %LOG_FILE%
    pause
    exit /b 1
)

:: Kiem tra phien ban Python
for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PYVER=%%v"
for /f "tokens=1,2 delims=." %%a in ("!PYVER!") do (
    set "PYMAJOR=%%a"
    set "PYMINOR=%%b"
)

echo  [OK] Python !PYVER! da duoc cai dat.
echo  [OK] Python !PYVER! >> "%LOG_FILE%"

if !PYMAJOR! lss 3 (
    echo  [LOI] Can Python 3.10+. Phien ban hien tai: !PYVER!
    echo  [LOI] Python version too old: !PYVER! >> "%LOG_FILE%"
    pause
    exit /b 1
)
if !PYMAJOR! equ 3 if !PYMINOR! lss 10 (
    echo  [LOI] Can Python 3.10+. Phien ban hien tai: !PYVER!
    echo  [LOI] Python version too old: !PYVER! >> "%LOG_FILE%"
    pause
    exit /b 1
)

call :mark_done "STEP1_PYTHON"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 2/8: CAI DAT FFMPEG
:: ─────────────────────────────────────────────────────────────────────
:step2
call :check_done "STEP2_FFMPEG"
if !errorlevel! equ 0 (
    call :show_progress 2 "FFmpeg [DA HOAN THANH]"
    goto step3
)

call :show_progress 2 "Kiem tra va cai dat FFmpeg..."
echo.

where ffmpeg >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=3" %%v in ('ffmpeg -version 2^>^&1 ^| findstr /i "ffmpeg version"') do (
        echo  [OK] FFmpeg da duoc cai dat: %%v
        echo  [OK] FFmpeg %%v >> "%LOG_FILE%"
    )
    call :mark_done "STEP2_FFMPEG"
    echo.
    goto step3
)

echo  [i] FFmpeg chua duoc cai dat. Dang tai tu dong...
echo  [i] Downloading FFmpeg... >> "%LOG_FILE%"

set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
set "FFMPEG_ZIP=%TEMP%\ffmpeg_forgex.zip"

:: Tai FFmpeg bang PowerShell (co hien thi % tai)
echo  [i] Dang tai FFmpeg (~90MB)... Vui long cho...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference = 'Continue'; " ^
    "try { " ^
    "    $wc = New-Object System.Net.WebClient; " ^
    "    $uri = '%FFMPEG_URL%'; " ^
    "    $out = '%FFMPEG_ZIP%'; " ^
    "    Write-Host '     Bat dau tai...'; " ^
    "    $wc.DownloadFile($uri, $out); " ^
    "    if (Test-Path $out) { " ^
    "        $size = [math]::Round((Get-Item $out).Length / 1MB, 1); " ^
    "        Write-Host \"     Tai thanh cong: $size MB\"; " ^
    "    } else { " ^
    "        Write-Host '     LOI: File khong duoc tao'; " ^
    "        exit 1; " ^
    "    } " ^
    "} catch { " ^
    "    Write-Host \"     LOI tai FFmpeg: $_\"; " ^
    "    exit 1; " ^
    "}"

if !errorlevel! neq 0 (
    echo  [LOI] Tai FFmpeg that bai!                                  >> "%LOG_FILE%"
    echo.
    echo  ┌─────────────────────────────────────────────────────────┐
    echo  │  [X] TAI FFMPEG THAT BAI                                 │
    echo  │                                                         │
    echo  │  Co the do mat mang hoac link tai bi chan.               │
    echo  │  Cach xu ly thu cong:                                   │
    echo  │  1. Truy cap: https://www.gyan.dev/ffmpeg/builds/       │
    echo  │  2. Tai ban "ffmpeg-release-essentials.zip"             │
    echo  │  3. Giai nen, copy ffmpeg.exe va ffprobe.exe            │
    echo  │     vao thu muc C:\Windows hoac them vao PATH           │
    echo  │  4. Chay lai setup.bat                                  │
    echo  └─────────────────────────────────────────────────────────┘
    echo.
    set /a ERRORS+=1
    echo  Nhan phim bat ky de tiep tuc cai dat cac thanh phan khac...
    pause >nul
    call :mark_done "STEP2_FFMPEG"
    goto step3
)

:: Giai nen FFmpeg
echo  [i] Dang giai nen FFmpeg...
if not exist "%FFMPEG_DIR%" mkdir "%FFMPEG_DIR%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "try { " ^
    "    Expand-Archive -Path '%FFMPEG_ZIP%' -DestinationPath '%FFMPEG_DIR%' -Force; " ^
    "    $binDir = Get-ChildItem -Path '%FFMPEG_DIR%' -Recurse -Directory -Filter 'bin' | Select-Object -First 1; " ^
    "    if ($binDir) { " ^
    "        Copy-Item (Join-Path $binDir.FullName 'ffmpeg.exe') -Destination '%FFMPEG_DIR%\ffmpeg.exe' -Force; " ^
    "        Copy-Item (Join-Path $binDir.FullName 'ffprobe.exe') -Destination '%FFMPEG_DIR%\ffprobe.exe' -Force; " ^
    "        Write-Host '     Giai nen thanh cong'; " ^
    "    } else { " ^
    "        Write-Host '     LOI: Khong tim thay thu muc bin'; " ^
    "        exit 1; " ^
    "    } " ^
    "} catch { " ^
    "    Write-Host \"     LOI giai nen: $_\"; " ^
    "    exit 1; " ^
    "}"

if !errorlevel! neq 0 (
    echo  [LOI] Giai nen FFmpeg that bai >> "%LOG_FILE%"
    set /a ERRORS+=1
) else (
    :: Them vao PATH tam thoi cho session nay
    set "PATH=%FFMPEG_DIR%;!PATH!"
    echo  [OK] FFmpeg da duoc cai tai: %FFMPEG_DIR%
    echo  [OK] FFmpeg installed to %FFMPEG_DIR% >> "%LOG_FILE%"

    :: Ghi vao .env
    echo  [i] Dang cap nhat duong dan FFmpeg vao .env...
)

:: Don dep file zip
del "%FFMPEG_ZIP%" >nul 2>&1
call :mark_done "STEP2_FFMPEG"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 3/8: TAO MOI TRUONG AO (VENV)
:: ─────────────────────────────────────────────────────────────────────
:step3
call :check_done "STEP3_VENV"
if !errorlevel! equ 0 (
    call :show_progress 3 "Virtual Environment [DA HOAN THANH]"
    goto step4_activate
)

call :show_progress 3 "Tao moi truong ao Python (venv)..."
echo.

if exist "%VENV_DIR%\Scripts\python.exe" (
    echo  [OK] Moi truong ao da ton tai: %VENV_DIR%
    echo  [OK] Venv already exists >> "%LOG_FILE%"
    call :mark_done "STEP3_VENV"
    goto step4_activate
)

echo  [i] Dang tao moi truong ao tai %VENV_DIR%...
python -m venv "%VENV_DIR%" 2>> "%LOG_FILE%"
if !errorlevel! neq 0 (
    echo  [LOI] Tao venv that bai! Chi tiet xem tai %LOG_FILE%
    echo  [LOI] venv creation failed >> "%LOG_FILE%"
    echo.
    echo  Thu cai dat venv module:
    echo    python -m pip install --upgrade pip virtualenv
    echo    python -m venv %VENV_DIR%
    set /a ERRORS+=1
    pause
    goto step4_activate
)

echo  [OK] Moi truong ao da duoc tao tai: %VENV_DIR%
echo  [OK] Venv created >> "%LOG_FILE%"
call :mark_done "STEP3_VENV"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  KICH HOAT VENV (luon chay, khong phai checkpoint)
:: ─────────────────────────────────────────────────────────────────────
:step4_activate
if exist "%VENV_DIR%\Scripts\activate.bat" (
    call "%VENV_DIR%\Scripts\activate.bat"
    echo  [OK] Da kich hoat moi truong ao.
) else if exist "venv\Scripts\activate.bat" (
    call "venv\Scripts\activate.bat"
    echo  [OK] Da kich hoat moi truong ao (venv).
) else (
    echo  [WARN] Khong tim thay venv, se cai vao Python he thong.
    echo  [WARN] No venv found, using system Python >> "%LOG_FILE%"
)

:: Nang cap pip truoc
echo  [i] Nang cap pip...
python -m pip install --upgrade pip --quiet 2>> "%LOG_FILE%"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 4/8: CAI THU VIEN CORE (FastAPI, yt-dlp, ...)
:: ─────────────────────────────────────────────────────────────────────
:step4
call :check_done "STEP4_CORE"
if !errorlevel! equ 0 (
    call :show_progress 4 "Thu vien Core [DA HOAN THANH]"
    goto step5
)

call :show_progress 4 "Cai thu vien Core (FastAPI, yt-dlp, database...)..."
echo.
echo  [i] Nhom 1/5: Core framework ^& utilities
echo  [i] Gom: fastapi, uvicorn, pydantic, yt-dlp, requests, psutil...
echo.

pip install ^
    "fastapi>=0.110.0" ^
    "uvicorn[standard]>=0.29.0" ^
    "pydantic>=2.0.0" ^
    "python-multipart>=0.0.9" ^
    "requests>=2.31.0" ^
    "cryptography>=42.0.0" ^
    "aiosqlite>=0.19.0" ^
    "yt-dlp>=2024.0.0" ^
    "pydub>=0.25.1" ^
    "python-dotenv>=1.0.0" ^
    "tqdm>=4.66.0" ^
    "psutil>=5.9.0" ^
    "edge-tts>=6.1.0" ^
    "gtts>=2.5.1" ^
    "pillow>=10.0.0" ^
    "qrcode[pil]>=7.4.2" ^
    "fonttools>=4.53.0" ^
    "google-api-python-client>=2.130.0" ^
    "google-auth>=2.29.0" ^
    2>> "%LOG_FILE%"

if !errorlevel! neq 0 (
    echo.
    echo  [LOI] Cai thu vien Core that bai! >> "%LOG_FILE%"
    echo  [LOI] Mot so thu vien Core cai that bai.
    echo        Chi tiet loi xem tai: %LOG_FILE%
    echo        Thu chay lai: pip install -r requirements.txt
    echo.
    set /a ERRORS+=1
) else (
    echo.
    echo  [OK] Thu vien Core da cai thanh cong.
    echo  [OK] Core packages installed >> "%LOG_FILE%"
)

call :mark_done "STEP4_CORE"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 5/8: CAI PYTORCH + WHISPER + ML
:: ─────────────────────────────────────────────────────────────────────
:step5
call :check_done "STEP5_ML"
if !errorlevel! equ 0 (
    call :show_progress 5 "PyTorch + Whisper [DA HOAN THANH]"
    goto step6
)

call :show_progress 5 "Cai PyTorch + Whisper + AI models (~2GB tai)..."
echo.
echo  [i] Nhom 2/5: Machine Learning ^& Speech
echo  [i] Gom: torch, faster-whisper, transformers, numpy, scipy...
echo  [!] Day la buoc TON NHIEU THOI GIAN NHAT (1-3GB tai ve)
echo.

:: Kiem tra GPU NVIDIA de chon ban PyTorch phu hop
set "TORCH_INDEX="
where nvidia-smi >nul 2>&1
if !errorlevel! equ 0 (
    echo  [i] Phat hien GPU NVIDIA. Cai PyTorch ban CUDA...
    echo  [i] NVIDIA GPU detected, installing CUDA PyTorch >> "%LOG_FILE%"
    set "TORCH_INDEX=--index-url https://download.pytorch.org/whl/cu121"
) else (
    echo  [i] Khong tim thay GPU NVIDIA. Cai PyTorch ban CPU.
    echo  [i] No NVIDIA GPU, installing CPU PyTorch >> "%LOG_FILE%"
)

pip install "torch>=2.2.0" "numpy>=1.24.0" !TORCH_INDEX! 2>> "%LOG_FILE%"
if !errorlevel! neq 0 (
    echo  [LOI] Cai PyTorch that bai! >> "%LOG_FILE%"
    echo  [LOI] Cai PyTorch that bai. Thu lai:
    echo        pip install torch numpy
    set /a ERRORS+=1
) else (
    echo  [OK] PyTorch da cai thanh cong.
)

echo.
echo  [i] Dang cai Faster-Whisper, Transformers, Demucs...
pip install ^
    "faster-whisper>=1.0.0" ^
    "whisperx>=3.1.0" ^
    "demucs>=4.0.0" ^
    "transformers>=4.57.6,<5.0.0" ^
    "sentencepiece>=0.2.0" ^
    "rapidocr_onnxruntime>=1.3.0" ^
    2>> "%LOG_FILE%"

if !errorlevel! neq 0 (
    echo  [LOI] Mot so thu vien ML cai that bai! >> "%LOG_FILE%"
    echo  [WARN] Mot so thu vien ML co the cai that bai.
    echo         Tool van chay duoc voi cac tinh nang co ban.
    echo         Chi tiet: %LOG_FILE%
    set /a ERRORS+=1
) else (
    echo  [OK] Thu vien ML da cai thanh cong.
)

call :mark_done "STEP5_ML"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 6/8: CAI TTS, VISION AI, SCENE DETECTION
:: ─────────────────────────────────────────────────────────────────────
:step6
call :check_done "STEP6_EXTRAS"
if !errorlevel! equ 0 (
    call :show_progress 6 "TTS + Vision AI [DA HOAN THANH]"
    goto step7
)

call :show_progress 6 "Cai TTS providers + Vision AI + Scene Detection..."
echo.
echo  [i] Nhom 3/5: TTS, Computer Vision, Video Analysis
echo  [i] Gom: azure-tts, elevenlabs, mediapipe, opencv, scenedetect...
echo.

pip install ^
    "azure-cognitiveservices-speech>=1.36.0" ^
    "elevenlabs>=0.2.0" ^
    "f5-tts>=1.1.16" ^
    "scenedetect>=0.6.2" ^
    "opencv-python>=4.9.0" ^
    "mediapipe>=0.10.0" ^
    "insightface>=0.7.3" ^
    "pyannote-audio>=4.0.0" ^
    "pywebview>=5.0" ^
    2>> "%LOG_FILE%"

if !errorlevel! neq 0 (
    echo  [LOI] Mot so thu vien phu cai that bai >> "%LOG_FILE%"
    echo  [WARN] Mot so thu vien phu co the cai that bai.
    echo         Cac tinh nang chinh (download, dich, TTS Edge) van hoat dong.
    echo         Chi tiet: %LOG_FILE%
    set /a ERRORS+=1
) else (
    echo  [OK] Thu vien TTS + Vision AI da cai thanh cong.
)

call :mark_done "STEP6_EXTRAS"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 7/8: TAO FILE .env
:: ─────────────────────────────────────────────────────────────────────
:step7
call :check_done "STEP7_ENV"
if !errorlevel! equ 0 (
    call :show_progress 7 "File .env [DA HOAN THANH]"
    goto step8
)

call :show_progress 7 "Tao file cau hinh .env..."
echo.

if exist ".env" (
    echo  [OK] File .env da ton tai, giu nguyen cau hinh hien tai.
) else if exist ".env.example" (
    copy ".env.example" ".env" >nul
    echo  [OK] Da tao file .env tu .env.example

    :: Neu FFmpeg duoc tai ve thu muc tools, cap nhat duong dan
    if exist "%FFMPEG_DIR%\ffmpeg.exe" (
        powershell -NoProfile -Command ^
            "$c = Get-Content '.env' -Raw; " ^
            "$c = $c -replace 'FFMPEG_PATH=ffmpeg', 'FFMPEG_PATH=%FFMPEG_DIR:\=\\%\\ffmpeg.exe'; " ^
            "$c = $c -replace 'FFPROBE_PATH=ffprobe', 'FFPROBE_PATH=%FFMPEG_DIR:\=\\%\\ffprobe.exe'; " ^
            "Set-Content '.env' $c -NoNewline"
        echo  [OK] Da cap nhat duong dan FFmpeg trong .env
    )
) else (
    echo  [WARN] Khong tim thay .env.example. Tao .env mac dinh...
    (
        echo HOST=127.0.0.1
        echo PORT=7860
        echo FFMPEG_PATH=ffmpeg
        echo FFPROBE_PATH=ffprobe
        echo WHISPER_MODEL=base
        echo WHISPER_DEVICE=auto
        echo WHISPER_COMPUTE_TYPE=auto
        echo MAX_QUEUE_WORKERS=1
    ) > ".env"
    echo  [OK] Da tao file .env voi cau hinh mac dinh.
)

call :mark_done "STEP7_ENV"
echo.

:: ─────────────────────────────────────────────────────────────────────
::  BUOC 8/8: TAI TRUOC WHISPER MODEL
:: ─────────────────────────────────────────────────────────────────────
:step8
call :check_done "STEP8_MODEL"
if !errorlevel! equ 0 (
    call :show_progress 8 "Whisper model [DA HOAN THANH]"
    goto done
)

call :show_progress 8 "Tai truoc Whisper model (base, ~145MB)..."
echo.
echo  [i] Tai truoc model de lan chay dau tien khong phai cho.
echo  [i] Model 'base' can khoang 145MB.
echo.

python -c "print('[i] Dang tai Whisper model base...'); from faster_whisper import WhisperModel; m = WhisperModel('base', device='cpu', compute_type='int8'); print('[OK] Whisper model base da tai thanh cong!')" 2>> "%LOG_FILE%"

if !errorlevel! neq 0 (
    echo  [WARN] Tai Whisper model that bai. Model se tu tai khi chay tool lan dau.
    echo  [WARN] Whisper model pre-download failed >> "%LOG_FILE%"
    echo         Neu loi do mat mang, chay lai setup.bat sau khi co mang.
    set /a ERRORS+=1
) else (
    echo  [OK] Whisper model da san sang.
    echo  [OK] Whisper model downloaded >> "%LOG_FILE%"
)

call :mark_done "STEP8_MODEL"
echo.

:: =====================================================================
::  HOAN THANH
:: =====================================================================
:done

echo.
echo  ╔══════════════════════════════════════════════════════╗

if !ERRORS! equ 0 (
    echo  ║     [V] CAI DAT HOAN TAT - KHONG CO LOI             ║
) else (
    echo  ║     [!] CAI DAT HOAN TAT - CO %ERRORS% CANH BAO/LOI         ║
)

echo  ╠══════════════════════════════════════════════════════╣
echo  ║                                                      ║
echo  ║  De chay ForgeX:                                     ║
echo  ║    1. Mo file  run.bat                               ║
echo  ║    2. Trinh duyet tu mo tai http://127.0.0.1:7860    ║
echo  ║                                                      ║

if !ERRORS! gtr 0 (
    echo  ║  [!] Co %ERRORS% van de can chu y:                        ║
    echo  ║      - Xem chi tiet tai: setup_log.txt               ║
    echo  ║      - Chay lai setup.bat de thu cai lai             ║
    echo  ║      - Xoa file .setup_checkpoint de cai lai tu dau  ║
    echo  ║                                                      ║
)

echo  ║  Cau hinh API key (tuy chon):                        ║
echo  ║    Mo file .env va dien cac API key can thiet         ║
echo  ║    (OPENAI_API_KEY, GEMINI_API_KEY, ...)              ║
echo  ║    Khong co key van chay duoc voi NLLB + Edge TTS.    ║
echo  ║                                                      ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

if !ERRORS! gtr 0 (
    echo  [LOG] Chi tiet loi: %LOG_FILE%
    echo  [TIP] Chay lai setup.bat se chi cai nhung buoc chua thanh cong.
    echo  [TIP] Muon cai lai tu dau? Xoa file .setup_checkpoint roi chay lai.
    echo.
)

echo  Nhan phim bat ky de thoat...
pause >nul
endlocal
