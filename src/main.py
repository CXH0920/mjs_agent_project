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
    # 提前初始化日志（在 QApplication 创建之前）
    from src.config.logging_config import setup_logging
    setup_logging(log_level="INFO", log_to_file=True)

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

    # Windows 任务栏图标修正：设置 AppUserModelID 确保自定义图标生效
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "MingJiangSha.MJSAgent"
            )
        except Exception:
            pass

    # 尽早设置应用图标（在 PaddleOCR 等耗时操作之前）
    icon_path = Path(__file__).resolve().parent.parent / "mjs.ico"
    if icon_path.exists():
        app_icon = QIcon(str(icon_path))
        app.setWindowIcon(app_icon)

    # 设置全局样式
    app.setStyleSheet(GLOBAL_STYLE)

    # 提前初始化 PaddleOCR（在应用启动时加载模型，避免首次识别卡顿）
    try:
        from src.ocr.recognizer import GeneralRecognizer
        from src.data.manager import DataFacade, DEFAULT_HEROES_FILE, DEFAULT_SYNERGIES_FILE, DEFAULT_GUIDES_FILE
        logger = logging.getLogger(__name__)
        logger.info("正在提前初始化 PaddleOCR ...")

        # 创建一个带武将名列表的 recognizer 以便后续复用
        facade = DataFacade(heroes_file=DEFAULT_HEROES_FILE,
                            synergies_file=DEFAULT_SYNERGIES_FILE,
                            guides_file=DEFAULT_GUIDES_FILE)
        facade.load_all()
        hero_names = [h.name for h in facade.heroes.list_heroes()]

        recognizer = GeneralRecognizer(hero_names=hero_names)
        recognizer.warmup()
        logger.info("PaddleOCR 初始化完成")
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.warning("PaddleOCR 提前初始化失败: %s（不影响启动，识别时再尝试）", e)

    try:
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.exception("应用启动失败")
        QMessageBox.critical(
            None, "启动失败",
            f"应用启动时发生未预期的错误:\n\n{e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
