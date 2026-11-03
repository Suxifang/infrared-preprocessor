from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, replace, field
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as W

from . import __version__
from .core import DataError, Result, Settings, detect_polarity, discover_files, infer_metadata, process, read_table
from .plotting import SpectrumCanvas, export_preview
from .storage import export_results, load_project, save_project


STYLE = """
QWidget { color: #25344A; font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 12px; }
QMainWindow, QWidget#workspace { background: #F3F6FA; }
QWidget#header { background: white; border-bottom: 1px solid #E4EAF2; }
QFrame#card { background: white; border: 1px solid #E1E8F0; border-radius: 12px; }
QLabel#title { font-size: 23px; font-weight: 700; color: #1C3046; }
QLabel#section { font-size: 15px; font-weight: 600; color: #263D55; }
QLabel#muted { color: #7A899B; font-size: 11px; }
QLabel#badge { color: #167BBA; background: #EAF5FC; border-radius: 10px; padding: 5px 10px; }
QLabel#step { font-size: 12px; color: #728298; padding: 7px 12px; }
QLabel#activeStep { font-size: 12px; color: #167BBA; background: #EAF5FC; border-radius: 8px; padding: 7px 12px; font-weight: 600; }
QLabel#notice { color: #526176; background: #F5F8FC; border: 1px solid #E4EAF2; border-radius: 7px; padding: 9px; }
QPushButton { background: white; border: 1px solid #D8E1EC; border-radius: 7px; padding: 8px 12px; min-height: 17px; }
QPushButton:hover { background: #F1F7FC; border-color: #96B9D3; }
QPushButton:pressed { background: #E3F0FA; }
QPushButton:disabled { color: #A4B0BE; border-color: #E5EAF0; background: #F7F9FC; }
QPushButton#primary { background: #147FB8; border-color: #147FB8; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #086FA7; }
QPushButton#primary:disabled { background: #AFCEDC; border-color: #AFCEDC; color: #F5FAFC; }
QPushButton#soft { background: #EFF7FC; color: #147FB8; border-color: #DCECF5; }
QToolButton { border: none; padding: 8px 0px; text-align: left; color: #53677D; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: white; border: 1px solid #D9E2ED; border-radius: 6px; padding: 6px 8px; min-height: 20px; selection-background-color: #CBE7F7; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #3393C3; }
QComboBox::drop-down { width: 21px; border: 0; }
QComboBox QAbstractItemView { background: white; padding: 5px; selection-background-color: #E5F3FC; }
QTableWidget { border: none; background: white; alternate-background-color: #FAFCFE; gridline-color: #F0F4F8; selection-background-color: #E7F3FC; selection-color: #244D6B; outline: none; }
QTableWidget::item { padding: 7px 4px; border-bottom: 1px solid #F0F4F8; }
QHeaderView::section { background: #F8FAFD; color: #7A899B; border: none; padding: 9px 5px; font-weight: 400; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #F7F9FC; width: 7px; margin: 0px; }
QScrollBar::handle:vertical { background: #CBD5E1; border-radius: 3px; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QCheckBox { spacing: 7px; }
QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #C9D4E2; border-radius: 3px; background: white; }
QCheckBox::indicator:checked { background: #147FB8; border-color: #147FB8; image: none; }
QProgressBar { border: none; background: #E5EDF5; border-radius: 3px; min-height: 6px; max-height: 6px; }
QProgressBar::chunk { background: #2895C1; border-radius: 3px; }
QSplitter::handle { background: #F3F6FA; width: 12px; }
QStatusBar { background: #F3F6FA; color: #7A899B; border-top: 1px solid #E4EAF2; }
QMenuBar, QMenu { background: white; }
QToolTip { background: white; color: #334155; border: 1px solid #D6E2EC; padding: 6px; }
"""


@dataclass
class Entry:
    path: Path
    settings: Settings
    checked: bool = True
    result: Result | None = None
    error: str = ""
    table: object = None
    metadata: dict = field(default_factory=dict)


class Worker(QtCore.QThread):
    item_ready = QtCore.Signal(str, object, str)
    progress = QtCore.Signal(int, int)

    def __init__(self, jobs, metadata, parent=None):
        super().__init__(parent)
        self.jobs, self.metadata = jobs, dict(metadata)
        self.stop_event = threading.Event()

    def run(self):
        for i, (path, settings, metadata) in enumerate(self.jobs, 1):
            if self.stop_event.is_set():
                break
            try:
                table = read_table(path)
                result = process(table, settings, metadata)
                self.item_ready.emit(str(path), result, "")
            except Exception as e:
                self.item_ready.emit(str(path), None, str(e))
            self.progress.emit(i, len(self.jobs))


def text_label(text, name=None):
    label = W.QLabel(text)
    if name:
        label.setObjectName(name)
    return label


def button(text, slot, style=None):
    b = W.QPushButton(text)
    if style:
        b.setObjectName(style)
    b.clicked.connect(slot)
    return b


class MainWindow(W.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"红外预处理 · {__version__}")
        self.resize(1480, 940)
        self.setMinimumSize(1100, 760)
        self.entries: list[Entry] = []
        self.current_index = -1
        self.worker = None
        self.loading = False
        self._last_polarity_message = ""
        self.update_dialog = None
        self.settings_store = QtCore.QSettings("LocalResearch", "InfraredPreprocessor")
        self._make_ui()
        self.setAcceptDrops(True)
        self.statusBar().showMessage("导入DAT文件或文件夹开始处理。可将文件直接拖入窗口。")

    @property
    def current(self):
        return self.entries[self.current_index] if 0 <= self.current_index < len(self.entries) else None

    def _make_ui(self):
        workspace = W.QWidget()
        workspace.setObjectName("workspace")
        self.setCentralWidget(workspace)
        outer = W.QVBoxLayout(workspace)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = W.QWidget()
        header.setObjectName("header")
        h = W.QHBoxLayout(header)
        h.setContentsMargins(25, 17, 25, 17)
        brand = W.QVBoxLayout()
        title_row = W.QHBoxLayout()
        title_row.addWidget(text_label("红外预处理", "title"))
        title_row.addWidget(text_label(f"测试版  {__version__}", "badge"))
        title_row.addStretch()
        brand.addLayout(title_row)
        brand.addWidget(text_label("把每一段信号处理清楚", "muted"))
        h.addLayout(brand)
        h.addStretch()
        for title, object_name in [("01  导入数据", "step"), ("02  反转与基线", "activeStep"), ("03  导出结果", "step")]:
            h.addWidget(text_label(title, object_name))
        h.addSpacing(15)
        self.project_open = button("打开项目", self.open_project)
        self.project_save = button("保存项目", self.save_project)
        h.addWidget(self.project_open)
        h.addWidget(self.project_save)
        self.update_button = button("检查更新", self.show_updates)
        h.addWidget(self.update_button)
        outer.addWidget(header)
        body = W.QWidget()
        body_l = W.QVBoxLayout(body)
        body_l.setContentsMargins(20, 19, 20, 13)
        body_l.setSpacing(12)
        split = W.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.setHandleWidth(12)
        split.setChildrenCollapsible(False)
        split.addWidget(self._file_card())
        split.addWidget(self._plot_card())
        split.addWidget(self._controls_card())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 0)
        split.setSizes([260, 850, 306])
        body_l.addWidget(split, 1)
        bottom = W.QHBoxLayout()
        self.count_label = text_label("还没有导入数据", "muted")
        bottom.addWidget(self.count_label)
        self.progress = W.QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        bottom.addWidget(self.progress, 1)
        bottom.addStretch()
        self.cancel_button = button("停止批量", self.cancel_processing)
        self.cancel_button.hide()
        bottom.addWidget(self.cancel_button)
        self.export_button = button("导出处理结果", self.export_dialog, "primary")
        self.export_button.setMinimumWidth(160)
        bottom.addWidget(self.export_button)
        body_l.addLayout(bottom)
        outer.addWidget(body, 1)
        help_menu = self.menuBar().addMenu("帮助")
        help_menu.addAction("操作说明", self.show_help)
        help_menu.addAction("检查更新…", self.show_updates)
        help_menu.addAction("开源组件与许可", self.show_licenses)

    def _card(self):
        card = W.QFrame()
        card.setObjectName("card")
        layout = W.QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)
        return card, layout

    def _file_card(self):
        card, layout = self._card()
        card.setMinimumWidth(235)
        row = W.QHBoxLayout()
        row.addWidget(text_label("数据文件", "section"))
        row.addStretch()
        self.file_count = text_label("0", "badge")
        row.addWidget(self.file_count)
        layout.addLayout(row)
        row = W.QHBoxLayout()
        self.import_files_button = button("＋ 文件", self.import_files, "soft")
        self.import_folder_button = button("＋ 文件夹", self.import_folder)
        row.addWidget(self.import_files_button)
        row.addWidget(self.import_folder_button)
        layout.addLayout(row)
        self.search = W.QLineEdit()
        self.search.setPlaceholderText("搜索文件或文件夹")
        self.search.textChanged.connect(self.filter_files)
        layout.addWidget(self.search)
        self.files = W.QTableWidget(0, 2)
        self.files.setHorizontalHeaderLabels(["文件", "状态"])
        self.files.verticalHeader().hide()
        self.files.setShowGrid(False)
        self.files.setAlternatingRowColors(True)
        self.files.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.files.setSelectionMode(W.QAbstractItemView.SelectionMode.SingleSelection)
        self.files.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.files.horizontalHeader().setSectionResizeMode(0, W.QHeaderView.ResizeMode.Stretch)
        self.files.horizontalHeader().setSectionResizeMode(1, W.QHeaderView.ResizeMode.Fixed)
        self.files.setColumnWidth(1, 69)
        self.files.currentCellChanged.connect(self.select_row)
        self.files.itemChanged.connect(self.checkbox_changed)
        layout.addWidget(self.files, 1)
        row = W.QHBoxLayout()
        row.addWidget(button("全选", lambda: self.select_all(True)))
        row.addWidget(button("取消勾选", lambda: self.select_all(False)))
        row.addWidget(button("移除", self.remove_current))
        layout.addLayout(row)
        self.demo_button = button("载入演示数据", self.load_demo)
        layout.addWidget(self.demo_button)
        return card

    def _plot_card(self):
        card, layout = self._card()
        row = W.QHBoxLayout()
        row.addWidget(text_label("曲线预览", "section"))
        row.addStretch()
        row.addWidget(text_label("采样坐标 · 未标定波数", "muted"))
        self.reset_button = button("重置视图", lambda: self.canvas.reset_view())
        row.addWidget(self.reset_button)
        layout.addLayout(row)
        self.filename = text_label("选择一个文件开始", "muted")
        self.filename.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.filename)
        self.canvas = SpectrumCanvas()
        self.empty = W.QWidget()
        e = W.QVBoxLayout(self.empty)
        e.addStretch()
        lead = text_label("从一段信号开始", "title")
        lead.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        e.addWidget(lead)
        sub = text_label("导入 DAT、TXT 或 CSV\n查看原始曲线，再预览反转与基线处理结果", "muted")
        sub.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #7A899B; font-size: 13px; line-height: 1.7;")
        e.addWidget(sub)
        e.addSpacing(20)
        row = W.QHBoxLayout()
        row.addStretch()
        row.addWidget(button("导入数据文件", self.import_files, "primary"))
        row.addStretch()
        e.addLayout(row)
        e.addStretch()
        self.plot_stack = W.QStackedWidget()
        self.plot_stack.addWidget(self.empty)
        self.plot_stack.addWidget(self.canvas)
        layout.addWidget(self.plot_stack, 1)
        self.statistics = text_label("同一份计算结果用于预览与导出", "muted")
        layout.addWidget(self.statistics)
        self.notice = text_label("上图核对原始信号与估计背景，下图核对校正结果。", "notice")
        self.notice.setWordWrap(True)
        self.notice.setMaximumHeight(100)
        layout.addWidget(self.notice)
        return card

    def _controls_card(self):
        card, layout = self._card()
        card.setMinimumWidth(287)
        card.setMaximumWidth(350)
        layout.addWidget(text_label("处理设置", "section"))
        area = W.QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = W.QWidget()
        content.setStyleSheet("background: white;")
        f = W.QVBoxLayout(content)
        f.setContentsMargins(0, 0, 5, 0)
        f.setSpacing(10)
        f.addWidget(text_label("吸收峰方向"))
        self.polarity = W.QComboBox()
        self.polarity.addItem("默认反转（向下 → 向上）", "down")
        self.polarity.addItem("已经向上，跳过反转", "up")
        self.polarity.addItem("自动识别（不明确时提示）", "auto")
        self.polarity.setToolTip("每次从输入原信号重新计算；不会在前次处理结果上反复反转。")
        f.addWidget(self.polarity)
        self.polarity_hint = text_label("默认按向下吸收峰处理。", "muted")
        self.polarity_hint.setWordWrap(True)
        f.addWidget(self.polarity_hint)
        f.addSpacing(6)
        f.addWidget(text_label("基线校正"))
        self.method = W.QComboBox()
        self.method.addItem("arPLS · 自动估计背景", "arpls")
        self.method.addItem("AsLS · 非对称最小二乘", "asls")
        self.method.addItem("跳过（已校正或只反转）", "none")
        f.addWidget(self.method)
        row = W.QHBoxLayout()
        row.addWidget(text_label("背景平滑度"))
        self.smooth = W.QDoubleSpinBox()
        self.smooth.setRange(2., 12.)
        self.smooth.setSingleStep(.25)
        self.smooth.setDecimals(2)
        self.smooth.setValue(5.)
        self.smooth.setToolTip("数值为log10 λ（1000点参考长度）。值越大，估计背景越平滑；请核对宽峰与峰翼。")
        self.smooth.setFixedWidth(90)
        row.addWidget(self.smooth)
        f.addLayout(row)
        note = text_label("较小：更贴合起伏   /   较大：更平滑", "muted")
        note.setWordWrap(True)
        f.addWidget(note)
        f.addSpacing(6)
        f.addWidget(text_label("有效区间 · 数值行号"))
        row = W.QHBoxLayout()
        self.start = W.QSpinBox()
        self.start.setRange(1, 100_000_000)
        self.start.setValue(1)
        self.stop = W.QSpinBox()
        self.stop.setRange(0, 100_000_000)
        self.stop.setSpecialValueText("到末尾")
        row.addWidget(self.start, 1)
        row.addWidget(text_label("—", "muted"))
        row.addWidget(self.stop, 1)
        f.addLayout(row)
        row = W.QHBoxLayout()
        self.select_region_button = button("图上选区", self.toggle_region)
        self.use_region_button = button("采用选区", self.apply_region)
        row.addWidget(self.select_region_button)
        row.addWidget(self.use_region_button)
        f.addLayout(row)
        n = text_label("多次扫描的记录，请先选一个有效周期。", "muted")
        n.setWordWrap(True)
        f.addWidget(n)
        self.advanced_toggle, advanced, a = self._fold("列设置与高级参数")
        self.x_column = W.QComboBox()
        self.x_column.addItem("第1列", 0)
        self.y_column = W.QComboBox()
        self.y_column.addItem("第2列", 1)
        a.addRow("横坐标列", self.x_column)
        a.addRow("信号列", self.y_column)
        self.x_kind = W.QComboBox()
        self.x_kind.addItem("采样序号 / 原采样坐标", "sample")
        self.x_kind.addItem("已知时间", "time")
        a.addRow("横坐标含义", self.x_kind)
        self.x_unit = W.QLineEdit()
        self.x_unit.setPlaceholderText("时间单位，例如 s、ms")
        a.addRow("时间单位", self.x_unit)
        self.scale_lambda = W.QCheckBox("按点数换算平滑强度")
        self.scale_lambda.setChecked(True)
        self.scale_lambda.setToolTip("实际 λ = 10^平滑度 × ((有效点数−1)/999)^4。关闭则直接使用10^平滑度。")
        a.addRow(self.scale_lambda)
        self.asymmetry = W.QDoubleSpinBox()
        self.asymmetry.setDecimals(4)
        self.asymmetry.setRange(.0001, .9999)
        self.asymmetry.setSingleStep(.005)
        self.asymmetry.setValue(.01)
        a.addRow("AsLS 非对称参数", self.asymmetry)
        f.addWidget(self.advanced_toggle)
        f.addWidget(advanced)
        self.metadata_toggle, meta, m = self._fold("样品与采集信息")
        self.sample = W.QLineEdit("水样")
        self.species = W.QLineEdit("水")
        self.pressure_unit = W.QComboBox()
        self.pressure_unit.addItems(["Pa", "kPa", "mbar", "Torr", "未知"])
        self.gas_temperature = W.QLineEdit()
        self.gas_temperature.setPlaceholderText("未知可留空")
        m.addRow("样品名称", self.sample)
        m.addRow("物质", self.species)
        m.addRow("压力单位", self.pressure_unit)
        m.addRow("气体温度 / K", self.gas_temperature)
        self.laser_temperature = W.QLineEdit()
        self.laser_current = W.QLineEdit()
        self.pressure = W.QLineEdit()
        for edit in (self.laser_temperature, self.laser_current, self.pressure):
            edit.setPlaceholderText("未知可留空")
            edit.setValidator(QtGui.QDoubleValidator(-1e10, 1e10, 6, edit))
            edit.editingFinished.connect(self.entry_metadata_changed)
        m.addRow("激光温度 / ℃", self.laser_temperature)
        m.addRow("电流 / mA", self.laser_current)
        m.addRow("气压", self.pressure)
        self.parameters_confirmed = W.QCheckBox("已核对文件名参数与单位")
        self.parameters_confirmed.toggled.connect(self.entry_metadata_changed)
        m.addRow(self.parameters_confirmed)
        self.inferred_label = text_label("文件名参数待识别；不用于自动标定。", "muted")
        self.inferred_label.setWordWrap(True)
        m.addRow(self.inferred_label)
        f.addWidget(self.metadata_toggle)
        f.addWidget(meta)
        f.addStretch()
        area.setWidget(content)
        layout.addWidget(area, 1)
        self.preview_button = button("计算当前预览", self.preview_current, "primary")
        self.batch_button = button("批量处理勾选文件", self.process_selected, "soft")
        self.apply_all_button = button("当前设置应用到勾选文件", self.apply_settings_selected)
        layout.addWidget(self.preview_button)
        layout.addWidget(self.batch_button)
        layout.addWidget(self.apply_all_button)
        note = text_label("原文件保留 · 不自动归一化或平滑信号", "muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.settings_widgets = [self.polarity, self.method, self.smooth, self.start, self.stop,
                                 self.x_column, self.y_column, self.x_kind, self.x_unit,
                                 self.scale_lambda, self.asymmetry]
        for combo in (self.polarity, self.method, self.x_column, self.y_column, self.x_kind,
                      self.pressure_unit):
            combo.setMinimumWidth(0)
            combo.setMinimumContentsLength(9)
            combo.setSizeAdjustPolicy(W.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Fixed)
        for widget in self.settings_widgets:
            if isinstance(widget, W.QComboBox):
                widget.currentIndexChanged.connect(self.settings_changed)
            elif isinstance(widget, (W.QSpinBox, W.QDoubleSpinBox)):
                widget.valueChanged.connect(self.settings_changed)
            elif isinstance(widget, W.QCheckBox):
                widget.toggled.connect(self.settings_changed)
            else:
                widget.textChanged.connect(self.settings_changed)
        self.metadata_widgets = [self.sample, self.species, self.pressure_unit, self.gas_temperature,
                                 self.laser_temperature, self.laser_current, self.pressure,
                                 self.parameters_confirmed]
        for edit in (self.sample, self.species, self.gas_temperature):
            edit.textChanged.connect(self.common_metadata_changed)
        self.pressure_unit.currentTextChanged.connect(self.common_metadata_changed)
        return card

    def _fold(self, title):
        toggle = W.QToolButton()
        toggle.setText(title)
        toggle.setToolButtonStyle(QtCore.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
        toggle.setCheckable(True)
        panel = W.QWidget()
        layout = W.QFormLayout(panel)
        layout.setContentsMargins(0, 3, 0, 5)
        layout.setSpacing(8)
        layout.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.setRowWrapPolicy(W.QFormLayout.RowWrapPolicy.WrapLongRows)
        panel.hide()
        def expand(value):
            panel.setVisible(value)
            toggle.setArrowType(QtCore.Qt.ArrowType.DownArrow if value else QtCore.Qt.ArrowType.RightArrow)
        toggle.toggled.connect(expand)
        return toggle, panel, layout

    def metadata(self):
        return {"sample_name": self.sample.text().strip(), "species": self.species.text().strip(),
                "pressure_unit": self.pressure_unit.currentText(),
                "gas_temperature_K": self.gas_temperature.text().strip() or None,
                "filename_parameters_confirmed": False}

    def metadata_for_entry(self, entry):
        return {**self.metadata(), **entry.metadata}

    def common_metadata_changed(self, *_):
        if self.loading or self.worker is not None:
            return
        for entry in self.entries:
            if entry.result is not None:
                entry.result.metadata.update(self.metadata_for_entry(entry))

    def entry_metadata_changed(self, *_):
        if self.loading or self.current is None or self.worker is not None:
            return
        for key, edit in [("temperature", self.laser_temperature),
                          ("current", self.laser_current), ("pressure", self.pressure)]:
            value = edit.text().strip()
            if value:
                try:
                    self.current.metadata[key] = float(value)
                except ValueError:
                    return
            else:
                self.current.metadata[key] = None
        self.current.metadata["filename_parameters_confirmed"] = self.parameters_confirmed.isChecked()
        if self.current.result:
            self.current.result.metadata.update(self.current.metadata)

    def gather_settings(self):
        return Settings(x_column=self.x_column.currentData(), y_column=self.y_column.currentData(),
                        polarity=self.polarity.currentData(), method=self.method.currentData(),
                        log_lambda=self.smooth.value(), scale_lambda=self.scale_lambda.isChecked(),
                        asymmetry=self.asymmetry.value(), start=self.start.value(), stop=self.stop.value(),
                        x_kind=self.x_kind.currentData(), x_unit=self.x_unit.text().strip())

    def populate_settings(self, entry):
        self.loading = True
        s = entry.settings
        columns = entry.table.columns if entry.table is not None else max(2, s.y_column + 1, (s.x_column or 0) + 1)
        self.x_column.clear()
        self.x_column.addItem("自动生成行号", None)
        self.y_column.clear()
        for i in range(columns):
            self.x_column.addItem(f"第{i + 1}列（{chr(65 + i) if i < 26 else i + 1}）", i)
            self.y_column.addItem(f"第{i + 1}列（{chr(65 + i) if i < 26 else i + 1}）", i)
        for widget, value in [(self.x_column, s.x_column), (self.y_column, s.y_column),
                              (self.polarity, s.polarity), (self.method, s.method), (self.x_kind, s.x_kind)]:
            index = widget.findData(value)
            widget.setCurrentIndex(max(0, index))
        self.start.setValue(s.start)
        self.stop.setValue(s.stop)
        self.smooth.setValue(s.log_lambda)
        self.scale_lambda.setChecked(s.scale_lambda)
        self.asymmetry.setValue(s.asymmetry)
        self.x_unit.setText(s.x_unit)
        meta = {**infer_metadata(entry.path), **entry.metadata}
        for key, edit in [("temperature", self.laser_temperature),
                          ("current", self.laser_current), ("pressure", self.pressure)]:
            value = meta.get(key)
            edit.setText("" if value is None else f"{value:g}")
        self.parameters_confirmed.setChecked(bool(meta.get("filename_parameters_confirmed", False)))
        self.loading = False
        self.method_controls()

    def method_controls(self):
        enabled = self.method.currentData() != "none"
        self.smooth.setEnabled(enabled and self.worker is None)
        self.scale_lambda.setEnabled(enabled and self.worker is None)
        self.asymmetry.setEnabled(self.method.currentData() == "asls" and self.worker is None)

    def settings_changed(self, *_):
        if self.loading or self.current is None or self.worker is not None:
            return
        s = self.gather_settings()
        if s != self.current.settings:
            self.current.settings = s
            self.current.result = None
            self.current.error = ""
            self.canvas.baseline_curve.setData([], [])
            self.canvas.corrected_curve.setData([], [])
            self.statistics.setText("设置已修改 · 请重新计算预览")
            self.notice.setText("当前设置尚未计算，导出前会要求重新处理。")
            self.refresh_status(self.current_index)
        self.method_controls()

    def import_files(self):
        if self.worker:
            return
        paths, _ = W.QFileDialog.getOpenFileNames(self, "导入数据文件", self.last_dir(),
                                                  "数值文本 (*.dat *.DAT *.txt *.csv *.tsv);;所有文件 (*)")
        if paths:
            self.add_paths(paths)

    def import_folder(self):
        if self.worker:
            return
        folder = W.QFileDialog.getExistingDirectory(self, "导入数据文件夹（包括子文件夹）", self.last_dir())
        if folder:
            W.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
            try:
                paths = discover_files(folder)
            finally:
                W.QApplication.restoreOverrideCursor()
            self.add_paths(paths)
            self.statusBar().showMessage(f"已扫描文件夹；忽略outputs、最终交付、处理档案等结果目录。")

    def last_dir(self):
        return str(self.settings_store.value("last_dir", str(Path.home() / "Desktop")))

    def add_paths(self, paths, settings=None):
        known = {str(e.path).casefold() for e in self.entries}
        first = len(self.entries)
        for path in paths:
            path = Path(path).resolve()
            if not path.is_file() or str(path).casefold() in known:
                continue
            self.entries.append(Entry(path, settings or Settings()))
            known.add(str(path).casefold())
        if len(self.entries) > first:
            self.settings_store.setValue("last_dir", str(self.entries[first].path.parent))
            self.rebuild_files()
            self.files.setCurrentCell(first, 0)
        self.update_counts()

    def rebuild_files(self):
        self.loading = True
        self.files.blockSignals(True)
        self.files.setRowCount(len(self.entries))
        for i, entry in enumerate(self.entries):
            text = f"{entry.path.parent.name} / {entry.path.name}"
            item = W.QTableWidgetItem(text)
            item.setToolTip(str(entry.path))
            item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.CheckState.Checked if entry.checked else QtCore.Qt.CheckState.Unchecked)
            self.files.setItem(i, 0, item)
            self.files.setItem(i, 1, W.QTableWidgetItem())
            self.files.setRowHeight(i, 40)
            self.refresh_status(i)
        # A restored project may have the same selected row as the old table.
        # Reset the Qt selection so selecting row 0 emits currentCellChanged again.
        self.files.setCurrentCell(-1, -1)
        self.files.blockSignals(False)
        self.loading = False
        self.filter_files()

    def refresh_status(self, row):
        entry = self.entries[row]
        if entry.error:
            status, color = "异常", "#C06A36"
        elif entry.result:
            if entry.result.diagnostics["notices"]:
                status, color = "需核对", "#B8872E"
            else:
                status, color = "已处理", "#29896D"
        else:
            status, color = "待处理", "#8291A5"
        item = self.files.item(row, 1)
        if item:
            item.setText(status)
            item.setForeground(QtGui.QColor(color))
            item.setToolTip(entry.error or "\n".join(entry.result.diagnostics["notices"] if entry.result else []))
        self.update_counts()

    def update_counts(self):
        total = len(self.entries)
        selected = sum(e.checked for e in self.entries)
        done = sum(e.result is not None for e in self.entries)
        if hasattr(self, "file_count"):
            self.file_count.setText(str(total))
        if hasattr(self, "count_label"):
            self.count_label.setText(f"{total} 个文件  ·  勾选 {selected}  ·  已处理 {done}")

    def checkbox_changed(self, item):
        if not self.loading and item.column() == 0:
            self.entries[item.row()].checked = item.checkState() == QtCore.Qt.CheckState.Checked
            self.update_counts()

    def select_all(self, value):
        for row, entry in enumerate(self.entries):
            if not self.files.isRowHidden(row):
                entry.checked = value
                self.files.item(row, 0).setCheckState(
                    QtCore.Qt.CheckState.Checked if value else QtCore.Qt.CheckState.Unchecked)
        self.update_counts()

    def filter_files(self, *_):
        needle = self.search.text().strip().casefold()
        for row, entry in enumerate(self.entries):
            self.files.setRowHidden(row, needle not in str(entry.path).casefold())

    def remove_current(self):
        if self.worker or self.current is None:
            return
        i = self.current_index
        self.entries.pop(i)
        self.current_index = -1
        self.rebuild_files()
        if self.entries:
            self.files.setCurrentCell(min(i, len(self.entries) - 1), 0)
        else:
            self.plot_stack.setCurrentIndex(0)
        self.update_counts()

    def select_row(self, row, *_):
        if self.loading or not 0 <= row < len(self.entries):
            return
        self.current_index = row
        entry = self.current
        try:
            if entry.table is None and entry.path.exists():
                entry.table = read_table(entry.path)
                if entry.table.columns == 1 and entry.settings == Settings():
                    entry.settings = replace(entry.settings, x_column=None, y_column=0)
            self.populate_settings(entry)
            self.filename.setText(f"{entry.path.parent.name}  /  {entry.path.name}")
            self.filename.setToolTip(str(entry.path))
            params = infer_metadata(entry.path)
            self.inferred_label.setText(
                "文件名候选：" + "，".join(f"{k}={v:g}" for k, v in params.items())
                + "。请核对含义和单位。" if params else "文件名未识别出采集参数，可在项目记录中补充。")
            self.plot_stack.setCurrentIndex(1)
            if entry.result:
                self.show_result(entry.result)
            elif entry.table:
                self.show_raw(entry)
            else:
                raise DataError("源文件暂不可访问，且项目中没有保存处理结果。")
        except Exception as e:
            entry.error = str(e)
            self.notice.setText(str(e))
            self.plot_stack.setCurrentIndex(1)
            self.canvas.raw_curve.setData([], [])
            self.canvas.baseline_curve.setData([], [])
            self.canvas.corrected_curve.setData([], [])
            self.refresh_status(row)

    def show_raw(self, entry):
        x, y, _, _ = entry.table.select(entry.settings)
        hint = detect_polarity(y)
        self.polarity_hint.setText(hint["message"])
        self.canvas.show_raw(x, y, entry.settings)
        self.statistics.setText(f"{len(x):,} 个有效点  ·  原始预览  ·  横轴未标定为波数")
        self.notice.setText("先核对峰方向与有效区间，再点击“计算当前预览”。" + hint["message"])

    def show_result(self, result):
        self.canvas.show_result(result)
        polarity = "已反转" if result.sign == -1 else "跳过反转"
        lam = result.diagnostics["effective_lambda"]
        lam_text = f"实际 λ {lam:.3g}" if lam is not None else "跳过基线"
        self.statistics.setText(f"{len(result.x):,} 个有效点  ·  {polarity}  ·  {result.settings.method}  ·  {lam_text}")
        self.polarity_hint.setText(result.diagnostics["polarity_detection"]["message"])
        notices = result.diagnostics["notices"]
        if notices:
            self.notice.setText("核对提示：" + "；".join(notices))
            self.notice.setStyleSheet("color:#9A6A2B; background:#FFFAEF; border:1px solid #F1E0BC; border-radius:7px; padding:9px;")
        else:
            self.notice.setText("已生成预览。请核对估计背景与峰翼；导出将使用当前显示的相同数值。")
            self.notice.setStyleSheet("")

    def toggle_region(self):
        if self.current is None or self.current.table is None:
            return
        try:
            # Select against the full original record, not a previously cropped result.
            full = replace(self.current.settings, start=1, stop=0, method="none")
            x, y, _, _ = self.current.table.select(full)
            self.canvas.show_raw(x, y, full)
            self.canvas.select_region()
            self.notice.setText("拖动上图两侧边界选取有效区间，再点击“采用选区”；曲线位置保持原采样坐标。")
        except Exception as e:
            self.error("无法选区", e)

    def apply_region(self):
        if self.current is None or self.canvas.selection is None:
            return
        try:
            lo, hi = sorted(self.canvas.selection.getRegion())
            full = replace(self.current.settings, start=1, stop=0, method="none")
            x, _, _, _ = self.current.table.select(full)
            inside = np.flatnonzero((x >= lo) & (x <= hi))
            if len(inside) < 8:
                raise DataError("选区至少需要8个点。")
            self.start.setValue(int(inside[0] + 1))
            self.stop.setValue(int(inside[-1] + 1))
            self.canvas.clear_selection()
            self.show_raw(self.current)
        except Exception as e:
            self.error("无法采用选区", e)

    def selected(self):
        return [e for e in self.entries if e.checked]

    def apply_settings_selected(self):
        if self.current is None or self.worker:
            return
        settings = self.gather_settings()
        for i, entry in enumerate(self.entries):
            if entry.checked and entry.settings != settings:
                entry.settings = settings
                entry.result = None
                entry.error = ""
                self.refresh_status(i)
        self.statusBar().showMessage("已将当前设置应用到勾选文件；每个文件仍从原始输入独立计算。")

    def preview_current(self):
        if self.current:
            self.start_processing([self.current])

    def process_selected(self):
        # Each file retains its own crop, columns and polarity. Bulk-setting is an explicit action.
        if not self.selected():
            self.error("没有选择文件", "请先勾选需要处理的文件。")
            return
        self.start_processing(self.selected())

    def start_processing(self, entries):
        if self.worker:
            return
        jobs = [(str(e.path), e.settings, self.metadata_for_entry(e)) for e in entries]
        self.worker = Worker(jobs, self.metadata(), self)
        self.worker.item_ready.connect(self.item_ready)
        self.worker.progress.connect(self.processing_progress)
        self.worker.finished.connect(self.processing_finished)
        self.started_at = time.perf_counter()
        self.set_busy(True)
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self.worker.start()

    def item_ready(self, path, result, error):
        for i, entry in enumerate(self.entries):
            if str(entry.path) == path:
                entry.result, entry.error = result, error
                self.refresh_status(i)
                if i == self.current_index:
                    if result:
                        self.show_result(result)
                    else:
                        self.notice.setText(error)
                break

    def processing_progress(self, current, total):
        self.progress.setValue(current)
        self.statusBar().showMessage(f"正在处理 {current} / {total}")

    def processing_finished(self):
        elapsed = time.perf_counter() - self.started_at
        old = self.worker
        self.worker = None
        if old:
            old.deleteLater()
        self.set_busy(False)
        self.method_controls()
        self.update_counts()
        self.statusBar().showMessage(f"本轮处理结束，用时 {elapsed:.2f} 秒。请核对预览后导出。")

    def set_busy(self, busy):
        for widget in [self.import_files_button, self.import_folder_button, self.preview_button,
                       self.batch_button, self.apply_all_button, self.project_open, self.project_save,
                       self.export_button, self.select_region_button, self.use_region_button,
                       self.demo_button, *self.settings_widgets, *self.metadata_widgets]:
            widget.setEnabled(not busy)
        self.progress.setVisible(busy)
        self.cancel_button.setVisible(busy)

    def cancel_processing(self):
        if self.worker:
            self.worker.stop_event.set()
            self.statusBar().showMessage("当前文件计算完成后停止；已经完成的结果会保留。")

    def export_dialog(self):
        if self.worker:
            return
        if not self.selected():
            self.error("没有选择文件", "请先勾选需要导出的文件。")
            return
        folder = W.QFileDialog.getExistingDirectory(self, "选择结果保存位置", self.last_dir())
        if not folder:
            return
        W.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
        try:
            destination = export_results(folder, self.selected(), self.metadata(), export_preview)
            self.statusBar().showMessage(f"已导出：{destination}")
            W.QMessageBox.information(self, "导出完成",
                f"结果已保存至：\n{destination}\n\n各段DAT、核对PNG与处理记录已保存。\n每个DAT均已回读核对，与预览数值相同。")
        except Exception as e:
            self.error("未完成导出", e)
        finally:
            W.QApplication.restoreOverrideCursor()

    def save_project(self):
        if self.worker:
            return
        path, _ = W.QFileDialog.getSaveFileName(self, "保存项目", self.last_dir() + "/红外预处理.irproj",
                                               "红外预处理项目 (*.irproj)")
        if path:
            try:
                save_project(path, self.entries, self.metadata())
                self.statusBar().showMessage("项目已保存，包含参数、原信号、估计背景和当前结果。")
            except Exception as e:
                self.error("项目保存失败", e)

    def open_project(self):
        if self.worker:
            return
        path, _ = W.QFileDialog.getOpenFileName(self, "打开项目", self.last_dir(),
                                               "红外预处理项目 (*.irproj)")
        if path:
            self.open_project_path(path)

    def open_project_path(self, path):
        try:
            items, meta = load_project(path)
            entries = [Entry(Path(i["path"]), i["settings"], i.get("checked", True),
                             i.get("result"), i.get("error", ""), metadata=i.get("metadata", {}))
                       for i in items]
            self.entries, self.current_index = entries, -1
            self.loading = True
            self.sample.setText(meta.get("sample_name", "水样"))
            self.species.setText(meta.get("species", "水"))
            self.pressure_unit.setCurrentText(meta.get("pressure_unit", "未知"))
            self.gas_temperature.setText(str(meta.get("gas_temperature_K") or ""))
            self.loading = False
            self.rebuild_files()
            if entries:
                self.files.setCurrentCell(0, 0)
            else:
                self.plot_stack.setCurrentIndex(0)
            self.update_counts()
            self.statusBar().showMessage(f"已打开项目：{path}")
            return True
        except Exception as e:
            self.loading = False
            self.error("项目打开失败", e)
            return False

    def load_demo(self):
        from .demo import create_demo
        folder = Path(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.AppLocalDataLocation))
        self.add_paths(create_demo(folder / "demo"))
        self.sample.setText("合成演示数据")
        self.preview_current()

    def show_help(self):
        W.QMessageBox.information(self, "使用说明",
            "1. 导入文件或文件夹，也可直接拖入窗口。\n"
            "2. 默认把向下吸收峰反转；已向上数据请选择“跳过反转”。\n"
            "3. 检查有效区间；多周期记录请先在图上选一个周期。\n"
            "4. 点击“计算当前预览”，查看原信号、背景和校正结果。\n"
            "5. 设置可应用到勾选文件，再批量处理；各段仍可独立调整。\n"
            "6. 导出DAT、PNG和处理记录，或保存项目以后继续。\n\n"
            "平滑度数值越大，背景越平滑；不能仅凭鼓包消失判断基线正确。\n"
            "默认按点数换算实际λ，高级设置可关闭；实际值会保存。\n"
            "首版不进行波数标定和拼接，横轴保留采样坐标或已知时间。\n"
            "异常文件不阻塞批处理；导出前须处理所有勾选文件。")

    def show_licenses(self):
        import sys
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        licenses = base / "THIRD_PARTY_NOTICES.md"
        text = licenses.read_text(encoding="utf-8") if licenses.exists() else (
            "pybaselines 1.2.1 · BSD-3-Clause\npyqtgraph · MIT\n"
            "PySide6 / Qt · LGPLv3等，具体许可随程序分发。\nNumPy / SciPy · BSD-3-Clause")
        dialog = W.QDialog(self)
        dialog.setWindowTitle("开源组件与许可")
        dialog.resize(700, 520)
        layout = W.QVBoxLayout(dialog)
        view = W.QTextBrowser()
        view.setOpenExternalLinks(True)
        view.setMarkdown(text)
        layout.addWidget(view)
        close = button("关闭", dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def show_updates(self):
        from .update_ui import UpdateDialog
        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self.start_updated_version, self)
        self.update_dialog.show()
        self.update_dialog.raise_()
        self.update_dialog.activateWindow()
        if not self.update_dialog.worker:
            self.update_dialog.check_updates()

    def start_updated_version(self, executable, version):
        from .updates import app_data_root, version_key
        if self.worker is not None:
            raise DataError("当前正在处理数据，请完成后再点击“保存项目并切换”。")
        version_key(version)
        executable = Path(executable).resolve()
        if not executable.is_file():
            raise DataError("新版程序不存在，请重新下载。")
        root = app_data_root()
        recovery = root / "recovery"
        recovery.mkdir(parents=True, exist_ok=True)
        ticket = uuid.uuid4().hex
        ready = root / "updates" / f"ready-{ticket}.json"
        ready.parent.mkdir(parents=True, exist_ok=True)
        args = [str(executable), "--update-ready", str(ready)]
        if self.entries:
            snapshot = recovery / f"更新前项目_{time.strftime('%Y%m%d_%H%M%S')}_{ticket[:6]}.irproj"
            save_project(snapshot, self.entries, self.metadata())
            args.extend(["--project", str(snapshot)])
        child = subprocess.Popen(args, cwd=str(executable.parent), close_fds=True)
        self.setEnabled(False)
        self.statusBar().showMessage("项目已保存，正在等待新版正常打开…")
        began = time.monotonic()
        timer = QtCore.QTimer(self)
        timer.setInterval(250)
        self._update_timer = timer

        def poll():
            success = False
            if ready.exists():
                try:
                    state = json.loads(ready.read_text(encoding="utf-8"))
                    success = state.get("pid") == child.pid and state.get("version") == version
                except (OSError, ValueError):
                    pass
            if success:
                timer.stop()
                ready.unlink(missing_ok=True)
                self.setEnabled(True)
                self.close()
            elif child.poll() is not None or time.monotonic() - began > 35:
                timer.stop()
                self.setEnabled(True)
                message = "新版未能确认正常打开。当前窗口和更新前保存的项目仍保留，请继续使用当前版本。"
                if self.update_dialog:
                    self.update_dialog.status.setText(message)
                self.error("未切换版本", message)
        timer.timeout.connect(poll)
        timer.start()

    def error(self, title, error):
        W.QMessageBox.warning(self, title, str(error))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and self.worker is None:
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            paths.extend(discover_files(path) if path.is_dir() else [path])
        self.add_paths(paths)

    def closeEvent(self, event):
        if self.update_dialog and self.update_dialog.worker and self.update_dialog.worker.isRunning():
            self.update_dialog.worker.cancel.set()
            event.ignore()
            self.statusBar().showMessage("正在停止更新任务，完成后可关闭窗口。")
        elif self.worker and self.worker.isRunning():
            self.worker.stop_event.set()
            event.ignore()
            self.statusBar().showMessage("正在停止计算，完成当前文件后可关闭窗口。")
        else:
            event.accept()
