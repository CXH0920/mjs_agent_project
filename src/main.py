"""
名将杀 Agent - 应用入口

桌面应用主入口点，初始化和启动 PySide6 应用。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from src.ui.main_window import MainWindow
from src.ui.style import GLOBAL_STYLE


def main() -> None:
    """应用主函数"""
    # 抑制 Qt 字体回退调试日志（Windows 上大量刷屏但无害）
    os.environ.setdefault("QT_LOGGING_RULES",
                          "qt.qpa.fonts=false;qt.text.font.db=false")

    # Windows cmd 默认 GBK，设置 stdout 为 UTF-8 避免中文乱码
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")

    # 设置高 DPI 支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("名将杀 Agent")
    app.setOrganizationName("MingJiangSha")
    app.setApplicationVersion("0.1.0")

    # 设置全局样式
    app.setStyleSheet(GLOBAL_STYLE)

    # 设置应用图标
    icon_path = Path(__file__).resolve().parent.parent / "mjs.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    try:
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logging.exception("应用启动失败")
        QMessageBox.critical(
            None, "启动失败",
            f"应用启动时发生未预期的错误:\n\n{e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
