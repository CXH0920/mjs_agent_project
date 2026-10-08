"""可忽略差异列表对话框公共骨架（P0-2 收口）。

CardSyncDialog 与 HeroUpdateConfirmDialog 的忽略入口/详情对比/选中联动
曾逐字复制且已开始偏移（hero 版忽略需 content_hash 门控、无服务时隐藏
入口；card 版选中即可忽略、恒显示入口）。本 mixin 承载同构骨架，
偏移语义提升为钩子由宿主实现：

- _ignore_candidate / _after_ignore_removed：写忽略名单与移除条目后的收尾
- _ignored_count / _exec_ignore_manager：忽略服务的查询与管理入口
- _update_ignore_button_state / _summary_text_for：选中联动的差异
- ignore_ui_optional = True：无 _ignore_service 时整组隐藏忽略入口
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem, QPushButton
from src.ui.shared.style import ROLE_SECONDARY
from src.ui.shared.widgets import set_ui_role

IGNORE_DIFF_TOOLTIP = "选中条目后可忽略：该差异不再提示，官网内容再变化时自动重现。"


class IgnoreableDiffListMixin:
    """宿主需提供 _list / _summary_browser，构建 UI 时调用 _append_ignore_actions。"""

    # card 版服务内建恒可用，宿主可不注入；hero 版注入 AnnouncementService
    _ignore_service = None
    ignore_ui_optional = False

    def _append_ignore_actions(self, actions: QHBoxLayout) -> None:
        if self.ignore_ui_optional and self._ignore_service is None:
            return
        self._ignore_button = QPushButton("忽略此条差异")
        set_ui_role(self._ignore_button, ROLE_SECONDARY)
        self._ignore_button.setEnabled(False)
        self._ignore_button.setToolTip(IGNORE_DIFF_TOOLTIP)
        self._ignore_button.clicked.connect(self._ignore_current)
        actions.addWidget(self._ignore_button)
        self._ignore_manager_button = QPushButton()
        self._ignore_manager_button.clicked.connect(self._open_ignore_manager)
        actions.addWidget(self._ignore_manager_button)
        self._refresh_ignore_label()

    def _append_detail_button(self, actions: QHBoxLayout) -> None:
        self._detail_button = QPushButton("查看全文对比")
        self._detail_button.clicked.connect(self._show_detail)
        actions.addWidget(self._detail_button)

    def _on_selection_changed(self, current: QListWidgetItem | None, _previous=None) -> None:
        candidate = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        self._update_ignore_button_state(candidate)
        if current is None:
            self._summary_browser.clear()
            return
        self._summary_browser.setPlainText(self._summary_text_for(candidate))

    def _update_ignore_button_state(self, candidate: dict | None) -> None:
        self._ignore_button.setEnabled(candidate is not None)

    def _summary_text_for(self, candidate: dict) -> str:
        summary = candidate.get("summary") or []
        return "\n".join(summary) if summary else "（无差异摘要）"

    def _show_detail(self) -> None:
        current = self._list.currentItem()
        if current is None:
            return
        candidate = current.data(Qt.ItemDataRole.UserRole)
        # 对话框类经钩子取自宿主模块命名空间（HeroDiffDetailDialog 定义在
        # hero_update_confirm_dialog；mixin 直接导入会与其对本模块的导入成环）
        self._detail_dialog_class()(
            f"{candidate['name']} 本地 vs 官网",
            candidate.get("local_full", ""),
            candidate.get("official_full", ""),
            self,
        ).exec()

    def _detail_dialog_class(self):
        raise NotImplementedError

    def _ignore_current(self) -> None:
        current = self._list.currentItem()
        if current is None:
            return
        candidate = current.data(Qt.ItemDataRole.UserRole)
        self._ignore_candidate(candidate)
        self._list.takeItem(self._list.row(current))
        self._after_ignore_removed(candidate)

    def _ignore_candidate(self, candidate: dict) -> None:
        raise NotImplementedError

    def _after_ignore_removed(self, candidate: dict) -> None:
        raise NotImplementedError

    def _open_ignore_manager(self) -> None:
        self._exec_ignore_manager()
        self._refresh_ignore_label()

    def _exec_ignore_manager(self) -> None:
        raise NotImplementedError

    def _refresh_ignore_label(self) -> None:
        self._ignore_manager_button.setText(f"已忽略 {self._ignored_count()} 条（管理）")

    def _ignored_count(self) -> int:
        raise NotImplementedError
