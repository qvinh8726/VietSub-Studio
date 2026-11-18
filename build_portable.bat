@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo Installing build dependencies...
python -m pip install -r requirements-build.txt
if errorlevel 1 goto :error

echo Building portable VietSub Studio...
python -m PyInstaller --noconfirm --clean --distpath dist_portable --workpath build_portable "VietSub Studio Portable.spec"
if errorlevel 1 goto :error

echo Packaging release assets in the updater format...
python tools\make_release.py --exe "dist_portable\VietSub Studio.exe" --output release --clean
if errorlevel 1 goto :error

set RELEASE_DIR=release
python -m unittest discover -s tests -p test_release_contract.py
if errorlevel 1 goto :error

echo.
echo Build complete: dist_portable\VietSub Studio.exe
echo Upload both files in the release folder to the GitHub Release unchanged.
pause
exit /b 0

:error
echo.
echo Build failed. See the error above.
pause
exit /b 1
