import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")

import numpy as np
import pytest
from PySide6 import QtCore, QtWidgets

from infrared_desktop.core import Settings
from infrared_desktop.demo import create_demo
from infrared_desktop.storage import load_project, save_project
from infrared_desktop.ui import MainWindow, Worker
from infrared_desktop.update_ui import UpdateDialog
from infrared_desktop.updates import ReleaseInfo


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    # Give deferred graphics-object destruction an event loop before QApplication exits.
    QtCore.QTimer.singleShot(0, app.quit)
    app.exec()


def test_one_bad_input_does_not_abort_other_batch_files(tmp_path, application):
    good = create_demo(tmp_path / "valid")[0]
    bad = tmp_path / "bad.dat"
    bad.write_text("no numeric values\n", encoding="utf-8")
    jobs = [(str(bad), Settings(), {}), (str(good), Settings(), {"pressure": 100})]
    worker = Worker(jobs, {})
    received = []
    worker.item_ready.connect(lambda *values: received.append(values))
    worker.run()
    assert len(received) == 2
    assert received[0][1] is None and "数值" in received[0][2]
    assert received[1][1].sign == -1 and received[1][2] == ""
    assert received[1][1].metadata["pressure"] == 100


def test_graphical_crop_preserves_coordinates_and_rows(tmp_path, application):
    window = MainWindow()
    try:
        window.add_paths(create_demo(tmp_path))
        x = window.current.table.values[:, 0]
        window.toggle_region()
        window.canvas.selection.setRegion((x[100], x[899]))
        window.apply_region()
        assert window.current.settings.start == 101
        assert window.current.settings.stop == 900
        np.testing.assert_array_equal(window.canvas.current_x, x[100:900])
    finally:
        window.close()
        window.deleteLater()


def test_confirmed_metadata_survives_project_reopen(tmp_path, application):
    window = MainWindow()
    try:
        window.add_paths(create_demo(tmp_path / "data"))
        window.laser_temperature.setText("27")
        window.laser_current.setText("125")
        window.pressure.setText("100")
        window.parameters_confirmed.setChecked(True)
        project = tmp_path / "metadata.irproj"
        save_project(project, window.entries, window.metadata())
        items, _ = load_project(project)
        assert items[0]["metadata"]["pressure"] == 100
        window.open_project_path(project)
        assert window.pressure.text() == "100"
        assert window.parameters_confirmed.isChecked()
    finally:
        window.close()
        window.deleteLater()


def test_update_dialog_shows_new_version_and_latest_state(application):
    dialog = UpdateDialog(lambda *_: None)
    try:
        dialog.apply_info(ReleaseInfo("0.3.0", "改善预览", "", ()))
        assert dialog.install.isEnabled()
        assert "0.3.0" in dialog.status.text()
        assert dialog.notes.toPlainText() == "改善预览"
        dialog.apply_info(None)
        assert not dialog.install.isEnabled()
        assert "最新" in dialog.status.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_switch_saves_project_before_launch(tmp_path, application, monkeypatch):
    from infrared_desktop import ui, updates
    monkeypatch.setattr(updates, "app_data_root", lambda: tmp_path / "appdata")
    calls = []
    class Child:
        pid = 12345
        def poll(self):
            return None
    monkeypatch.setattr(ui.subprocess, "Popen", lambda args, **kwargs: (calls.append(args), Child())[1])
    exe = tmp_path / "new.exe"
    exe.write_bytes(b"not launched")
    window = MainWindow()
    try:
        window.add_paths(create_demo(tmp_path / "source"))
        window.start_updated_version(exe, "0.3.0")
        snapshot = calls[0][calls[0].index("--project") + 1]
        items, _ = load_project(snapshot)
        assert len(items) == len(window.entries)
        assert items[0]["path"] == str(window.entries[0].path)
        assert "--update-ready" in calls[0]
        window._update_timer.stop()
    finally:
        window.setEnabled(True)
        window.close()
        window.deleteLater()
