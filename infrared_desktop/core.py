from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pybaselines
from pybaselines import Baseline
from scipy.ndimage import gaussian_filter1d

from . import __version__


class DataError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    x_column: int | None = 0
    y_column: int = 1
    polarity: str = "down"
    method: str = "arpls"
    log_lambda: float = 5.0
    scale_lambda: bool = True
    asymmetry: float = 0.01
    start: int = 1
    stop: int = 0
    max_iterations: int = 100
    tolerance: float = 0.001
    x_kind: str = "sample"
    x_unit: str = ""

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        allowed = cls.__dataclass_fields__
        return cls(**{k: v for k, v in value.items() if k in allowed})


@dataclass
class Table:
    path: Path
    values: np.ndarray
    source_lines: np.ndarray
    digest: str
    encoding: str
    header_lines: int

    @property
    def columns(self):
        return self.values.shape[1]

    def select(self, settings: Settings):
        n = len(self.values)
        start, stop = settings.start - 1, settings.stop or n
        if start < 0 or stop > n or stop <= start:
            raise DataError(f"有效区间应在第1至{n}个数值行之间，且起点小于终点。")
        if settings.y_column < 0 or settings.y_column >= self.columns:
            raise DataError(f"没有第{settings.y_column + 1}列，请先检查列设置。")
        if settings.x_column is not None:
            if settings.x_column < 0 or settings.x_column >= self.columns:
                raise DataError(f"没有第{settings.x_column + 1}列，请先检查横坐标列。")
            if settings.x_column == settings.y_column:
                raise DataError("横坐标和信号不能选择同一列。")
        rows = np.arange(start + 1, stop + 1)
        x = rows.astype(float) if settings.x_column is None else self.values[start:stop, settings.x_column].copy()
        y = self.values[start:stop, settings.y_column].copy()
        good = np.isfinite(x) & np.isfinite(y)
        if not good.all():
            line = int(self.source_lines[start:stop][np.flatnonzero(~good)[0]])
            raise DataError(f"原文件第{line}行有空值、非数值或无穷值；请调整列或有效区间，程序不会自动删点。")
        if len(x) < 8:
            raise DataError("有效区间至少需要8个数据点。")
        if settings.x_kind not in ("sample", "time"):
            raise DataError("首版横坐标仅支持采样序号或已知时间。")
        dx = np.diff(x)
        if not (np.all(dx > 0) or np.all(dx < 0)):
            raise DataError("横坐标有重复或回跳，请检查列设置，或先选择一个有效扫描区间。")
        if settings.method != "none" and not np.allclose(dx, np.median(dx), rtol=0.01, atol=1e-12):
            raise DataError("横坐标明显不等间隔，当前基线方法按等间隔采样计算；请核对数据，程序不会自动重采样。")
        return x, y, rows, self.source_lines[start:stop].copy()


@dataclass
class Result:
    source: str
    source_sha256: str
    settings: Settings
    x: np.ndarray
    original: np.ndarray
    background: np.ndarray
    corrected: np.ndarray
    rows: np.ndarray
    source_lines: np.ndarray
    sign: int
    diagnostics: dict
    metadata: dict

    def __post_init__(self):
        for key in ("x", "original", "background", "corrected", "rows", "source_lines"):
            array = np.asarray(getattr(self, key))
            array.setflags(write=False)
            setattr(self, key, array)

    def summary(self):
        return {
            "source": self.source,
            "source_sha256": self.source_sha256,
            "settings": self.settings.to_dict(),
            "sign": self.sign,
            "diagnostics": self.diagnostics,
            "metadata": self.metadata,
        }


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_table(path) -> Table:
    path = Path(path).resolve()
    if path.stat().st_size > 256 * 1024 * 1024:
        raise DataError("首版单文件上限为256 MB，请先拆分文件。")
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text, encoding = raw.decode("utf-16"), "utf-16"
    else:
        for encoding in ("utf-8-sig", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise DataError("无法识别文本编码；请另存为UTF-8、GBK或带BOM的UTF-16文本。")
    records, lines, header_count = [], [], 0
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip("\ufeff")
        if not line.strip() or line.lstrip().startswith(("#", "//", ";")):
            header_count += 1
            continue
        if "\t" in line:
            cells = line.split("\t")
        elif "," in line:
            cells = next(csv.reader([line]))
        else:
            cells = re.split(r"[;\s]+", line.strip())
        values, numeric = [], False
        for cell in cells:
            try:
                value = float(cell.strip().replace("D", "e").replace("d", "e"))
                numeric = True
            except ValueError:
                value = float("nan")
            values.append(value)
        if not numeric:
            header_count += 1
            if records:
                raise DataError(f"原文件第{number}行在数据中途出现非数值内容，请检查文件。")
            continue
        records.append(values)
        lines.append(number)
    if not records:
        raise DataError("文件中没有可读取的数值数据。")
    width = max(map(len, records))
    values = np.full((len(records), width), np.nan)
    for i, row in enumerate(records):
        values[i, :len(row)] = row
    return Table(path, values, np.asarray(lines, dtype=np.int64), hashlib.sha256(raw).hexdigest(),
                 encoding, header_count)


def detect_polarity(y):
    """A conservative suggestion, not chemical identification."""
    y = np.asarray(y, dtype=float)
    residual = y - gaussian_filter1d(y, max(3.0, len(y) / 35.0))
    centered = residual - np.median(residual)
    positive = max(0.0, float(np.quantile(centered, .998)))
    negative = max(0.0, float(-np.quantile(centered, .002)))
    noise = float(np.median(np.abs(np.diff(y) - np.median(np.diff(y)))) / .954)
    floor = max(noise, np.finfo(float).eps * max(1., float(np.max(np.abs(y)))))
    ratio = max(positive, negative) / max(min(positive, negative), floor)
    if max(positive, negative) < 8 * floor or ratio < 2.0:
        kind, message = "uncertain", "极性提示不明确；请检查原始曲线。"
    elif negative > positive:
        kind, message = "down", "检测更像向下吸收峰。"
    else:
        kind, message = "up", "检测更像已向上的峰，请注意避免再次反转。"
    return {"suggestion": kind, "message": message, "tail_ratio": float(ratio), "noise_estimate": noise}


def infer_metadata(path):
    path = Path(path)
    out = {}
    for part in [path.stem, *[p.name for p in list(path.parents)[:3]]]:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", part)
        if m:
            out.update(temperature=float(m[1]), current=float(m[2]), pressure=float(m[3]))
            break
    if not out:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", path.stem)
        t = re.match(r"^(\d+(?:\.\d+)?)(?:[（(].*)?$", path.parent.name)
        if m:
            out.update(current=float(m[1]), pressure=float(m[2]))
            if t:
                out["temperature"] = float(t[1])
    return out


def process(table: Table, settings: Settings, metadata=None) -> Result:
    x, raw, rows, source_lines = table.select(settings)
    suggestion = detect_polarity(raw)
    if settings.polarity not in ("down", "up", "auto"):
        raise DataError("不支持的峰极性设置。")
    if settings.polarity == "auto":
        if suggestion["suggestion"] == "uncertain":
            raise DataError("自动识别无法确定峰方向。请选择“默认反转”或“已向上”。")
        sign = -1 if suggestion["suggestion"] == "down" else 1
    else:
        sign = -1 if settings.polarity == "down" else 1
    oriented = sign * raw
    n = len(raw)
    if not (2 <= settings.log_lambda <= 12):
        raise DataError("基线平滑度超出支持范围。")
    effective_lambda = 10.0 ** settings.log_lambda
    if settings.scale_lambda:
        effective_lambda *= ((n - 1) / 999.0) ** 4
    notices = []
    if settings.polarity == "down" and suggestion["suggestion"] == "up":
        notices.append("检测更像向上峰；当前按默认设置反转，请核对极性。")
    elif settings.polarity == "up" and suggestion["suggestion"] == "down":
        notices.append("检测更像向下峰；当前跳过反转，请核对极性。")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        if settings.method == "none":
            base, history = np.zeros(n), []
            converged = True
        elif settings.method in ("arpls", "asls"):
            fitter = Baseline(check_finite=True)
            kwargs = dict(lam=effective_lambda, max_iter=settings.max_iterations, tol=settings.tolerance)
            if settings.method == "asls":
                if not 0 < settings.asymmetry < 1:
                    raise DataError("AsLS非对称参数必须介于0和1之间。")
                kwargs["p"] = settings.asymmetry
            base, info = getattr(fitter, settings.method)(oriented, **kwargs)
            history = np.asarray(info.get("tol_history", []), dtype=float).tolist()
            converged = bool(history and np.isfinite(history[-1]) and history[-1] <= settings.tolerance)
        else:
            raise DataError("不支持的基线方法。")
    corrected = oriented - base
    background = sign * base
    if not np.isfinite(corrected).all() or not np.isfinite(background).all():
        raise DataError("基线计算产生了无效数值，请调整参数或有效区间。")
    if not converged:
        notices.append("基线迭代未达到收敛条件，请调整平滑度或方法后重算。")
    notices.extend(str(w.message) for w in caught)
    # A reset-sized derivative is a warning only: narrow absorption peaks can also have large slopes.
    jumps = np.abs(np.diff(raw))
    median_jump = float(np.median(jumps))
    span = float(np.ptp(raw))
    abrupt = np.flatnonzero((jumps > max(25 * median_jump, .12 * span)) & (span > 0))
    if len(abrupt):
        notices.append("记录中存在陡变；可能是窄峰或扫描回跳，请检查有效区间。")
    diagnostics = dict(
        application_version=__version__, pybaselines_version=pybaselines.__version__,
        points=n, effective_lambda=effective_lambda if settings.method != "none" else None,
        tolerance_history=history, converged=converged, polarity_detection=suggestion,
        notices=list(dict.fromkeys(notices)), encoding=table.encoding,
        header_lines=table.header_lines,
        reconstruction_max_error=float(np.max(np.abs(sign * (raw - background) - corrected))),
        wavenumber_calibrated=False, absorbance=False, quantitative_validation=False,
    )
    metadata_final = infer_metadata(table.path)
    metadata_final.update(metadata or {})
    return Result(str(table.path), table.digest, settings, x, raw, background, corrected, rows,
                  source_lines, sign, diagnostics, metadata_final)


def discover_files(root):
    import os
    excluded = {".venv", ".git", "__pycache__", "outputs", "build", "dist", "最终交付",
                "处理档案", "导出结果", "发布", "红外预处理软件"}
    found = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d.casefold() not in excluded and not d.startswith("."))
        for name in sorted(files):
            if Path(name).suffix.casefold() in (".dat", ".txt", ".csv", ".tsv"):
                found.append(Path(directory, name).resolve())
    return sorted(found, key=lambda p: tuple(int(v) if v.isdigit() else v.casefold()
                  for v in re.split(r"(\d+)", str(p))))


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
