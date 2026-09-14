"""卡牌百科更新检查与官网同步确认对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)
from src.business.card_sync import CardSyncCheckResult, CardSyncService
from src.scraper.official_source.card_baike import (
    card_field_diff_summary,
    format_card_full_text,
)
from src.ui.data_admin.hero_update_confirm_dialog import HeroDiffDetailDialog
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY
from src.ui.shared.widgets import PageHeader, set_ui_role

CHANGE_LABELS = {"modified": "修改", "added": "新增", "removed": "官网已删除"}
# 可应用官网值的变更类型；removed 仅展示提醒（删本地卡风险不对称，人工处理）
APPLICABLE_CHANGES = ("modified", "added")


class CardSyncDialog(QDialog):
    """检查官网手牌库差异，勾选后应用官网值覆盖本地（card_amount 保留）。"""

    def __init__(
        self,
        card_sync_service: CardSyncService,
        card_repository,
        card_point_names,
        parent=None,
        auto_check: bool = True,
    ) -> None:
        super().__init__(parent)
        self._service = card_sync_service
        self._cards = card_repository
        self._card_point_names = set(card_point_names or [])
        self._official_cards: dict[str, dict] = {}
        self.applied_count = 0
        self.setWindowTitle("卡牌百科更新")
        self.setMinimumSize(780, 560)
        self._build_ui()
        if auto_check:
            # 菜单入口语义即"检查"，打开后自动触发一次；冷却/忙碌由结果区提示
            self.check()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "卡牌百科更新",
            "手动检查官网手牌库与本地卡牌差异；应用后官网文本覆盖本地，牌堆数量保留本地值。",
        ))

        self._diff_label = QLabel("正在检查官网手牌库...")
        self._diff_label.setWordWrap(True)
        layout.addWidget(self._diff_label)

        actions = QHBoxLayout()
        self._check_button = QPushButton("重新检查")
        set_ui_role(self._check_button, ROLE_SECONDARY)
        self._check_button.clicked.connect(self.check)
        actions.addWidget(self._check_button)
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
        self._list.itemChanged.connect(self._update_count_label)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        self._list.itemDoubleClicked.connect(lambda _item: self._show_detail())
        layout.addWidget(self._list, 1)

        self._summary_browser = QTextBrowser()
        self._summary_browser.setMinimumHeight(110)
        layout.addWidget(self._summary_browser)

        tip = QLabel(
            "提示：官网已删除的卡牌仅作提醒，不会自动从本地移除；"
            "牌堆数量(card_amount)官网不提供，应用后保持本地值。"
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        footer = QHBoxLayout()
        footer.addStretch()
        self._apply_button = QPushButton("应用选中")
        set_ui_role(self._apply_button, ROLE_PRIMARY)
        self._apply_button.setEnabled(False)
        self._apply_button.clicked.connect(self._apply_selected)
        footer.addWidget(self._apply_button)
        close_button = QPushButton("关闭")
        set_ui_role(close_button, ROLE_SECONDARY)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self._service.check_finished.connect(self._on_check_finished)

    # ---------------------------------------------------------------
    # 检查
    # ---------------------------------------------------------------

    def check(self) -> None:
        """触发一次官网检查（受冷却限制）。"""
        if self._service.is_busy:
            self._diff_label.setText("检查正在进行中，请稍候。")
            return
        remaining = self._service.cooldown_remaining
        if remaining > 0:
            self._diff_label.setText(f"检查过于频繁，请 {int(remaining) + 1} 秒后再试。")
            return
        self._diff_label.setText("正在检查官网手牌库...")
        self._apply_button.setEnabled(False)
        if not self._service.check_now():
            self._diff_label.setText("未能启动检查（服务忙碌或冷却中）。")

    def _on_check_finished(self, result: CardSyncCheckResult) -> None:
        if result.error:
            self._diff_label.setText(f"检查失败：{result.error}")
            self._apply_button.setEnabled(False)
            return
        self._official_cards = {
            str(card.get("id")): card for card in result.official_cards
        }
        self._refresh_list(self._build_candidates(result))
        if not any(result.diff.values()):
            self._diff_label.setText("上次检查：官网手牌库与本地卡牌一致，无变化。")
            self._apply_button.setEnabled(False)
            return
        parts = []
        for group in ("added", "modified", "removed"):
            entries = result.diff.get(group) or []
            if entries:
                parts.append(f"{CHANGE_LABELS[group]} {len(entries)}")
        self._diff_label.setText(f"上次检查：官网手牌库有变化 —— {'；'.join(parts)}。")

    def _build_candidates(self, result: CardSyncCheckResult) -> list[dict]:
        """diff 三态 → 候选列表（含字段级摘要、全文与点数提醒）。"""
        candidates: list[dict] = []
        for group in ("modified", "added", "removed"):
            for entry in result.diff.get(group) or []:
                card_id = str(entry.get("id"))
                official = self._official_cards.get(card_id) or {}
                local_card = self._cards.get_card(card_id)
                local = local_card.model_dump(mode="json") if local_card else {}
                candidate = {
                    "card_id": card_id,
                    "name": str(entry.get("name") or official.get("name") or ""),
                    "change": group,
                    "summary": [],
                    "local_full": "",
                    "official_full": "",
                }
                if group == "modified":
                    candidate["summary"] = card_field_diff_summary(local, official)
                    candidate["local_full"] = format_card_full_text(local)
                    candidate["official_full"] = format_card_full_text(official)
                    point_hint = local.get("name") in self._card_point_names
                elif group == "added":
                    candidate["summary"] = [f"官网新增卡牌（ID {card_id}），收录后数量默认 1，请人工核对"]
                    candidate["official_full"] = format_card_full_text(official)
                    point_hint = candidate["name"] in self._card_point_names
                else:
                    candidate["summary"] = ["官网手牌库已不存在该卡，请人工确认是否从本地移除。"]
                    candidate["local_full"] = format_card_full_text(local)
                    point_hint = local.get("name") in self._card_point_names
                if point_hint:
                    candidate["summary"].insert(0, "该卡在点数表中有配置，记得核对花色点数。")
                candidates.append(candidate)
        return candidates

    # ---------------------------------------------------------------
    # 列表
    # ---------------------------------------------------------------

    def _refresh_list(self, candidates: list[dict]) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for candidate in candidates:
            label = f"{candidate['name']}（{CHANGE_LABELS.get(candidate['change'], candidate['change'])}）"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            summary = candidate.get("summary") or []
            if summary:
                item.setToolTip("\n".join(summary))
            if candidate["change"] in APPLICABLE_CHANGES:
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.count():
            self._list.setCurrentRow(0)
        self._update_count_label()

    def _checked_candidates(self) -> list[dict]:
        selected = []
        for index in range(self._list.count()):
            item = self._list.item(index)
            candidate = item.data(Qt.ItemDataRole.UserRole)
            if candidate["change"] in APPLICABLE_CHANGES and item.checkState() == Qt.CheckState.Checked:
                selected.append(candidate)
        return selected

    def _update_count_label(self, *_args) -> None:
        total = sum(
            1 for index in range(self._list.count())
            if self._list.item(index).data(Qt.ItemDataRole.UserRole)["change"] in APPLICABLE_CHANGES
        )
        self._diff_label.setToolTip(f"待更新 {total} 张，已选择 {len(self._checked_candidates())} 张")

    def _select_all(self) -> None:
        self._list.blockSignals(True)
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole)["change"] in APPLICABLE_CHANGES:
                item.setCheckState(Qt.CheckState.Checked)
        self._list.blockSignals(False)
        self._update_count_label()

    def _clear_selection(self) -> None:
        self._list.blockSignals(True)
        for index in range(self._list.count()):
            item = self._list.item(index)
            if item.data(Qt.ItemDataRole.UserRole)["change"] in APPLICABLE_CHANGES:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._list.blockSignals(False)
        self._update_count_label()

    # ---------------------------------------------------------------
    # 详情与应用
    # ---------------------------------------------------------------

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self._summary_browser.clear()
            return
        candidate = current.data(Qt.ItemDataRole.UserRole)
        summary = candidate.get("summary") or []
        self._summary_browser.setPlainText("\n".join(summary) if summary else "（无差异摘要）")

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

    def _apply_selected(self) -> None:
        modified_ids = [
            candidate["card_id"] for candidate in self._checked_candidates()
            if candidate["change"] == "modified"
        ]
        added_ids = [
            candidate["card_id"] for candidate in self._checked_candidates()
            if candidate["change"] == "added"
        ]
        if not modified_ids and not added_ids:
            return
        try:
            summary = self._service.apply_updates(modified_ids, added_ids)
        except RuntimeError as error:
            QMessageBox.warning(self, "无法应用", str(error))
            return
        self.applied_count += summary["applied"]
        applied = set(modified_ids) | set(added_ids)
        for index in range(self._list.count() - 1, -1, -1):
            candidate = self._list.item(index).data(Qt.ItemDataRole.UserRole)
            if candidate["card_id"] in applied:
                self._list.takeItem(index)
        self._update_count_label()
        remaining = len(self._checked_candidates())
        self._diff_label.setText(
            f"已应用 {summary['applied']} 张（修改 {summary['modified']} / 新增 {summary['added']}）。"
            "未勾选的卡下次检查继续提示。"
        )
        self._apply_button.setEnabled(remaining > 0)
