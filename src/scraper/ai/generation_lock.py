"""AI 批量生成任务互斥锁。

生成结果以"启动时快照 + 全量覆盖"方式分批提交（见 generation.py），
两个生成进程并发写同一 guides/synergies 文件时，后提交者会静默抹掉
对方已提交的成果。本锁用 OS 级文件锁（Windows msvcrt / POSIX fcntl）
保证同一时刻只有一个批量生成任务运行；持有进程退出（含崩溃断电）时
锁由 OS 自动释放，不会留下需要人工清理的残留锁。

持有者信息（pid + 获取时间）写入旁路 ``*.holder`` 文件而非锁文件本体：
Windows 字节锁会拒绝其他句柄对锁文件的常规读取，旁路文件无锁随时可读。
holder 内容仅为占用提示；进程崩溃后可能残留旧记录，互斥判定以 OS 锁
为准（措辞用"最近持有记录"）。
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def _try_lock_byte(fd: int) -> None:
    """对锁文件第 0 字节加非阻塞独占锁；被其他任务持有时抛 OSError。"""
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _holder_path(path: Path) -> Path:
    return path.with_name(path.name + ".holder")


class GenerationLock:
    """持有中的生成锁；release 关闭句柄即解锁（fd=-1 表示降级哑锁）。"""

    def __init__(self, path: Path, fd: int) -> None:
        self._path = path
        self._fd = fd

    @property
    def path(self) -> Path:
        return self._path

    def release(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError as error:
                logger.warning("生成锁句柄关闭失败 %s: %s", self._path, error)
            self._fd = -1


def acquire_generation_lock(path: str | Path) -> GenerationLock | None:
    """尝试获取生成任务互斥锁。

    Returns:
        持有锁的 GenerationLock；已被其他任务占用时返回 None（调用方应
        拒绝启动）。锁文件本身不可用时返回哑锁降级放行（保持无锁旧行为），
        仅记警告，不因锁机制故障阻断生成。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT)
    except OSError as error:
        logger.warning("生成锁文件创建失败，本次不加锁继续: %s: %s", path, error)
        return GenerationLock(path, -1)
    try:
        _try_lock_byte(fd)
    except OSError:
        holder = _read_holder(_holder_path(path))
        logger.warning("生成锁已被其他任务占用（最近持有记录: %s）", holder or "未知")
        os.close(fd)
        return None
    _write_holder(path)
    return GenerationLock(path, fd)


def _write_holder(path: Path) -> None:
    """把本进程的持有信息写入旁路 holder 文件（失败仅告警，不影响互斥）。"""
    try:
        _holder_path(path).write_text(
            f"{os.getpid()}\t{datetime.now().isoformat(timespec='seconds')}\n",
            encoding="utf-8",
        )
    except OSError as error:
        logger.warning("生成锁持有信息写入失败（不影响互斥）: %s", error)


def _read_holder(holder_file: Path) -> str:
    """读取 holder 文件的持有者信息（pid + 获取时间），供占用提示与日志。"""
    try:
        first = holder_file.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, ValueError):
        return ""
    pid, _, started = first.partition("\t")
    return f"PID {pid}，始于 {started}" if started else first
