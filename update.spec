# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['UI\\update.py'],
    pathex=[],
    binaries=[],
    datas=[('UI\\assets\\logo.ico', 'assets')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'test', 'unittest', 'email', 'xml', 'pydoc', 'pydoc_data'],
    noarchive=False,
    optimize=2,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='update',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    upx_exclude=['python311.dll', 'python3.dll', 'ucrtbase.dll', 'vcruntime140.dll'],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['UI\\assets\\logo.ico'],
)
