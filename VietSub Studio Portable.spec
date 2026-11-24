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
    # yt-dlp is imported lazily; yt_dlp_ejs holds the YouTube challenge scripts.
    hiddenimports=['webview', 'yt_dlp', 'yt_dlp_ejs', 'Cryptodome.PublicKey.ECC', 'Cryptodome.Signature.eddsa', 'Cryptodome.Hash.SHA512'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Heavy speech/separation runtimes execute in an external Python worker only.
    excludes=['edge_tts', 'audio_separator', 'onnxruntime', 'torch', 'torchaudio', 'demucs', 'Cryptodome.SelfTest'],
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
