"""
名将杀 Agent - 日志配置中心

统一管理全项目日志格式、输出目标和日志轮转策略。
CLI 入口和桌面应用入口各调用一次 setup_logging() 即可。
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from src.config.env import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / "logs"

LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

DEFAULT_LEVEL = "INFO"
DEFAULT_MAX_MB = 10
DEFAULT_BACKUP_COUNT = 5
_MANAGED_HANDLER_ATTR = "_mjs_managed_handler"
_QPROCESS_CHILD_ENV = "MJS_QPROCESS_CHILD"


class ModuleFilter(logging.Filter):
    """按 logger name 前缀过滤日志记录"""

    def __init__(self, startswith: list[str] | None = None,
                 exclude_startswith: list[str] | None = None):
        super().__init__()
        self._startswith = startswith or []
        self._exclude_startswith = exclude_startswith or []

    def filter(self, record: logging.LogRecord) -> bool:
        name = record.name
        for prefix in self._exclude_startswith:
            if name.startswith(prefix):
                return False
        if self._startswith:
            for prefix in self._startswith:
                if name.startswith(prefix):
                    return True
            return False
        return True


def setup_logging(
    log_level: str = DEFAULT_LEVEL,
    log_to_file: bool = True,
    log_max_mb: int = DEFAULT_MAX_MB,
    log_backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    """配置全局日志系统

    桌面应用和直接运行的 CLI 入口均根据 config.env 决定是否写文件。
    由桌面应用启动的 QProcess 子进程只输出控制台日志，由父进程统一收集。

    Args:
        log_level: DEBUG/INFO/WARNING/ERROR
        log_to_file: 是否同时写入日志文件
        log_max_mb: 单个日志文件最大 MB
        log_backup_count: 保留的备份文件数
    """
    level = LEVEL_MAP.get(str(log_level).upper(), logging.INFO)
    root = logging.getLogger()

    # 只移除本模块之前创建的 Handler，保留 pytest/宿主程序等外部 Handler。
    for handler in root.handlers[:]:
        if getattr(handler, _MANAGED_HANDLER_ATTR, False):
            root.removeHandler(handler)
            handler.close()

    # root 下限 WARNING：第三方库（chromadb/transformers 等）是 root 的直接子、
    # NOTSET，有效级别继承 root，其 INFO/DEBUG 在 logger 层即被挡、不创建 LogRecord
    # （零库名清单的高效压制）。项目 src/subprocess 前缀在下方单独设 DEBUG 全量创建。
    root.setLevel(max(level, logging.WARNING))

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # === 控制台 Handler ===
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    setattr(console, _MANAGED_HANDLER_ATTR, True)
    root.addHandler(console)

    # QProcess 子进程的 stdout/stderr 会被父进程统一收集，避免多个进程
    # 同时轮转同一组文件导致 Windows 文件占用和备份竞争。
    is_qprocess_child = os.getenv(_QPROCESS_CHILD_ENV) == "1"
    if not log_to_file or is_qprocess_child:
        return

    max_bytes = max(log_max_mb, 1) * 1024 * 1024

    # === 文件 Handler 定义 ===
    # 第 4 元素 keep_debug=True 的文件承载子进程 stdout/stderr 转发流，handler
    # 级别固定 DEBUG（不跟随用户级别），保证子进程原始输出在 WARNING 模式下也不
    # 丢失；纯 src 路由文件跟随用户级别，由 handler 层裁剪。
    file_handlers: list[tuple[str, list[str] | None, list[str] | None, bool]] = [
        # (文件名, startswith, exclude_startswith, keep_debug)
        ("app.log",               None,
         ["src.scraper", "src.business", "src.data", "src.ocr", "src.capture", "src.rag", "subprocess."],
         False),
        ("scraper/official.log", ["src.scraper", "subprocess.official"], ["src.scraper.ai"], True),
        ("scraper/ai_generation.log", ["src.scraper.ai", "subprocess.ai"], None, True),
        ("business/fetching.log", ["src.business.fetching"], None, False),
        ("business/emulator.log", ["src.business.emulator"], None, False),
        ("business/recognition.log", ["src.business.recognition"], None, False),
        ("business/business.log", ["src.business"], [
            "src.business.fetching",
            "src.business.emulator",
            "src.business.recognition",
        ], False),
        ("data/data.log",         ["src.data"],     None, False),
        ("ocr/ocr.log",           ["src.ocr"],      None, False),
        ("capture/capture.log",   ["src.capture"],  None, False),
        ("rag/rag.log",           ["src.rag"],      None, False),
        ("subprocess/unclassified.log", ["subprocess"], [
            "subprocess.official",
            "subprocess.ai",
        ], True),
    ]

    for rel_path, starts, excludes, keep_debug in file_handlers:
        log_path = LOG_DIR / rel_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=log_backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG if keep_debug else level)
        handler.setFormatter(formatter)
        handler.addFilter(ModuleFilter(startswith=starts, exclude_startswith=excludes))
        setattr(handler, _MANAGED_HANDLER_ATTR, True)
        root.addHandler(handler)

    # debug.log：跨模块全量留底（DEBUG、无前缀过滤），单独较大轮转上限。
    debug_handler = logging.handlers.RotatingFileHandler(
        LOG_DIR / "debug.log",
        maxBytes=max_bytes * 2,
        backupCount=max(log_backup_count, 1),
        encoding="utf-8",
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(formatter)
    setattr(debug_handler, _MANAGED_HANDLER_ATTR, True)
    root.addHandler(debug_handler)

    # 反转级别分配：项目 src/subprocess 前缀恒定 DEBUG 全量创建，成全 debug.log 留底
    # 与子进程输出转发；常规 src 文件由 handler 级别裁剪跟随用户。
    logging.getLogger("src").setLevel(logging.DEBUG)
    logging.getLogger("subprocess").setLevel(logging.DEBUG)

    logging.getLogger(__name__).info(
        "日志系统初始化完成: level=%s, file=%s, qprocess_child=%s",
        log_level,
        log_to_file,
        is_qprocess_child,
    )
