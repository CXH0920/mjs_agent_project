"""
名将杀 Agent - 应用入口

桌面应用主入口点，初始化和启动 PySide6 应用。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox, QSplashScreen
from src.config.env import BUNDLE_ROOT, IS_FROZEN, PROJECT_ROOT
from src.ui.app.chinese_translator import install_chinese_qt_translator
from src.ui.app.main_window import MainWindow
from src.ui.shared.style import GLOBAL_STYLE

logger = logging.getLogger(__name__)


def _ensure_clean_runtime() -> None:
    """frozen 下首启在 exe 同级生成可写运行时骨架，不覆盖已有用户数据。

    开发态（IS_FROZEN=False）直接返回，不影响开发流程。打包产物不含
    config.env / edge_profile / logs 等用户资料，由本函数首次启动补齐。
    """
    if not IS_FROZEN:
        return
    # config.env：从打包模板复制（用户首启填写 API Key），不覆盖已有
    env_file = PROJECT_ROOT / "config.env"
    if not env_file.exists():
        template = BUNDLE_ROOT / "config.env.example"
        if template.exists():
            shutil.copy2(template, env_file)
    # 可写运行时目录（用户数据 / 日志 / 截图 / 模板 / 配置）
    for name in ("data", "logs", "config", "templates", "images"):
        (PROJECT_ROOT / name).mkdir(parents=True, exist_ok=True)
    # ROI 用户配置：从默认复制可改副本
    user_roi = PROJECT_ROOT / "config" / "ocr_rois.json"
    if not user_roi.exists():
        default_roi = BUNDLE_ROOT / "config" / "ocr_rois.default.json"
        if default_roi.exists():
            shutil.copy2(default_roi, user_roi)
    # 打包资料部署：BUNDLE_ROOT/data 的静态资料（核心库 json / 官方榜单 csv / RAG 语料 /
    # 评估集 / raw_guides 等）复制到运行时根——维护脚本、构建脚本等读 PROJECT_ROOT/data，
    # 不部署会全量报"缺源"（task_states）。只补缺失文件，不覆盖用户已有数据
    bundle_data = BUNDLE_ROOT / "data"
    if bundle_data.is_dir():
        for src in bundle_data.rglob("*"):
            if not src.is_file():
                continue
            dst = PROJECT_ROOT / "data" / src.relative_to(bundle_data)
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
    # 元规则母本（元规则/术语/FAQ 语料任务的源，build_rule_corpus 读 PROJECT_ROOT/docs/）
    meta_doc = BUNDLE_ROOT / "docs" / "元规则整理-完整版.md"
    target_doc = PROJECT_ROOT / "docs" / "元规则整理-完整版.md"
    if meta_doc.is_file() and not target_doc.exists():
        target_doc.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(meta_doc, target_doc)


def _install_no_window_patch() -> None:
    """frozen 下永久 patch subprocess.Popen，抑制所有子进程的控制台弹窗。

    windowed 打包（console=False）后父进程无控制台，每次调 adb 等控制台子程序，
    Windows 会为其新建控制台窗口（轮询黑窗）。patch 给 Popen.__init__ 注入
    CREATE_NO_WINDOW 规避。开发态不 patch，保留控制台便于调试。
    subprocess.run 内部走 Popen，一并覆盖。
    """
    if not IS_FROZEN:
        return
    import subprocess

    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if not no_window:
        return  # 非 Windows 无此常量
    original_init = subprocess.Popen.__init__

    def hidden_init(self, *args, **kwargs):
        kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | no_window
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = hidden_init


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
    _ensure_clean_runtime()
    _install_no_window_patch()
    # 提前初始化日志（在 QApplication 创建之前）
    from src.config.env import get_runtime_params
    from src.config.logging_config import setup_logging
    runtime_params = get_runtime_params()
    setup_logging(
        log_level=runtime_params["log_level"],
        log_to_file=runtime_params["log_to_file"],
    )

    # 首次启动迁移：旧 DEEPSEEK_* 三件套 → 默认档案（幂等，文件已存在即跳过）
    from src.config.env import migrate_legacy_api_config
    migrate_legacy_api_config()

    # 抑制 Qt 字体回退调试日志（Windows 上大量刷屏但无害）
    os.environ.setdefault("QT_LOGGING_RULES",
                          "qt.qpa.fonts=false;qt.text.font.db=false")

    # Windows cmd 默认 GBK，设置 stdout/stderr 为 UTF-8 避免中文乱码
    # windowed 打包模式双击启动时 sys.stdout/stderr 为 None，需守卫避免崩溃
    if sys.platform == "win32":
        if sys.stdout is not None:
            sys.stdout.reconfigure(encoding="utf-8")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8")

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
        except Exception as error:
            logger.debug("AppUserModelID 设置失败（任务栏图标回退默认）: %s", error)

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
        # 在启动画面阶段完成 OCR 预热：Paddle 初始化会长时间持有 Python GIL，
        # 若预热与主窗口事件循环同时运行会导致界面卡住，故先预热后显示。
        # 模型冷加载实测可达 90 秒以上，超时须覆盖加载全程，避免窗口显示后
        # 预热仍占用 GIL 导致界面反复未响应。
        splash.showMessage("正在加载 OCR 模型…")
        app.processEvents()
        window.wait_ocr_warmup(timeout_ms=120_000)
        window.show()
        splash.finish(window)
        sys.exit(app.exec())
    except Exception as e:
        splash.close()
        logger.exception("应用启动失败")
        QMessageBox.critical(
            None, "启动失败",
            f"应用启动时发生未预期的错误:\n\n{e}"
        )
        sys.exit(1)


if __name__ == "__main__":
    # frozen 下 QProcess 用 sys.executable(=mjs_agent.exe) -m <module> 跑子脚本
    # （AI 攻略/相性/武将生成走 src.scraper.ai_batch 等 -m 模块）。
    # exe 重入时识别 -m 走 runpy 模块模式，否则会启动 GUI（表现为又开一个 exe 实例）。
    # 开发态 python 自己处理 -m，不触发此分支。
    if IS_FROZEN and len(sys.argv) > 2 and sys.argv[1] == "-m":
        import runpy

        _module = sys.argv[2]
        sys.argv = [sys.argv[0]] + sys.argv[3:]  # 去掉 -m <module>，剩余参数留给脚本
        runpy.run_module(_module, run_name="__main__", alter_sys=False)
        sys.exit(0)
    main()
