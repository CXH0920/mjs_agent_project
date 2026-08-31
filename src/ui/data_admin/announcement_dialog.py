"""公告更新记录与百科 diff 展示对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from src.business.announcement import clean_html
from src.data.announcement_manager import (
    Announcement,
    AnnouncementManager,
    AnnouncementStatus,
)
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY
from src.ui.shared.widgets import PageHeader, set_ui_role

STATUS_LABELS = {
    AnnouncementStatus.PENDING: "待生效",
    AnnouncementStatus.READY: "可更新",
    AnnouncementStatus.APPLIED: "已处理",
}


class AnnouncementDialog(QDialog):
    """公告列表、全文与百科 diff 的只读查看对话框。"""

    check_requested = Signal()
    update_requested = Signal()

    def __init__(self, announcement_manager: AnnouncementManager, parent=None) -> None:
        super().__init__(parent)
        self._manager = announcement_manager
        self._diff: dict = {"added": [], "modified": [], "removed": []}
        self._checked = False
        self.setWindowTitle("公告更新")
        self.setMinimumSize(780, 560)
        self._build_ui()
        self.reload()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "公告更新",
            "手动检查官方更新公告与武将数据变更，不自动联网。",
        ))

        self._diff_label = QLabel("尚未检查，请点击「检查更新」。")
        self._diff_label.setWordWrap(True)
        layout.addWidget(self._diff_label)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

        self._content = QTextBrowser()
        self._content.setOpenExternalLinks(True)
        layout.addWidget(self._content, 2)

        footer = QHBoxLayout()
        footer.addStretch()
        self._check_button = QPushButton("检查更新")
        set_ui_role(self._check_button, ROLE_SECONDARY)
        self._check_button.clicked.connect(self.check_requested.emit)
        footer.addWidget(self._check_button)
        self._update_button = QPushButton("更新武将数据")
        set_ui_role(self._update_button, ROLE_PRIMARY)
        self._update_button.clicked.connect(self.update_requested.emit)
        footer.addWidget(self._update_button)
        close_button = QPushButton("关闭")
        set_ui_role(close_button, ROLE_SECONDARY)
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    # ---------------------------------------------------------------
    # 数据刷新
    # ---------------------------------------------------------------

    def reload(self) -> None:
        """从管理器重新加载公告列表。"""
        self._list.blockSignals(True)
        self._list.clear()
        for announcement in self._manager.list_announcements():
            status = STATUS_LABELS.get(announcement.status, announcement.status)
            date_text = (announcement.publishdate or "")[:10]
            text = f"[{status}] {date_text}  {announcement.title}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, announcement)
            item.setToolTip(self._detail_text(announcement))
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._refresh_update_button()

    def set_diff(self, diff: dict) -> None:
        """展示最近一次检查的百科 diff。"""
        self._diff = diff or {"added": [], "modified": [], "removed": []}
        self._checked = True
        self._update_diff_label()
        self._refresh_update_button()

    def _update_diff_label(self) -> None:
        if not self._checked:
            self._diff_label.setText("尚未检查，请点击「检查更新」。")
            return
        if not any(self._diff.values()):
            self._diff_label.setText("上次检查：百科数据无变化。")
            return
        parts = []
        for group, label in (("added", "新增"), ("modified", "修改"), ("removed", "删除")):
            entries = self._diff.get(group) or []
            if entries:
                names = "、".join(entry["name"] for entry in entries[:6])
                suffix = "…" if len(entries) > 6 else ""
                parts.append(f"{label} {len(entries)}：{names}{suffix}")
        self._diff_label.setText(f"上次检查：百科数据有变化 —— {'；'.join(parts)}。")

    def _refresh_update_button(self) -> None:
        # 始终可点：点击后由主窗口给出明确反馈（无候选时提示等待百科更新）
        self._update_button.setEnabled(True)

    # ---------------------------------------------------------------
    # 展示辅助
    # ---------------------------------------------------------------

    @staticmethod
    def _detail_text(announcement: Announcement) -> str:
        status = STATUS_LABELS.get(announcement.status, announcement.status)
        parts = [f"状态：{status}"]
        if announcement.matched_heroes:
            tags = []
            for change in announcement.matched_heroes:
                tag = f"{change.name}（{change.change}）"
                if not change.known:
                    tag += "·未收录"
                tags.append(tag)
            parts.append("涉及：" + "、".join(tags))
        if announcement.content_missing:
            parts.append("正文解析失败，请打开官网查看")
        return "；".join(parts)

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        if current is None:
            self._content.clear()
            return
        announcement: Announcement = current.data(Qt.ItemDataRole.UserRole)
        self._content.setPlainText(self._full_text(announcement))

    @staticmethod
    def _full_text(announcement: Announcement) -> str:
        lines = [
            f"标题：{announcement.title}",
            f"日期：{announcement.publishdate}",
            AnnouncementDialog._detail_text(announcement),
        ]
        if announcement.url:
            lines.append(f"原文：{announcement.url}")
        if announcement.content_missing:
            lines.append("")
            lines.append("（本次未获取到正文，请打开官网原文查看。）")
        else:
            lines.append("")
            lines.append(clean_html(announcement.content))
        return "\n".join(lines)