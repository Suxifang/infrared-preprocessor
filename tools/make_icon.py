"""Render the original vector application icon; no external artwork or fonts."""
import io
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image
from PySide6 import QtCore, QtGui, QtSvg, QtWidgets

root = Path(__file__).resolve().parents[1]
app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
renderer = QtSvg.QSvgRenderer(str(root / "assets" / "app.svg"))
canvas = QtGui.QImage(256, 256, QtGui.QImage.Format.Format_ARGB32)
canvas.fill(QtCore.Qt.GlobalColor.transparent)
painter = QtGui.QPainter(canvas)
renderer.render(painter)
painter.end()
buffer = QtCore.QBuffer()
buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
canvas.save(buffer, "PNG")
Image.open(io.BytesIO(bytes(buffer.data()))).save(root / "assets" / "app.ico", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
print("Application icon generated")
