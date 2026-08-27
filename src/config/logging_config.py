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
_AI_CHILD_ENV = "MJS_AI_CHILD"


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

    root.setLevel(level)

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
    # AI 子进程例外：父进程把子进程 stdout 统一以 INFO 级转发，root level≥WARNING
    # 时 api_generator 的失败原因（429/length/JSON 等）会被过滤丢失，故 AI 子进程
    # 直写文件。AI 生成是单子进程串行，不触发多进程轮转竞争。
    is_ai_child = os.getenv(_AI_CHILD_ENV) == "1"
    if not log_to_file or (is_qprocess_child and not is_ai_child):
        return

    max_bytes = max(log_max_mb, 1) * 1024 * 1024

    # === 文件 Handler 定义 ===
    file_handlers: list[tuple[str, list[str] | None, list[str] | None]] = [
        # (文件名, startswith, exclude_startswith)
        ("app.log",               None,
         ["src.scraper", "src.business", "src.data", "src.ocr", "src.capture", "subprocess."]),
        ("scraper/official.log", ["src.scraper", "subprocess.official"], ["src.scraper.ai"]),
        ("scraper/ai_generation.log", ["src.scraper.ai", "subprocess.ai"], None),
        ("business/fetching.log", ["src.business.fetching"], None),
        ("business/emulator.log", ["src.business.emulator"], None),
        ("business/recognition.log", ["src.business.recognition"], None),
        ("business/business.log", ["src.business"], [
            "src.business.fetching",
            "src.business.emulator",
            "src.business.recognition",
        ]),
        ("data/data.log",         ["src.data"],     None),
        ("ocr/ocr.log",           ["src.ocr"],      None),
        ("capture/capture.log",   ["src.capture"],  None),
        ("subprocess/unclassified.log", ["subprocess"], [
            "subprocess.official",
            "subprocess.ai",
        ]),
    ]

    for rel_path, starts, excludes in file_handlers:
        log_path = LOG_DIR / rel_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=log_backup_count,
            encoding="utf-8",
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(formatter)
        handler.addFilter(ModuleFilter(startswith=starts, exclude_startswith=excludes))
        setattr(handler, _MANAGED_HANDLER_ATTR, True)
        root.addHandler(handler)

    logging.getLogger(__name__).info(
        "日志系统初始化完成: level=%s, file=%s, qprocess_child=%s",
        log_level,
        log_to_file,
        is_qprocess_child,
    )
