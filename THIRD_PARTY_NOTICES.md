# 第三方开源组件

本程序使用以下未修改的开源组件。版本来自本次构建环境，完整声明在 licenses 目录。
Qt / PySide6 / Shiboken 使用可替换的动态库；允许为调试这些 LGPL 库的修改而逆向工程。
本程序源码与构建说明随本地项目提供，不包含用户原始测量数据。

| 组件 | 构建版本 | 许可 | 对应源码 |
|---|---|---|---|
| pybaselines | 1.2.1 | BSD-3-Clause | https://github.com/derb12/pybaselines |
| pyqtgraph | 0.14.0 | MIT | https://github.com/pyqtgraph/pyqtgraph |
| PySide6-Essentials | 6.12.0 | LGPL-3.0-only (selected open-source license) | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| shiboken6 | 6.12.0 | LGPL-3.0-only (selected open-source license) | https://code.qt.io/cgit/pyside/pyside-setup.git/ |
| numpy | 2.4.6 | BSD-3-Clause and bundled notices | https://github.com/numpy/numpy |
| scipy | 1.17.1 | BSD-3-Clause and bundled notices | https://github.com/scipy/scipy |
| colorama | 0.4.6 | BSD-3-Clause | https://github.com/tartley/colorama |
| pyinstaller | 6.22.3 | GPL with bootloader distribution exception | https://github.com/pyinstaller/pyinstaller |
| Python | 3.11.9 | PSF License and included notices | https://www.python.org/downloads/source/ |

## Qt / PySide6 对应源码与库替换

LGPL 与其引用的 GPL 全文见 LGPL-3.0.txt、GPL-3.0.txt。
Qt for Python 对应源码：https://download.qt.io/official_releases/QtForPython/
Qt 对应源码：https://download.qt.io/official_releases/qt/
各模块与第三方声明：https://doc.qt.io/qt-6/licenses-used-in-qt.html
Qt for Python 声明：https://doc.qt.io/qtforpython-6/licenses.html
请保留与所分发版本对应的声明。程序没有修改 Qt / PySide6；原始源码按上述组件版本获取。
动态库位于 _internal/PySide6 与 _internal/shiboken6；可用同版本、同 ABI 的自行构建库替换。
源码运行：安装 requirements.txt 后运行 app.py；打包：运行 build.ps1。

## 基线算法来源

arPLS: Baek et al., Analyst (2015), 140, 250–257. DOI: 10.1039/C4AN01061B.
本程序调用 pybaselines 的公开实现，完整记录实际参数和收敛结果。

Windows 系统字体仅在当前电脑上读取，不随程序分发。
