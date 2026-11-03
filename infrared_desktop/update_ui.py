from __future__ import annotations

import sys
import threading

from PySide6 import QtCore, QtGui, QtWidgets as W

from . import __version__
from .updates import RELEASE_PAGE, UpdateError, check_release, prepare_update


class UpdateWorker(QtCore.QThread):
    done = QtCore.Signal(object, str)
    progress = QtCore.Signal(int, str)

    def __init__(self, info=None, parent=None):
        super().__init__(parent)
        self.info = info
        self.cancel = threading.Event()

    def run(self):
        try:
            value = check_release() if self.info is None else prepare_update(
                self.info, progress=self.progress.emit, cancel=self.cancel)
            self.done.emit(value, "")
        except Exception as error:
            self.done.emit(None, str(error))


class UpdateDialog(W.QDialog):
    def __init__(self, switch_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("软件更新")
        self.setMinimumSize(540, 380)
        self.resize(570, 430)
        self.switch_callback = switch_callback
        self.worker = None
        self.info = None
        self.prepared = None
        self.closing = False
        self.task = ""
        self.outcome = (None, "")
        layout = W.QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(13)
        title = W.QLabel("保持软件更新")
        title.setObjectName("title")
        layout.addWidget(title)
        version = W.QLabel(f"当前版本  {__version__}  ·  Windows 测试版")
        version.setObjectName("muted")
        layout.addWidget(version)
        self.status = W.QLabel("从项目的 GitHub 发布页获取新版。")
        self.status.setWordWrap(True)
        self.status.setObjectName("notice")
        layout.addWidget(self.status)
        self.notes = W.QTextEdit()
        self.notes.setReadOnly(True)
        self.notes.setPlaceholderText("检查后在这里显示更新说明。")
        self.notes.setStyleSheet("QTextEdit { background:white; border:1px solid #E1E8F0; border-radius:8px; padding:10px; }")
        layout.addWidget(self.notes, 1)
        self.progress = W.QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        note = W.QLabel("下载会校验完整性；切换前保存当前项目，保留原始数据和旧版本。")
        note.setWordWrap(True)
        note.setObjectName("muted")
        layout.addWidget(note)
        row = W.QHBoxLayout()
        self.page = W.QPushButton("GitHub 发布页")
        self.page.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(RELEASE_PAGE)))
        row.addWidget(self.page)
        row.addStretch()
        self.check = W.QPushButton("检查更新")
        self.check.clicked.connect(self.check_updates)
        row.addWidget(self.check)
        self.install = W.QPushButton("下载并切换新版")
        self.install.setObjectName("primary")
        self.install.setEnabled(False)
        self.install.clicked.connect(self.install_update)
        row.addWidget(self.install)
        layout.addLayout(row)

    def check_updates(self):
        if self.worker:
            return
        self.info = self.prepared = None
        self.install.setText("下载并切换新版")
        self.status.setText("正在检查 GitHub 上的发布版本…")
        self.start_task("check")

    def start_task(self, task):
        self.task = task
        self.outcome = (None, "")
        self.worker = UpdateWorker(self.info if task == "download" else None, self)
        self.worker.done.connect(lambda value, error: setattr(self, "outcome", (value, error)))
        self.worker.progress.connect(self.on_progress)
        self.worker.finished.connect(self.task_finished)
        self.check.setEnabled(False)
        self.install.setEnabled(False)
        self.progress.setRange(0, 0 if task == "check" else 100)
        self.progress.setValue(0)
        self.progress.show()
        self.worker.start()

    def on_progress(self, value, text):
        self.progress.setValue(value)
        self.status.setText(text)

    def apply_info(self, info):
        self.info = info
        if info:
            self.status.setText(f"发现新版本 {info.version}，可以下载更新。")
            self.notes.setPlainText(info.notes)
            self.install.setEnabled(True)
        else:
            self.status.setText(f"已是最新发布版本（{__version__}）。")
            self.notes.setPlainText("本次检查没有发现比当前版本更新的可用安装包。")
            self.install.setEnabled(False)

    def task_finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self.check.setEnabled(True)
        self.progress.hide()
        value, error = self.outcome
        if self.closing:
            self.closing = False
            self.close()
            return
        if error:
            self.status.setText(error)
            self.install.setEnabled(self.info is not None)
        elif self.task == "check":
            self.apply_info(value)
        else:
            self.prepared = value
            self.install.setEnabled(True)
            self.install.setText("保存项目并切换")
            self.install_update()

    def install_update(self):
        if self.worker:
            return
        if self.prepared:
            try:
                self.switch_callback(self.prepared, self.info.version)
            except Exception as error:
                self.status.setText(str(error))
            return
        if not self.info:
            return
        if not getattr(sys, "frozen", False):
            self.status.setText("源码运行模式请同步仓库代码；打包程序支持下载并切换新版。")
            return
        self.start_task("download")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.closing = True
            self.worker.cancel.set()
            self.status.setText("正在停止更新任务，请稍候…")
            event.ignore()
        else:
            event.accept()
