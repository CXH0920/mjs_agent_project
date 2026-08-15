# -*- coding: utf-8 -*-
"""知识库维护工作台测试：任务状态计算、审计摘要与面板渲染。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.ui.maintenance.rag_maintenance_panel import (
    RagMaintenancePanel,
    audit_summary,
    task_states,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _utime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    now = time.time()
    # 数据源
    _write(root / "data" / "heroes.json", [{"id": 1, "name": "乐广"}, {"id": 2, "name": "张华"}])
    _write(root / "data" / "cards.json", [{"id": "8", "name": "杀"}])
    _write(root / "data" / "card_annotations.json", {"annotations": []})
    _write(root / "data" / "special_cards.json", [{"category": "专属牌", "name": "龙泉剑", "hero": "赵云"}])
    _write(root / "data" / "hero_classification.json", {"hero_categories": {"乐广": ["爆发型"]}})
    _write(root / "data" / "mjs卡牌点数.xlsx", [])
    _write(root / "docs" / "元规则整理-完整版.md", "# 元规则")
    # 语料输出（全部已生成）
    for name in ("武将RAG语料.json", "卡牌RAG语料.json", "卡牌点数花色语料.json",
                 "装备属性语料.json", "加强削弱语料.json", "元规则RAG语料-章节块.json",
                 "术语表.json", "FAQ裁定块.json", "特殊机制语料.json", "武将分类语料.json"):
        _write(root / "data" / "rag_corpus" / name, [{"block_id": "x"}])
    for path in root.rglob("*"):
        if path.is_file():
            _utime(path, now - 100)
    return root


def test_all_tasks_up_to_date(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    rows = task_states(root)
    assert len(rows) == 8
    assert all(row["status"] == "最新" for row in rows)
    assert all(row["count"] == 1 for row in rows)


def test_stale_when_source_newer(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _utime(root / "data" / "heroes.json", time.time() + 60)
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["武将语料"] == "待重建"
    assert rows["武将分类语料"] == "待重建"
    assert rows["卡牌语料"] == "最新"


def test_missing_output_marks_rebuild(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "data" / "rag_corpus" / "特殊机制语料.json").unlink()
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["特殊机制语料"] == "待重建"


def test_missing_source(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / "data" / "special_cards.json").unlink()
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["特殊机制语料"] == "缺源"


def test_audit_reports_unclassified_and_unknown_hero(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    issues = audit_summary(root)
    texts = "\n".join(issues)
    assert "未归类武将" in texts
    assert "专属牌引用未知武将" in texts


def test_panel_renders(tmp_path: Path) -> None:
    _app()
    root = _make_root(tmp_path)
    panel = RagMaintenancePanel(root=root)
    assert panel._tabs.count() == 3
    assert [panel._tabs.tabText(i) for i in range(panel._tabs.count())] == [
        "语料状态", "专属牌维护", "武将分类维护",
    ]
    assert panel._table.rowCount() == 8
    assert "所有语料与数据源一致" in panel._status_label.text()
    assert "人工维护提示" in panel._audit_label.text()
    assert hasattr(panel, "_special_cards")
    assert hasattr(panel, "_classification")
    panel.close()