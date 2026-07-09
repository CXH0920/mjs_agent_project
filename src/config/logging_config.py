"""
名将杀 Agent - 日志配置中心

统一管理全项目日志格式、输出目标和日志轮转策略。
CLI 入口和桌面应用入口各调用一次 setup_logging() 即可。
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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

    桌面应用入口 (src/main.py) 调用时启用文件日志。
    CLI 脚本入口调用时 log_to_file=False，日志仅输出到控制台。

    Args:
        log_level: DEBUG/INFO/WARNING/ERROR
        log_to_file: 是否同时写入日志文件
        log_max_mb: 单个日志文件最大 MB
        log_backup_count: 保留的备份文件数
    """
    level = LEVEL_MAP.get(log_level.upper(), logging.INFO)

    # 避免重复配置导致日志重复
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(level)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # === 控制台 Handler ===
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(formatter)
    root.addHandler(console)

    if not log_to_file:
        return

    max_bytes = max(log_max_mb, 1) * 1024 * 1024

    # === 文件 Handler 定义 ===
    file_handlers: list[tuple[str, list[str] | None, list[str] | None]] = [
        # (文件名, startswith, exclude_startswith)
        ("app.log",               None,
         ["src.scraper.ai_", "src.business.", "subprocess."]),
        ("scraper/scraper.log",   ["src.scraper"], ["src.scraper.ai_"]),
        ("scraper/ai_batch.log",  ["src.scraper.ai_"], None),
        ("business/business.log", ["src.business"], None),
        ("data/data.log",         ["src.data"],     None),
        ("ocr/ocr.log",           ["src.ocr"],      None),
        ("capture/capture.log",   ["src.capture"],  None),
        ("subprocess/stdout.log", ["subprocess.stdout"], None),
        ("subprocess/stderr.log", ["subprocess.stderr"], None),
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
        root.addHandler(handler)

    logging.getLogger(__name__).info("日志系统初始化完成: level=%s, file=%s", log_level, log_to_file)
