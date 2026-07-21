@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo Building portable VietSub Studio...
python -m PyInstaller --noconfirm --clean --distpath dist_portable --workpath build_portable "VietSub Studio Portable.spec"
if errorlevel 1 goto :error

echo.
echo Build complete: dist_portable\VietSub Studio.exe
pause
exit /b 0

:error
echo.
echo Build failed. See the error above.
pause
exit /b 1
