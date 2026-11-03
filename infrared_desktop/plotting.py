from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from .core import Result

pg.setConfigOptions(antialias=True, background="w", foreground="#526176")


class SpectrumCanvas(pg.GraphicsLayoutWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground("w")
        self.setMinimumSize(400, 400)
        self.ci.layout.setContentsMargins(12, 5, 18, 12)
        self.ci.layout.setVerticalSpacing(24)
        self.top = self.addPlot(row=0, col=0)
        self.bottom = self.addPlot(row=1, col=0)
        self.bottom.setXLink(self.top)
        self.result = None
        self.selection = None
        self.current_x = None
        for plot in (self.top, self.bottom):
            plot.showGrid(x=True, y=True, alpha=.13)
            plot.setMenuEnabled(False)
            plot.hideButtons()
            plot.getViewBox().setDefaultPadding(.045)
            for side in ("left", "bottom"):
                axis = plot.getAxis(side)
                axis.setPen(pg.mkPen("#D7DFE9"))
                axis.setTextPen("#64748B")
                axis.setStyle(tickFont=QtGui.QFont("Microsoft YaHei UI", 9))
            plot.setLabel("left", "输入信号", color="#64748B", size="10pt")
        self.top.setTitle("原始信号与估计背景", color="#334155", size="11pt")
        self.bottom.setTitle("校正结果", color="#334155", size="11pt")
        self.top.addLegend(offset=(14, 8), labelTextColor="#526176", labelTextSize="10pt")
        self.bottom.addLegend(offset=(14, 8), labelTextColor="#526176", labelTextSize="10pt")
        self.raw_curve = self.top.plot(pen=pg.mkPen("#8796AD", width=1.3), name="原始信号")
        self.baseline_curve = self.top.plot(pen=pg.mkPen("#E6A23C", width=1.9), name="估计背景")
        self.corrected_curve = self.bottom.plot(pen=pg.mkPen("#167BBA", width=1.7), name="校正后信号")
        self.zero = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen("#BCC7D6", style=QtCore.Qt.PenStyle.DashLine))
        self.bottom.addItem(self.zero)
        self.set_axis("sample", "")

    def set_axis(self, kind, unit):
        label = "时间" if kind == "time" else "采样坐标"
        if kind == "time" and unit:
            label += f"（{unit}）"
        for plot in (self.top, self.bottom):
            plot.setLabel("bottom", label, color="#64748B", size="10pt")
        self.bottom.setLabel("left", "校正信号（输入单位）", color="#64748B", size="10pt")

    def show_raw(self, x, y, settings):
        self.result = None
        self.current_x = np.asarray(x)
        self.clear_selection()
        self.set_axis(settings.x_kind, settings.x_unit)
        self.raw_curve.setData(x, y, connect="finite")
        self.baseline_curve.setData([], [])
        self.corrected_curve.setData([], [])
        self.top.enableAutoRange()
        self.bottom.enableAutoRange()

    def show_result(self, result: Result):
        self.result = result
        self.current_x = result.x
        self.clear_selection()
        self.set_axis(result.settings.x_kind, result.settings.x_unit)
        self.raw_curve.setData(result.x, result.original)
        self.baseline_curve.setData(result.x, result.background)
        self.corrected_curve.setData(result.x, result.corrected)
        self.top.enableAutoRange()
        self.bottom.enableAutoRange()

    def select_region(self):
        if self.current_x is None or len(self.current_x) < 2:
            return None
        self.clear_selection()
        lo, hi = sorted((float(self.current_x[0]), float(self.current_x[-1])))
        self.selection = pg.LinearRegionItem(
            values=(lo + .12 * (hi - lo), hi - .12 * (hi - lo)),
            brush=pg.mkBrush(14, 116, 180, 25), pen=pg.mkPen("#4BA3CA"),
            bounds=(lo, hi))
        self.top.addItem(self.selection)
        return self.selection

    def clear_selection(self):
        if self.selection is not None:
            self.top.removeItem(self.selection)
            self.selection = None

    def reset_view(self):
        self.top.enableAutoRange()
        self.bottom.enableAutoRange()


def export_preview(result, path):
    from pyqtgraph.exporters import ImageExporter
    canvas = SpectrumCanvas()
    canvas.setAttribute(QtCore.Qt.WidgetAttribute.WA_DontShowOnScreen, True)
    canvas.resize(1400, 900)
    canvas.show_result(result)
    canvas.top.setTitle(f"{Path(result.source).parent.name} / {Path(result.source).name} · 原始信号与估计背景",
                        color="#334155", size="11pt")
    action = "反转后" if result.sign == -1 else "原极性"
    canvas.bottom.setTitle(f"校正结果 · {action} · {result.settings.method} · 未标定波数",
                           color="#334155", size="11pt")
    # Polish the hidden widget so Qt lays out both plots at the requested size.
    # Export the layout item, not an enlarged scene with unused default geometry.
    canvas.show()
    QtWidgets.QApplication.processEvents(QtCore.QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    canvas.ci.layout.activate()
    exporter = ImageExporter(canvas.ci)
    exporter.parameters()["width"] = 1800
    exporter.parameters()["background"] = QtGui.QColor("white")
    exporter.export(str(path))
    canvas.close()
    canvas.deleteLater()
