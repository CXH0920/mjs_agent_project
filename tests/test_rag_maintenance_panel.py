# -*- coding: utf-8 -*-
"""知识库维护工作台测试：任务状态计算、审计摘要与面板渲染。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.business.maintenance.maintenance_repositories import build
from src.business.rag.audit_service import AuditIssue, audit_summary
from src.ui.maintenance.rag_maintenance_panel import (
    INDEX_TASK_NAME,
    RagMaintenancePanel,
    index_state,
    task_states,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _panel(root: Path) -> RagMaintenancePanel:
    """构造带注入仓储的面板（组合根 build 的测试等价物）。"""
    return RagMaintenancePanel(root, repositories=build(root))


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _utime(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _issue_texts(issues: list[AuditIssue]) -> str:
    """AuditIssue 列表 → 纯文本（断言便捷转换）。"""
    return "\n".join(issue.message for issue in issues)


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
    # 武将变更时间轴（武将语料/武将攻略语料任务的数据源）
    _write(root / "data" / "mjs_adjustments.json", {"events": []})
    _write(root / "docs" / "元规则整理-完整版.md", "# 元规则")
    # 组合语料源（build_combo_corpus 依赖的社区 combo 材料）
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "bilibili_videos_weijiang.csv", "url\n")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "强力组合.md", "# combo")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "巴清搭配.md", "# combo")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "平阳公主强势组合盘点.md", "# combo")
    _write(root / "data" / "raw_guides" / "jinxia" / "combos" / "孟尝君 + 黄月英.md", "# combo")
    # 武将攻略语料源（目录源，建目录+占位即可）
    _write(root / "data" / "raw_guides" / "jinxia" / "guides" / "曹操.md", "# guide")
    # 语料输出（全部已生成；.md 伴文件与 json 同批写，供内容查看器用例断言）
    for name in ("武将RAG语料.json", "卡牌RAG语料.json", "卡牌点数花色语料.json",
                 "装备属性语料.json", "加强削弱语料.json", "元规则RAG语料-章节块.json",
                 "术语表.json", "FAQ裁定块.json", "特殊机制语料.json", "武将分类语料.json",
                 "组合RAG语料.json", "武将攻略RAG语料.json"):
        _write(root / "data" / "rag_corpus" / name, [{"block_id": "x"}])
        (root / "data" / "rag_corpus" / name).with_suffix(".md").write_text(
            f"# {name} 正文", encoding="utf-8")
    # 向量索引段文件（index_state 判定源；与语料同 mtime → 状态「最新」）
    seg = root / "data" / "rag_index" / "chroma" / "seg-uuid" / "data_level0.bin"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_text("x", encoding="utf-8")
    for path in root.rglob("*"):
        _utime(path, now - 100)
    return root


def test_all_tasks_up_to_date(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    rows = task_states(root)
    assert len(rows) == 10
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
    texts = _issue_texts(audit_summary(root))
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
    panel = _panel(root)
    # 布局重排：左栏 11 项（5 维护对象 + 5 只读语料 + 向量索引），右侧复用 5 个现有面板
    nav = panel._workspace.nav
    assert nav.item_keys()[:5] == ["武将分类", "专属牌", "卡牌点数", "装备属性", "元规则母本"]
    assert nav.item_keys()[5:] == ["武将语料", "卡牌语料", "加强削弱", "组合语料", "武将攻略",
                                   "向量索引"]
    assert panel._workspace.stack.count() == 5
    assert nav.current_key() == "武将分类"  # 默认选中首个维护对象
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
    texts = _issue_texts(issues)
    assert "白蹄乌" in texts
    assert "众多武将" not in texts


def test_audit_reports_bad_card_points(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _write(root / "data" / "card_points.json", {
        "cards": [{"name": "火杀", "suit": "星", "point": "9"}, {"name": "杀", "suit": "♦", "point": "2"}],
        "judge_rules": [],
    })
    texts = _issue_texts(audit_summary(root))
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
    texts = _issue_texts(audit_summary(root))
    assert "缺结算详情 1 个" in texts
    assert "新战法" in texts
    assert "死士" not in texts


def test_audit_reports_bad_equip_attrs(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    _write(root / "data" / "equip_attrs.json", [
        {"name": "赤兔", "subtype": "飞船", "attack_range": None, "distance_mod": 99},
    ])
    texts = _issue_texts(audit_summary(root))
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
    panel = _panel(root)
    issue = next(i for i in audit_summary(root) if i.kind == "unclassified_hero")
    panel._jump_to_issue(issue)
    assert panel._workspace.current_source_key() == "武将分类"
    assert panel._classification._tabs.currentIndex() == 2  # 武将归类子页签
    current = panel._classification._hero_tab.hero_list.currentItem()
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
    panel = _panel(root)
    issue = next(i for i in audit_summary(root) if i.kind == "missing_settlement")
    panel._jump_to_issue(issue)
    assert panel._workspace.current_source_key() == "专属牌"
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
    panel = _panel(root)
    issue = next(i for i in audit_summary(root) if i.kind in ("bad_card_suit", "bad_card_point"))
    panel._jump_to_issue(issue)
    # 非法行已被 repository 过滤（仅记日志），跳转只定位到对应维护对象
    assert panel._workspace.current_source_key() == "卡牌点数"
    panel.close()


def test_jump_to_card_curated_stale_opens_refinement(tmp_path: Path, monkeypatch) -> None:
    """卡牌精化过期条目的「去复核」应打开索引精化对话框（与武将侧同路径）。

    回归背景：card_curated_stale 曾不在 _jump_to_issue 直连名单内，落入
    target_tab fallback 而左栏无「索引精化」维护对象，按钮点击静默无效。
    P1-1：结构化命中清单随跳转传给弹窗入口，供定位到具体块。
    """
    _app()
    panel = _panel(_make_root(tmp_path))
    calls: list = []
    monkeypatch.setattr(panel, "_open_refinement", lambda focus=None: calls.append(focus))
    target = [{"card": "多多益善", "curated_at": "2026-10-01", "changed_at": "2026-11-05"}]

    panel._jump_to_issue(AuditIssue(
        kind="card_curated_stale",
        message="1 个卡牌块的精化早于该卡最近官网同步：多多益善",
        target_tab="索引精化",
        target=target,
    ))

    assert calls == [target]
    panel.close()


def test_refinement_focus_query_prefers_skill_then_hero_or_card() -> None:
    """命中清单 → 定位搜索词：技能名优先（精确到块），回退武将/卡牌名。"""
    assert RagMaintenancePanel._refinement_focus_query(
        [{"skill": "观星", "hero": "诸葛亮"}]) == "观星"
    assert RagMaintenancePanel._refinement_focus_query([{"hero": "张飞"}]) == "张飞"
    assert RagMaintenancePanel._refinement_focus_query([{"card": "多多益善"}]) == "多多益善"
    assert RagMaintenancePanel._refinement_focus_query(None) == ""
    assert RagMaintenancePanel._refinement_focus_query([]) == ""
    assert RagMaintenancePanel._refinement_focus_query(["非字典"]) == ""


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
    panel = _panel(root)
    assert "索引精化（1）" in panel._refine_button.text()
    assert panel._refine_button.isEnabled()
    # 全部补全 curated 后按钮禁用并显示完成态
    _write(root / "data" / "rag_corpus" / "卡牌RAG语料.json", [
        {"block_id": "x", "timing": ["回合开始"], "trigger_condition": [],
         "keywords": [], "related": [], "curated": {"method": "manual"}},
    ])
    panel.refresh()
    assert panel._refine_button.text() == "索引精化 ✓"
    assert panel._refine_button.isEnabled()  # 无待办仍可进入浏览/管理已精化块
    panel.close()


# ---------------------------------------------------------------
# 布局重排（maintenance_workspace）新增用例
# ---------------------------------------------------------------

def test_source_save_marks_nav_item_stale(tmp_path: Path) -> None:
    """数据源变更后：对应左栏项状态变「待重建」且 ↻ 按钮可见（保存→重建闭环）。"""
    _app()
    root = _make_root(tmp_path)
    panel = _panel(root)
    nav = panel._workspace.nav
    assert nav.status_text("专属牌") == "最新"
    assert not nav.rebuild_button("专属牌").isVisibleTo(panel)
    _utime(root / "data" / "special_cards.json", time.time() + 60)
    panel.refresh()
    assert nav.status_text("专属牌") == "待重建"
    assert nav.rebuild_button("专属牌").isVisibleTo(panel)
    assert nav.status_text("武将分类") == "最新"  # 其他项不受影响
    panel.close()


def test_rebuild_button_runs_only_matching_task(tmp_path: Path) -> None:
    """点左栏 ↻ 触发 --only <该语料>，参数正确（维护对象与只读语料均可重建）。"""
    _app()
    root = _make_root(tmp_path)
    panel = _panel(root)
    calls: list[list[str]] = []
    panel._run = lambda args: calls.append(args)  # 拦截，不启动真实子进程
    panel._workspace.nav.rebuild_button("专属牌").click()
    assert calls == [["--force", "--only", "特殊机制语料"]]
    panel._workspace.nav.rebuild_button("武将攻略").click()
    assert calls[-1] == ["--force", "--only", "武将攻略语料"]
    panel.close()


# ---------------------------------------------------------------
# 向量索引新鲜度（index_state）与 ↻ 分派
# ---------------------------------------------------------------

def test_index_state_fresh(tmp_path: Path) -> None:
    """段文件与语料同 mtime（fixture 统一设定）→ 索引最新。"""
    root = _make_root(tmp_path)
    row = index_state(root)
    assert row["name"] == INDEX_TASK_NAME
    assert row["status"] == "最新"
    assert row["built"] > 0


def test_index_state_missing_dir(tmp_path: Path) -> None:
    """索引目录不存在 → 未构建（不与语料比较）。"""
    import shutil

    root = _make_root(tmp_path)
    shutil.rmtree(root / "data" / "rag_index")
    assert index_state(root)["status"] == "未构建"


def test_index_rebuild_button_runs_indexer(tmp_path: Path, monkeypatch) -> None:
    """索引落后：左栏「向量索引」待同步、↻ 可见且分派到 _run_index（indexer）。"""
    _app()
    root = _make_root(tmp_path)
    seg = next((root / "data" / "rag_index" / "chroma").glob("*/*"))
    _utime(seg, time.time() - 10000)  # 索引远旧于语料（语料为 now-100）
    assert index_state(root)["status"] == "待同步"
    panel = _panel(root)
    assert panel._workspace.nav.status_text(INDEX_TASK_NAME) == "待同步"
    assert panel._workspace.nav.rebuild_button(INDEX_TASK_NAME).isVisibleTo(panel)
    assert "索引待同步" in panel._status_label.text()
    calls: list[str] = []
    monkeypatch.setattr(panel, "_run_index", lambda: calls.append(1))
    panel._workspace.nav.rebuild_button(INDEX_TASK_NAME).click()
    assert calls == [1]
    panel.close()


def test_audit_banner_limits_rows_and_folds_rest(tmp_path: Path) -> None:
    """审计提示默认显示 3 条，超出折叠为「展开全部（还有 N 条）」按钮。"""
    _app()
    root = _make_root(tmp_path)
    _write(root / "data" / "special_cards.json", [
        {"category": "专属牌", "name": "龙泉剑", "hero": "白蹄乌", "settlement": ""},
        {"category": "专属牌", "name": "青釭剑", "hero": "乐广", "settlement": ""},
    ])
    panel = _panel(root)
    issues = audit_summary(root)
    assert len(issues) > 3
    assert len(panel._audit_rows) == 4  # 3 条提示 + 切换按钮
    note = next(row for row in panel._audit_rows
                if "展开全部" in getattr(row, "text", lambda: "")())
    assert note.text() == f"展开全部（还有 {len(issues) - 3} 条）"
    assert all(row.isVisibleTo(panel) for row in panel._audit_rows)
    # 无提示时横幅整体隐藏
    panel._refresh_audit_banner([])
    assert not panel._audit_banner.isVisibleTo(panel)
    panel.close()


def test_audit_banner_expand_and_collapse(tmp_path: Path) -> None:
    """点「展开全部」显示全部提示行；再点「收起」回到 3 条；展开态跨刷新保留。"""
    _app()
    root = _make_root(tmp_path)
    _write(root / "data" / "special_cards.json", [
        {"category": "专属牌", "name": "龙泉剑", "hero": "白蹄乌", "settlement": ""},
        {"category": "专属牌", "name": "青釭剑", "hero": "乐广", "settlement": ""},
    ])
    panel = _panel(root)
    issues = audit_summary(root)
    assert len(issues) > 3

    def find_toggle():
        return next(row for row in panel._audit_rows
                    if "展开全部" in getattr(row, "text", lambda: "")()
                    or getattr(row, "text", lambda: "")() == "收起")

    find_toggle().click()  # 展开
    assert panel._banner_expanded is True
    assert len(panel._audit_rows) == len(issues) + 1
    assert all(row.isVisibleTo(panel) for row in panel._audit_rows)
    assert find_toggle().text() == "收起"
    # 展开态跨刷新保留（切页/保存触发的 refresh 不把处理中的清单折叠回去）
    panel._refresh_audit_banner(audit_summary(root))
    assert panel._banner_expanded is True
    assert len(panel._audit_rows) == len(issues) + 1
    find_toggle().click()  # 收起
    assert panel._banner_expanded is False
    assert len(panel._audit_rows) == 4
    panel.close()


def test_nav_items_fit_without_scrollbar(tmp_path: Path) -> None:
    """左栏 10 项常驻不滚动：默认态与构建态（日志展开 180px）均无纵向滚动。

    按文档高度预算用 760px 窗口测；审计横幅高度随提示条目与字体浮动，
    与「左栏内容适配」无关，先隐藏以保证断言稳定。
    """
    _app()
    root = _make_root(tmp_path)
    panel = _panel(root)
    panel.resize(900, 760)
    panel._audit_banner.hide()
    panel.show()
    QApplication.processEvents()
    bar = panel._workspace.nav._scroll.verticalScrollBar()
    assert bar.maximum() == 0
    panel._workspace.expand_log()
    QApplication.processEvents()
    assert bar.maximum() == 0
    panel.close()


def test_log_collapsed_by_default_and_expands_on_run(tmp_path: Path) -> None:
    """日志默认折叠 32px；展开到 180px 可手动收起；折叠态累计未读输出条数。"""
    _app()
    root = _make_root(tmp_path)
    panel = _panel(root)
    ws = panel._workspace
    assert not ws.is_log_expanded()
    assert ws._log_surface.height() == 32
    ws.expand_log()
    assert ws.is_log_expanded()
    assert ws._log_surface.height() == 180
    ws.collapse_log()
    assert ws._log_surface.height() == 32
    # 折叠期间产生输出：角标显示未读行数，展开后清零
    ws.on_log_output("line1\nline2")
    assert ws.log_unread_badge.isVisibleTo(panel)
    assert ws.log_unread_badge.text() == "2 行新输出"
    ws.expand_log()
    assert not ws.log_unread_badge.isVisibleTo(panel)
    panel.close()


def test_panels_reused_across_source_switch(tmp_path: Path) -> None:
    """左侧切换数据源：右侧面板实例复用不重建（保留选中项与滚动位置）。"""
    _app()
    root = _make_root(tmp_path)
    panel = _panel(root)
    ws = panel._workspace
    ws.select_source("武将分类")
    classification = ws.stack.currentWidget()
    assert classification is panel._classification
    ws.select_source("专属牌")
    assert ws.stack.currentWidget() is panel._special_cards
    ws.select_source("武将分类")
    assert ws.stack.currentWidget() is classification
    panel.close()


def test_rule_doc_output_forwards_to_workspace_log(tmp_path: Path) -> None:
    """C5' 方案 A：元规则脚本输出转发到工作台底部日志（模块单一日志出口）。"""
    _app()
    root = _make_root(tmp_path)
    panel = _panel(root)
    panel._rule_doc._clear_script_output()
    panel._rule_doc._append_log("$ python -m src.scripts.audit_rule_doc.py\n✔ 完成".encode("utf-8"))
    log_text = panel._workspace.log.toPlainText()
    assert "$ python -m src.scripts.audit_rule_doc.py" in log_text
    assert "✔ 完成" in log_text
    panel.close()


def test_nav_task_names_cover_all_tasks() -> None:
    """左栏导航任务名与 task_defs 全集一致：任务改名或新增未同步 UI 即红。

    set_task_states 按任务名查状态，失配时静默降级为「—」（单项重建按钮
    也不出现），无此守护则改名静默失配且测试全绿。向量索引为额外只读项
    （非 maintain_rag 任务，↻ 走 indexer 命令）。
    """
    from src.business.rag.task_defs import TASKS
    from src.ui.maintenance.rag_maintenance_panel import (
        EDITABLE_SOURCE_ITEMS,
        READONLY_CORPUS_ITEMS,
    )

    ui_names = {task for _, task in EDITABLE_SOURCE_ITEMS + READONLY_CORPUS_ITEMS}
    assert ui_names == {task["name"] for task in TASKS}


# ---------------------------------------------------------------
# 执行历史快照与历史下拉（P1-4）
# ---------------------------------------------------------------

def test_log_run_snapshot_captures_two_runs(tmp_path: Path) -> None:
    """两路执行各留一条快照：正文齐全、label 带结论与耗时、元规则占位名被首行解析。"""
    _app()
    panel = _panel(_make_root(tmp_path))
    # 路①：语料单项重建（直调快照原语模拟一次完整执行）
    panel._begin_log_run(panel._friendly_label(
        "src.scripts.maintain_rag", ["--force", "--only", "武将语料"]))
    panel._active_run["text"] = "$ python -m src.scripts.maintain_rag --force --only 武将语料"
    panel._append_log_run_text("\n[执行] 武将语料\n✅ 生成与校验通过")
    panel._finish_log_run(0, 12.3)
    # 路②：元规则脚本（开始时拿不到命令，封口时从正文首行解析替换占位名）
    panel._begin_log_run("元规则脚本")
    panel._append_log_run_text("$ python -m src.scripts.audit_rule_doc.py\n✔ 执行完成")
    panel._finish_log_run(0, None)
    assert len(panel._log_runs) == 2
    first, second = panel._log_runs
    assert first["label"] == "重建武将语料 ✔ 12.3s"
    assert "生成与校验通过" in first["text"]
    assert second["label"] == "元规则文档检查 ✔"
    panel.close()


def test_log_history_combo_lists_runs_newest_first(tmp_path: Path) -> None:
    """历史下拉倒序（最新在上）、userData 指向快照索引；无历史时隐藏。"""
    _app()
    panel = _panel(_make_root(tmp_path))
    combo = panel._workspace.log_history_combo
    assert not combo.isVisibleTo(panel)
    panel._begin_log_run("重建特殊机制语料")
    panel._finish_log_run(0, 1.0)
    panel._begin_log_run("元规则文档检查")
    panel._finish_log_run(1, None)
    labels = [combo.itemText(i) for i in range(combo.count())]
    assert labels == ["元规则文档检查 ✘", "重建特殊机制语料 ✔ 1.0s"]
    assert [combo.itemData(i) for i in range(combo.count())] == [1, 0]
    assert combo.isVisibleTo(panel)
    panel.close()


def test_history_selected_opens_viewer_with_full_text(tmp_path: Path, monkeypatch) -> None:
    """历史下拉点选：弹只读查看器显示该次完整输出（含失败明细与退出码）。"""
    _app()
    panel = _panel(_make_root(tmp_path))
    panel._begin_log_run("重建武将语料")
    panel._active_run["text"] = "$ python -m src.scripts.maintain_rag --force --only 武将语料"
    panel._append_log_run_text("\n❌ 执行失败：build_rag_corpus.py")
    panel._finish_log_run(1, 8.2)
    opened: list = []
    monkeypatch.setattr(
        "src.ui.maintenance.readonly_viewer_dialog.ReadonlyViewerDialog.exec",
        lambda self: opened.append(self))
    panel._show_log_run(0)
    assert opened
    assert "执行失败：build_rag_corpus.py" in opened[0].body.toPlainText()
    assert "退出码 1" in opened[0].meta_label.text()
    # 越界索引静默忽略（下拉重建期间的迟到信号）
    panel._show_log_run(99)
    assert len(opened) == 1
    panel.close()


# ---------------------------------------------------------------
# 只读语料内容查看器与向量索引行（P1-5）
# ---------------------------------------------------------------

def _capture_viewer(monkeypatch) -> list:
    """拦截查看器 exec，返回捕获的对话框列表（防真实模态）。"""
    opened: list = []
    monkeypatch.setattr(
        "src.ui.maintenance.readonly_viewer_dialog.ReadonlyViewerDialog.exec",
        lambda self: opened.append(self))
    return opened


def test_corpus_meta_opens_content_viewer(tmp_path: Path, monkeypatch) -> None:
    """点只读语料行：弹内容查看器，正文为 .md 伴文件、头部含块数元信息。"""
    _app()
    panel = _panel(_make_root(tmp_path))
    opened = _capture_viewer(monkeypatch)
    panel._show_corpus_meta("武将语料")
    assert len(opened) == 1
    dialog = opened[0]
    assert "输出块数" in dialog.meta_label.text()
    assert "武将RAG语料.md" in dialog.meta_label.text()
    assert dialog.body.toPlainText() == "# 武将RAG语料.json 正文"
    assert not hasattr(dialog, "note_label")  # 非攻略语料无截断提示
    # 攻略语料的 .md 构建时按块截断，头部如实标注预览属性
    panel._show_corpus_meta("武将攻略")
    assert len(opened) == 2
    assert opened[1].note_label.text().startswith("⚠ 本语料正文为前 150 字预览")
    panel.close()


def test_corpus_meta_falls_back_without_md(tmp_path: Path, monkeypatch) -> None:
    """.md 伴文件缺失：降级为 json 前 50 块摘要，不显示空白。"""
    _app()
    root = _make_root(tmp_path)
    (root / "data" / "rag_corpus" / "武将RAG语料.md").unlink()
    panel = _panel(root)
    opened = _capture_viewer(monkeypatch)
    panel._show_corpus_meta("武将语料")
    assert len(opened) == 1
    assert "x" in opened[0].body.toPlainText()  # fixture json 的 block_id
    assert "json 前 50 块摘要" in opened[0].meta_label.text()
    panel.close()


def test_index_meta_opens_viewer(tmp_path: Path, monkeypatch) -> None:
    """向量索引行点击不再静默：弹索引元信息（状态/构建时间/段文件清单）。"""
    _app()
    panel = _panel(_make_root(tmp_path))
    opened = _capture_viewer(monkeypatch)
    panel._show_corpus_meta("向量索引")
    assert len(opened) == 1
    assert "状态：最新" in opened[0].meta_label.text()
    assert "data_level0.bin" in opened[0].body.toPlainText()
    panel.close()
