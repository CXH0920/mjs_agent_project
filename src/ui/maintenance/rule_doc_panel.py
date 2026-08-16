# -*- coding: utf-8 -*-
"""元规则维护页签（知识库维护 → 元规则维护）。

维护对象：docs/元规则整理-完整版.md（规则知识库 T0 母本，只增不删、机器校验）。
四个子页签能力：
1. 文档状态：跑 scripts/audit_rule_doc.py 展示 ERROR/WARN/INFO 摘要与问题明细；
2. 数据段差异：跑 scripts/sync_rule_stats.py --json 预览差异，"应用已确认差异"执行 --apply；
3. 提案工作台：列出 docs/archive/proposals 提案，"生成提案"（propose_rule_changes.py）、
   "合入已确认提案"（apply_rule_proposal.py）；
4. 疑难登记：本地待办 docs/rule_doc_pending.json 的增查与"转为 FAQ 提案"。
顶部引导卡说明建议流程；底部日志区可折叠，所有脚本输出统一汇入。
"""

from __future__ import annotations

import html
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
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
    SPACE_MD,
    SPACE_SM,
    TONE_INFO,
    TONE_SUCCESS,
    TONE_WARNING,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import DialogFooter, NoticeBanner, PageActionBar, ScriptRunner, show_toast

PYTHON = sys.executable

# 数据段差异类型 → 中文语义（A4 术语提示）
_KIND_LABELS = {"full": "全自动", "candidate": "候选", "checkpoint": "校验点"}
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
    """展示列 item：去掉 ItemIsEditable（仅「确认值」列可编辑）。"""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item

# ---------------------------------------------------------------------------
# 执行记录日志（logs/rule_doc_ops.log）：用户操作轨迹，与 app.log（内部异常）分离
# ---------------------------------------------------------------------------
_OPS_LOGGER = logging.getLogger("rule_doc_ops")
_OPS_LOGGER.propagate = False
_OPS_HANDLER_ROOT: Path | None = None


def _ensure_ops_handler(root: Path) -> None:
    """按项目根注册 rule_doc_ops 文件 handler（root 变化时换绑，防重复注册）。"""
    global _OPS_HANDLER_ROOT
    if _OPS_HANDLER_ROOT == root:
        return
    if _OPS_HANDLER_ROOT is not None:
        for handler in _OPS_LOGGER.handlers[:]:
            _OPS_LOGGER.removeHandler(handler)
            handler.close()
    log_path = root / "logs" / "rule_doc_ops.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    handler.setLevel(logging.INFO)
    _OPS_LOGGER.addHandler(handler)
    _OPS_LOGGER.setLevel(logging.INFO)
    _OPS_HANDLER_ROOT = root


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
        meta.setObjectName("specialCardEditMeta")
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
        if status == "revised" and not text:
            QMessageBox.warning(self, "校验失败", "「修改后通过」需要填写合入文本")
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
        meta.setObjectName("specialCardEditMeta")
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
                # 与脚本 _apply_faq_revise 一致：仅替换裁定列，保留行结构
                cells = local.split("|")
                if len(cells) >= 3:
                    cells[2] = " %s " % official
                    official = "|".join(cells)
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
        meta.setObjectName("specialCardEditMeta")
        layout.addWidget(meta)
        summary = QLabel("差异摘要：%s" % diff.get("message", ""))
        summary.setObjectName("specialCardEditMeta")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        # 警示条：文档当前行与检查时快照不一致 → 应用会失败
        self._stale_warning = QLabel()
        self._stale_warning.setObjectName("specialCardEditMeta")
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


class RuleDocPanel(QWidget):
    data_changed = Signal()

    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self._root = root
        self._runner = ScriptRunner(self)
        self._runner.output.connect(self._append_log)
        self._runner.finished.connect(self._on_runner_finished)
        self._pending_finished: tuple = ()  # (on_finished, sentinel_codes, sentinel_note, failure_codes)
        self._pending_sync = False
        # 尚未完成 audit+sync 检查（A2 未检查引导）
        self._checked = False
        self._audit_counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
        self._proposal_pending = 0
        self._pending_open = 0
        # B2/B3 状态
        self._diffs: list[dict] = []
        self._current_proposal_path = ""
        self._last_command = ""
        _ensure_ops_handler(root)
        self._setup_ui()
        self.refresh()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._action_bar = PageActionBar()
        self._refresh_button = QPushButton("刷新状态")
        self._refresh_button.clicked.connect(self.refresh_all)
        self._action_bar.add_action(self._refresh_button, ROLE_PRIMARY)
        self._log_toggle_button = QPushButton("收起日志")
        self._log_toggle_button.clicked.connect(self._toggle_log)
        self._action_bar.add_action(self._log_toggle_button, ROLE_SECONDARY)
        layout.addWidget(self._action_bar)

        # A1 工作流导语卡（可折叠）：说明维护对象与建议流程
        self._guide_card = QFrame()
        self._guide_card.setObjectName("noticeBanner")
        set_tone(self._guide_card, TONE_INFO)
        guide_layout = QVBoxLayout(self._guide_card)
        guide_layout.setContentsMargins(SPACE_MD, SPACE_SM, SPACE_MD, SPACE_SM)
        guide_layout.setSpacing(4)
        guide_top = QHBoxLayout()
        guide_title = QLabel("维护规则母本 docs/元规则整理-完整版.md（只增不删、机器校验）")
        guide_title.setObjectName("noticeBannerTitle")
        guide_top.addWidget(guide_title)
        guide_top.addStretch(1)
        self._guide_toggle_button = QPushButton("收起引导")
        self._guide_toggle_button.clicked.connect(self._toggle_guide)
        guide_top.addWidget(self._guide_toggle_button)
        guide_layout.addLayout(guide_top)
        guide_body = QLabel(
            "建议流程：① 刷新检查 → ② 应用数据段差异 → ③ 生成/合入提案 → ④ 登记疑难；"
            "完成改动后回到「语料状态」重建语料+索引。")
        guide_body.setObjectName("noticeBannerMessage")
        guide_body.setWordWrap(True)
        guide_layout.addWidget(guide_body)
        layout.addWidget(self._guide_card)

        # 四个能力拆为子页签，避免纵向堆叠过长
        self._tabs = QTabWidget()
        self._tabs.setObjectName("ruleDocSubTabs")
        self._tabs.addTab(self._build_audit_tab(), "文档状态")
        self._tabs.addTab(self._build_diff_tab(), "数据段差异")
        self._tabs.addTab(self._build_proposal_tab(), "提案工作台")
        self._tabs.addTab(self._build_pending_tab(), "疑难登记")

        # 日志常驻底部，QSplitter 支持拖拽/折叠
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setObjectName("ragLogSurface")
        self._log.setPlaceholderText("脚本输出将显示在这里……")
        self._log.setMaximumBlockCount(2000)
        self._log.setMinimumHeight(60)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(True)
        splitter.addWidget(self._tabs)
        splitter.addWidget(self._log)
        splitter.setSizes([620, 180])
        layout.addWidget(splitter, 1)

    def _toggle_log(self) -> None:
        """收起/展开底部日志区。"""
        visible = not self._log.isVisible()
        self._log.setVisible(visible)
        self._log_toggle_button.setText("收起日志" if visible else "展开日志")

    def _toggle_guide(self) -> None:
        """收起/展开工作流导语卡（按 isHidden 判断，不依赖窗口是否已显示）。"""
        visible = self._guide_card.isHidden()
        self._guide_card.setVisible(visible)
        self._guide_toggle_button.setText("收起引导" if visible else "展开引导")

    def _build_audit_tab(self) -> QWidget:
        # 1. 文档状态：汇总计数 + 问题明细（B1）
        status_card = QFrame()
        status_card.setObjectName("ragTableSurface")
        s_layout = QVBoxLayout(status_card)
        self._audit_banner = NoticeBanner("文档校验", "", TONE_SUCCESS, self)
        self._audit_table = QTableWidget(0, 3)
        self._audit_table.setHorizontalHeaderLabels(["级别", "数量", "说明"])
        self._audit_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._audit_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        s_layout.addWidget(self._audit_banner)
        s_layout.addWidget(self._audit_table)
        self._audit_empty_label = QLabel("尚未检查文档，点击顶部「刷新状态」")
        self._audit_empty_label.setObjectName("specialCardEditMeta")
        s_layout.addWidget(self._audit_empty_label)
        self._audit_detail_table = QTableWidget(0, 3)
        self._audit_detail_table.setHorizontalHeaderLabels(["级别", "问题", "建议动作"])
        self._audit_detail_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._audit_detail_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._audit_detail_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._audit_detail_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._audit_detail_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._audit_detail_table.setShowGrid(False)
        self._audit_detail_table.verticalHeader().setVisible(False)
        s_layout.addWidget(self._audit_detail_table)
        self._audit_detail_empty = QLabel("未发现 ERROR/WARN")
        self._audit_detail_empty.setObjectName("specialCardEditMeta")
        s_layout.addWidget(self._audit_detail_empty)
        return status_card

    def _build_diff_tab(self) -> QWidget:
        # 2. 数据段差异（B2：勾选 + 确认值 + 一键应用）
        diff_card = QFrame()
        diff_card.setObjectName("ragTableSurface")
        d_layout = QVBoxLayout(diff_card)
        d_top = QHBoxLayout()
        d_top.addWidget(QLabel("数据段差异（sync_rule_stats）"))
        d_top.addStretch(1)
        self._apply_diff_button = QPushButton("应用已确认差异")
        self._apply_diff_button.setEnabled(False)
        self._apply_diff_button.clicked.connect(self._apply_diffs)
        d_top.addWidget(self._apply_diff_button)
        d_layout.addLayout(d_top)
        # A4 术语提示：差异类型语义
        self._diff_hint = QLabel("类型：全自动=可直接应用 / 候选=需人工确认 / 校验点=仅提示；勾选后可在「确认值」列修改应用值")
        self._diff_hint.setObjectName("specialCardEditMeta")
        d_layout.addWidget(self._diff_hint)
        self._diff_summary_label = QLabel("全自动 0 · 候选 0 · 校验点 0 ｜ 已勾选 0 项可应用")
        self._diff_summary_label.setObjectName("specialCardEditMeta")
        d_layout.addWidget(self._diff_summary_label)
        self._diff_table = QTableWidget(0, 7)
        self._diff_table.setHorizontalHeaderLabels(["应用", "段", "行号", "类型", "差异摘要", "确认值", "操作"])
        self._diff_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._diff_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._diff_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._diff_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._diff_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._diff_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self._diff_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._diff_table.setEditTriggers(QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed)
        self._diff_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._diff_table.setShowGrid(False)
        self._diff_table.verticalHeader().setVisible(False)
        d_layout.addWidget(self._diff_table)
        self._diff_empty_label = QLabel("尚未检查数据段差异（点「刷新状态」）")
        self._diff_empty_label.setObjectName("specialCardEditMeta")
        d_layout.addWidget(self._diff_empty_label)
        return diff_card

    def _build_proposal_tab(self) -> QWidget:
        # 3. 提案工作台
        prop_card = QFrame()
        prop_card.setObjectName("ragTableSurface")
        p_layout = QVBoxLayout(prop_card)
        p_top = QHBoxLayout()
        p_top.addWidget(QLabel("提案工作台"))
        self._proposal_combo = QComboBox()
        self._proposal_combo.currentIndexChanged.connect(self._load_proposal_detail)
        p_top.addWidget(self._proposal_combo, 1)
        self._generate_button = QPushButton("生成提案")
        self._generate_button.clicked.connect(self._generate_proposal)
        p_top.addWidget(self._generate_button)
        self._apply_proposal_button = QPushButton("合入已确认提案")
        self._apply_proposal_button.clicked.connect(self._apply_proposal)
        p_top.addWidget(self._apply_proposal_button)
        p_layout.addLayout(p_top)
        self._proposal_summary_label = QLabel("待确认 0 · 已确认 0 · 已驳回 0")
        self._proposal_summary_label.setObjectName("specialCardEditMeta")
        p_layout.addWidget(self._proposal_summary_label)
        self._proposal_table = QTableWidget(0, 6)
        self._proposal_table.setHorizontalHeaderLabels(["提案号", "类型", "目标", "建议文本", "状态", "操作"])
        self._proposal_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._proposal_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._proposal_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._proposal_table.setShowGrid(False)
        self._proposal_table.verticalHeader().setVisible(False)
        p_layout.addWidget(self._proposal_table)
        self._proposal_empty_label = QLabel("暂无提案，可点击「生成提案」")
        self._proposal_empty_label.setObjectName("specialCardEditMeta")
        p_layout.addWidget(self._proposal_empty_label)
        return prop_card

    def _build_pending_tab(self) -> QWidget:
        # 4. 疑难登记
        pending_card = QFrame()
        pending_card.setObjectName("ragTableSurface")
        n_layout = QVBoxLayout(pending_card)
        n_top = QHBoxLayout()
        n_top.addWidget(QLabel("疑难登记（本地待办）"))
        n_top.addStretch(1)
        self._add_pending_button = QPushButton("新增登记")
        self._add_pending_button.clicked.connect(self._add_pending)
        n_top.addWidget(self._add_pending_button)
        self._to_proposal_button = QPushButton("转为 FAQ 提案")
        self._to_proposal_button.clicked.connect(self._to_proposal)
        n_top.addWidget(self._to_proposal_button)
        n_layout.addLayout(n_top)
        self._pending_table = QTableWidget(0, 5)
        self._pending_table.setHorizontalHeaderLabels(["ID", "日期", "描述", "涉及", "状态"])
        self._pending_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._pending_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._pending_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        n_layout.addWidget(self._pending_table)
        self._pending_empty_label = QLabel("无待办疑难")
        self._pending_empty_label.setObjectName("specialCardEditMeta")
        n_layout.addWidget(self._pending_empty_label)
        return pending_card

    # ---------------------------------------------------------------
    # 刷新（本地只读解析，不发进程）
    # ---------------------------------------------------------------
    def refresh(self) -> None:
        """本地只读刷新（提案/疑难/差异表格），不启动进程。"""
        self._refresh_proposals()
        self._refresh_pending()
        self._load_proposal_detail()
        if not self._checked:
            # A2：打开页面尚未检查时给出明确的第一步指引
            self._action_bar.set_status("尚未检查，点击「刷新状态」开始检查文档", TONE_INFO)

    def refresh_all(self) -> None:
        """串行刷新：先 audit，完成后 sync（避免并发 QProcess 冲突）。"""
        self._pending_sync = True
        self.refresh_audit()

    def reload_data(self) -> None:
        self.refresh()

    def refresh_audit(self) -> None:
        self._run_script(["audit_rule_doc.py"], self._on_audit_finished)

    def refresh_diffs(self) -> None:
        json_path = rds.sync_json_path(self._root)
        # 退出码 1 = 检测到差异（脚本哨兵语义，非失败）
        self._run_script(["sync_rule_stats.py", "--json", str(json_path)], self._on_sync_finished,
                         sentinel_codes={1}, sentinel_note="检测到差异，见差异表")

    def _refresh_proposals(self) -> None:
        proposals = rds.list_proposals(self._root)
        self._proposal_pending = sum(
            p["total"] - p["approved"] - p["rejected"] for p in proposals)
        current = self._proposal_combo.currentText()
        self._proposal_combo.blockSignals(True)
        self._proposal_combo.clear()
        for p in proposals:
            label = "%s（%d 条，已确认 %d）" % (p["proposal_id"], p["total"], p["approved"])
            self._proposal_combo.addItem(label, p["path"])
        if current:
            idx = self._proposal_combo.findText(current)
            if idx >= 0:
                self._proposal_combo.setCurrentIndex(idx)
        self._proposal_combo.blockSignals(False)

    def _refresh_pending(self) -> None:
        items = rds.load_pending(self._root)
        self._pending_open = sum(1 for it in items if it.get("status") == "open")
        self._pending_table.setRowCount(len(items))
        for i, it in enumerate(items):
            for col, key in enumerate(("id", "date", "description", "involved", "status")):
                self._pending_table.setItem(i, col, QTableWidgetItem(str(it.get(key, ""))))
        self._pending_empty_label.setVisible(not items)

    # ---------------------------------------------------------------
    # 提案详情
    # ---------------------------------------------------------------
    def _load_proposal_detail(self) -> None:
        path = self._proposal_combo.currentData()
        if not path:
            self._proposal_table.setRowCount(0)
            self._proposal_empty_label.setVisible(True)
            self._proposal_summary_label.setText("待确认 0 · 已确认 0 · 已驳回 0")
            self._current_proposal_path = ""
            return
        try:
            proposal = rds.parse_proposal(path)
        except (OSError, json.JSONDecodeError):
            self._proposal_table.setRowCount(0)
            self._proposal_empty_label.setVisible(True)
            self._proposal_summary_label.setText("待确认 0 · 已确认 0 · 已驳回 0")
            self._current_proposal_path = ""
            return
        self._current_proposal_path = path
        items = proposal.get("items", [])
        self._proposal_table.setRowCount(len(items))
        pending = confirmed = rejected = 0
        for i, it in enumerate(items):
            status = it.get("status", "pending")
            values = (it.get("id", ""),
                      _TYPE_LABELS.get(it.get("type", ""), it.get("type", "")),
                      it.get("target", ""),
                      str(it.get("suggested_text", ""))[:80],
                      _STATUS_LABELS.get(status, status))
            for col, v in enumerate(values):
                self._proposal_table.setItem(i, col, _readonly_item(str(v)))
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
            self._proposal_table.setCellWidget(i, 5, actions)
            if status == "pending":
                pending += 1
            elif status in ("approved", "revised"):
                confirmed += 1
            elif status == "rejected":
                rejected += 1
        self._proposal_summary_label.setText(
            "待确认 %d · 已确认 %d · 已驳回 %d" % (pending, confirmed, rejected))
        self._proposal_empty_label.setVisible(not items)

    def _open_detail_dialog(self, row: int) -> None:
        """打开提案项详情（差异对比 + 文档上下文，只读）。"""
        if not self._current_proposal_path:
            return
        try:
            proposal = rds.parse_proposal(self._current_proposal_path)
            item = proposal["items"][row]
        except (OSError, json.JSONDecodeError, KeyError, IndexError):
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
        except (OSError, json.JSONDecodeError, KeyError, IndexError):
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
        self._load_proposal_detail()
        self._refresh_proposals()
        show_toast(self, "已确认：%s" % _STATUS_LABELS.get(status, status))

    # ---------------------------------------------------------------
    # 疑难登记动作
    # ---------------------------------------------------------------
    def _add_pending(self) -> None:
        desc, ok = QInputDialog.getMultiLineText(self, "新增疑难登记", "疑难描述：")
        if not ok or not desc.strip():
            return
        involved, ok2 = QInputDialog.getText(self, "新增疑难登记", "涉及技能/卡牌（可空）：")
        involved = involved.strip() if ok2 else ""
        rds.add_pending(self._root, desc, involved)
        self._refresh_pending()

    def _to_proposal(self) -> None:
        row = self._pending_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条疑难登记。")
            return
        item = rds.load_pending(self._root)[row]
        try:
            path = rds.pending_to_proposal(self._root, item["id"])
        except ValueError as exc:
            QMessageBox.warning(self, "失败", str(exc))
            return
        self._refresh_pending()
        self._refresh_proposals()
        QMessageBox.information(self, "已生成", "提案已生成：%s" % path)

    # ---------------------------------------------------------------
    # 脚本执行（ScriptRunner 封装 QProcess）
    # ---------------------------------------------------------------
    def _run_script(self, args: list[str], on_finished,
                    sentinel_codes: set[int] | None = None,
                    sentinel_note: str = "",
                    failure_codes: dict[int, str] | None = None) -> None:
        """启动脚本；sentinel_codes 为"业务性非零退出码"（如 sync 的 1=有差异），
        failure_codes 为"业务失败但需定制文案"的退出码映射。
        """
        if self._runner.is_running():
            QMessageBox.information(self, "正在执行", "已有任务运行中，请等待完成。")
            return
        script = self._root / "scripts" / args[0]
        if not script.exists():
            QMessageBox.critical(self, "脚本缺失", "未找到 %s" % script)
            return
        self._last_command = " ".join(args)
        self._log.appendPlainText("$ python scripts/" + self._last_command)
        self._pending_finished = (on_finished, sentinel_codes, sentinel_note, failure_codes)
        self._runner.run(PYTHON, script, args[1:], self._root)

    def _on_runner_finished(self, code: int) -> None:
        """ScriptRunner 完成回调：转发到原 _on_finished（保留签名供测试直接调用）。"""
        self._on_finished(code, None, *self._pending_finished)

    def _append_log(self, data: bytes) -> None:
        text = bytes(data).decode("utf-8", errors="replace")
        self._log.appendPlainText(text.rstrip("\n"))

    # 结论行着色（脚本原始输出保持纯文本，不影响 _last_output 解析）
    _MARK_COLORS = {"success": "#2e7d32", "warning": "#b26a00", "error": "#c62828"}

    def _append_marked(self, text: str, kind: str = "default") -> None:
        color = self._MARK_COLORS.get(kind)
        if color is None:
            self._log.appendPlainText(text)
        else:
            self._log.appendHtml('<span style="color:%s">%s</span>' % (color, html.escape(text)))
        self._log.ensureCursorVisible()

    def _on_finished(self, code: int, _status, on_finished,
                     sentinel_codes: set[int] | None = None,
                     sentinel_note: str = "",
                     failure_codes: dict[int, str] | None = None) -> None:
        if code == 0:
            conclusion, kind = "✔ 执行完成", "success"
        elif sentinel_codes and code in sentinel_codes:
            note = ("：" + sentinel_note) if sentinel_note else ""
            conclusion, kind = "⚠ 执行完成（退出码 %d%s）" % (code, note), "warning"
        elif failure_codes and code in failure_codes:
            conclusion, kind = "✘ " + failure_codes[code], "error"
        else:
            conclusion, kind = "✘ 执行失败（退出码 %d）" % code, "error"
        self._append_marked(conclusion, kind)
        # 执行记录持久化（rule_doc_ops.log）：用户操作轨迹
        _OPS_LOGGER.info("%s → exit=%d %s", self._last_command, code, conclusion)
        try:
            on_finished()
        except Exception as exc:  # noqa: BLE001
            self._log.appendPlainText("刷新失败：%s" % exc)
        self.data_changed.emit()

    # ---------------------------------------------------------------
    # 各命令完成回调
    # ---------------------------------------------------------------
    def _on_audit_finished(self) -> None:
        # 输出已在日志中；audit 汇总行解析展示
        summary = self._last_output()
        issues = rds.parse_audit_output(summary)
        counts = rds.audit_issue_counts(issues)
        self._audit_counts = counts
        self._audit_table.setRowCount(3)
        for i, level in enumerate(("ERROR", "WARN", "INFO")):
            self._audit_table.setItem(i, 0, QTableWidgetItem(level))
            self._audit_table.setItem(i, 1, QTableWidgetItem(str(counts[level])))
            self._audit_table.setItem(i, 2, QTableWidgetItem(""))
        self._audit_empty_label.setVisible(False)
        # B1 问题明细：ERROR/WARN 逐条 + 建议动作
        detail_rows = [it for it in issues if it["level"] in ("ERROR", "WARN")]
        self._audit_detail_table.setRowCount(len(detail_rows))
        for row, it in enumerate(detail_rows):
            self._audit_detail_table.setItem(row, 0, QTableWidgetItem(it["level"]))
            self._audit_detail_table.setItem(row, 1, QTableWidgetItem(it["message"]))
            if "数据段一致性" in it["message"]:
                button = QPushButton("去同步")
                set_ui_role(button, ROLE_SECONDARY)
                button.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
                self._audit_detail_table.setCellWidget(row, 2, button)
        self._audit_detail_empty.setVisible(not detail_rows)
        badge = counts["ERROR"] + counts["WARN"]
        if counts["ERROR"] or counts["WARN"]:
            self._audit_banner.set_tone(TONE_WARNING)
            self._audit_banner.message_label.setText(f"存在 {badge} 个 ERROR/WARN，见下方明细")
            self._audit_banner.message_label.setVisible(True)
        else:
            self._audit_banner.set_tone(TONE_SUCCESS)
            self._audit_banner.message_label.setText("文档校验通过")
            self._audit_banner.message_label.setVisible(True)
        # A4 页签角标：文档状态（ERROR+WARN 数）
        self._tabs.setTabText(0, "文档状态" if not badge else f"文档状态（{badge}）")
        if self._pending_sync:
            self._pending_sync = False
            self.refresh_diffs()

    def _on_sync_finished(self) -> None:
        self._checked = True
        self._diffs = rds.parse_sync_diff(rds.sync_json_path(self._root))
        self._diff_table.setRowCount(len(self._diffs))
        for i, d in enumerate(self._diffs):
            check = QCheckBox()
            check.setEnabled(d["kind"] != "checkpoint")
            check.setChecked(d["kind"] == "full")
            check.stateChanged.connect(lambda _=0: self._refresh_diff_summary())
            self._diff_table.setCellWidget(i, 0, check)
            self._diff_table.setItem(i, 1, _readonly_item(d["section"]))
            self._diff_table.setItem(i, 2, _readonly_item(str(d["line_no"] + 1)))
            self._diff_table.setItem(i, 3, _readonly_item(_KIND_LABELS.get(d["kind"], d["kind"])))
            self._diff_table.setItem(i, 4, _readonly_item(d["message"]))
            value_item = QTableWidgetItem(d.get("new") or "")
            if d["kind"] == "checkpoint":
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._diff_table.setItem(i, 5, value_item)
            view_button = QPushButton("查看")
            set_ui_role(view_button, ROLE_SECONDARY)
            view_button.clicked.connect(lambda _=False, idx=i: self._open_diff_detail(idx))
            self._diff_table.setCellWidget(i, 6, view_button)
        self._diff_empty_label.setText("数据段与数据源一致")
        self._diff_empty_label.setVisible(not self._diffs)
        self._refresh_diff_summary()
        # A4 页签角标：数据段差异（条数）
        self._tabs.setTabText(1, "数据段差异" if not self._diffs else f"数据段差异（{len(self._diffs)}）")
        # A3 状态驱动"下一步建议"
        status, tone = self._compose_next_step(self._diffs)
        self._action_bar.set_status(status, tone)

    def _open_diff_detail(self, row: int) -> None:
        """打开数据段差异详情（差异对比 + 文档上下文，只读）。"""
        if row < 0 or row >= len(self._diffs):
            return
        value_item = self._diff_table.item(row, 5)
        confirm_value = value_item.text() if value_item else ""
        dialog = DiffDetailDialog(self._root, self._diffs[row], confirm_value, self)
        dialog.exec()

    def _refresh_diff_summary(self) -> None:
        """B2：按勾选状态刷新统计与「应用已确认差异」按钮。"""
        counts = {"full": 0, "candidate": 0, "checkpoint": 0}
        checked = 0
        for d in self._diffs:
            counts[d["kind"]] = counts.get(d["kind"], 0) + 1
        for row, d in enumerate(self._diffs):
            widget = self._diff_table.cellWidget(row, 0)
            if widget is not None and widget.isChecked():
                checked += 1
        self._diff_summary_label.setText(
            "全自动 %d · 候选 %d · 校验点 %d ｜ 已勾选 %d 项可应用"
            % (counts["full"], counts["candidate"], counts["checkpoint"], checked))
        self._apply_diff_button.setText(
            "应用已确认差异（%d）" % checked if checked else "应用已确认差异")
        self._apply_diff_button.setEnabled(checked > 0)

    def _collect_confirmed_rows(self) -> list[dict] | None:
        """B2：收集勾选行的确认 payload；UI 校验失败返回 None。"""
        rows = []
        for row, d in enumerate(self._diffs):
            widget = self._diff_table.cellWidget(row, 0)
            if widget is None or not widget.isChecked():
                continue
            value_item = self._diff_table.item(row, 5)
            new = value_item.text().strip() if value_item else ""
            if not new:
                QMessageBox.warning(
                    self, "校验失败", "第 %d 行（段 %s）确认值为空，请填写后重试" % (row + 1, d["section"]))
                return None
            if not (new.startswith("|") and new.endswith("|")):
                QMessageBox.warning(
                    self, "校验失败",
                    "第 %d 行（段 %s）确认值不是完整表格行（需以 | 开头和结尾）" % (row + 1, d["section"]))
                return None
            if d.get("old") and len(new.split("|")) != len(d["old"].split("|")):
                QMessageBox.warning(
                    self, "校验失败",
                    "第 %d 行（段 %s）确认值列数与原文不一致，会破坏表格结构" % (row + 1, d["section"]))
                return None
            rows.append({
                "section": d["section"],
                "line_no": d["line_no"],
                "old": d.get("old"),
                "new": new,
                "message": d.get("message", ""),
            })
        return rows

    def _compose_next_step(self, diffs: list[dict]) -> tuple[str, str]:
        """按审计/差异/提案/疑难状态给出下一步建议（A3）。"""
        counts = self._audit_counts
        full = sum(1 for d in diffs if d["kind"] == "full")
        candidate = sum(1 for d in diffs if d["kind"] == "candidate")
        checkpoint = sum(1 for d in diffs if d["kind"] == "checkpoint")
        if counts["ERROR"]:
            return "文档校验有 ERROR，请查看「文档状态」", TONE_WARNING
        if full:
            return f"全自动差异 {full} 处可一键应用（数据段差异页）", TONE_WARNING
        if counts["WARN"]:
            return "文档校验有告警，查看「文档状态」", TONE_WARNING
        if candidate:
            return f"候选差异 {candidate} 处需人工确认（数据段差异页）", TONE_WARNING
        if checkpoint:
            return f"校验点 {checkpoint} 处需人工核对（数据段差异页）", TONE_WARNING
        if self._proposal_pending:
            return f"提案 {self._proposal_pending} 条待确认（提案工作台）", TONE_WARNING
        if self._pending_open:
            return f"疑难 {self._pending_open} 条待消化（疑难登记）", TONE_WARNING
        return "校验通过、数据一致、无待办", TONE_SUCCESS

    def _apply_diffs(self) -> None:
        """B2：收集勾选行 → 写确认清单 → sync_rule_stats.py --apply-json 落地文档。"""
        rows = self._collect_confirmed_rows()
        if rows is None:
            return
        answer = QMessageBox.question(
            self, "应用差异",
            "将用「确认值」列原位替换文档数据段（%d 处，脚本预检 old 匹配，失败整批不写入）。继续？" % len(rows))
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            path = rds.confirmed_diff_path(self._root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                            encoding="utf-8", newline="\n")
        except OSError as exc:
            QMessageBox.critical(self, "写入失败", "无法写入确认清单：%s" % exc)
            return
        self._run_script(
            ["sync_rule_stats.py", "--apply-json", str(path)], self._on_apply_json_finished,
            failure_codes={
                1: "应用失败：文档可能已被修改，未写入（见上方明细，可刷新后重试）",
                2: "前置失败：确认清单或文档不可读，未写入",
            })

    def _on_apply_json_finished(self) -> None:
        """应用完成后重跑 --json 刷新差异表（成功行消失，失败行保留）。"""
        self.refresh_diffs()

    def _generate_proposal(self) -> None:
        self._run_script(["propose_rule_changes.py", "--no-llm"], self._refresh_proposals)

    def _apply_proposal(self) -> None:
        path = self._proposal_combo.currentData()
        if not path:
            QMessageBox.information(self, "提示", "请先选择提案。")
            return
        answer = QMessageBox.question(self, "合入提案", "合入所选提案的已确认项（audit 失败将回滚）。继续？")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._run_script(["apply_rule_proposal.py", "--proposal", str(path)], self._refresh_proposals)

    def _last_output(self) -> str:
        return self._log.toPlainText()