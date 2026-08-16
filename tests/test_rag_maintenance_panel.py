# -*- coding: utf-8 -*-
"""知识库维护工作台测试：任务状态计算、审计摘要与面板渲染。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.business.rag.audit_service import audit_summary, format_audit_issues
from src.ui.maintenance.rag_maintenance_panel import (
    RagMaintenancePanel,
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
    _write(root / "data" / "card_points.json", {"cards": [], "judge_rules": []})
    _write(root / "data" / "equip_attrs.json", [])
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


def test_task_defs_include_card_corpus_link(tmp_path: Path) -> None:
    """#1 回归：任务表为单一事实源，装备属性语料必须声明对卡牌语料的依赖。"""
    from src.business.rag.task_defs import TASKS
    equip = next(t for t in TASKS if t["name"] == "装备属性语料")
    assert "data/rag_corpus/卡牌RAG语料.json" in equip["sources"]


def test_equip_task_tracks_card_corpus_dependency(tmp_path: Path) -> None:
    """#1 回归：卡牌RAG语料.json 变更后装备属性语料应标记待重建（跨语料联动）。"""
    root = _make_root(tmp_path)
    _utime(root / "data" / "rag_corpus" / "卡牌RAG语料.json", time.time() + 60)
    rows = {row["name"]: row["status"] for row in task_states(root)}
    assert rows["装备属性语料"] == "待重建"
    assert rows["卡牌语料"] == "最新"


def test_audit_reports_unclassified_and_unknown_hero(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    texts = "\n".join(format_audit_issues(audit_summary(root)))
    assert "未归类武将" in texts
    assert "专属牌引用未知武将" in texts


def test_audit_reports_orphan_category_keys(tmp_path: Path) -> None:
    """#10 回归：分类表引用 heroes.json 中不存在的武将应报出。"""
    root = _make_root(tmp_path)
    _write(root / "data" / "hero_classification.json", {
        "hero_categories": {"乐广": ["爆发型"], "张华": ["爆发型"], "贾诩(限定)": ["爆发型"]},
    })
    issues = audit_summary(root)
    orphan = next(i for i in issues if i.kind == "orphan_category_key")
    assert "贾诩(限定)" in orphan.message
    assert orphan.target_tab == "武将分类维护"


def test_panel_renders(tmp_path: Path) -> None:
    _app()
    root = _make_root(tmp_path)
    panel = RagMaintenancePanel(root=root)
    assert panel._tabs.count() == 6
    assert [panel._tabs.tabText(i) for i in range(panel._tabs.count())] == [
        "语料状态", "元规则维护", "专属牌维护", "卡牌点数维护", "装备属性维护",
        "武将分类维护",
    ]
    assert panel._table.rowCount() == 8
    assert "所有语料与数据源一致" in panel._status_label.text()
    assert "人工维护提示" in panel._audit_label.text()
    assert hasattr(panel, "_special_cards")
    assert hasattr(panel, "_classification")
    assert hasattr(panel, "_card_points")
    assert hasattr(panel, "_equip_attrs")
    panel.close()


def test_audit_splits_hero_field_and_skips_generic(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    special = root / "data" / "special_cards.json"
    special.write_text(json.dumps([
        {"category": "概念", "name": "击杀", "hero": "白蹄乌、李信、杜预"},
        {"category": "概念", "name": "限定技", "hero": "众多武将"},
    ], ensure_ascii=False), encoding="utf-8")
    issues = audit_summary(root)
    texts = "\n".join(format_audit_issues(issues))
    assert "白蹄乌" in texts
    assert "众多武将" not in texts


def test_audit_reports_bad_card_points(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _write(root / "data" / "card_points.json", {
        "cards": [{"name": "火杀", "suit": "星", "point": "9"}, {"name": "杀", "suit": "♦", "point": "2"}],
        "judge_rules": [],
    })
    texts = "\n".join(format_audit_issues(audit_summary(root)))
    assert "卡牌点数张数 2 != 期望 162" in texts
    assert "异常花色" in texts
    assert "异常点数" in texts


def test_audit_reports_missing_settlement_but_skips_exempt(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    special = root / "data" / "special_cards.json"
    special.write_text(json.dumps([
        {"category": "专属牌", "name": "死士", "card_type": "标记（非实体牌）"},
        {"category": "专属战法牌", "name": "新战法", "effect": "x"},
    ], ensure_ascii=False), encoding="utf-8")
    texts = "\n".join(format_audit_issues(audit_summary(root)))
    assert "缺结算详情 1 个" in texts
    assert "新战法" in texts
    assert "死士" not in texts


def test_audit_reports_bad_equip_attrs(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _write(root / "data" / "equip_attrs.json", [
        {"name": "赤兔", "subtype": "飞船", "attack_range": None, "distance_mod": 99},
    ])
    texts = "\n".join(format_audit_issues(audit_summary(root)))
    assert "装备属性件数 1 != 期望 26" in texts
    assert "细分类型异常" in texts
    assert "距离修正异常" in texts


def test_audit_issues_structured(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    special = root / "data" / "special_cards.json"
    special.write_text(json.dumps([
        {"category": "专属牌", "name": "龙泉剑", "hero": "白蹄乌"},
        {"category": "专属牌", "name": "青釭剑", "hero": "乐广"},
    ], ensure_ascii=False), encoding="utf-8")
    issues = audit_summary(root)
    by_kind = {issue.kind: issue for issue in issues}
    unknown = by_kind["unknown_hero"]
    assert unknown.target_tab == "专属牌维护"
    assert unknown.target == ("专属牌", "龙泉剑")
    missing = by_kind["missing_settlement"]
    assert missing.target == ("专属牌", "龙泉剑")
    unclassified = by_kind["unclassified_hero"]
    assert unclassified.target_tab == "武将分类维护"
    assert "张华" in unclassified.target


def test_jump_to_unclassified(tmp_path: Path) -> None:
    _app()
    root = _make_root(tmp_path)
    panel = RagMaintenancePanel(root=root)
    issue = next(i for i in audit_summary(root) if i.kind == "unclassified_hero")
    panel._jump_to_issue(issue)
    assert panel._tabs.tabText(panel._tabs.currentIndex()) == "武将分类维护"
    assert panel._classification._tabs.currentIndex() == 2  # 武将归类子页签
    current = panel._classification._hero_list.currentItem()
    assert current is not None
    assert current.data(Qt.ItemDataRole.UserRole) in issue.target
    panel.close()


def test_jump_to_missing_settlement(tmp_path: Path) -> None:
    _app()
    root = _make_root(tmp_path)
    special = root / "data" / "special_cards.json"
    special.write_text(json.dumps([
        {"category": "专属牌", "name": "龙泉剑", "hero": "乐广"},
    ], ensure_ascii=False), encoding="utf-8")
    panel = RagMaintenancePanel(root=root)
    issue = next(i for i in audit_summary(root) if i.kind == "missing_settlement")
    panel._jump_to_issue(issue)
    assert panel._tabs.tabText(panel._tabs.currentIndex()) == "专属牌维护"
    assert panel._special_cards._current is not None
    assert panel._special_cards._current.name == "龙泉剑"
    panel.close()


def test_jump_to_bad_card_points(tmp_path: Path) -> None:
    _app()
    root = _make_root(tmp_path)
    _write(root / "data" / "card_points.json", {
        "cards": [{"name": "火杀", "suit": "星", "point": "9"}],
        "judge_rules": [],
    })
    panel = RagMaintenancePanel(root=root)
    issue = next(i for i in audit_summary(root) if i.kind in ("bad_card_suit", "bad_card_point"))
    panel._jump_to_issue(issue)
    # 非法行已被 repository 过滤（仅记日志），跳转只切页签展示「加载异常」提示
    assert panel._tabs.tabText(panel._tabs.currentIndex()) == "卡牌点数维护"
    panel.close()


def test_audit_reports_pending_refinement(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    issues = audit_summary(root)
    refinement = next(i for i in issues if i.kind == "pending_refinement")
    assert "1 块" in refinement.message
    assert issues[0].kind == "pending_refinement"  # 精化待办排在审计清单第一条
    # 全部补全 curated 后不再提示
    _write(root / "data" / "rag_corpus" / "卡牌RAG语料.json", [
        {"block_id": "x", "curated": {"method": "manual"}},
    ])
    assert not any(i.kind == "pending_refinement" for i in audit_summary(root))


def test_refine_button_shows_pending_count(tmp_path: Path) -> None:
    _app()
    root = _make_root(tmp_path)
    panel = RagMaintenancePanel(root=root)
    assert "索引精化（1）" in panel._refine_button.text()
    assert panel._refine_button.isEnabled()
    # 全部补全 curated 后按钮禁用并显示完成态
    _write(root / "data" / "rag_corpus" / "卡牌RAG语料.json", [
        {"block_id": "x", "timing": ["回合开始"], "trigger_condition": [],
         "keywords": [], "related": [], "curated": {"method": "manual"}},
    ])
    panel.refresh()
    assert panel._refine_button.text() == "索引精化 ✓"
    assert not panel._refine_button.isEnabled()
    panel.close()
