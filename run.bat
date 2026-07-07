@echo off
cd /d "%~dp0"
title Tool Review Master V2.1.1 Server Launcher

chcp 65001 >nul

echo ====================================================
echo   Tool Review Master V2.1.1 - HE THONG KHOI DONG SERVER
echo ====================================================
echo.

:: 1. Kiem tra Python
where python >nul 2>&1
if %errorlevel% equ 0 goto python_ok
echo [LOI] Khong tim thay Python tren he thong.
echo       Vui long tai va cai dat Python 3.10+ tu:
echo       https://www.python.org/downloads/
echo       Nho tich vao o "Add Python to PATH" khi cai dat.
echo.
pause
exit /b 1
:python_ok

:: 2. Kich hoat Virtual Environment neu co
if exist ".venv\Scripts\activate.bat" goto activate_venv
if exist "venv\Scripts\activate.bat" goto activate_venv2
goto venv_done

:activate_venv
echo [INFO] Kich hoat moi truong ao (.venv)...
call .venv\Scripts\activate.bat
goto venv_done

:activate_venv2
echo [INFO] Kich hoat moi truong ao (venv)...
call venv\Scripts\activate.bat

:venv_done
python --version

:: 3. Kiem tra file .env
if exist ".env" goto env_ok
echo [INFO] Khong tim thay file .env. Dang tao tu .env.example...
copy .env.example .env >nul
echo [INFO] Da tao file .env. Ban co the chinh sua thong tin cau hinh trong do.
echo.
:env_ok

:: 4. Kiem tra va cai dat thu vien
echo [INFO] Dang kiem tra cac thu vien can thiet...
python -c "import fastapi, uvicorn, pydub, dotenv" 2>nul
if %errorlevel% equ 0 goto libs_ok

echo [INFO] Thieu thu vien. Dang tien hanh cai dat tu requirements.txt...
pip install -r requirements.txt
if %errorlevel% equ 0 goto libs_ok
echo [LOI] Cai dat thu vien that bai.
pause
exit /b 1

:libs_ok
echo [OK] Tat ca thu vien da san sang.

:: 5. Giai phong cong truoc khi chay
python scripts/server_helper.py kill

:: 6. Tu dong mo trinh duyet khi server san sang (chay ngam)
start /b python scripts/server_helper.py open

:: 7. Khoi dong Server
echo.
echo ====================================================
echo   Dang khoi dong server...
echo   Ctrl+C de dung server
echo ====================================================
echo.

python -m backend.main
if %errorlevel% equ 0 goto end

echo.
echo [LOI] Server bi tat dot ngot hoac gap loi khi khoi dong.
echo       Vui long kiem tra log chi tiet phia tren.
pause

:end
