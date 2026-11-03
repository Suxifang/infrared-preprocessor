"""Exercise the actual desktop widgets, worker, project and exported preview."""
import json
import traceback
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from .core import DataError
from .demo import create_demo
from .plotting import export_preview
from .storage import check_export, export_results, save_project


def run_smoke(app, window, root):
    root.mkdir(parents=True, exist_ok=True)
    report = {"ok": False}
    window.grab().save(str(root / "界面_空白.png"))

    def fail(error):
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        (root / "smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        app.exit(1)

    def finish():
        try:
            assert window.worker is None
            assert len(window.entries) == 3
            assert all(e.result is not None for e in window.entries)
            assert all(e.result.sign == -1 for e in window.entries)
            assert all(e.result.diagnostics["converged"] for e in window.entries)
            # Test the real parameter signals and stale-result invalidation.
            before = window.current.result
            window.smooth.setValue(6.)
            assert window.current.result is None
            try:
                check_export(window.entries)
            except DataError:
                report["stale_export_blocked"] = True
            else:
                raise AssertionError("A stale result was exportable")
            window.current.settings = before.settings
            window.current.result = before
            window.populate_settings(window.current)
            window.show_result(before)
            window.laser_temperature.setText("27")
            window.laser_current.setText("125")
            window.pressure.setText("100")
            window.parameters_confirmed.setChecked(True)
            window.sample.setText("合成演示 · 参数保存测试")
            assert before.metadata["pressure"] == 100
            assert before.metadata["filename_parameters_confirmed"] is True
            assert all(e.result.metadata["sample_name"] == window.sample.text() for e in window.entries)
            destination = export_results(root / "导出", window.entries, window.metadata(), export_preview)
            picture = QtGui.QImage(str(next(destination.rglob("核对预览.png")))).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
            pixels = np.frombuffer(picture.constBits(), dtype=np.uint8).reshape(picture.height(), picture.bytesPerLine() // 4, 4)
            blue = (pixels[:, :, 2].astype(int) - pixels[:, :, 0].astype(int) > 60) & (pixels[:, :, 0] < 100)
            blue_rows, blue_columns = np.nonzero(blue)
            assert blue_rows.max() > .8 * picture.height()
            assert blue_columns.max() > .8 * picture.width()
            report["preview_fills_export_canvas"] = True
            project = root / "测试项目.irproj"
            save_project(project, window.entries, window.metadata())
            window.open_project_path(project)
            assert all(e.result is not None for e in window.entries)
            assert window.current.metadata["pressure"] == 100
            assert window.current.result.metadata["filename_parameters_confirmed"] is True
            assert window.parameters_confirmed.isChecked()
            np.testing.assert_array_equal(window.current.result.corrected, before.corrected)
            QtWidgets.QApplication.processEvents()
            window.grab().save(str(root / "界面_预览.png"))
            report.update(ok=True, files=3, export=str(destination),
                          project_roundtrip=True, default_inversion=True, exact_export_readback=True,
                          metadata_confirmation_preserved=True)
            (root / "smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            app.exit(0)
        except Exception as e:
            fail(e)

    try:
        window.add_paths(create_demo(root / "合成演示"))
        window.sample.setText("合成演示数据")
        window.batch_button.click()
        assert window.worker is not None
        window.worker.finished.connect(lambda: QtCore.QTimer.singleShot(150, finish))
        QtCore.QTimer.singleShot(45_000, lambda: fail("Desktop smoke test timed out") if not report["ok"] else None)
    except Exception as e:
        fail(e)
