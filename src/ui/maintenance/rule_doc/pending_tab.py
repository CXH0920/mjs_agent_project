# -*- coding: utf-8 -*-
"""元规则维护·疑难登记页签（pending，第二轮第 15 批审查拆分）。

从 RuleDocPanel 拆出：本地待办 docs/rule_doc_pending.json 的增查与
「转为 FAQ 提案」收敛到本页签（读写全走 rule_doc_service，本地操作不经
脚本）。转提案成功后发 proposal_created 信号，由宿主刷新提案工作台清单。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from src.business.rag import rule_doc_service as rds
from src.ui.shared.widgets import EmptyState

logger = logging.getLogger(__name__)


class PendingTab(QWidget):
    """「疑难登记」页签：本地待办清单 + 新增登记 + 转提案。"""

    proposal_created = Signal()  # 疑难转提案成功：请求宿主刷新提案清单

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self._root = root
        self._pending_open = 0
        self._build_ui()

    @property
    def open_count(self) -> int:
        """open 状态的疑难条数（宿主下一步建议用）。"""
        return self._pending_open

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _build_ui(self) -> None:
        card = QFrame()
        card.setObjectName("panelCardSurface")
        layout = QVBoxLayout(card)
        top = QHBoxLayout()
        top.addWidget(QLabel("疑难登记（本地待办）"))
        top.addStretch(1)
        self._add_button = QPushButton("新增登记")
        self._add_button.clicked.connect(self._add_pending)
        top.addWidget(self._add_button)
        self._to_proposal_button = QPushButton("转为 FAQ 提案")
        self._to_proposal_button.clicked.connect(self._to_proposal)
        top.addWidget(self._to_proposal_button)
        layout.addLayout(top)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["ID", "日期", "描述", "涉及", "状态"])
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)
        self._empty_label = EmptyState("无待办疑难", "完成检查后可在此登记疑难。")
        layout.addWidget(self._empty_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    # ---------------------------------------------------------------
    # 清单与操作（读写全走 rule_doc_service，本地操作不经脚本）
    # ---------------------------------------------------------------
    def refresh(self) -> None:
        items = rds.load_pending(self._root)
        self._pending_open = sum(1 for it in items if it.get("status") == "open")
        self._table.setRowCount(len(items))
        for i, it in enumerate(items):
            for col, key in enumerate(("id", "date", "description", "involved", "status")):
                self._table.setItem(i, col, QTableWidgetItem(str(it.get(key, ""))))
        self._empty_label.setVisible(not items)

    def _add_pending(self) -> None:
        desc, ok = QInputDialog.getMultiLineText(self, "新增疑难登记", "疑难描述：")
        if not ok or not desc.strip():
            return
        involved, ok2 = QInputDialog.getText(self, "新增疑难登记", "涉及技能/卡牌（可空）：")
        involved = involved.strip() if ok2 else ""
        try:
            rds.add_pending(self._root, desc, involved)
        except OSError as exc:
            # atomic_write_json 失败原样抛出（读侧 load_pending 已吞读异常）
            logger.exception("新增疑难登记写盘失败")
            QMessageBox.critical(self, "登记失败", "无法写入疑难登记文件：%s" % exc)
            return
        self.refresh()

    def _to_proposal(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条疑难登记。")
            return
        item = rds.load_pending(self._root)[row]
        try:
            path = rds.pending_to_proposal(self._root, item["id"])
        except ValueError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self.refresh()
        self.proposal_created.emit()
        QMessageBox.information(self, "已生成", "提案已生成：%s" % path)
