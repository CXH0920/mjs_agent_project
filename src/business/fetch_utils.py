"""
名将杀 Agent - QProcess 业务服务公共工具函数

提供 QProcess 生命周期管理中频繁重复的代码片段的共享函数，
消除 guide_fetch_service.py 和 synergy_fetch_service.py 之间的重复。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QProcess

logger = logging.getLogger(__name__)


def is_process_busy(process: QProcess | None, service_name: str) -> bool:
    """检查 QProcess 是否正在运行，若忙则打 warning 并返回 True。"""
    if process and process.state() != QProcess.ProcessState.NotRunning:
        logger.warning("%s 服务正忙，忽略重复请求", service_name)
        return True
    return False


def cancel_process(process: QProcess | None) -> None:
    """请求终止当前 QProcess，完成收尾由 finished 信号处理。"""
    if process and process.state() != QProcess.ProcessState.NotRunning:
        process.kill()


def get_qprocess_error_name(error: QProcess.ProcessError) -> str:
    """将 QProcess.ProcessError 枚举映射为可读中文错误名。"""
    error_map = {
        QProcess.ProcessError.FailedToStart: "子进程启动失败",
        QProcess.ProcessError.Crashed: "子进程崩溃",
        QProcess.ProcessError.Timedout: "子进程超时",
        QProcess.ProcessError.WriteError: "写入子进程管道失败",
        QProcess.ProcessError.ReadError: "读取子进程管道失败",
    }
    return error_map.get(error, f"未知错误({error})")


def log_process_error(error_name: str, process: QProcess | None) -> str:
    """记录 QProcess 错误日志，返回完整的错误消息字符串。"""
    error_msg = process.errorString() if process else "未知错误"
    full_msg = f"{error_name}: {error_msg}"
    logger.error("子进程错误: %s", full_msg)
    return full_msg
