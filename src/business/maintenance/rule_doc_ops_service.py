"""元规则维护业务操作服务（#A3）。

把此前压在 RuleDocPanel 的业务规则收进 business 层：
确认行校验规则、确认清单落盘。脚本编排在 UI 侧经 ScriptRunner（business/common）
执行，参数拼装语义由本服务的常量与函数约定。
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.business.rag import rule_doc_service as rds
from src.data.json_repository import atomic_write_json

logger = logging.getLogger(__name__)


def validate_confirmed_row(diff: dict, new: str) -> str | None:
    """校验一行确认值；返回错误消息（None 表示通过）。

    规则：非空、完整表格行（| 开头结尾）、列数与原文一致。
    """
    section = diff.get("section", "")
    if not new:
        return f"确认值为空，请填写后重试（段 {section}）"
    if not (new.startswith("|") and new.endswith("|")):
        return f"确认值不是完整表格行（需以 | 开头和结尾）（段 {section}）"
    if diff.get("old") and len(new.split("|")) != len(diff["old"].split("|")):
        return f"确认值列数与原文不一致，会破坏表格结构（段 {section}）"
    return None


def save_confirmed_diffs(root: Path, rows: list[dict]) -> Path:
    """确认清单原子写盘（sync_rule_stats.py --apply-json 的输入）。"""
    path = rds.confirmed_diff_path(root)
    atomic_write_json(path, rows)
    logger.info("确认清单已写入 %s（%d 行）", path, len(rows))
    return path
