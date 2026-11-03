import numpy.lib._datasource as datasource

from infrared_desktop.core import read_table
from infrared_desktop.demo import create_demo


def test_demo_is_readable_on_english_windows(tmp_path, monkeypatch):
    original_open = datasource.open

    def western_default(path, mode="r", encoding=None, newline=None):
        return original_open(path, mode, encoding=encoding or "cp1252", newline=newline)

    monkeypatch.setattr(datasource, "open", western_default)
    paths = create_demo(tmp_path / "合成演示")
    assert len(paths) == 3
    for path in paths:
        assert path.read_text(encoding="utf-8").startswith("# 合成演示数据")
        assert read_table(path).values.shape == (2000, 2)
