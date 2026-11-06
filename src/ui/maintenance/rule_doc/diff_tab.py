# -*- coding: utf-8 -*-
"""元规则维护·数据段差异页签（sync，第二轮第 15 批审查拆分）。

从 RuleDocPanel 拆出：sync_rule_stats.py --json 差异预览（勾选 + 确认值 +
一键应用）与差异详情对话框收敛到本页签，DiffDetailDialog 随迁。脚本由宿主
执行：刷新差异经 apply_diff_result() 注入渲染结果；「应用已确认差异」收集
校验并原子落盘确认清单后，经 apply_requested(path) 信号由宿主执行
--apply-json（失败整批不写入、业务失败码文案在宿主侧约定）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from src.business.maintenance.rule_doc_ops_service import save_confirmed_diffs, validate_confirmed_row
from src.business.rag import rule_doc_service as rds
from src.ui.shared.rich_diff import build_diff_rows, rows_to_html
from src.ui.shared.style import ROLE_SECONDARY, set_ui_role
from src.ui.shared.widgets import DialogFooter, EmptyState

logger = logging.getLogger(__name__)

# 数据段差异类型 → 中文语义（A4 术语提示）
_KIND_LABELS = {"full": "全自动", "candidate": "候选", "checkpoint": "校验点"}


def _readonly_item(text: str) -> QTableWidgetItem:
    """展示列 item：去掉 ItemIsEditable（仅「确认值」列可编辑）。"""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class DiffDetailDialog(QDialog):
    """数据段差异详情（只读）：带上下文的 Git 风格差异对比。

    local 侧现场读文档当前行（与 apply_confirmed 同一行号定位），
    official 侧优先取「确认值」列当前文本；文档行与快照不一致时显示警示条。
    """

    def __init__(self, root: Path, diff: dict, confirm_value: str = "", parent=None):
        super().__init__(parent)
        self._root = root
        self._diff = diff
        self._confirm_value = confirm_value
        line_no = (diff.get("line_no") or 0) + 1
        self.setWindowTitle("数据段差异详情 %s · 第 %d 行" % (diff.get("section", "?"), line_no))
        self.setMinimumSize(860, 560)
        self._setup_ui()

    def _doc_path(self) -> Path:
        return self._root / "docs" / "元规则整理-完整版.md"

    def _setup_ui(self) -> None:
        diff = self._diff
        line_no = (diff.get("line_no") or 0) + 1
        layout = QVBoxLayout(self)
        meta = QLabel("段：%s · 行号：%d · 类型：%s" % (
            diff.get("section", "?"), line_no,
            _KIND_LABELS.get(diff.get("kind", ""), diff.get("kind", ""))))
        meta.setObjectName("metaText")
        layout.addWidget(meta)
        summary = QLabel("差异摘要：%s" % diff.get("message", ""))
        summary.setObjectName("metaText")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        # 警示条：文档当前行与检查时快照不一致 → 应用会失败
        self._stale_warning = QLabel()
        self._stale_warning.setObjectName("metaText")
        self._stale_warning.setWordWrap(True)
        local = rds.doc_line_at(self._doc_path(), diff.get("line_no"))
        stale = (diff.get("kind") != "checkpoint" and local is not None
                 and diff.get("old") is not None and local != diff["old"])
        if stale:
            self._stale_warning.setText(
                "⚠ 文档当前行与检查时快照不一致（可能已被其他途径修改），应用该行会失败，请刷新后再应用。")
        self._stale_warning.setVisible(bool(stale))
        layout.addWidget(self._stale_warning)

        self._tabs = QTabWidget()
        self._diff_browser = QTextBrowser()
        if diff.get("kind") == "checkpoint":
            self._diff_browser.setPlainText("校验点仅提示数字不一致，无自动建议值；请人工核对后直接修改文档。")
        else:
            self._diff_browser.setHtml(self._build_diff_html())
        self._context_browser = QTextBrowser()
        context = rds.doc_context_around(self._doc_path(), diff.get("line_no"))
        self._context_browser.setPlainText(context or "（未找到目标位置上下文）")
        self._tabs.addTab(self._diff_browser, "差异对比")
        self._tabs.addTab(self._context_browser, "文档上下文")
        layout.addWidget(self._tabs, 1)

        footer = DialogFooter(accept_text="关闭", cancel_text="", show_cancel=False)
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)

    def _build_diff_html(self) -> str:
        diff = self._diff
        local = rds.doc_line_at(self._doc_path(), diff.get("line_no"))
        if local is None:
            local = "（未找到目标行：行号越界或文档已变化）"
        official = self._confirm_value or (diff.get("new") or "")
        return rows_to_html(build_diff_rows(local, official))


class DiffTab(QWidget):
    """「数据段差异」页签：差异预览（勾选 + 确认值）+ 一键应用（B2）。"""

    apply_requested = Signal(object)  # 确认清单 Path：请求宿主执行 --apply-json

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self._root = root
        self._diffs: list[dict] = []
        self._build_ui()

    @property
    def diffs(self) -> list[dict]:
        """当前渲染的差异清单（宿主角标与下一步建议用）。"""
        return self._diffs

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _build_ui(self) -> None:
        card = QFrame()
        card.setObjectName("panelCardSurface")
        layout = QVBoxLayout(card)
        top = QHBoxLayout()
        top.addWidget(QLabel("数据段差异（sync_rule_stats）"))
        top.addStretch(1)
        self._apply_button = QPushButton("应用已确认差异")
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply_diffs)
        top.addWidget(self._apply_button)
        layout.addLayout(top)
        # A4 术语提示：差异类型语义
        self._hint = QLabel("类型：全自动=可直接应用 / 候选=需人工确认 / 校验点=仅提示；勾选后可在「确认值」列修改应用值")
        self._hint.setObjectName("metaText")
        layout.addWidget(self._hint)
        self._summary_label = QLabel("全自动 0 · 候选 0 · 校验点 0 ｜ 已勾选 0 项可应用")
        self._summary_label.setObjectName("metaText")
        layout.addWidget(self._summary_label)
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(["应用", "段", "行号", "类型", "差异摘要", "确认值", "操作"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)
        self._empty_label = EmptyState("暂无数据段差异", "点击顶部「刷新状态」开始检查。")
        layout.addWidget(self._empty_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    # ---------------------------------------------------------------
    # 结果渲染与勾选统计（脚本由宿主执行）
    # ---------------------------------------------------------------
    def apply_diff_result(self) -> None:
        """解析 sync --json 产物并整表重建（解析源在磁盘，重入即恢复）。"""
        try:
            diffs = rds.parse_sync_diff(rds.sync_json_path(self._root))
        except (OSError, ValueError) as error:
            # 手改/半截写坏的差异报告降级为空表而非页面崩溃（第 9 批审查
            # P3-4；ValueError 覆盖 JSONDecodeError 与 UnicodeDecodeError）
            logger.warning("差异报告解析失败：%s", error)
            diffs = []
        self._diffs = diffs
        self._table.setRowCount(len(self._diffs))
        for i, d in enumerate(self._diffs):
            check = QCheckBox()
            check.setEnabled(d["kind"] != "checkpoint")
            check.setChecked(d["kind"] == "full")
            check.stateChanged.connect(lambda _=0: self._refresh_summary())
            self._table.setCellWidget(i, 0, check)
            self._table.setItem(i, 1, _readonly_item(d["section"]))
            self._table.setItem(i, 2, _readonly_item(str(d["line_no"] + 1)))
            self._table.setItem(i, 3, _readonly_item(_KIND_LABELS.get(d["kind"], d["kind"])))
            self._table.setItem(i, 4, _readonly_item(d["message"]))
            value_item = QTableWidgetItem(d.get("new") or "")
            if d["kind"] == "checkpoint":
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._table.setItem(i, 5, value_item)
            view_button = QPushButton("查看")
            set_ui_role(view_button, ROLE_SECONDARY)
            view_button.clicked.connect(lambda _=False, idx=i: self._open_detail(idx))
            self._table.setCellWidget(i, 6, view_button)
        self._empty_label.set_description("数据段与数据源一致")
        self._empty_label.setVisible(not self._diffs)
        self._refresh_summary()

    def unsaved_edit_count(self) -> int:
        """统计差异表偏离默认状态的行数：勾选被手动改过或确认值 ≠ 建议值。

        刷新会整表重建，这些用户输入随之丢失；checkpoint 行勾选与确认值
        均不可改，天然不会偏离默认。
        """
        count = 0
        for row, d in enumerate(self._diffs):
            widget = self._table.cellWidget(row, 0)
            if widget is not None and widget.isChecked() != (d["kind"] == "full"):
                count += 1
                continue
            value_item = self._table.item(row, 5)
            if value_item is not None and value_item.text().strip() != (d.get("new") or ""):
                count += 1
        return count

    def _refresh_summary(self) -> None:
        """B2：按勾选状态刷新统计与「应用已确认差异」按钮。"""
        counts = {"full": 0, "candidate": 0, "checkpoint": 0}
        checked = 0
        for d in self._diffs:
            counts[d["kind"]] = counts.get(d["kind"], 0) + 1
        for row, d in enumerate(self._diffs):
            widget = self._table.cellWidget(row, 0)
            if widget is not None and widget.isChecked():
                checked += 1
        self._summary_label.setText(
            "全自动 %d · 候选 %d · 校验点 %d ｜ 已勾选 %d 项可应用"
            % (counts["full"], counts["candidate"], counts["checkpoint"], checked))
        self._apply_button.setText(
            "应用已确认差异（%d）" % checked if checked else "应用已确认差异")
        self._apply_button.setEnabled(checked > 0)

    # ---------------------------------------------------------------
    # 应用差异（B2）
    # ---------------------------------------------------------------
    def _apply_diffs(self) -> None:
        """收集勾选行 → 写确认清单 → 发 apply_requested 由宿主落地文档。"""
        rows = self._collect_confirmed_rows()
        if rows is None:
            return
        answer = QMessageBox.question(
            self, "应用差异",
            "将用「确认值」列原位替换文档数据段（%d 处，脚本预检 old 匹配，失败整批不写入）。继续？" % len(rows))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path = save_confirmed_diffs(self._root, rows)
        except OSError as exc:
            logger.exception("写入确认清单失败")
            QMessageBox.critical(self, "写入失败", "无法写入确认清单：%s" % exc)
            return
        self.apply_requested.emit(path)

    def _collect_confirmed_rows(self) -> list[dict] | None:
        """B2：收集勾选行的确认 payload；行校验规则在 RuleDocOpsService（#A3）。"""
        rows = []
        for row, d in enumerate(self._diffs):
            widget = self._table.cellWidget(row, 0)
            if widget is None or not widget.isChecked():
                continue
            value_item = self._table.item(row, 5)
            new = value_item.text().strip() if value_item else ""
            error = validate_confirmed_row(d, new)
            if error:
                QMessageBox.warning(self, "校验失败", "第 %d 行：%s" % (row + 1, error))
                return None
            rows.append({
                "section": d["section"],
                "line_no": d["line_no"],
                "old": d.get("old"),
                "new": new,
                "message": d.get("message", ""),
            })
        return rows

    def _open_detail(self, row: int) -> None:
        """打开数据段差异详情（差异对比 + 文档上下文，只读）。"""
        if row < 0 or row >= len(self._diffs):
            return
        value_item = self._table.item(row, 5)
        confirm_value = value_item.text() if value_item else ""
        dialog = DiffDetailDialog(self._root, self._diffs[row], confirm_value, self)
        dialog.exec()
