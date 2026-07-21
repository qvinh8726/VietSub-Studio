@echo off
setlocal
title VietSub Studio
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
python -c "import flask, playwright" >nul 2>&1
if errorlevel 1 (
    echo Dang cai dat cac thu vien can thiet...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo Cai dat thu vien that bai.
        pause
        exit /b 1
    )
)
echo Khoi dong VietSub Studio...
python app.py
pause
