"""武将数据更新确认对话框（本地 vs 官网字段级差异）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
)
from src.ui.shared.rich_diff import build_diff_rows, rows_to_html
from src.ui.shared.widgets import DialogFooter, PageHeader

CHANGE_LABELS = {
    "增强": "增强",
    "削弱": "削弱",
    "调整": "调整",
    "新增": "新增",
}


class HeroDiffDetailDialog(QDialog):
    """本地全文 vs 官网全文：Git 风格 diff（默认）+ 本地/官网原文。"""

    def __init__(self, title: str, local_text: str, official_text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(860, 560)
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            title,
            "差异对比：`-` 删除（红） / `+` 新增（绿），修改内容为红删 + 绿增",
        ))

        self._tabs = QTabWidget()
        tabs = self._tabs
        self._diff_browser = QTextBrowser()
        self._diff_browser.setHtml(rows_to_html(build_diff_rows(local_text, official_text)))
        self._local_browser = QTextBrowser()
        self._local_browser.setPlainText(local_text or "（无本地内容）")
        self._official_browser = QTextBrowser()
        self._official_browser.setPlainText(official_text or "（无官网内容）")
        tabs.addTab(self._diff_browser, "差异对比")
        tabs.addTab(self._local_browser, "本地原文")
        tabs.addTab(self._official_browser, "官网原文")
        layout.addWidget(tabs, 1)

        footer = DialogFooter(accept_text="关闭", cancel_text="", show_cancel=False)
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)


class HeroUpdateConfirmDialog(QDialog):
    """列出待更新武将，用户勾选要覆盖的；未勾选的保留本地内容。"""

    def __init__(self, candidates: list[dict], parent=None) -> None:
        super().__init__(parent)
        self._candidates = list(candidates)
        self.selected_ids: list[int] = []
        self.update_new = False
        self.setWindowTitle("更新武将数据确认")
        self.setMinimumSize(720, 520)
        self._build_ui()
        self._refresh_list()
        if self._list.count():
            self._list.setCurrentRow(0)

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "更新武将数据确认",
            "勾选要覆盖的武将；未勾选的保留本地内容，本次不会被覆盖。",
        ))

        self._count_label = QLabel()
        layout.addWidget(self._count_label)

        actions = QHBoxLayout()
        select_all_button = QPushButton("全选")
        select_all_button.clicked.connect(self._select_all)
        actions.addWidget(select_all_button)
        clear_button = QPushButton("清空选择")
        clear_button.clicked.connect(self._clear_selection)
        actions.addWidget(clear_button)
        actions.addStretch()
        self._detail_button = QPushButton("查看全文对比")
        self._detail_button.clicked.connect(self._show_detail)
        actions.addWidget(self._detail_button)
        layout.addLayout(actions)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemChanged.connect(self._on_item_changed)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(lambda _item: self._show_detail())
        layout.addWidget(self._list, 1)

        self._summary_browser = QTextBrowser()
        self._summary_browser.setMinimumHeight(120)
        layout.addWidget(self._summary_browser)

        tip = QLabel("提示：官网百科可能存在错别字、重复描述或疏漏，覆盖前请核对差异。")
        tip.setWordWrap(True)
        layout.addWidget(tip)

        footer = DialogFooter(accept_text="更新勾选武将", cancel_text="取消")
        footer.accepted.connect(self._accept_selection)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    # ---------------------------------------------------------------
    # 列表刷新
    # ---------------------------------------------------------------

    def _refresh_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for candidate in self._candidates:
            label = f"{candidate['name']}（{CHANGE_LABELS.get(candidate['change'], candidate['change'])}）"
            if not candidate.get("known"):
                label += "·未收录"
            label += f" — {candidate.get('source', '')}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            summary = candidate.get("summary") or []
            if summary:
                item.setToolTip("\n".join(summary))
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._update_count_label()

    def _selected_candidates(self) -> list[dict]:
        selected = []
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                selected.append(item.data(Qt.ItemDataRole.UserRole))
        return selected

    def _update_count_label(self) -> None:
        selected = len(self._selected_candidates())
        self._count_label.setText(
            f"共 {len(self._candidates)} 个待更新武将，已选择 {selected} 个"
        )

    def _select_all(self) -> None:
        self._list.blockSignals(True)
        for index in range(self._list.count()):
            self._list.item(index).setCheckState(Qt.CheckState.Checked)
        self._list.blockSignals(False)
        self._update_count_label()

    def _clear_selection(self) -> None:
        self._list.blockSignals(True)
        for index in range(self._list.count()):
            self._list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        self._update_count_label()

    # ---------------------------------------------------------------
    # 事件与确认
    # ---------------------------------------------------------------

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        self._update_count_label()

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self._summary_browser.clear()
            return
        candidate = current.data(Qt.ItemDataRole.UserRole)
        summary = candidate.get("summary") or []
        if summary:
            self._summary_browser.setPlainText("\n".join(summary))
        elif not candidate.get("known") and candidate.get("change") == "新增":
            self._summary_browser.setPlainText("（官网数据暂不可用，请以官网公告为准）")
        else:
            self._summary_browser.setPlainText("（差异摘要暂不可用，可点击“查看全文对比”核对）")

    def _show_detail(self) -> None:
        current = self._list.currentItem()
        if current is None:
            return
        candidate = current.data(Qt.ItemDataRole.UserRole)
        dialog = HeroDiffDetailDialog(
            f"{candidate['name']} 本地 vs 官网",
            candidate.get("local_full", ""),
            candidate.get("official_full", ""),
            self,
        )
        dialog.exec()

    def _accept_selection(self) -> None:
        selected = self._selected_candidates()
        self.selected_ids = [
            int(candidate["hero_id"])
            for candidate in selected
            if candidate.get("hero_id") is not None and candidate.get("change") != "新增"
        ]
        self.update_new = any(candidate.get("change") == "新增" for candidate in selected)
        self.accept()