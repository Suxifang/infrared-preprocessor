from pathlib import Path
import numpy as np


def create_demo(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    x = np.arange(1, 2001, dtype=float)
    t = (x - 1) / 1999
    for i in range(3):
        rng = np.random.default_rng(120 + i)
        background = .052 + .018 * t - .006 * t ** 2
        peaks = (.018 * np.exp(-.5 * ((t - .24 - i * .018) / .012) ** 2)
                 + .036 * np.exp(-.5 * ((t - .52) / .026) ** 2)
                 + .010 * np.exp(-.5 * ((t - .77) / .008) ** 2))
        y = background - peaks + rng.normal(0, .00010, len(x))
        path = folder / f"演示合成_{i + 1}.dat"
        np.savetxt(path, np.column_stack((x, y)), fmt="%.17g", delimiter="\t",
                   header="合成演示数据，不是真实样品", encoding="utf-8")
        paths.append(path)
    return paths
