"""
名将杀 Agent - 应用入口

桌面应用主入口点，初始化和启动 PySide6 应用。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from src.ui.main_window import MainWindow


def main() -> None:
    """应用主函数"""
    # 设置高 DPI 支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("名将杀 Agent")
    app.setOrganizationName("MingJiangSha")
    app.setApplicationVersion("0.1.0")

    # 设置应用样式
    app.setStyle("Fusion")

    try:
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logging.exception("应用启动失败")
        QMessageBox.critical(
            None, "启动失败",
            f"应用启动时发生未预期的错误:\\n\\n{e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
