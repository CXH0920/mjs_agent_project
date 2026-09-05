"""rag_common.install_crash_logger 行为锚（批次7步骤3）。

锁三条契约：未处理异常 → ①traceback 落入 logs/rag/<script>.log；②stdout 打
"❌ 执行失败"标记行；③KeyboardInterrupt 透传给系统 excepthook（不吞 Ctrl+C）。
"""

from __future__ import annotations

import logging
import sys

from src.config.env import PROJECT_ROOT
from src.scripts.rag_common import install_crash_logger


def _cleanup_script_logger(name: str) -> None:
    """关闭并摘除脚本 logger 的 handlers 后删除日志文件（Windows 句柄锁）。"""
    logger = logging.getLogger(f"rag_script.{name}")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    (PROJECT_ROOT / "logs" / "rag" / f"{name}.log").unlink(missing_ok=True)


def test_hook_logs_traceback_and_prints_marker(capsys) -> None:
    name = "unittest_crash_hook"
    install_crash_logger(name)
    try:
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())

        out = capsys.readouterr().out
        assert "❌ 执行失败" in out
        assert name in out  # 面板能看到日志文件位置
        log_text = (PROJECT_ROOT / "logs" / "rag" / f"{name}.log").read_text(encoding="utf-8")
        assert "未处理异常" in log_text
        assert "boom" in log_text  # 完整堆栈落文件
    finally:
        _cleanup_script_logger(name)


def test_hook_passes_keyboard_interrupt_through(capsys) -> None:
    """Ctrl+C 不被吞：透传系统 excepthook 且不打 ❌ 标记。"""
    name = "unittest_ki_hook"
    install_crash_logger(name)
    forwarded: list[type[BaseException]] = []
    original = sys.__excepthook__
    sys.__excepthook__ = lambda exc_type, *_: forwarded.append(exc_type)
    try:
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            sys.excepthook(*sys.exc_info())

        assert "❌" not in capsys.readouterr().out
        assert forwarded == [KeyboardInterrupt]
    finally:
        sys.__excepthook__ = original
        _cleanup_script_logger(name)
