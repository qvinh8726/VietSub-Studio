# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [('templates', 'templates'), ('avatar.jpg', '.'), ('avatar.ico', '.')]
binaries = []
hiddenimports = []
webview_data, webview_binaries, webview_hiddenimports = collect_all('webview')
datas += webview_data
binaries += webview_binaries
hiddenimports += webview_hiddenimports

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VietSub Studio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon='avatar.ico',
    codesign_identity=None,
    entitlements_file=None,
)
