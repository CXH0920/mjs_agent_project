"""百科忽略名单管理对话框：查看/恢复被压制的武将与卡牌差异条目。

全局兜底入口（主窗口菜单）：武将侧全部差异被忽略后公告不再 ready、确认
对话框不再弹出，管理能力必须独立于 diff 对话框可达。名单读写经两个业务
服务（AnnouncementService / CardSyncService），UI 不直接触数据层。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)
from src.ui.shared.style import ROLE_SECONDARY
from src.ui.shared.widgets import DialogFooter, PageHeader, set_ui_role

KIND_LABELS = {"heroes": "武将", "cards": "卡牌"}
STATE_LABELS = {"added": "新增", "modified": "修改", "removed": "官网已删除"}


class BaikeIgnoreManagerDialog(QDialog):
    """列出已忽略的武将/卡牌条目，支持单条（多选）恢复与全部恢复。"""

    def __init__(
        self,
        parent=None,
        announcement_service=None,
        card_sync_service=None,
    ) -> None:
        super().__init__(parent)
        self._announcement_service = announcement_service
        self._card_sync_service = card_sync_service
        self.setWindowTitle("百科忽略名单管理")
        self.setMinimumSize(560, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "百科忽略名单管理",
            "被忽略的差异条目在百科检查中不再提示；恢复后下次检查将重新显示"
            "（官网内容在此期间又变化时也会自动重现）。",
        ))

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._list, 1)

        actions = QHBoxLayout()
        restore_button = QPushButton("恢复选中")
        set_ui_role(restore_button, ROLE_SECONDARY)
        restore_button.clicked.connect(self._restore_selected)
        actions.addWidget(restore_button)
        restore_all_button = QPushButton("全部恢复")
        set_ui_role(restore_all_button, ROLE_SECONDARY)
        restore_all_button.clicked.connect(self._restore_all)
        actions.addWidget(restore_all_button)
        actions.addStretch()
        layout.addLayout(actions)

        footer = DialogFooter(accept_text="关闭", cancel_text="", show_cancel=False)
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)

        self._refresh()

    def _sections(self) -> list[tuple[str, dict]]:
        """[(kind, 名单段)]：kind 标签 + 服务提供的当前名单内容。"""
        sections = []
        if self._announcement_service is not None:
            sections.append(("heroes", self._announcement_service.list_ignored_heroes()))
        if self._card_sync_service is not None:
            sections.append(("cards", self._card_sync_service.list_ignored_cards()))
        return sections

    def _refresh(self) -> None:
        self._list.clear()
        for kind, section in self._sections():
            for entry_id, entry in sorted(section.items(), key=lambda pair: pair[1].name):
                state = STATE_LABELS.get(entry.state, entry.state)
                label = (
                    f"{KIND_LABELS[kind]} · {entry.name}（{state}）"
                    f" · 忽略于 {entry.ignored_at or '未知时间'}"
                )
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, (kind, entry_id))
                self._list.addItem(item)

    def _restore_selected(self) -> None:
        hero_ids = []
        card_ids = []
        for item in self._list.selectedItems():
            kind, entry_id = item.data(Qt.ItemDataRole.UserRole)
            (hero_ids if kind == "heroes" else card_ids).append(entry_id)
        if hero_ids:
            self._announcement_service.restore_heroes(hero_ids)
        if card_ids:
            self._card_sync_service.restore_cards(card_ids)
        if hero_ids or card_ids:
            self._refresh()

    def _restore_all(self) -> None:
        if self._announcement_service is not None:
            self._announcement_service.restore_heroes()
        if self._card_sync_service is not None:
            self._card_sync_service.restore_cards()
        self._refresh()
