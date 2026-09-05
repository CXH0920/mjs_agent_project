"""
名将杀 Agent - 武将浏览器

提供武将列表浏览、搜索筛选、详情查看和攻略展示功能。
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from src.business.maintenance.data_management_service import DataMutationService
from src.data.combo_manager import ComboManager
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide
from src.data.synergy_manager import SynergyManager
from src.ui.library.guide_edit_dialog import GuideEditDialog
from src.ui.library.hero_detail_views import HeroGuideSummaryView, HeroInfoView, HeroSynergyView
from src.ui.library.hero_edit_dialog import HeroEditDialog
from src.ui.library.synergy_edit_dialog import SynergyEditDialog
from src.ui.shared.guide_detail_dialog import GuideDetailDialog
from src.ui.shared.persist import run_edit_dialog
from src.ui.shared.style import ROLE_GHOST, ROLE_SECONDARY, set_ui_role

logger = logging.getLogger(__name__)


class HeroListPanel(QWidget):
    """左侧武将列表面板

    包含搜索框、势力筛选和武将列表。
    """

    hero_selected = Signal(int)  # 发出武将 ID

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._all_heroes: list[Hero] = []
        self._filtered_heroes: list[Hero] = []
        self._last_hero_id: int | None = None

        self._setup_ui()
        self._load_heroes()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setObjectName("heroListPane")
        self.setMinimumWidth(240)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # 搜索框
        self._search_box = QLineEdit()
        self._search_box.setObjectName("heroSearchInput")
        self._search_box.setPlaceholderText("搜索武将名称...")
        self._search_box.textChanged.connect(self._apply_filters)
        layout.addWidget(self._search_box)

        # 势力筛选
        faction_layout = QHBoxLayout()
        faction_layout.addWidget(QLabel("势力:"))
        self._faction_combo = QComboBox()
        self._faction_combo.setObjectName("heroFactionFilter")
        self._faction_combo.currentTextChanged.connect(self._apply_filters)
        faction_layout.addWidget(self._faction_combo, 1)
        layout.addLayout(faction_layout)

        self._count_label = QLabel()
        self._count_label.setObjectName("libraryResultCount")
        layout.addWidget(self._count_label)

        # 武将列表
        self._list = QListWidget()
        self._list.setObjectName("heroList")
        self._list.setAlternatingRowColors(True)
        self._list.currentRowChanged.connect(self._on_selection_changed)
        layout.addWidget(self._list, 1)

    # ---------------------------------------------------------------
    # 数据加载
    # ---------------------------------------------------------------

    def _load_heroes(self) -> None:
        """加载武将数据和势力列表"""
        self._all_heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)

        # 填充势力筛选
        self._faction_combo.blockSignals(True)
        self._faction_combo.clear()
        self._faction_combo.addItem("全部")
        for faction in self._hero_mgr.list_factions():
            self._faction_combo.addItem(faction)
        self._faction_combo.blockSignals(False)

        self._apply_filters()

    def reload(self) -> None:
        """公有接口：重新加载武将数据"""
        self._load_heroes()

    def _apply_filters(self) -> None:
        """应用搜索和筛选条件"""
        search_text = self._search_box.text().strip()
        faction = self._faction_combo.currentText()

        self._filtered_heroes = []
        for hero in self._all_heroes:
            # 势力筛选
            if faction != "全部" and hero.faction != faction:
                continue
            # 名称搜索
            if search_text and search_text not in hero.name:
                continue
            self._filtered_heroes.append(hero)

        self._count_label.setText(
            f"显示 {len(self._filtered_heroes)} / 共 {len(self._all_heroes)} 名武将"
        )
        self._refresh_list()

    def _refresh_list(self) -> None:
        """刷新列表显示，尽可能恢复上一次选中的武将"""
        current_id = self._last_hero_id
        self._list.blockSignals(True)
        self._list.clear()
        for hero in self._filtered_heroes:
            text = f"{hero.name}  [{hero.position}]" if hero.position else hero.name
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, hero.id)
            item.setToolTip(f"{hero.title} - {hero.faction}")
            self._list.addItem(item)
        self._list.blockSignals(False)

        if self._filtered_heroes:
            # 优先恢复之前的选中项
            target_row = 0
            if current_id is not None:
                for i, hero in enumerate(self._filtered_heroes):
                    if hero.id == current_id:
                        target_row = i
                        break
            self._list.setCurrentRow(target_row)

    def select_hero(self, hero_id: int) -> None:
        """按武将 ID 选中列表项，供攻略关系标签跳转使用。"""
        for row, hero in enumerate(self._filtered_heroes):
            if hero.id == hero_id:
                self._list.setCurrentRow(row)
                return

    def selected_hero_id(self) -> int | None:
        """返回当前列表选中的武将 ID。"""
        row = self._list.currentRow()
        if 0 <= row < len(self._filtered_heroes):
            return self._filtered_heroes[row].id
        return None

    def _on_selection_changed(self, row: int) -> None:
        """列表选中项变化"""
        if 0 <= row < len(self._filtered_heroes):
            hero_id = self._filtered_heroes[row].id
            self._last_hero_id = hero_id
            self.hero_selected.emit(hero_id)

class HeroDetailPanel(QWidget):
    """武将详情面板"""

    data_changed = Signal()  # 数据变更后通知刷新列表
    synergies_changed = Signal()  # 相性变更后通知刷新关联视图
    hero_requested = Signal(int)  # 请求切换到关联武将

    def __init__(
        self,
        hero_manager: HeroManager,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
        parent=None,
        combo_manager: ComboManager | None = None,
    ):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager
        self._synergy_mgr = synergy_manager
        self._data_mutation_service = DataMutationService(
            self._hero_mgr,
            self._guide_mgr,
            self._synergy_mgr,
        )
        self._current_hero: Optional[Hero] = None
        self._current_guide: Optional[HeroGuide] = None

        self._setup_ui(combo_manager)

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self, combo_manager: ComboManager | None = None) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._identity_bar = QFrame()
        self._identity_bar.setObjectName("heroIdentityBar")
        identity_layout = QHBoxLayout(self._identity_bar)
        identity_layout.setContentsMargins(14, 8, 14, 8)
        identity_layout.setSpacing(10)

        identity_text_layout = QVBoxLayout()
        identity_text_layout.setContentsMargins(0, 0, 0, 0)
        identity_text_layout.setSpacing(2)
        self._identity_name = QLabel("请选择一个武将")
        self._identity_name.setObjectName("heroIdentityName")
        self._identity_name.setWordWrap(True)
        identity_text_layout.addWidget(self._identity_name)
        self._identity_meta = QLabel("从左侧列表选择后查看资料、攻略和相性")
        self._identity_meta.setObjectName("heroIdentityMeta")
        self._identity_meta.setWordWrap(True)
        identity_text_layout.addWidget(self._identity_meta)
        identity_layout.addLayout(identity_text_layout, 1)

        self._context_edit_btn = QPushButton("编辑武将")
        self._context_edit_btn.setObjectName("heroContextEditButton")
        set_ui_role(self._context_edit_btn, ROLE_SECONDARY)
        self._context_edit_btn.clicked.connect(self._on_context_edit)
        identity_layout.addWidget(self._context_edit_btn)

        self._context_menu = QMenu(self)
        self._context_menu.setObjectName("heroContextMenu")
        self._context_delete_action = self._context_menu.addAction("删除武将")
        self._context_delete_action.triggered.connect(self._on_context_delete)
        self._context_more_btn = QToolButton()
        self._context_more_btn.setObjectName("heroContextMoreButton")
        self._context_more_btn.setText("⋯")
        self._context_more_btn.setToolTip("更多操作")
        self._context_more_btn.setAccessibleName("更多操作")
        self._context_more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._context_more_btn.setMenu(self._context_menu)
        set_ui_role(self._context_more_btn, ROLE_GHOST)
        identity_layout.addWidget(self._context_more_btn)
        layout.addWidget(self._identity_bar)

        # 右侧只承担当前武将的内容切换，样式弱于外层资料库导航。
        self._detail_tabs = QTabWidget()
        self._detail_tabs.setObjectName("heroDetailTabs")

        self._info_tab = HeroInfoView()
        self._detail_tabs.addTab(self._info_tab, "武将信息")

        self._guide_tab = HeroGuideSummaryView(self._hero_mgr)
        self._guide_tab.hero_requested.connect(self.hero_requested)
        self._guide_tab.detail_requested.connect(self._open_guide_detail)
        self._detail_tabs.addTab(self._guide_tab, "攻略指南")

        self._synergy_tab = HeroSynergyView(self._hero_mgr, self._synergy_mgr, combo_manager)
        self._synergy_tab.selection_changed.connect(self._update_context_actions)
        self._synergy_tab.edit_requested.connect(self._on_synergy_edit)
        self._detail_tabs.addTab(self._synergy_tab, "武将相性")

        self._detail_tabs.currentChanged.connect(self._update_context_actions)

        layout.addWidget(self._detail_tabs, 1)
        self._update_context_actions()

    def _update_context_actions(self, _index: int | None = None) -> None:
        """根据当前详情页签映射编辑和删除入口。"""
        index = self._detail_tabs.currentIndex()
        labels = (
            ("编辑武将", "删除武将"),
            ("编辑攻略", "删除攻略"),
            ("编辑相性", "删除相性"),
        )
        edit_label, delete_label = labels[index]
        self._context_edit_btn.setText(edit_label)
        self._context_delete_action.setText(delete_label)
        if index == 0:
            enabled = self._current_hero is not None
        elif index == 1:
            enabled = self._current_guide is not None
        else:
            enabled = self._synergy_tab.selected_synergy() is not None
        self._context_edit_btn.setEnabled(enabled)
        self._context_delete_action.setEnabled(enabled)
        self._context_more_btn.setEnabled(enabled)

    def _on_context_edit(self) -> None:
        handlers = (self._on_info_edit, self._on_guide_edit, self._on_synergy_edit)
        handlers[self._detail_tabs.currentIndex()]()

    def _on_context_delete(self) -> None:
        handlers = (self._on_info_delete, self._on_guide_delete, self._on_synergy_delete)
        handlers[self._detail_tabs.currentIndex()]()

    def show_hero(self, hero_id: int) -> None:
        """展示指定武将的详细信息和攻略。"""
        hero = self._hero_mgr.get_hero(hero_id)
        guide = self._guide_mgr.get_guide(hero_id)

        self._current_hero = hero
        self._current_guide = guide

        if not hero:
            self._update_identity_bar(None)
            self._info_tab.show_missing(hero_id)
            self._guide_tab.show_guide(None)
            self._synergy_tab.show_hero(None)
            self._update_context_actions()
            return

        self._update_identity_bar(hero)
        self._info_tab.show_hero(hero)
        self._guide_tab.show_guide(guide)
        self._synergy_tab.show_hero(hero)
        self._update_context_actions()

    def _update_identity_bar(self, hero: Hero | None) -> None:
        """更新始终可见的当前武将身份信息。"""
        if hero is None:
            self._identity_name.setText("未找到武将")
            self._identity_meta.setText("请在左侧列表重新选择武将")
            return

        title = f" · {hero.title}" if hero.title else ""
        self._identity_name.setText(f"{hero.name}{title}")
        self._identity_meta.setText(
            " · ".join((
                hero.faction or "势力未设置",
                hero.position or "定位未设置",
                f"体力 {hero.max_hp}",
                f"手牌 {hero.max_hand}",
            ))
        )

    @property
    def current_hero_id(self) -> int | None:
        return self._current_hero.id if self._current_hero else None

    def refresh_synergies(self) -> None:
        """刷新当前相性视图。"""
        self._synergy_tab.refresh()

    def _open_guide_detail(self) -> None:
        """打开攻略详情窗口。"""
        if not self._current_hero or not self._current_guide:
            return
        dialog = GuideDetailDialog(
            self._current_hero.name,
            self._current_guide,
            self._hero_mgr,
            self,
        )

        def _open_related_hero(hero_id: int) -> None:
            dialog.accept()
            self.hero_requested.emit(hero_id)

        dialog.hero_requested.connect(_open_related_hero)
        dialog.exec()

    # ---------------------------------------------------------------
    # 相性 CRUD
    # ---------------------------------------------------------------

    def _on_synergy_edit(self) -> None:
        """编辑表格中选中的相性。"""
        synergy = self._synergy_tab.selected_synergy()
        if not self._current_hero or not synergy:
            return
        dialog = SynergyEditDialog(
            self._hero_mgr,
            synergy,
            parent=self,
        )
        saved = run_edit_dialog(
            dialog,
            lambda: self._data_mutation_service.update_synergy(dialog.get_synergy()),
            parent=self,
            success_message="相性修改已保存",
            failure_hint="编辑内容已保留。",
            attempts=None,
        )
        if saved:
            self._synergy_tab.refresh()
            self._update_context_actions()
            self.synergies_changed.emit()

    def _on_synergy_delete(self) -> None:
        """删除表格中选中的相性。"""
        synergy = self._synergy_tab.selected_synergy()
        if not synergy:
            return
        first_name = self._hero_mgr.get_hero(synergy.hero_a_id)
        second_name = self._hero_mgr.get_hero(synergy.hero_b_id)
        first_text = first_name.name if first_name else f"#{synergy.hero_a_id}"
        second_text = second_name.name if second_name else f"#{synergy.hero_b_id}"
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定要删除「{first_text}」与「{second_text}」的相性吗？\n该操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._data_mutation_service.delete_synergy(synergy.hero_a_id, synergy.hero_b_id)
            self._synergy_tab.refresh()
            self._update_context_actions()
            self.synergies_changed.emit()
            QMessageBox.information(
                self,
                "删除完成",
                f"已删除「{first_text}」与「{second_text}」的相性。",
            )
        except Exception as e:
            logger.exception("删除相性失败")
            QMessageBox.critical(self, "删除失败", f"无法删除相性:\n{e}")

    def _on_info_edit(self) -> None:
        """打开编辑对话框修改武将信息。"""
        if not self._current_hero:
            return
        dialog = HeroEditDialog(self._current_hero, parent=self)
        saved = run_edit_dialog(
            dialog,
            lambda: self._data_mutation_service.update_hero(dialog.get_hero()),
            parent=self,
            success_message="武将资料已保存",
            failure_hint="编辑内容已保留。",
            attempts=None,
        )
        if saved:
            updated = dialog.get_hero()
            self._current_hero = updated
            self._update_identity_bar(updated)
            self._info_tab.show_hero(updated)
            self.data_changed.emit()

    def _on_info_delete(self) -> None:
        """删除当前武将（含确认）"""
        if not self._current_hero:
            return
        synergy_count = len(self._synergy_mgr.list_synergies_for_hero(self._current_hero.id))
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除武将「{self._current_hero.name}」吗？\n"
            f"该操作不可撤销，关联的攻略和 {synergy_count} 条相性评分也将被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        hero_name = self._current_hero.name
        try:
            self._data_mutation_service.delete_hero_with_relations(self._current_hero.id)
            self._current_hero = None
            self._current_guide = None
            self._info_tab.show_deleted()
            self._guide_tab.show_guide(None)
            self._synergy_tab.show_hero(None)
            self._update_context_actions()
            self.data_changed.emit()
            self.synergies_changed.emit()
            QMessageBox.information(
                self,
                "删除完成",
                f"武将「{hero_name}」及其关联攻略和相性已删除。",
            )
        except Exception as e:
            logger.exception("删除武将失败")
            QMessageBox.critical(self, "删除失败", f"无法删除武将:\n{e}")

    # ---------------------------------------------------------------
    # 攻略 CRUD
    # ---------------------------------------------------------------

    def _on_guide_edit(self) -> None:
        """打开编辑对话框修改攻略"""
        if not self._current_guide:
            return
        dialog = GuideEditDialog(self._current_guide, self._hero_mgr, parent=self)
        saved = run_edit_dialog(
            dialog,
            lambda: self._data_mutation_service.update_guide(dialog.get_guide()),
            parent=self,
            success_message="攻略修改已保存",
            failure_hint="编辑内容已保留。",
            attempts=None,
        )
        if saved:
            updated = dialog.get_guide()
            self._current_guide = updated
            self._guide_tab.show_guide(updated)
            self.data_changed.emit()

    def _on_guide_delete(self) -> None:
        """删除当前攻略（含确认）"""
        if not self._current_guide:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            "确定要删除当前武将的攻略吗？\n该操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._data_mutation_service.delete_guide(self._current_guide.hero_id)
            self._current_guide = None
            self._guide_tab.show_guide(None)
            self._update_context_actions()
            self.data_changed.emit()
            QMessageBox.information(self, "删除完成", "当前武将的攻略已删除。")
        except Exception as e:
            logger.exception("删除攻略失败")
            QMessageBox.critical(self, "删除失败", f"无法删除攻略:\n{e}")

class HeroBrowser(QWidget):
    """武将浏览器主组件，列表选择后在右侧展示摘要和攻略预览。"""

    synergies_changed = Signal()

    def __init__(
        self,
        hero_manager: HeroManager,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
        parent=None,
        combo_manager: ComboManager | None = None,
    ):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager
        self._synergy_mgr = synergy_manager

        self._setup_ui(combo_manager)

    def _setup_ui(self, combo_manager: ComboManager | None = None) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setObjectName("heroBrowserSplitter")
        self._splitter.setChildrenCollapsible(False)
        self._list_panel = HeroListPanel(self._hero_mgr)
        self._splitter.addWidget(self._list_panel)

        # 右侧：详情面板
        self._detail_panel = HeroDetailPanel(
            self._hero_mgr,
            self._guide_mgr,
            self._synergy_mgr,
            combo_manager=combo_manager,
        )
        self._splitter.addWidget(self._detail_panel)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([280, 720])
        layout.addWidget(self._splitter, 1)

        # 连接信号
        self._list_panel.hero_selected.connect(self._detail_panel.show_hero)
        self._detail_panel.hero_requested.connect(self._list_panel.select_hero)
        self._detail_panel.data_changed.connect(self.reload_data)
        self._detail_panel.synergies_changed.connect(self.synergies_changed)

        # 列表面板在构造时已经默认选中首项，此时信号尚未连接，需要主动同步详情。
        initial_hero_id = self._list_panel.selected_hero_id()
        if initial_hero_id is not None:
            self._detail_panel.show_hero(initial_hero_id)

    def reload_data(self) -> None:
        """重新加载列表并刷新当前详情。"""
        current_hero_id = self._detail_panel.current_hero_id
        self._list_panel.reload()
        if current_hero_id is not None:
            self._detail_panel.show_hero(current_hero_id)

    def refresh_synergies(self) -> None:
        """刷新当前武将的相性表格。"""
        self._detail_panel.refresh_synergies()
