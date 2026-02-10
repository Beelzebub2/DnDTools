# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['UI\\app.py'],
    pathex=[],
    binaries=[],
    datas=[('UI\\networking\\protos', 'networking/protos'), ('UI\\templates', 'templates'), ('UI\\static', 'static'), ('build\\assets_no_icons', 'assets')],
    hiddenimports=['clr', 'asyncio.events', 'asyncio.windows_events', 'asyncio.windows_utils', 'pyshark.capture.live_capture', 'pyshark.capture.capture', 'pyshark.tshark.tshark'],
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
    [],
    exclude_binaries=True,
    name='DnDTools',
    debug=False,
    bootloader_ignore_signals=False,
    strip=True,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['UI\\assets\\logo.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=True,
    upx=True,
    upx_exclude=[],
    name='DnDTools',
)
