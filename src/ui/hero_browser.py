"""
名将杀 Agent - 武将浏览器

提供武将列表浏览、搜索筛选、详情查看和攻略展示功能。
"""

from __future__ import annotations

import logging
from typing import Optional

import mistune

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.guide_manager import GuideManager
from src.data.synergy_manager import SynergyManager
from src.data.models import Hero, HeroGuide, SynergyScore
from src.ui.checkable_combo import CheckableComboBox
from src.ui.guide_edit_dialog import GuideEditDialog
from src.ui.hero_edit_dialog import HeroEditDialog
from src.ui.hero_relation_select_dialog import HeroRelationSelectDialog
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.synergy_edit_dialog import SynergyEditDialog

logger = logging.getLogger(__name__)


class DoubleClickTextBrowser(QTextBrowser):
    """支持双击打开完整 Markdown 内容的文本预览控件。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 搜索框
        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("搜索武将名称...")
        self._search_box.textChanged.connect(self._apply_filters)
        layout.addWidget(self._search_box)

        # 势力筛选
        faction_layout = QHBoxLayout()
        faction_layout.addWidget(QLabel("势力:"))
        self._faction_combo = QComboBox()
        self._faction_combo.currentTextChanged.connect(self._apply_filters)
        faction_layout.addWidget(self._faction_combo, 1)
        layout.addLayout(faction_layout)

        # 武将列表
        self._list = QListWidget()
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
    guide_detail_requested = Signal(str, str)  # 请求弹窗查看完整 Markdown

    def __init__(
        self,
        hero_manager: HeroManager,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
        parent=None,
    ):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager
        self._synergy_mgr = synergy_manager
        self._current_hero: Optional[Hero] = None
        self._current_guide: Optional[HeroGuide] = None

        self._setup_ui()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 使用 Tab 切换: 武将信息 / 攻略指南
        self._detail_tabs = QTabWidget()

        # Tab 1: 武将信息
        self._info_tab = QWidget()
        self._setup_info_tab()
        self._detail_tabs.addTab(self._info_tab, "武将信息")

        # Tab 2: 攻略指南
        self._guide_tab = QWidget()
        self._setup_guide_tab()
        self._detail_tabs.addTab(self._guide_tab, "攻略指南")

        # Tab 3: 武将相性
        self._synergy_tab = QWidget()
        self._setup_synergy_tab()
        self._detail_tabs.addTab(self._synergy_tab, "武将相性")

        # Tab 栏右角：操作按钮组
        self._setup_corner_buttons()

        layout.addWidget(self._detail_tabs, 1)

    def _setup_corner_buttons(self) -> None:
        """在 Tab 栏右侧放置修改/删除按钮，与页签同水平高度"""
        corner = QWidget()
        hlayout = QHBoxLayout(corner)
        hlayout.setContentsMargins(0, 0, 4, 0)
        hlayout.setSpacing(4)

        edit_btn_style = (
            "QPushButton { background-color: transparent; color: #1890FF; "
            "border: 1px solid #1890FF; border-radius: 4px; padding: 2px 12px; "
            "font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: #E6F7FF; }"
        )
        delete_btn_style = (
            "QPushButton { background-color: #F5222D; color: #FFFFFF; border: none; "
            "border-radius: 4px; padding: 2px 12px; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: #DC1F29; }"
        )

        # 武将信息按钮组
        self._info_edit_btn = QPushButton("修改")
        self._info_edit_btn.setStyleSheet(edit_btn_style)
        self._info_edit_btn.clicked.connect(self._on_info_edit)
        hlayout.addWidget(self._info_edit_btn)

        self._info_delete_btn = QPushButton("删除")
        self._info_delete_btn.setStyleSheet(delete_btn_style)
        self._info_delete_btn.clicked.connect(self._on_info_delete)
        hlayout.addWidget(self._info_delete_btn)

        # 攻略按钮组
        self._guide_edit_btn = QPushButton("修改")
        self._guide_edit_btn.setStyleSheet(edit_btn_style)
        self._guide_edit_btn.clicked.connect(self._on_guide_edit)
        hlayout.addWidget(self._guide_edit_btn)

        self._guide_delete_btn = QPushButton("删除")
        self._guide_delete_btn.setStyleSheet(delete_btn_style)
        self._guide_delete_btn.clicked.connect(self._on_guide_delete)
        hlayout.addWidget(self._guide_delete_btn)

        # 相性按钮组
        self._synergy_edit_btn = QPushButton("修改")
        self._synergy_edit_btn.setStyleSheet(edit_btn_style)
        self._synergy_edit_btn.clicked.connect(self._on_synergy_edit)
        hlayout.addWidget(self._synergy_edit_btn)

        self._synergy_delete_btn = QPushButton("删除")
        self._synergy_delete_btn.setStyleSheet(delete_btn_style)
        self._synergy_delete_btn.clicked.connect(self._on_synergy_delete)
        hlayout.addWidget(self._synergy_delete_btn)

        # 初始隐藏攻略和相性按钮组
        self._guide_edit_btn.hide()
        self._guide_delete_btn.hide()
        self._synergy_edit_btn.hide()
        self._synergy_delete_btn.hide()

        self._detail_tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self._detail_tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        """Tab 切换时切换对应的操作按钮组。"""
        self._info_edit_btn.setVisible(index == 0)
        self._info_delete_btn.setVisible(index == 0)
        self._guide_edit_btn.setVisible(index == 1)
        self._guide_delete_btn.setVisible(index == 1)
        self._synergy_edit_btn.setVisible(index == 2)
        self._synergy_delete_btn.setVisible(index == 2)
        self._update_synergy_buttons()

    def _setup_info_tab(self) -> None:
        """构建武将信息页面"""
        layout = QVBoxLayout(self._info_tab)
        layout.setContentsMargins(8, 8, 8, 8)

        # 基本信息区
        self._basic_info = QLabel("请选择一个武将")
        self._basic_info.setWordWrap(True)
        self._basic_info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._basic_info)

        # 技能区域（带分隔线）
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._skills_widget = QWidget()
        self._skills_layout = QVBoxLayout(self._skills_widget)
        self._skills_layout.setContentsMargins(0, 4, 0, 4)
        scroll.setWidget(self._skills_widget)
        layout.addWidget(scroll, 1)

    def _setup_guide_tab(self) -> None:
        """构建攻略页面"""
        layout = QVBoxLayout(self._guide_tab)
        layout.setContentsMargins(8, 8, 8, 8)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        self._guide_layout = QVBoxLayout(content)
        self._guide_layout.setContentsMargins(4, 4, 4, 4)
        self._guide_layout.setSpacing(10)

        self._guide_body = DoubleClickTextBrowser()
        self._guide_body.setOpenExternalLinks(False)
        self._guide_body.setPlaceholderText("暂无攻略正文")
        self._guide_body.setMinimumHeight(260)
        self._guide_body.setMaximumHeight(420)
        self._guide_body.double_clicked.connect(self._request_guide_detail)

        area.setWidget(content)
        layout.addWidget(area, 1)

        self._guide_layout.addWidget(QLabel("请选择一个武将"))
        self._guide_layout.addStretch()

    def _setup_synergy_tab(self) -> None:
        """构建当前武将的相性管理页面。"""
        layout = QVBoxLayout(self._synergy_tab)
        layout.setContentsMargins(8, 8, 8, 8)

        self._synergy_context_label = QLabel("请选择一个武将")
        self._synergy_context_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._synergy_context_label)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("关联武将:"))
        self._synergy_search_edit = QLineEdit()
        self._synergy_search_edit.setPlaceholderText("搜索名称或 ID")
        self._synergy_search_edit.textChanged.connect(self._refresh_synergy_table)
        filter_layout.addWidget(self._synergy_search_edit, 1)
        filter_layout.addWidget(QLabel("总评:"))
        self._synergy_rating_combo = QComboBox()
        self._synergy_rating_combo.addItems(["全部", "S", "A", "B", "C", "D"])
        self._synergy_rating_combo.currentTextChanged.connect(self._refresh_synergy_table)
        filter_layout.addWidget(self._synergy_rating_combo)
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self._reset_synergy_filters)
        filter_layout.addWidget(reset_btn)
        layout.addLayout(filter_layout)

        self._synergy_table = QTableWidget(0, 7)
        self._synergy_table.setHorizontalHeaderLabels([
            "搭配武将", "综合评分", "总评", "配合上限", "配合稳定性", "环境适应力", "相性说明",
        ])
        self._synergy_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._synergy_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._synergy_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._synergy_table.setAlternatingRowColors(True)
        self._synergy_table.itemSelectionChanged.connect(self._update_synergy_buttons)
        self._synergy_table.itemDoubleClicked.connect(self._on_synergy_table_double_clicked)
        header = self._synergy_table.horizontalHeader()
        header.setStretchLastSection(True)
        self._synergy_table.setColumnWidth(0, 150)
        self._synergy_table.setColumnWidth(1, 76)
        self._synergy_table.setColumnWidth(2, 52)
        self._synergy_table.setColumnWidth(3, 82)
        self._synergy_table.setColumnWidth(4, 92)
        self._synergy_table.setColumnWidth(5, 92)
        layout.addWidget(self._synergy_table, 1)

    def _reset_synergy_filters(self) -> None:
        """清空相性筛选条件。"""
        self._synergy_search_edit.clear()
        self._synergy_rating_combo.setCurrentText("全部")

    def _refresh_synergy_table(self) -> None:
        """按当前武将和筛选条件刷新相性表格。"""
        hero = self._current_hero
        self._synergy_table.setRowCount(0)
        if not hero:
            self._synergy_context_label.setText("请选择一个武将")
            self._update_synergy_buttons()
            return

        synergies = self._synergy_mgr.list_synergies_for_hero(hero.id)
        self._synergy_context_label.setText(
            f"当前武将：{hero.name}（#{hero.id}）｜共 {len(synergies)} 条相性记录"
        )
        search_text = self._synergy_search_edit.text().strip().lower()
        rating = self._synergy_rating_combo.currentText()
        rows: list[tuple[SynergyScore, Hero | None]] = []
        for synergy in synergies:
            partner_id = synergy.hero_b_id if synergy.hero_a_id == hero.id else synergy.hero_a_id
            partner = self._hero_mgr.get_hero(partner_id)
            partner_text = f"{partner.name} #{partner_id}" if partner else f"#{partner_id}"
            if search_text and search_text not in partner_text.lower():
                continue
            if rating != "全部" and synergy.synergy_rating != rating:
                continue
            rows.append((synergy, partner))

        rows.sort(key=lambda item: (-item[0].score, item[1].name if item[1] else str(
            item[0].hero_b_id if item[0].hero_a_id == hero.id else item[0].hero_a_id
        )))
        self._synergy_table.setRowCount(len(rows))
        for row, (synergy, partner) in enumerate(rows):
            partner_id = synergy.hero_b_id if synergy.hero_a_id == hero.id else synergy.hero_a_id
            partner_name = partner.name if partner else f"#{partner_id}"
            cells = [
                partner_name,
                str(synergy.score),
                synergy.synergy_rating,
                str(synergy.combo_ceiling),
                str(synergy.combo_stability),
                str(synergy.adaptability),
                synergy.description.replace("\n", " "),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        (synergy.hero_a_id, synergy.hero_b_id),
                    )
                if column == 6:
                    item.setToolTip(synergy.description)
                self._synergy_table.setItem(row, column, item)
        self._update_synergy_buttons()

    def _on_synergy_table_double_clicked(self, item: QTableWidgetItem) -> None:
        """双击说明时预览 Markdown，双击其它列时编辑相性。"""
        if item.column() == 6:
            self._show_synergy_description(item.row())
            return
        self._on_synergy_edit()

    def _show_synergy_description(self, row: int) -> None:
        """以攻略详情相同格式展示相性说明。"""
        item = self._synergy_table.item(row, 0)
        if not item:
            return
        ids = item.data(Qt.ItemDataRole.UserRole)
        if not ids:
            return
        synergy = self._synergy_mgr.get_synergy(*ids)
        if not synergy or not synergy.description.strip():
            return

        first_hero = self._hero_mgr.get_hero(synergy.hero_a_id)
        second_hero = self._hero_mgr.get_hero(synergy.hero_b_id)
        first_name = first_hero.name if first_hero else f"#{synergy.hero_a_id}"
        second_name = second_hero.name if second_hero else f"#{synergy.hero_b_id}"

        dialog = QDialog(self)
        dialog.setWindowTitle(f"{first_name} 与 {second_name} 的相性说明")
        dialog.setMinimumSize(500, 350)
        layout = QVBoxLayout(dialog)
        browser = QTextBrowser()
        browser.setHtml(self._markdown_to_html(synergy.description))
        browser.setOpenExternalLinks(False)
        layout.addWidget(browser, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)
        dialog.exec()

    def _selected_synergy(self) -> SynergyScore | None:
        """返回表格当前选中行关联的相性记录。"""
        row = self._synergy_table.currentRow()
        if row < 0:
            return None
        item = self._synergy_table.item(row, 0)
        if not item:
            return None
        ids = item.data(Qt.ItemDataRole.UserRole)
        if not ids:
            return None
        return self._synergy_mgr.get_synergy(*ids)

    def _update_synergy_buttons(self) -> None:
        """同步相性操作按钮可用状态。"""
        if not hasattr(self, "_synergy_edit_btn"):
            return
        has_selection = self._selected_synergy() is not None
        self._synergy_edit_btn.setEnabled(has_selection)
        self._synergy_delete_btn.setEnabled(has_selection)

    def show_hero(self, hero_id: int) -> None:
        """展示指定武将的详细信息和攻略。"""
        hero = self._hero_mgr.get_hero(hero_id)
        guide = self._guide_mgr.get_guide(hero_id)

        self._current_hero = hero
        self._current_guide = guide

        if not hero:
            self._basic_info.setText(f"武将 #{hero_id} 未找到")
            self._info_edit_btn.setEnabled(False)
            self._info_delete_btn.setEnabled(False)
            self._guide_edit_btn.setEnabled(False)
            self._guide_delete_btn.setEnabled(False)
            self._refresh_synergy_table()
            return

        self._info_edit_btn.setEnabled(True)
        self._info_delete_btn.setEnabled(True)
        self._guide_edit_btn.setEnabled(bool(guide))
        self._guide_delete_btn.setEnabled(bool(guide))

        self._update_info_tab(hero)
        self._update_guide_tab(guide)
        self._refresh_synergy_table()

    def _update_info_tab(self, hero: Hero) -> None:
        """更新武将信息页面"""
        hp_str = str(hero.max_hp)
        hand_str = str(hero.max_hand)

        gender_cn = "男" if hero.gender.value == "男" else "女"
        title_part = f"「{hero.title}」" if hero.title else ""

        # 难度星级（使用 HTML 实体确保跨字体兼容显示）
        star_filled = "&#9733;" * hero.difficulty.value
        star_empty = "&#9734;" * (5 - hero.difficulty.value)
        star_display = f"{star_filled}{star_empty}"

        html = f"""
        <h2 style="margin-bottom:4px;">{hero.name} {title_part}</h2>
        <p style="margin:2px 0 8px 0; color:#555;">
            <b>定位：</b>{hero.position}　　<b>难度：</b>{star_display}
        </p>
        <table style="width:320px;">
        <tr>
            <td style="width:50px;"><b>势力</b></td>
            <td style="width:110px;">{hero.faction}</td>
            <td style="width:50px;"><b>性别</b></td>
            <td style="width:110px;">{gender_cn}</td>
        </tr>
        <tr>
            <td><b>体力</b></td>
            <td>{hp_str}</td>
            <td><b>手牌</b></td>
            <td>{hand_str}</td>
        </tr>
        </table>
        """
        self._basic_info.setText(html)

        # 更新技能
        self._update_skills(hero)

    def _update_skills(self, hero: Hero) -> None:
        """更新技能展示"""
        self._clear_skills()

        if not hero.skills:
            self._skills_layout.addWidget(QLabel("无技能"))
            self._skills_layout.addStretch()
            return

        for skill in hero.skills:
            frame = QFrame()
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            skill_layout = QVBoxLayout(frame)
            skill_layout.setContentsMargins(8, 6, 8, 6)

            # 技能名
            name_label = QLabel(f"<b>{skill.name}</b>")
            name_label.setStyleSheet("font-size: 14px;")
            skill_layout.addWidget(name_label)

            # 技能描述
            desc_label = QLabel(skill.description)
            desc_label.setWordWrap(True)
            skill_layout.addWidget(desc_label)

            # 结算详情（可折叠）
            if skill.settlement:
                toggle = QPushButton("▸ 展开结算")
                toggle.setCheckable(True)
                toggle.setStyleSheet(
                    "QPushButton { background-color: #e8e8e8; color: #666; border: 1px solid #ccc; "
                    "border-radius: 3px; padding: 2px 10px; font-size: 12px; font-weight: normal; "
                    "text-align: center; min-height: 18px; }"
                    "QPushButton:hover { background-color: #d0d0d0; color: #444; }"
                    "QPushButton:checked { background-color: #d0d0d0; color: #444; }"
                )
                settle_label = QLabel(skill.settlement)
                settle_label.setWordWrap(True)
                settle_label.setStyleSheet("color: #666; padding-left: 8px; border-left: 2px solid #ddd;")
                settle_label.setVisible(False)
                toggle.toggled.connect(
                    lambda checked, label=settle_label, btn=toggle: (
                        label.setVisible(checked),
                        btn.setText("▾ 收起结算" if checked else "▸ 展开结算")
                    )
                )
                skill_layout.addWidget(toggle)
                skill_layout.addWidget(settle_label)

            self._skills_layout.addWidget(frame)

        self._skills_layout.addStretch()

    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """将 Markdown 转换为 HTML"""
        if not text:
            return ""
        return mistune.html(text)

    def _update_guide_tab(self, guide: Optional[HeroGuide]) -> None:
        """更新攻略指南"""
        # Markdown 预览控件会在多个武将之间复用，不能随着动态布局一起删除。
        self._guide_body.clear()
        while self._guide_layout.count():
            item = self._guide_layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget is not self._guide_body:
                item.widget().deleteLater()

        if not guide:
            self._guide_body.hide()
            no_data = QLabel("暂无攻略数据")
            no_data.setStyleSheet("color: #a08060; font-size: 14px; padding: 20px;")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._guide_layout.addWidget(no_data)
            self._guide_layout.addStretch()
            return

        hero = self._current_hero
        if hero:
            title = f"{hero.name} {f'「{hero.title}」' if hero.title else ''}"
            header = QLabel(
                f"<div style='font-size:18px; font-weight:bold; color:#2c3e50;'>{title}</div>"
                f"<div style='color:#6b7c93; margin-top:4px;'>"
                f"{hero.faction} · {hero.position or '定位未设置'} · 更新于 {guide.last_updated}</div>"
            )
            header.setWordWrap(True)
            self._guide_layout.addWidget(header)

        self._add_guide_section_title(self._guide_layout, "核心要点")
        # 操作要点
        if guide.key_points:
            for point in guide.key_points:
                pl = QLabel(f"• {point}")
                pl.setWordWrap(True)
                self._guide_layout.addWidget(pl)
        else:
            self._guide_layout.addWidget(QLabel("暂无核心要点"))

        # 新手提示
        if guide.tips_for_beginners:
            self._add_guide_section_title(self._guide_layout, "新手提示")
            tips = QLabel(guide.tips_for_beginners)
            tips.setWordWrap(True)
            tips.setStyleSheet("background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 8px;")
            self._guide_layout.addWidget(tips)

        # 克制 / 搭配
        if guide.counters:
            self._add_relation_tags(self._guide_layout, "被克制", guide.counters, "#fde8e8", "#c62828")

        if guide.synergizes_with:
            self._add_relation_tags(self._guide_layout, "搭配推荐", guide.synergizes_with, "#e8f4e8", "#2e7d32")

        # 攻略正文（Markdown 渲染）
        self._add_guide_section_title(self._guide_layout, "攻略正文（双击查看完整内容）")
        self._guide_body.show()
        self._guide_layout.addWidget(self._guide_body)
        if guide.description:
            self._guide_body.setHtml(self._markdown_to_html(guide.description))
        else:
            self._guide_body.setHtml("<p style='color:#8a98a8;'>暂无攻略正文</p>")

        self._guide_layout.addStretch()

    @staticmethod
    def _add_guide_section_title(layout: QVBoxLayout, title: str) -> None:
        """添加攻略摘要区块标题。"""
        label = QLabel(title)
        label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #357abd; "
            "padding-top: 6px; border-bottom: 1px solid #dce6f0;"
        )
        layout.addWidget(label)

    def _add_relation_tags(self, layout: QVBoxLayout, title: str, hero_ids: list[int],
                           background: str, foreground: str) -> None:
        """将克制/搭配关系渲染为可点击武将标签。"""
        self._add_guide_section_title(layout, title)
        tags = QWidget()
        grid = QGridLayout(tags)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, hero_id in enumerate(hero_ids[:10]):
            hero = self._hero_mgr.get_hero(hero_id)
            button = QPushButton(hero.name if hero else f"#{hero_id}")
            button.setFixedSize(88, 28)
            button.setToolTip(hero.name if hero else f"#{hero_id}")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ background-color: {background}; color: {foreground}; border: 1px solid {foreground}; "
                "border-radius: 10px; padding: 3px 8px; font-size: 12px; font-weight: normal; }"
                f"QPushButton:hover {{ background-color: {foreground}; color: white; }}"
            )
            button.clicked.connect(lambda checked=False, target=hero_id: self.hero_requested.emit(target))
            grid.addWidget(button, index // 2, index % 2)
        layout.addWidget(tags)

    def _request_guide_detail(self) -> None:
        """双击攻略正文预览时请求弹窗展示完整 Markdown。"""
        if self._current_hero and self._current_guide and self._current_guide.description:
            self.guide_detail_requested.emit(
                self._current_hero.name,
                self._current_guide.description,
            )

    # ---------------------------------------------------------------
    # 相性 CRUD
    # ---------------------------------------------------------------

    def _on_synergy_edit(self) -> None:
        """编辑表格中选中的相性。"""
        synergy = self._selected_synergy()
        if not self._current_hero or not synergy:
            return
        dialog = SynergyEditDialog(
            self._hero_mgr,
            synergy,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._synergy_mgr.update_synergy(dialog.get_synergy())
            self._synergy_mgr.save()
            self._refresh_synergy_table()
            self.synergies_changed.emit()
        except Exception as e:
            logger.exception("保存相性失败")
            QMessageBox.critical(self, "保存失败", f"无法保存相性:\n{e}")

    def _on_synergy_delete(self) -> None:
        """删除表格中选中的相性。"""
        synergy = self._selected_synergy()
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
            self._synergy_mgr.delete_synergy(synergy.hero_a_id, synergy.hero_b_id)
            self._synergy_mgr.save()
            self._refresh_synergy_table()
            self.synergies_changed.emit()
        except Exception as e:
            logger.exception("删除相性失败")
            QMessageBox.critical(self, "删除失败", f"无法删除相性:\n{e}")

    def _on_info_edit(self) -> None:
        """打开编辑对话框修改武将信息。"""
        if not self._current_hero:
            return
        dialog = HeroEditDialog(self._current_hero, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.get_hero()
        try:
            self._hero_mgr.update_hero(updated)
            self._hero_mgr.save()
            self._update_info_tab(updated)
            self.data_changed.emit()
        except Exception as e:
            logger.exception("保存武将信息失败")
            QMessageBox.critical(self, "保存失败", f"无法保存武将信息:\n{e}")

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
        try:
            self._hero_mgr.delete_hero(self._current_hero.id)
            self._guide_mgr.delete_guide(self._current_hero.id)
            self._synergy_mgr.delete_synergies_for_hero(self._current_hero.id)
            self._hero_mgr.save()
            self._guide_mgr.save()
            self._synergy_mgr.save()
            self._current_hero = None
            self._current_guide = None
            self._basic_info.setText("武将已删除，请选择其他武将")
            self._clear_skills()
            self._update_guide_tab(None)
            self._refresh_synergy_table()
            self.data_changed.emit()
            self.synergies_changed.emit()
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
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        updated = dialog.get_guide()
        try:
            self._guide_mgr.update_guide(updated)
            self._guide_mgr.save()
            self._update_guide_tab(updated)
            self.data_changed.emit()
        except Exception as e:
            logger.exception("保存攻略失败")
            QMessageBox.critical(self, "保存失败", f"无法保存攻略:\n{e}")

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
            self._guide_mgr.delete_guide(self._current_guide.hero_id)
            self._guide_mgr.save()
            self._current_guide = None
            self._update_guide_tab(None)
            self._guide_edit_btn.setEnabled(False)
            self._guide_delete_btn.setEnabled(False)
            self.data_changed.emit()
        except Exception as e:
            logger.exception("删除攻略失败")
            QMessageBox.critical(self, "删除失败", f"无法删除攻略:\n{e}")

    def _clear_skills(self) -> None:
        """清空技能展示区域"""
        while self._skills_layout.count():
            item = self._skills_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                # 模态提示框会进入嵌套事件循环，延迟删除前先隐藏旧卡片，避免其残留绘制。
                widget.hide()
                widget.deleteLater()


class GuideMarkdownDialog(QDialog):
    """攻略正文 Markdown 详情弹窗。"""

    def __init__(self, hero_name: str, markdown_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{hero_name} - 攻略正文")
        self.setMinimumSize(760, 560)
        self.resize(900, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel(f"{hero_name} · 完整攻略")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50; padding: 4px;")
        layout.addWidget(title)

        body = QTextBrowser()
        body.setOpenExternalLinks(False)
        body.setHtml(mistune.html(markdown_text))
        layout.addWidget(body, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("关闭")
        close_button.setFixedWidth(90)
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)


class HeroBrowser(QWidget):
    """武将浏览器主组件，列表选择后在右侧展示摘要和攻略预览。"""

    synergies_changed = Signal()

    def __init__(
        self,
        hero_manager: HeroManager,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
        parent=None,
    ):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager
        self._synergy_mgr = synergy_manager

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._list_panel = HeroListPanel(self._hero_mgr)
        splitter.addWidget(self._list_panel)

        # 右侧：详情面板
        self._detail_panel = HeroDetailPanel(
            self._hero_mgr,
            self._guide_mgr,
            self._synergy_mgr,
        )
        splitter.addWidget(self._detail_panel)
        splitter.setSizes([280, 720])
        layout.addWidget(splitter, 1)

        # 连接信号
        self._list_panel.hero_selected.connect(self._detail_panel.show_hero)
        self._detail_panel.hero_requested.connect(self._list_panel.select_hero)
        self._detail_panel.guide_detail_requested.connect(self._show_guide_markdown_dialog)
        self._detail_panel.data_changed.connect(self.reload_data)
        self._detail_panel.synergies_changed.connect(self.synergies_changed)

        # 列表面板在构造时已经默认选中首项，此时信号尚未连接，需要主动同步详情。
        initial_hero_id = self._list_panel.selected_hero_id()
        if initial_hero_id is not None:
            self._detail_panel.show_hero(initial_hero_id)

    def _show_guide_markdown_dialog(self, hero_name: str, markdown_text: str) -> None:
        """双击攻略正文预览后打开完整 Markdown 弹窗。"""
        dialog = GuideMarkdownDialog(hero_name, markdown_text, self)
        dialog.exec()

    def reload_data(self) -> None:
        """重新加载列表并刷新当前详情。"""
        current_hero = self._detail_panel._current_hero
        self._list_panel.reload()
        if current_hero:
            self._detail_panel.show_hero(current_hero.id)

    def refresh_synergies(self) -> None:
        """刷新当前武将的相性表格。"""
        self._detail_panel._refresh_synergy_table()
