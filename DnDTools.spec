# -*- mode: python ; coding: utf-8 -*-


from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['UI\\app.py'],
    pathex=[],
    binaries=[],
    datas=[('UI\\networking\\protos', 'networking/protos'), ('UI\\templates', 'templates'), ('UI\\static', 'static'), ('build\\assets_no_icons', 'assets')],
    hiddenimports=[
        'asyncio.events',
        'asyncio.windows_events',
        'asyncio.windows_utils',
    ] + collect_submodules('pyshark'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # --- unused stdlib modules ---
        'tkinter', '_tkinter',
        'test',
        'xmlrpc',
        'pydoc', 'pydoc_data',
        'lib2to3', 'ensurepip', 'venv',
        'idlelib', 'turtledemo', 'turtle',
        'curses',
        'multiprocessing.popen_spawn_posix',
        'multiprocessing.popen_fork',
        'multiprocessing.popen_forkserver',
        # --- heavy packages not used at runtime ---
        'scipy',
        'matplotlib',
        'IPython', 'notebook', 'jupyter',
        'pandas',
        # --- numpy test / f2py bloat ---
        'numpy.f2py', 'numpy.testing', 'numpy.distutils',
        # --- sklearn test bloat ---
        'sklearn.tests', 'sklearn.datasets',
        # --- Pillow unused codecs ---
        'PIL.ImageTk',
    ],
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
    strip=False,
    upx=False,
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
    strip=False,
    upx=False,
    upx_exclude=[],
    name='DnDTools',
)
