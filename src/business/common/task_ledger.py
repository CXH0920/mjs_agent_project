"""任务结果台账：JSONL 追加，跨日志轮转窗口留住每次任务的成功/失败记录。

日志按 10MB×5 轮转（65~78 天窗口），失败痕迹会随轮转消失；台账是唯一能让
"某天某任务是否失败"任何时候可查的手段。写者为各任务的主导进程（AI 生成
由 UI 父进程写、维护脚本/索引构建由脚本自身写），彼此没有先后约定，
因此追加必须靠跨进程文件锁串行化——实测四个进程各写 400 条（NTFS）时，
仅靠 O_APPEND 会静默丢 2%~12% 的记录，且不抛出任何异常。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)

LEDGER_PATH = PROJECT_ROOT / "logs" / "task_results.jsonl"

# 锁竞争的有界等待：台账写入发生在任务收尾，等待上限必须远小于用户可感知阈值；
# 超过上限则降级为无锁写入——宁可极端并发下少记一条，也不拖住任务流程
_LOCK_RETRY_TIMES = 50
_LOCK_RETRY_INTERVAL_S = 0.02


def _lock_path(target: Path) -> Path:
    """锁文件与台账同目录、同名加 .lock 后缀；进程崩溃时由 OS 自动释放。"""
    return target.with_name(target.name + ".lock")


def _try_lock(fd: int) -> bool:
    """对锁文件首字节加非阻塞独占锁；被占用时返回 False 而不是抛出。"""
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


@contextmanager
def _ledger_lock(target: Path):
    """串行化同一台账文件的并发追加。

    锁文件本身不可用（文件系统不支持、权限异常）时降级为无锁写入：台账是
    辅助设施，不能因为锁机制故障而让任务本身失败。
    """
    lock_file = _lock_path(target)
    try:
        fd = os.open(lock_file, os.O_RDWR | os.O_CREAT)
    except OSError as error:
        logger.warning("任务台账锁文件创建失败，本次不加锁写入 %s: %s", lock_file, error)
        yield
        return
    locked = False
    try:
        for _ in range(_LOCK_RETRY_TIMES):
            if _try_lock(fd):
                locked = True
                break
            time.sleep(_LOCK_RETRY_INTERVAL_S)
        if not locked:
            logger.warning("任务台账锁等待超时，本次降级为无锁写入 %s", target)
        yield
    finally:
        try:
            os.close(fd)
        except OSError as error:
            logger.warning("任务台账锁文件关闭失败 %s: %s", lock_file, error)


def record_task(task: str, *, ok: bool, exit_code: int = 0, total: int = 0,
                failed: int = 0, duration_s: float = 0.0, reason: str = "",
                path: Path | None = None) -> None:
    """追加一条任务记录；写失败仅告警不抛出（台账是辅助设施，不得影响任务本身）。"""
    target = path or LEDGER_PATH
    try:
        # 归一化放在 try 内：调用方传入 None/字符串等脏值时这里是"尽力记录"，
        # 不能因为台账字段类型不对而中断任务收尾流程
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "task": task,
            "ok": bool(ok),
            "exit_code": int(exit_code),
            "total": int(total),
            "failed": int(failed),
            "duration_s": round(float(duration_s), 1),
            "reason": str(reason)[:200],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        with _ledger_lock(target):
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
    except (OSError, TypeError, ValueError) as error:
        logger.warning("任务台账写入失败 %s: %s", target, error)


def read_entries(path: Path | None = None, *, task: str | None = None,
                 only_failures: bool = False) -> list[dict]:
    """读取台账（可按任务名过滤、只看失败）；文件缺失或单行损坏时跳过。"""
    target = path or LEDGER_PATH
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    skipped = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        # 合法 JSON 但非对象（如外部工具追加的 123、["x"]）同样跳过：
        # 否则 entry.get 抛 AttributeError，整份台账都读不出来
        if not isinstance(entry, dict):
            skipped += 1
            continue
        if task and entry.get("task") != task:
            continue
        if only_failures and entry.get("ok"):
            continue
        out.append(entry)
    if skipped:
        logger.warning("任务台账有 %d 行无法解析已跳过: %s", skipped, target)
    return out
