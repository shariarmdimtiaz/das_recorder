# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


block_cipher = None
project_root = Path.cwd()


a = Analysis(
    ["run_app.py"],
    pathex=[str(project_root), str(project_root / "src")],
    binaries=[],
    datas=[
        (str(project_root / "config" / "default_config.json"), "config"),
        (str(project_root / "src" / "das_recorder_ui" / "assets" / "das_icon.ico"), "das_recorder_ui/assets"),
    ],
    hiddenimports=[
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "pyqtgraph",
        "numpy",
        "h5py",
        "nptdms",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "OpenGL",
        "PIL",
        "cryptography",
        "cv2",
        "fsspec",
        "jedi",
        "jsonschema",
        "matplotlib",
        "nbformat",
        "notebook",
        "pandas",
        "parso",
        "scipy",
        "sklearn",
        "sympy",
        "tensorboard",
        "tensorflow",
        "torch",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DASRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "src" / "das_recorder_ui" / "assets" / "das_icon.ico"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DASRecorder",
)
