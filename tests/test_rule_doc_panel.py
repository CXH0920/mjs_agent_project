# -*- coding: utf-8 -*-
"""元规则维护页签测试：本地刷新（提案/疑难/章节解析）与面板渲染。

进程类动作（audit/sync/propose/apply）通过 QProcess 异步执行，测试只覆盖
纯本地刷新与 UI 构造，避免启动真实子进程。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.ui.maintenance.rule_doc_panel import RuleDocPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    _write(root / "docs" / "元规则整理-完整版.md", "# 元规则")
    _write(root / "docs" / "archive" / "proposals" / "CP-2026-08-16-01.json",
           {"proposal_id": "CP-2026-08-16-01", "created_at": "2026-08-16",
            "items": [{"id": "P-01", "type": "faq_new", "target": "5.2",
                       "suggested_text": "新裁定", "status": "approved"}]})
    _write(root / "docs" / "rule_doc_pending.json",
           {"items": [{"id": 1, "date": "2026-08-16", "description": "组合盲点",
                       "involved": "张华", "source": "实战", "status": "open"}]})
    return root


def test_panel_renders_and_local_refresh(tmp_path):
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    # 四个能力拆为子页签
    assert panel._tabs.count() == 4
    assert panel._tabs.objectName() == "sectionTabs"  # C4' 分区页签统一下划线样式
    assert [panel._tabs.tabText(i) for i in range(panel._tabs.count())] == [
        "文档状态", "数据段差异", "提案工作台", "疑难登记",
    ]
    assert panel._proposal_combo.count() == 1
    # 提案详情已加载
    assert panel._proposal_table.rowCount() == 1
    # 疑难登记已加载
    assert panel._pending_table.rowCount() == 1
    assert panel._pending_table.item(0, 2).text() == "组合盲点"


def test_add_pending_refreshes_table(tmp_path):
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    # 直接调用服务后刷新
    from src.business.rag import rule_doc_service as rds
    rds.add_pending(root, "新疑难", "武将X", "测试")
    panel.refresh()
    assert panel._pending_table.rowCount() == 2


def test_to_proposal_updates_combo(tmp_path):
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    from src.business.rag import rule_doc_service as rds
    path = rds.pending_to_proposal(root, 1)
    assert path.exists()
    panel.refresh()
    assert panel._proposal_combo.count() == 2


def test_sync_sentinel_exit_code(tmp_path):
    """sync_rule_stats 退出码 1 = 检测到差异（哨兵），不显示执行失败。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    panel._clear_script_output()
    called = []
    panel._on_finished(1, None, lambda: called.append(True),
                       sentinel_codes={1}, sentinel_note="检测到差异，见差异表")
    text = panel._last_output()
    assert "⚠ 执行完成（退出码 1：检测到差异，见差异表）" in text
    assert "✘" not in text
    assert called == [True]
    # 未声明哨兵时，非零退出码仍按失败显示
    panel._clear_script_output()
    panel._on_finished(1, None, lambda: None)
    assert "✘ 执行失败（退出码 1）" in panel._last_output()


def test_initial_status_prompt(tmp_path):
    """A2 打开页面尚未检查时给出第一步指引；已检查后 refresh 不重置为未检查提示。"""
    _app()
    root = _make_root(tmp_path)
    from src.ui.shared.style import TONE_SUCCESS
    panel = RuleDocPanel(root)
    assert "尚未检查" in panel._action_bar.status_label.text()
    panel._checked = True
    panel._action_bar.set_status("其他状态", TONE_SUCCESS)
    panel.refresh()
    assert "尚未检查" not in panel._action_bar.status_label.text()


def test_audit_detail_renders(tmp_path):
    """B1 文档状态明细：ERROR/WARN 逐条 + 数据段一致性带「去同步」+ 页签角标。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    panel._clear_script_output()
    panel._append_log(
        "  [WARN] 数据段一致性：8 处全自动差异（段：0.2、3.5、5.2）\n"
        "  [INFO] 数据段候选：17 处半自动候选差异（段：3.1、3.2）\n"
        "汇总：ERROR 0 / WARN 1 / INFO 2".encode("utf-8"))
    panel._on_audit_finished()
    assert panel._audit_table.item(1, 1).text() == "1"
    assert panel._audit_detail_table.rowCount() == 1
    button = panel._audit_detail_table.cellWidget(0, 2)
    assert button is not None and button.text() == "去同步"
    assert panel._tabs.tabText(0) == "文档状态（1）"
    assert panel._audit_detail_empty.isHidden()
    # 无 ERROR/WARN 时明细表为空提示可见
    panel._clear_script_output()
    panel._append_log("汇总：ERROR 0 / WARN 0 / INFO 0".encode("utf-8"))
    panel._on_audit_finished()
    assert panel._audit_detail_table.rowCount() == 0
    assert not panel._audit_detail_empty.isHidden()
    assert panel._tabs.tabText(0) == "文档状态"


def test_sync_status_suggests_next_step(tmp_path):
    """A3/A4 sync 后：类型中文语义、页签角标、状态驱动下一步建议。"""
    _app()
    root = _make_root(tmp_path)
    from src.business.rag import rule_doc_service as rds
    panel = RuleDocPanel(root)
    import json as _json
    report = rds.sync_json_path(root)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_json.dumps([
        {"section": "0.2", "line_no": 29, "kind": "full", "old": "a", "new": "b",
         "message": "武将数不一致"},
        {"section": "3.1", "line_no": 158, "kind": "candidate", "old": "c", "new": "d",
         "message": "时机频次候选"},
    ], ensure_ascii=False), encoding="utf-8")
    panel._audit_counts = {"ERROR": 0, "WARN": 1, "INFO": 2}
    panel._on_sync_finished()
    assert panel._diff_table.item(0, 3).text() == "全自动"
    assert panel._diff_table.item(1, 3).text() == "候选"
    assert panel._tabs.tabText(1) == "数据段差异（2）"
    assert "全自动差异 1 处可一键应用" in panel._action_bar.status_label.text()
    assert panel._checked is True
    assert panel._diff_empty_label.isHidden()


def test_compose_clean_status(tmp_path):
    """A3 全部干净时的状态文案。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    # _make_root 自带 1 条 open 疑难，先清零待办计数
    panel._proposal_pending = 0
    panel._pending_open = 0
    status, tone = panel._compose_next_step([])
    assert "校验通过、数据一致、无待办" in status


# ---------------------------------------------------------------------------
# B2：数据段差异确认工作台
# ---------------------------------------------------------------------------

def _write_sync_report(root: Path) -> None:
    import json as _json
    from src.business.rag import rule_doc_service as rds
    report = rds.sync_json_path(root)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(_json.dumps([
        {"section": "0.2", "line_no": 29, "kind": "full", "old": "| 武将数 | 171 |",
         "new": "| 武将数 | 172 |", "message": "武将体系统计与 heroes.json 不一致"},
        {"section": "3.1", "line_no": 158, "kind": "candidate", "old": "| 出牌阶段 | 194 |",
         "new": "| 出牌阶段 | 130 |", "message": "时机频次为半自动候选值"},
        {"section": "3.5", "line_no": 213, "kind": "checkpoint", "old": "x", "new": None,
         "message": "每种牌限1次处数：文档 5 vs 数据 6"},
    ], ensure_ascii=False), encoding="utf-8")


def test_diff_table_renders_b2(tmp_path):
    """B2：7 列表格；full 默认勾选、candidate 未勾选、checkpoint 置灰不可勾。"""
    _app()
    root = _make_root(tmp_path)
    from PySide6.QtCore import Qt
    _write_sync_report(root)
    panel = RuleDocPanel(root)
    panel._audit_counts = {"ERROR": 0, "WARN": 1, "INFO": 2}
    panel._on_sync_finished()
    assert panel._diff_table.columnCount() == 7
    assert panel._diff_table.rowCount() == 3
    full_check = panel._diff_table.cellWidget(0, 0)
    cand_check = panel._diff_table.cellWidget(1, 0)
    ckpt_check = panel._diff_table.cellWidget(2, 0)
    assert full_check.isChecked() and full_check.isEnabled()
    assert not cand_check.isChecked() and cand_check.isEnabled()
    assert not ckpt_check.isChecked() and not ckpt_check.isEnabled()
    # 确认值默认 = new；checkpoint 不可编辑
    assert panel._diff_table.item(0, 5).text() == "| 武将数 | 172 |"
    assert not (panel._diff_table.item(2, 5).flags() & Qt.ItemFlag.ItemIsEditable)
    # 统计与按钮
    assert "全自动 1 · 候选 1 · 校验点 1 ｜ 已勾选 1 项可应用" in panel._diff_summary_label.text()
    assert panel._apply_diff_button.text() == "应用已确认差异（1）"
    assert panel._apply_diff_button.isEnabled()


def test_collect_confirmed_rows_payload(tmp_path):
    """B2：勾选收集 payload；空值与竖线被拦截。"""
    _app()
    root = _make_root(tmp_path)
    _write_sync_report(root)
    panel = RuleDocPanel(root)
    panel._on_sync_finished()
    panel._diff_table.cellWidget(1, 0).setChecked(True)  # 勾选候选行
    rows = panel._collect_confirmed_rows()
    assert len(rows) == 2
    payload = rows[1]
    assert payload["section"] == "3.1"
    assert payload["line_no"] == 158
    assert payload["old"] == "| 出牌阶段 | 194 |"
    assert payload["new"] == "| 出牌阶段 | 130 |"
    assert payload["message"]


def test_collect_confirmed_rows_rejects_invalid(monkeypatch, tmp_path):
    """B2：确认值为空 / 含竖线 → 拦截（返回 None，弹提示）。"""
    _app()
    root = _make_root(tmp_path)
    _write_sync_report(root)
    panel = RuleDocPanel(root)
    panel._on_sync_finished()
    warned = []
    monkeypatch.setattr("src.ui.maintenance.rule_doc_panel.QMessageBox.warning",
                        lambda *a, **k: warned.append(a))
    panel._diff_table.item(0, 5).setText("")
    assert panel._collect_confirmed_rows() is None
    assert warned
    panel._diff_table.item(0, 5).setText("| 武将数 | 172 | 多列")
    assert panel._collect_confirmed_rows() is None
    assert len(warned) == 2


# ---------------------------------------------------------------------------
# B3：提案确认工作台
# ---------------------------------------------------------------------------

def test_proposal_table_renders_b3(tmp_path):
    """B3：6 列表格、状态中文、统计、每行确认按钮。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    assert panel._proposal_table.columnCount() == 6
    assert panel._proposal_table.rowCount() == 1
    # _make_root 的提案项为 approved（P-01）
    assert panel._proposal_table.item(0, 4).text() == "已确认"
    assert panel._proposal_table.item(0, 1).text() == "新增FAQ"
    from PySide6.QtWidgets import QPushButton
    actions = panel._proposal_table.cellWidget(0, 5)
    assert [b.text() for b in actions.findChildren(QPushButton)] == ["查看", "确认"]
    assert "待确认 0 · 已确认 1 · 已驳回 0" in panel._proposal_summary_label.text()


def test_proposal_actions_two_buttons(tmp_path):
    """提案操作列：查看 + 确认 两个按钮并存。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    from PySide6.QtWidgets import QPushButton
    actions = panel._proposal_table.cellWidget(0, 5)
    buttons = [b.text() for b in actions.findChildren(QPushButton)]
    assert buttons == ["查看", "确认"]


def test_proposal_detail_dialog_diff(tmp_path):
    """查看详情：faq_revise 显示 Git 风格 diff（红删 + 绿增）+ 文档上下文。"""
    _app()
    root = _make_root(tmp_path)
    # 覆盖文档与提案：faq_revise，31 个技能 → 32 个技能
    doc = root / "docs" / "元规则整理-完整版.md"
    doc.write_text("### 5.2 武将类裁定（15 条）\n"
                   "| # | 裁定 | 来源 |\n|---|---|---|\n"
                   "| 61 | 限定技=本局限1次，描述以\"限定，\"开头（31个技能） | 数据统计 |\n",
                   encoding="utf-8")
    from src.ui.maintenance.rule_doc_panel import ProposalDetailDialog
    item = {"id": "P-01", "type": "faq_revise", "target": "faq_61",
            "suggested_text": "限定技=本局限1次，描述以\"限定，\"开头（32个技能）",
            "source": "公告", "basis": "官方公告", "rationale": "统计修订", "status": "pending"}
    dialog = ProposalDetailDialog(root, item)
    diff_text = dialog._diff_browser.toPlainText()
    assert "-" in diff_text and "+" in diff_text  # 红删绿增标记
    assert "31" in diff_text and "32" in diff_text
    context_text = dialog._context_browser.toPlainText()
    assert "| 61 |" in context_text
    assert "### 5.2" in context_text
    dialog.close()


def test_diff_table_readonly_columns(tmp_path):
    """改善点 1：差异表展示列只读，仅「确认值」列可编辑（checkpoint 除外）。"""
    _app()
    root = _make_root(tmp_path)
    from PySide6.QtCore import Qt
    _write_sync_report(root)
    panel = RuleDocPanel(root)
    panel._on_sync_finished()
    editable = Qt.ItemFlag.ItemIsEditable
    assert not (panel._diff_table.item(0, 1).flags() & editable)  # 段
    assert not (panel._diff_table.item(0, 2).flags() & editable)  # 行号
    assert not (panel._diff_table.item(0, 3).flags() & editable)  # 类型
    assert not (panel._diff_table.item(0, 4).flags() & editable)  # 摘要
    assert panel._diff_table.item(0, 5).flags() & editable        # 确认值（full 行）
    assert not (panel._diff_table.item(2, 5).flags() & editable)  # 确认值（checkpoint 行）


def test_diff_table_view_button(tmp_path):
    """差异表每行最右侧有 [查看] 按钮。"""
    _app()
    root = _make_root(tmp_path)
    _write_sync_report(root)
    panel = RuleDocPanel(root)
    panel._on_sync_finished()
    for row in range(3):
        button = panel._diff_table.cellWidget(row, 6)
        assert button is not None and button.text() == "查看"


def test_diff_detail_dialog_full(tmp_path):
    """full 行详情：Git 风格 diff（红删绿增）+ 文档上下文，无警示。"""
    _app()
    root = _make_root(tmp_path)
    doc = root / "docs" / "元规则整理-完整版.md"
    doc.write_text("| 武将数 | 171 |\n| 阵营 | 17 种 |\n| 定位 | 6 种 |\n", encoding="utf-8")
    from src.ui.maintenance.rule_doc_panel import DiffDetailDialog
    diff = {"section": "0.2", "line_no": 0, "kind": "full", "old": "| 武将数 | 171 |",
            "new": "| 武将数 | 172 |", "message": "武将体系统计不一致"}
    dialog = DiffDetailDialog(root, diff, "| 武将数 | 172 |")
    text = dialog._diff_browser.toPlainText()
    assert "-" in text and "+" in text
    assert "171" in text and "172" in text
    assert "| 武将数 |" in dialog._context_browser.toPlainText()
    assert dialog._stale_warning.isHidden()
    dialog.close()


def test_diff_detail_stale_warning(tmp_path):
    """文档当前行与检查时快照不一致 → 警示条可见。"""
    _app()
    root = _make_root(tmp_path)
    doc = root / "docs" / "元规则整理-完整版.md"
    doc.write_text("| 武将数 | 999 |\n", encoding="utf-8")  # 已被其他途径修改
    from src.ui.maintenance.rule_doc_panel import DiffDetailDialog
    diff = {"section": "0.2", "line_no": 0, "kind": "full", "old": "| 武将数 | 171 |",
            "new": "| 武将数 | 172 |", "message": "m"}
    dialog = DiffDetailDialog(root, diff, "| 武将数 | 172 |")
    assert not dialog._stale_warning.isHidden()
    assert "不一致" in dialog._stale_warning.text()
    dialog.close()


def test_diff_detail_checkpoint(tmp_path):
    """checkpoint 行详情：无自动建议值说明。"""
    _app()
    root = _make_root(tmp_path)
    from src.ui.maintenance.rule_doc_panel import DiffDetailDialog
    diff = {"section": "3.5", "line_no": 0, "kind": "checkpoint", "old": "x", "new": None,
            "message": "每种牌限1次处数：文档 5 vs 数据 6"}
    dialog = DiffDetailDialog(root, diff)
    assert "无自动建议值" in dialog._diff_browser.toPlainText()
    dialog.close()


def test_confirm_proposal_updates_json(tmp_path):
    """B3：确认结果写回提案 JSON，表格刷新后状态列更新。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    path = panel._proposal_combo.currentData()
    from src.business.rag import rule_doc_service as rds
    rds.update_proposal_item(root, path, "P-01", "rejected")
    panel._load_proposal_detail()
    assert panel._proposal_table.item(0, 4).text() == "已驳回"
    assert "待确认 0 · 已确认 0 · 已驳回 1" in panel._proposal_summary_label.text()


# ---------------------------------------------------------------------------
# 日志模块
# ---------------------------------------------------------------------------

def test_append_marked_preserves_plain_text(tmp_path):
    """结论行写入输出缓冲（兼容 _last_output 解析）。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    panel._clear_script_output()
    panel._append_marked("✔ 执行完成", "success")
    panel._append_marked("✘ 失败", "error")
    assert panel._last_output() == "✔ 执行完成\n✘ 失败"


def test_on_finished_writes_ops_log(tmp_path):
    """每次脚本执行落一条 rule_doc_ops.log 记录（命令 + 退出码 + 结论）。"""
    _app()
    root = _make_root(tmp_path)
    panel = RuleDocPanel(root)
    panel._last_command = "audit_rule_doc.py"
    panel._on_finished(0, None, lambda: None)
    log_path = root / "logs" / "rule_doc_ops.log"
    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "audit_rule_doc.py → exit=0 ✔ 执行完成" in text