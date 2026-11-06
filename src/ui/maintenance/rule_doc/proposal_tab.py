# -*- coding: utf-8 -*-
"""元规则维护·提案工作台页签（propose，第二轮第 15 批审查拆分）。

从 RuleDocPanel 拆出：提案清单/详情/逐条确认收敛到本页签，
ProposalItemConfirmDialog 与 ProposalDetailDialog 随迁。提案的生成与合入
脚本由宿主执行：「生成提案」直接发 generate_requested，「合入已确认提案」
校验选择并确认后发 apply_requested(path)；脚本完成由宿主回调本页签刷新。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from src.business.rag import rule_doc_service as rds
from src.ui.shared.rich_diff import build_diff_rows, rows_to_html
from src.ui.shared.style import (
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    set_ui_role,
)
from src.ui.shared.widgets import DialogFooter, EmptyState, show_toast

logger = logging.getLogger(__name__)

# 提案条目状态 → 中文
_STATUS_LABELS = {"pending": "待确认", "approved": "已确认", "revised": "已修订", "rejected": "已驳回"}
# 提案类型 → 中文
_TYPE_LABELS = {
    "faq_new": "新增FAQ", "faq_revise": "修订FAQ", "term_new": "新增术语",
    "row_revise": "修订表格行", "section_new": "新增小节", "none": "无需动文档",
}
# 表格类类型：合入文本含竖线会破坏表格行
_TABLE_TYPES = {"faq_new", "faq_revise", "term_new", "row_revise"}


def _readonly_item(text: str) -> QTableWidgetItem:
    """展示列 item：去掉 ItemIsEditable。"""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


class ProposalItemConfirmDialog(QDialog):
    """逐条确认提案项：查看上下文、修改合入文本、选状态（通过/修改后通过/驳回）。"""

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self._item = item
        self._status = ""
        self._edited_text: str | None = None
        self.setWindowTitle("确认提案项 %s" % item.get("id", ""))
        self.setMinimumWidth(580)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        meta = QLabel("类型：%s · 目标：%s" % (
            _TYPE_LABELS.get(self._item.get("type", ""), self._item.get("type", "")),
            self._item.get("target", "-")))
        meta.setObjectName("metaText")
        layout.addWidget(meta)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("来源:", QLabel(self._item.get("source") or "—"))
        form.addRow("依据:", QLabel(self._item.get("basis") or "—"))
        form.addRow("理由:", QLabel(self._item.get("rationale") or "—"))
        layout.addLayout(form)
        divider = QFrame()
        divider.setObjectName("contentDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(divider)
        layout.addWidget(QLabel("合入文本（可编辑，将作为文档落地的内容）："))
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlainText(
            self._item.get("edited_text") or self._item.get("suggested_text") or "")
        self._text_edit.setMinimumHeight(120)
        layout.addWidget(self._text_edit)
        footer = QHBoxLayout()
        footer.addStretch(1)
        for text, status, role in (
            ("驳回", "rejected", ROLE_SECONDARY),
            ("修改后通过", "revised", ROLE_SECONDARY),
            ("通过", "approved", ROLE_PRIMARY),
        ):
            button = QPushButton(text)
            set_ui_role(button, role)
            button.clicked.connect(lambda _=False, s=status: self._choose(s))
            footer.addWidget(button)
        cancel_button = QPushButton("取消")
        set_ui_role(cancel_button, ROLE_SECONDARY)
        cancel_button.clicked.connect(self.reject)
        footer.addWidget(cancel_button)
        layout.addLayout(footer)

    def _choose(self, status: str) -> None:
        text = self._text_edit.toPlainText().strip()
        if status in ("approved", "revised") and self._item.get("type") in _TABLE_TYPES and "|" in text:
            QMessageBox.warning(self, "校验失败", "合入文本含竖线会破坏表格行，请移除后重试")
            return
        # approved 与 revised 同样校验非空：空合入文本对任何类型都无意义
        # （脚本侧仅部分类型有空拒绝，不能作为唯一防线）
        if status in ("approved", "revised") and not text:
            QMessageBox.warning(self, "校验失败", "「通过 / 修改后通过」需要填写合入文本")
            return
        original = (self._item.get("edited_text") or self._item.get("suggested_text") or "").strip()
        self._status = status
        self._edited_text = text if (status in ("approved", "revised") and text != original) else None
        self.accept()

    def choice(self) -> tuple[str, str | None]:
        """返回 (status, edited_text)；edited_text 为 None 表示未改动（脚本用 suggested_text）。"""
        return self._status, self._edited_text


class ProposalDetailDialog(QDialog):
    """提案项详情（只读）：带上下文的 Git 风格差异对比（复用 rich_diff）。"""

    def __init__(self, root: Path, item: dict, parent=None):
        super().__init__(parent)
        self._root = root
        self._item = item
        self.setWindowTitle("提案项详情 %s" % item.get("id", ""))
        self.setMinimumSize(860, 560)
        self._setup_ui()

    def _doc_path(self) -> Path:
        return self._root / "docs" / "元规则整理-完整版.md"

    def _setup_ui(self) -> None:
        item = self._item
        layout = QVBoxLayout(self)
        meta = QLabel("类型：%s · 目标：%s · 状态：%s" % (
            _TYPE_LABELS.get(item.get("type", ""), item.get("type", "")),
            item.get("target", "-"),
            _STATUS_LABELS.get(item.get("status", "pending"), item.get("status", "pending"))))
        meta.setObjectName("metaText")
        layout.addWidget(meta)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("来源:", QLabel(item.get("source") or "—"))
        form.addRow("依据:", QLabel(item.get("basis") or "—"))
        form.addRow("理由:", QLabel(item.get("rationale") or "—"))
        layout.addLayout(form)

        self._tabs = QTabWidget()
        self._diff_browser = QTextBrowser()
        self._diff_browser.setHtml(self._build_diff_html())
        self._context_browser = QTextBrowser()
        context = rds.doc_section_context(self._doc_path(), item.get("target", ""))
        self._context_browser.setPlainText(context or "（未找到目标位置上下文）")
        self._tabs.addTab(self._diff_browser, "差异对比")
        self._tabs.addTab(self._context_browser, "文档上下文")
        layout.addWidget(self._tabs, 1)

        footer = DialogFooter(accept_text="关闭", cancel_text="", show_cancel=False)
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)

    def _build_diff_html(self) -> str:
        """按类型构造 diff：修订类用文档当前行 vs 建议文本；新增类整行绿增。"""
        item = self._item
        ptype = item.get("type", "")
        if ptype == "none":
            return "<p>数据变更不涉及 T0 语义，无需动文档。</p>"
        official = (item.get("edited_text") or item.get("suggested_text") or "").strip()
        if ptype == "faq_revise":
            local = rds.doc_target_line(self._doc_path(), item)
            if local is None:
                local = "（未找到目标行：文档可能已变化）"
            elif local.startswith("|"):
                # 与脚本实合入共用同一替换规则（#A7）
                official = rds.build_faq_revise_row(local, official)
            return rows_to_html(build_diff_rows(local, official))
        if ptype == "row_revise":
            local = rds.doc_target_line(self._doc_path(), item)
            if local is None:
                local = "（未找到目标行：old_text 不匹配，文档可能已变化）"
            return rows_to_html(build_diff_rows(local, official))
        # 新增类（faq_new / term_new / section_new）
        if ptype == "section_new":
            hint = "（新增小节，将追加到第 %s 章末尾）" % item.get("target", "?")
        else:
            hint = "（新增内容，当前文档无对应行）"
        return rows_to_html(build_diff_rows(hint, official))


class ProposalTab(QWidget):
    """「提案工作台」页签：提案清单 + 逐条确认（B3）。"""

    generate_requested = Signal()  # 请求宿主执行 propose_rule_changes.py
    apply_requested = Signal(str)  # 请求宿主执行 apply_rule_proposal.py（提案路径）

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self._root = root
        self._current_proposal_path = ""
        self._proposal_pending = 0
        self._build_ui()

    @property
    def pending_count(self) -> int:
        """全部提案的待确认条目数（宿主下一步建议用）。"""
        return self._proposal_pending

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _build_ui(self) -> None:
        card = QFrame()
        card.setObjectName("panelCardSurface")
        layout = QVBoxLayout(card)
        top = QHBoxLayout()
        top.addWidget(QLabel("提案工作台"))
        self._combo = QComboBox()
        self._combo.currentIndexChanged.connect(self.load_detail)
        top.addWidget(self._combo, 1)
        self._generate_button = QPushButton("生成提案")
        self._generate_button.clicked.connect(self._generate)
        top.addWidget(self._generate_button)
        self._apply_button = QPushButton("合入已确认提案")
        self._apply_button.clicked.connect(self._apply)
        top.addWidget(self._apply_button)
        layout.addLayout(top)
        self._summary_label = QLabel("待确认 0 · 已确认 0 · 已驳回 0")
        self._summary_label.setObjectName("metaText")
        layout.addWidget(self._summary_label)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["提案号", "类型", "目标", "建议文本", "状态", "操作"])
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)
        self._empty_label = EmptyState("暂无提案", "可点击「生成提案」生成。")
        layout.addWidget(self._empty_label)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)

    # ---------------------------------------------------------------
    # 清单刷新与详情（脚本完成回调也走这里）
    # ---------------------------------------------------------------
    def refresh_proposals(self) -> None:
        proposals = rds.list_proposals(self._root)
        self._proposal_pending = sum(
            p["total"] - p["approved"] - p["rejected"] for p in proposals)
        current = self._combo.currentText()
        self._combo.blockSignals(True)
        self._combo.clear()
        for p in proposals:
            label = "%s（%d 条，已确认 %d）" % (p["proposal_id"], p["total"], p["approved"])
            self._combo.addItem(label, p["path"])
        if current:
            idx = self._combo.findText(current)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)

    def load_detail(self) -> None:
        path = self._combo.currentData()
        if not path:
            self._table.setRowCount(0)
            self._empty_label.setVisible(True)
            self._summary_label.setText("待确认 0 · 已确认 0 · 已驳回 0")
            self._current_proposal_path = ""
            return
        try:
            proposal = rds.parse_proposal(path)
        except (OSError, ValueError):
            # ValueError 覆盖 JSONDecodeError 与 UnicodeDecodeError——手改
            # 坏的提案文件（含 GBK 等错编码保存）降级为空态而非崩溃
            self._table.setRowCount(0)
            self._empty_label.setVisible(True)
            self._summary_label.setText("待确认 0 · 已确认 0 · 已驳回 0")
            self._current_proposal_path = ""
            return
        self._current_proposal_path = path
        items = proposal.get("items", [])
        self._table.setRowCount(len(items))
        pending = confirmed = rejected = 0
        for i, it in enumerate(items):
            status = it.get("status", "pending")
            values = (it.get("id", ""),
                      _TYPE_LABELS.get(it.get("type", ""), it.get("type", "")),
                      it.get("target", ""),
                      str(it.get("suggested_text", ""))[:80],
                      _STATUS_LABELS.get(status, status))
            for col, v in enumerate(values):
                self._table.setItem(i, col, _readonly_item(str(v)))
            # 操作列：[查看]（详情 diff）+ [确认]（确认对话框）
            actions = QWidget()
            actions_layout = QHBoxLayout(actions)
            actions_layout.setContentsMargins(0, 0, 0, 0)
            actions_layout.setSpacing(4)
            view_button = QPushButton("查看")
            set_ui_role(view_button, ROLE_SECONDARY)
            view_button.clicked.connect(lambda _=False, idx=i: self._open_detail_dialog(idx))
            actions_layout.addWidget(view_button)
            confirm_button = QPushButton("确认")
            set_ui_role(confirm_button, ROLE_SECONDARY)
            confirm_button.clicked.connect(lambda _=False, idx=i: self._open_confirm_dialog(idx))
            actions_layout.addWidget(confirm_button)
            self._table.setCellWidget(i, 5, actions)
            if status == "pending":
                pending += 1
            elif status in ("approved", "revised"):
                confirmed += 1
            elif status == "rejected":
                rejected += 1
        self._summary_label.setText(
            "待确认 %d · 已确认 %d · 已驳回 %d" % (pending, confirmed, rejected))
        self._empty_label.setVisible(not items)

    def _open_detail_dialog(self, row: int) -> None:
        """打开提案项详情（差异对比 + 文档上下文，只读）。"""
        if not self._current_proposal_path:
            return
        try:
            proposal = rds.parse_proposal(self._current_proposal_path)
            item = proposal["items"][row]
        except (OSError, ValueError, KeyError, IndexError):
            QMessageBox.warning(self, "读取失败", "无法读取提案文件")
            return
        dialog = ProposalDetailDialog(self._root, item, self)
        dialog.exec()

    def _open_confirm_dialog(self, row: int) -> None:
        """B3：打开逐条确认对话框，确认结果写回提案 JSON。"""
        if not self._current_proposal_path:
            return
        try:
            proposal = rds.parse_proposal(self._current_proposal_path)
            item = proposal["items"][row]
        except (OSError, ValueError, KeyError, IndexError):
            QMessageBox.warning(self, "读取失败", "无法读取提案文件")
            return
        dialog = ProposalItemConfirmDialog(item, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        status, edited_text = dialog.choice()
        try:
            rds.update_proposal_item(
                self._root, self._current_proposal_path, item["id"], status, edited_text)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.load_detail()
        self.refresh_proposals()
        show_toast(self, "已确认：%s" % _STATUS_LABELS.get(status, status))

    # ---------------------------------------------------------------
    # 生成 / 合入（脚本由宿主执行）
    # ---------------------------------------------------------------
    def _generate(self) -> None:
        self.generate_requested.emit()

    def _apply(self) -> None:
        path = self._combo.currentData()
        if not path:
            QMessageBox.information(self, "提示", "请先选择提案。")
            return
        answer = QMessageBox.question(self, "合入提案", "合入所选提案的已确认项（audit 失败将回滚）。继续？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.apply_requested.emit(path)
