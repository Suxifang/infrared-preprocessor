# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

root = Path(SPECPATH)
app_name = "红外预处理"
excluded = ["matplotlib", "pandas", "torch", "sklearn", "numba", "llvmlite", "cv2", "h5py",
            "IPython", "jupyter", "pytest", "tkinter", "PyQt5", "PyQt6", "PySide2"]
a = Analysis(
    [str(root / "app.py")], pathex=[str(root)], binaries=[],
    datas=[(str(root / "assets"), "assets"), (str(root / "licenses"), "licenses"),
           (str(root / "THIRD_PARTY_NOTICES.md"), ".")],
    hiddenimports=["PySide6.QtSvg", "PySide6.QtPrintSupport"],
    excludes=excluded, noarchive=False,
)
# Qt 6.12's Windows wheel imports the Windows system ICU ABI (unsuffixed symbols).
# PyInstaller can otherwise pick a conflicting ICU from Torch/Anaconda's DLL search path.
# Windows supplies the required icuuc.dll; never package another vendor's version.
conflicting_icu = {"icuuc.dll", "icudt78.dll"}
a.binaries = [item for item in a.binaries if Path(item[0]).name.casefold() not in conflicting_icu]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name=app_name, debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, icon=str(root / "assets/app.ico"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=app_name)
