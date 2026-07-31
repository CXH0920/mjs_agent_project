"""
名将杀 Agent - 应用入口

桌面应用主入口点，初始化和启动 PySide6 应用。
"""

from __future__ import annotations

import logging
import os
import sys
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen

from src.ui.app.main_window import MainWindow
from src.ui.app.chinese_translator import install_chinese_qt_translator
from src.ui.shared.style import GLOBAL_STYLE


def _create_startup_splash() -> QSplashScreen:
    """创建主窗口初始化期间显示的启动页。"""
    pixmap = QPixmap(420, 220)
    pixmap.fill(QColor("#f4f8fc"))

    painter = QPainter(pixmap)
    painter.setPen(QColor("#2c5f91"))
    title_font = QFont()
    title_font.setPointSize(22)
    title_font.setBold(True)
    painter.setFont(title_font)
    painter.drawText(pixmap.rect().adjusted(24, 52, -24, -96), Qt.AlignmentFlag.AlignCenter, "名将杀 Agent")

    painter.setPen(QColor("#6b7c93"))
    subtitle_font = QFont()
    subtitle_font.setPointSize(11)
    painter.setFont(subtitle_font)
    painter.drawText(pixmap.rect().adjusted(24, 126, -24, -42), Qt.AlignmentFlag.AlignCenter, "正在加载数据和界面…")
    painter.end()

    splash = QSplashScreen(pixmap)
    splash.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    return splash


def main() -> None:
    """应用主函数"""
    # 提前初始化日志（在 QApplication 创建之前）
    from src.config.env import get_runtime_params
    from src.config.logging_config import setup_logging
    runtime_params = get_runtime_params()
    setup_logging(
        log_level=runtime_params["log_level"],
        log_to_file=runtime_params["log_to_file"],
    )

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
    _translator = install_chinese_qt_translator(app)

    # Windows 任务栏图标修正：设置 AppUserModelID 确保自定义图标生效
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MingJiangSha.MJSAgent"
            )
        except Exception:
            pass

    # 尽早设置并持续维护应用图标（在 PaddleOCR 等耗时操作之前）
    from src.ui.app.app_icon import install_app_icon
    install_app_icon(app)

    # 设置全局样式
    app.setStyleSheet(GLOBAL_STYLE)
    splash = _create_startup_splash()
    splash.show()
    app.processEvents()

    try:
        window = MainWindow()
        window.start_ocr_warmup()
        window.show()
        splash.finish(window)
        sys.exit(app.exec())
    except Exception as e:
        splash.close()
        logger = logging.getLogger(__name__)
        logger.exception("应用启动失败")
        QMessageBox.critical(
            None, "启动失败",
            f"应用启动时发生未预期的错误:\n\n{e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
