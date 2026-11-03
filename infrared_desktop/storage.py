from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__
from .core import DataError, Result, Settings, json_text, sha256_file


def safe_stem(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).rstrip(". ")[:100] or "数据"


def save_project(path, entries, metadata):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".irproject-", dir=path.parent)
    os.close(fd)
    try:
        manifest = {"format": "infrared-preprocess-project", "schema": 1, "version": __version__,
                    "metadata": metadata, "entries": []}
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for i, entry in enumerate(entries):
                item = {"path": str(entry.path), "settings": entry.settings.to_dict(),
                        "checked": entry.checked, "error": entry.error,
                        "metadata": getattr(entry, "metadata", {})}
                if entry.result is not None:
                    result = entry.result
                    data = io.BytesIO()
                    np.savez_compressed(data, **{k: getattr(result, k) for k in
                        ("x", "original", "background", "corrected", "rows", "source_lines")})
                    filename = f"results/{i}.npz"
                    z.writestr(filename, data.getvalue())
                    item["result"] = result.summary()
                    item["arrays"] = filename
                manifest["entries"].append(item)
            z.writestr("project.json", json_text(manifest))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_project(path):
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 1024 ** 3:
            raise DataError("项目展开大小超过1 GB，首版不读取此项目。")
        manifest = json.loads(z.read("project.json"))
        if manifest.get("format") != "infrared-preprocess-project" or manifest.get("schema") != 1:
            raise DataError("不支持的项目格式。")
        entries = []
        for item in manifest["entries"]:
            item["settings"] = Settings.from_dict(item["settings"])
            if "result" in item:
                meta = item["result"]
                with np.load(io.BytesIO(z.read(item["arrays"])), allow_pickle=False) as a:
                    arrays = {k: a[k].copy() for k in
                              ("x", "original", "background", "corrected", "rows", "source_lines")}
                lengths = {len(v) for v in arrays.values()}
                if len(lengths) != 1 or any(v.ndim != 1 or not np.isfinite(v).all() for v in arrays.values()):
                    raise DataError("项目内处理数据损坏。")
                result = Result(
                    meta["source"], meta["source_sha256"], Settings.from_dict(meta["settings"]),
                    **arrays, sign=meta["sign"], diagnostics=meta["diagnostics"], metadata=meta["metadata"])
                if result.settings != item["settings"]:
                    raise DataError("项目中的预览结果与参数不一致，请重新处理。")
                if not np.allclose(result.sign * (result.original - result.background), result.corrected,
                                   atol=1e-13, rtol=1e-12):
                    raise DataError("项目内原信号、背景与校正结果不一致。")
                item["result"] = result
            entries.append(item)
    return entries, manifest.get("metadata", {})


def check_export(entries):
    if not entries:
        raise DataError("请先勾选需要导出的文件。")
    for entry in entries:
        result = entry.result
        if result is None or result.settings != entry.settings:
            raise DataError(f"{entry.path.name} 尚未按当前设置处理，请先计算预览或批量处理。")
        if not result.diagnostics["converged"]:
            raise DataError(f"{entry.path.name} 的基线尚未收敛，请调整参数后重算。")
        if entry.path.exists() and sha256_file(entry.path) != result.source_sha256:
            raise DataError(f"{entry.path.name} 的源文件已变化，请重新读取并处理。")


def export_results(target, entries, metadata, preview_writer=None):
    """Export the exact immutable arrays drawn in the preview; no recalculation."""
    check_export(entries)
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix=".infrared-export-", dir=target))
    records = []
    try:
        for i, entry in enumerate(entries, 1):
            result = entry.result
            parent = safe_stem(entry.path.parent.name)
            stem = safe_stem(entry.path.stem)
            name = f"{i:03d}_{parent}_{stem}"
            folder = root / name
            folder.mkdir()
            data_path = folder / f"{stem}_校正.dat"
            x_label = "Time" if result.settings.x_kind == "time" else "Sample_coordinate"
            with data_path.open("w", encoding="utf-8", newline="\n") as f:
                f.write(f"# {x_label}\tCorrected_signal\n")
                f.write("# Preprocessed electrical signal; not wavenumber-calibrated; not absorbance.\n")
                np.savetxt(f, np.column_stack((result.x, result.corrected)), fmt="%.17g", delimiter="\t")
            back = np.loadtxt(data_path)
            if not (np.array_equal(back[:, 0], result.x) and np.array_equal(back[:, 1], result.corrected)):
                raise DataError("导出回读与预览数值不一致，导出已中止。")
            # This archive supports scientific reconstruction without cluttering the DAT directory.
            original_data = io.BytesIO()
            np.savez_compressed(original_data, x=result.x, original=result.original,
                                background=result.background, corrected=result.corrected,
                                original_numeric_row=result.rows, source_line=result.source_lines)
            record = result.summary()
            record["export"] = {"data": str(data_path.relative_to(root)),
                                "source_available": entry.path.exists(),
                                "exact_xy_readback": True}
            records.append(record)
            archive = root / "处理记录"
            archive.mkdir(exist_ok=True)
            (archive / f"{name}.npz").write_bytes(original_data.getvalue())
            if preview_writer:
                preview_writer(result, folder / "核对预览.png")
        manifest = {
            "application": "红外预处理", "version": __version__,
            "exported_at": datetime.now().astimezone().isoformat(),
            "metadata": metadata, "results": records,
            "scope": "各段预处理；横轴未标定为波数；没有拼接",
        }
        (root / "处理记录.json").write_text(json_text(manifest), encoding="utf-8")
        (root / "先读我.txt").write_text(
            "本目录为本次选定的各段预处理结果。\n"
            "DAT与预览使用完全相同的数值，保留原横坐标与负值。\n"
            "横轴尚未转换为波数，各段尚未拼接；纵轴是校正电信号，不自动等同吸光度。\n"
            "各段PNG：上图原信号与估计背景，下图校正结果。\n"
            "处理记录.json保存极性、有效区间、算法、实际参数、版本、来源哈希和检测提示。\n"
            "处理记录中的NPZ保存原信号、基线与原始行号，可用于数值重建。\n"
            "软件完成计算不代表所有宽起伏已经被物理背景验证；请核对峰翼与极性。\n",
            encoding="utf-8")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destination = target / f"红外预处理_{stamp}"
        suffix = 2
        while destination.exists():
            destination = target / f"红外预处理_{stamp}_{suffix}"
            suffix += 1
        os.replace(root, destination)
        return destination
    except Exception:
        # Only remove the application-created staging directory, never an input or user directory.
        if root.parent.resolve() == target.resolve() and root.name.startswith(".infrared-export-"):
            shutil.rmtree(root)
        raise
