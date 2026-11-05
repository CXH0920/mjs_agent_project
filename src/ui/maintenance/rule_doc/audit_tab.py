# -*- coding: utf-8 -*-
"""元规则维护·文档状态页签（audit，第二轮第 15 批审查拆分）。

从 RuleDocPanel 拆出：audit_rule_doc.py 输出的汇总计数与 ERROR/WARN 问题
明细渲染收敛到本页签。脚本由宿主执行，结果经 apply_audit_result(ok, summary)
注入（ok=False 表示脚本崩溃/启动失败，无业务产物）；明细中的「数据段一致性」
问题提供「去同步」按钮，经 sync_requested 信号由宿主切到数据段差异页签。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from src.business.rag import rule_doc_service as rds
from src.ui.shared.style import ROLE_SECONDARY, TONE_SUCCESS, TONE_WARNING, set_ui_role
from src.ui.shared.widgets import EmptyState, NoticeBanner


class AuditTab(QWidget):
    """「文档状态」页签：audit 汇总计数 + 问题明细（B1）。"""

    sync_requested = Signal()

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self._root = root
        self._counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
        self._build_ui()

    @property
    def counts(self) -> dict[str, int]:
        """最近一次 audit 的 ERROR/WARN/INFO 计数（宿主下一步建议与角标用）。"""
        return self._counts

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _build_ui(self) -> None:
        card = QFrame()
        card.setObjectName("panelCardSurface")
        layout = QVBoxLayout(card)
        self._banner = NoticeBanner("文档校验", "", TONE_SUCCESS, self)
        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["级别", "数量", "说明"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._banner)
        layout.addWidget(self._table)
        self._empty_label = EmptyState("尚未检查文档", "点击顶部「刷新状态」开始检查。")
        layout.addWidget(self._empty_label)
        self._detail_table = QTableWidget(0, 3)
        self._detail_table.setHorizontalHeaderLabels(["级别", "问题", "建议动作"])
        self._detail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._detail_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._detail_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._detail_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._detail_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._detail_table.setShowGrid(False)
        self._detail_table.verticalHeader().setVisible(False)
        layout.addWidget(self._detail_table)
        self._detail_empty = EmptyState("文档校验通过", "未发现 ERROR / WARN 级别问题。")
        layout.addWidget(self._detail_empty)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    # ---------------------------------------------------------------
    # 结果渲染（脚本由宿主执行）
    # ---------------------------------------------------------------
    def apply_audit_result(self, ok: bool, summary: str) -> None:
        """渲染一次 audit 结果；ok=False（崩溃/启动失败）时标注未完成，表格保持上次结果。"""
        if not ok:
            # 脚本崩溃/启动失败时无汇总行可解析，空清单会被渲染成
            # 「文档校验通过」+ 角标清零；显式标注未完成
            self._banner.set_tone(TONE_WARNING)
            self._banner.message_label.setText("文档校验未完成（脚本异常退出，见日志）")
            self._banner.message_label.setVisible(True)
            return
        issues = rds.parse_audit_output(summary)
        counts = rds.audit_issue_counts(issues)
        self._counts = counts
        self._table.setRowCount(3)
        for i, level in enumerate(("ERROR", "WARN", "INFO")):
            self._table.setItem(i, 0, QTableWidgetItem(level))
            self._table.setItem(i, 1, QTableWidgetItem(str(counts[level])))
            self._table.setItem(i, 2, QTableWidgetItem(""))
        self._empty_label.setVisible(False)
        # B1 问题明细：ERROR/WARN 逐条 + 建议动作
        detail_rows = [it for it in issues if it["level"] in ("ERROR", "WARN")]
        self._detail_table.setRowCount(len(detail_rows))
        for row, it in enumerate(detail_rows):
            self._detail_table.setItem(row, 0, QTableWidgetItem(it["level"]))
            self._detail_table.setItem(row, 1, QTableWidgetItem(it["message"]))
            if "数据段一致性" in it["message"]:
                button = QPushButton("去同步")
                set_ui_role(button, ROLE_SECONDARY)
                button.clicked.connect(self.sync_requested.emit)
                self._detail_table.setCellWidget(row, 2, button)
        self._detail_empty.setVisible(not detail_rows)
        if counts["ERROR"] or counts["WARN"]:
            self._banner.set_tone(TONE_WARNING)
            self._banner.message_label.setText(
                f"存在 {counts['ERROR'] + counts['WARN']} 个 ERROR/WARN，见下方明细")
        else:
            self._banner.set_tone(TONE_SUCCESS)
            self._banner.message_label.setText("文档校验通过")
        self._banner.message_label.setVisible(True)
