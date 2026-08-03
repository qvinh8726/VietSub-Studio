# -*- mode: python ; coding: utf-8 -*-

datas = [
    ('templates', 'templates'),
    ('static', 'static'),
    ('avatar.jpg', '.'),
    ('avatar.ico', '.'),
]


a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['webview'],
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
