from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*")
    parser.add_argument("--smoke-test", metavar="OUTPUT_FOLDER")
    parser.add_argument("--project")
    parser.add_argument("--update-ready", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
    from PySide6 import QtCore, QtGui, QtWidgets
    from infrared_desktop.ui import MainWindow, STYLE
    app = QtWidgets.QApplication(sys.argv[:1])
    # Qt's Windows offscreen backend starts with an empty font database.
    # Load installed system fonts for previews/tests; no font files are redistributed.
    if not QtGui.QFontDatabase.families():
        font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        for name in ("segoeui.ttf", "arial.ttf", "simhei.ttf", "simsun.ttc", "msyh.ttc", "msyhbd.ttc"):
            file = font_dir / name
            if file.exists():
                QtGui.QFontDatabase.addApplicationFont(str(file))
    app.setApplicationName("红外预处理")
    app.setOrganizationName("LocalResearch")
    app.setStyle("Fusion")
    app.setFont(QtGui.QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(STYLE)
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    app.setWindowIcon(QtGui.QIcon(str(resource_root / "assets" / "app.svg")))
    window = MainWindow()
    window.show()
    if args.project:
        restored = window.open_project_path(args.project)
    elif args.files:
        window.add_paths(args.files)
    if args.update_ready and (not args.project or restored):
        from infrared_desktop import __version__
        from infrared_desktop.updates import app_data_root
        ready = Path(args.update_ready).resolve()
        if ready.parent != (app_data_root() / "updates").resolve() or not ready.name.startswith("ready-"):
            raise ValueError("Invalid update readiness marker")
        def signal_ready():
            ready.parent.mkdir(parents=True, exist_ok=True)
            temporary = ready.with_suffix(".tmp")
            temporary.write_text(json.dumps({"pid": os.getpid(), "version": __version__}), encoding="utf-8")
            os.replace(temporary, ready)
        QtCore.QTimer.singleShot(400, signal_ready)
    if args.smoke_test:
        from infrared_desktop.smoke import run_smoke
        QtCore.QTimer.singleShot(100, lambda: run_smoke(app, window, Path(args.smoke_test)))
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        report = traceback.format_exc()
        try:
            import tempfile
            p = Path(tempfile.gettempdir()) / "infrared-preprocess-error.txt"
            p.write_text(report, encoding="utf-8")
            if "--smoke-test" not in sys.argv:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, f"启动失败，错误记录：\n{p}\n\n{report[-1200:]}", "红外预处理", 16)
        finally:
            if "--smoke-test" in sys.argv:
                sys.exit(1)
            raise
