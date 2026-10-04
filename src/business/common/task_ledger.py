"""任务结果台账：JSONL 追加，跨日志轮转窗口留住每次任务的成功/失败记录。

日志按 10MB×5 轮转（65~78 天窗口），失败痕迹会随轮转消失；台账是唯一能让
"某天某任务是否失败"任何时候可查的手段。写者为各任务的主导进程（AI 生成
由 UI 父进程写、维护脚本/索引构建由脚本自身写），天然无并发冲突。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)

LEDGER_PATH = PROJECT_ROOT / "logs" / "task_results.jsonl"


def record_task(task: str, *, ok: bool, exit_code: int = 0, total: int = 0,
                failed: int = 0, duration_s: float = 0.0, reason: str = "",
                path: Path | None = None) -> None:
    """追加一条任务记录；写失败仅告警不抛出（台账是辅助设施，不得影响任务本身）。"""
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
    target = path or LEDGER_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
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
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if task and entry.get("task") != task:
            continue
        if only_failures and entry.get("ok"):
            continue
        out.append(entry)
    return out
