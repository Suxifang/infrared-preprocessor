from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from infrared_desktop.core import DataError, Settings, detect_polarity, infer_metadata, process, read_table
from infrared_desktop.storage import check_export, export_results, load_project, save_project


def sample(tmp_path, direction="down"):
    x = np.arange(1, 2001, dtype=float)
    t = (x - 1) / 1999
    b = .05 + .012 * t + .005 * t**2
    peak = .025 * np.exp(-.5 * ((t - .3) / .012)**2) + .018 * np.exp(-.5 * ((t - .65) / .02)**2)
    y = b - peak if direction == "down" else b + peak
    y += np.random.default_rng(12).normal(0, 0.00005, len(x))
    path = tmp_path / f"{direction}.dat"
    np.savetxt(path, np.c_[x, y], fmt="%.17g")
    return read_table(path), b, peak


def entry(table, result):
    return SimpleNamespace(path=table.path, settings=result.settings, checked=True, result=result, error="")


def test_known_background_and_peak_recovery(tmp_path):
    table, baseline, peaks = sample(tmp_path)
    result = process(table, Settings())
    assert result.sign == -1
    assert result.diagnostics["converged"]
    assert np.sqrt(np.mean((result.background - baseline)**2)) < 0.0005
    assert abs(result.corrected[np.argmax(peaks)] - peaks.max()) < 0.0007
    np.testing.assert_allclose(-(result.original - result.background), result.corrected, atol=2e-17)
    assert result.corrected.min() < 0
    assert not result.diagnostics["wavenumber_calibrated"]


def test_calculation_is_not_applied_twice(tmp_path):
    table, _, _ = sample(tmp_path)
    a, b = process(table, Settings()), process(table, Settings())
    np.testing.assert_array_equal(a.corrected, b.corrected)
    np.testing.assert_array_equal(a.original, table.values[:, 1])
    assert not a.corrected.flags.writeable


@pytest.mark.parametrize("direction,sign", [("down", -1), ("up", 1)])
def test_automatic_direction_on_unambiguous_peaks(tmp_path, direction, sign):
    table, _, _ = sample(tmp_path, direction)
    result = process(table, Settings(polarity="auto"))
    assert result.sign == sign
    assert result.corrected.max() > .02


def test_ambiguous_auto_direction_stops(tmp_path):
    p = tmp_path / "flat.dat"
    np.savetxt(p, np.c_[np.arange(50), np.zeros(50)])
    with pytest.raises(DataError, match="无法确定峰方向"):
        process(read_table(p), Settings(polarity="auto"))


def test_skip_baseline_and_preserve_input_values(tmp_path):
    table, _, _ = sample(tmp_path, "up")
    result = process(table, Settings(polarity="up", method="none"))
    np.testing.assert_array_equal(result.corrected, table.values[:, 1])
    assert np.count_nonzero(result.background) == 0


@pytest.mark.parametrize("encoding", ["utf-8-sig", "gb18030", "utf-16"])
def test_encoding_headers_and_original_lines(tmp_path, encoding):
    p = tmp_path / "中文数据.dat"
    p.write_text("采样序号\t信号\n# 原始测量\n" + "\n".join(f"{i}\t{.5-i/1000}" for i in range(1, 21)), encoding=encoding)
    table = read_table(p)
    result = process(table, Settings(start=4, stop=17, method="none"))
    assert result.rows[0] == 4 and result.rows[-1] == 17
    assert result.source_lines[0] == 6
    assert result.x[0] == 4 and result.x[-1] == 17


def test_four_columns_are_selected_explicitly(tmp_path):
    p = tmp_path / "four.csv"
    p.write_text("a,b,c,d\n" + "\n".join(f"0,0.03,{i},{.3-i/100}" for i in range(1, 20)), encoding="utf-8")
    result = process(read_table(p), Settings(x_column=2, y_column=3, method="none"))
    np.testing.assert_array_equal(result.x, np.arange(1, 20))
    assert result.original[0] == .29


def test_bad_numeric_row_is_not_silently_deleted(tmp_path):
    p = tmp_path / "broken.csv"
    p.write_text("\n".join(f"{i},{'bad' if i==12 else .3}" for i in range(1, 30)), encoding="utf-8")
    with pytest.raises(DataError, match="第12行"):
        process(read_table(p), Settings())


def test_nonuniform_time_and_reset_are_not_sorted_or_resampled(tmp_path):
    p = tmp_path / "time.dat"
    x = np.arange(20, dtype=float)
    x[9] = 9.2
    np.savetxt(p, np.c_[x, np.ones(20)])
    with pytest.raises(DataError, match="不等间隔"):
        process(read_table(p), Settings(x_kind="time"))
    x[9] = 3
    np.savetxt(p, np.c_[x, np.ones(20)])
    with pytest.raises(DataError, match="重复或回跳"):
        process(read_table(p), Settings())


def test_generated_x_is_not_rebased_after_crop(tmp_path):
    p = tmp_path / "one.dat"
    np.savetxt(p, np.arange(1, 30) / 100)
    result = process(read_table(p), Settings(x_column=None, y_column=0, start=6, stop=23, method="none"))
    np.testing.assert_array_equal(result.x, np.arange(6, 24))


def test_metadata_does_not_hardcode_columns():
    assert infer_metadata(Path("17（17-19为50平均）/95-100.dat")) == {
        "current": 95., "pressure": 100., "temperature": 17.}
    assert infer_metadata(Path("25-125-300/平均50.dat"))["pressure"] == 300.


def test_lambda_length_scaling_is_recorded(tmp_path):
    table, _, _ = sample(tmp_path)
    a = process(table, Settings())
    b = process(table, Settings(scale_lambda=False))
    assert a.diagnostics["effective_lambda"] == pytest.approx(1e5 * ((2000-1)/999)**4)
    assert b.diagnostics["effective_lambda"] == 1e5


def test_export_exact_readback_and_provenance(tmp_path):
    table, _, _ = sample(tmp_path)
    result = process(table, Settings())
    e = entry(table, result)
    destination = export_results(tmp_path / "export", [e], {"sample_name": "水样"})
    saved = next(destination.rglob("*_校正.dat"))
    a = np.loadtxt(saved)
    np.testing.assert_array_equal(a[:, 0], result.x)
    np.testing.assert_array_equal(a[:, 1], result.corrected)
    assert (destination / "处理记录.json").exists()
    assert len(list((destination / "处理记录").glob("*.npz"))) == 1


def test_changed_settings_or_source_block_export(tmp_path):
    table, _, _ = sample(tmp_path)
    e = entry(table, process(table, Settings()))
    e.settings = replace(e.settings, log_lambda=6)
    with pytest.raises(DataError, match="当前设置"):
        check_export([e])
    e.settings = e.result.settings
    table.path.write_text("1 2\n", encoding="utf-8")
    with pytest.raises(DataError, match="源文件已变化"):
        check_export([e])


def test_project_roundtrip_is_numerically_exact(tmp_path):
    table, _, _ = sample(tmp_path)
    result = process(table, Settings())
    p = tmp_path / "项目.irproj"
    save_project(p, [entry(table, result)], {"species": "水"})
    entries, meta = load_project(p)
    assert meta["species"] == "水"
    restored = entries[0]["result"]
    assert restored.settings == result.settings
    for key in ["x", "original", "background", "corrected", "rows", "source_lines"]:
        np.testing.assert_array_equal(getattr(restored, key), getattr(result, key))


def test_nonconverged_background_is_not_final_export(tmp_path):
    table, _, _ = sample(tmp_path)
    result = process(table, Settings(max_iterations=0))
    assert not result.diagnostics["converged"]
    with pytest.raises(DataError, match="尚未收敛"):
        check_export([entry(table, result)])
