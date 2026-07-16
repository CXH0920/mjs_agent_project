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
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.guide_manager import GuideManager
from src.data.models import Hero, HeroGuide, Gender, Difficulty

logger = logging.getLogger(__name__)


# ============================================================
# 武将信息编辑弹窗
# ============================================================


class HeroEditDialog(QDialog):
    """武将信息编辑对话框"""

    def __init__(self, hero: Hero, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑武将信息")
        self.setMinimumWidth(400)
        self._hero = hero
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name_edit = QLineEdit(self._hero.name)
        form.addRow("名称:", self._name_edit)

        self._title_edit = QLineEdit(self._hero.title)
        form.addRow("称号:", self._title_edit)

        self._faction_edit = QLineEdit(self._hero.faction)
        form.addRow("势力:", self._faction_edit)

        self._position_edit = QLineEdit(self._hero.position)
        form.addRow("定位:", self._position_edit)

        self._hp_spin = QSpinBox()
        self._hp_spin.setRange(1, 20)
        self._hp_spin.setValue(self._hero.max_hp)
        form.addRow("体力上限:", self._hp_spin)

        self._hand_spin = QSpinBox()
        self._hand_spin.setRange(1, 20)
        self._hand_spin.setValue(self._hero.max_hand)
        form.addRow("手牌上限:", self._hand_spin)

        self._gender_combo = QComboBox()
        self._gender_combo.addItems(["男", "女"])
        self._gender_combo.setCurrentText(self._hero.gender.value)
        form.addRow("性别:", self._gender_combo)

        self._diff_spin = QSpinBox()
        self._diff_spin.setRange(1, 5)
        self._diff_spin.setValue(self._hero.difficulty.value)
        form.addRow("难度(1-5):", self._diff_spin)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("padding: 6px 24px;")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("padding: 6px 24px;")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def get_hero(self) -> Hero:
        """返回编辑后的 Hero 对象"""
        self._hero.name = self._name_edit.text().strip()
        self._hero.title = self._title_edit.text().strip()
        self._hero.faction = self._faction_edit.text().strip()
        self._hero.position = self._position_edit.text().strip()
        self._hero.max_hp = self._hp_spin.value()
        self._hero.max_hand = self._hand_spin.value()
        self._hero.gender = Gender.MALE if self._gender_combo.currentText() == "男" else Gender.FEMALE
        self._hero.difficulty = Difficulty(self._diff_spin.value())
        return self._hero


# ============================================================
# 攻略编辑弹窗
# ============================================================


class GuideEditDialog(QDialog):
    """攻略编辑对话框"""

    def __init__(self, guide: HeroGuide, hero_mgr: HeroManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑攻略")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self._guide = guide
        self._hero_mgr = hero_mgr
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # 核心要点（每行一个）
        self._key_points_edit = QTextEdit()
        self._key_points_edit.setPlaceholderText("每行一个核心要点")
        self._key_points_edit.setMaximumHeight(100)
        self._key_points_edit.setText("\n".join(self._guide.key_points))
        form.addRow("核心要点:", self._key_points_edit)

        # 新手提示
        self._tips_edit = QTextEdit()
        self._tips_edit.setPlaceholderText("新手提示文字")
        self._tips_edit.setMaximumHeight(80)
        self._tips_edit.setText(self._guide.tips_for_beginners)
        form.addRow("新手提示:", self._tips_edit)

        # 被克制（武将名，顿号分隔）
        counter_names = []
        for hid in self._guide.counters:
            h = self._hero_mgr.get_hero(hid)
            counter_names.append(h.name if h else f"#{hid}")
        self._counters_edit = QLineEdit("、".join(counter_names))
        self._counters_edit.setPlaceholderText("武将名，顿号分隔")
        form.addRow("被克制:", self._counters_edit)

        # 搭配推荐（武将名，顿号分隔）
        synergy_names = []
        for hid in self._guide.synergizes_with:
            h = self._hero_mgr.get_hero(hid)
            synergy_names.append(h.name if h else f"#{hid}")
        self._synergy_edit = QLineEdit("、".join(synergy_names))
        self._synergy_edit.setPlaceholderText("武将名，顿号分隔")
        form.addRow("搭配推荐:", self._synergy_edit)

        # 攻略正文（Markdown）
        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText("攻略正文（支持 Markdown）")
        self._desc_edit.setText(self._guide.description)
        form.addRow("攻略正文:", self._desc_edit)

        layout.addLayout(form)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        save_btn = QPushButton("保存")
        save_btn.setStyleSheet("padding: 6px 24px;")
        save_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet("padding: 6px 24px;")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(save_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

    def _resolve_hero_ids(self, text: str) -> list[int]:
        """将顿号/逗号分隔的武将名或 ID 解析为 ID 列表"""
        ids: list[int] = []
        if not text.strip():
            return ids
        for part in text.replace("，", "、").split("、"):
            part = part.strip()
            if not part:
                continue
            # 优先按名称查找
            hero = self._hero_mgr.get_hero_by_name(part)
            if hero:
                ids.append(hero.id)
            else:
                try:
                    ids.append(int(part))
                except ValueError:
                    logger.warning("无法解析武将: %s", part)
        return ids

    def get_guide(self) -> HeroGuide:
        """返回编辑后的 HeroGuide 对象"""
        self._guide.key_points = [
            line.strip()
            for line in self._key_points_edit.toPlainText().split("\n")
            if line.strip()
        ]
        self._guide.tips_for_beginners = self._tips_edit.toPlainText().strip()
        self._guide.counters = self._resolve_hero_ids(self._counters_edit.text())
        self._guide.synergizes_with = self._resolve_hero_ids(self._synergy_edit.text())
        self._guide.description = self._desc_edit.toPlainText()
        return self._guide


# ============================================================
# 列表面板
# ============================================================


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

    def _on_selection_changed(self, row: int) -> None:
        """列表选中项变化"""
        if 0 <= row < len(self._filtered_heroes):
            hero_id = self._filtered_heroes[row].id
            self._last_hero_id = hero_id
            self.hero_selected.emit(hero_id)


class HeroDetailPanel(QWidget):
    """武将详情面板"""

    data_changed = Signal()  # 数据变更后通知刷新列表

    def __init__(self, hero_manager: HeroManager, guide_manager: GuideManager, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager
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

        # Tab 栏右角：修改/删除按钮组
        self._setup_corner_buttons()

        layout.addWidget(self._detail_tabs, 1)

    def _setup_corner_buttons(self) -> None:
        """在 Tab 栏右侧放置修改/删除按钮，与页签同水平高度"""
        corner = QWidget()
        hlayout = QHBoxLayout(corner)
        hlayout.setContentsMargins(0, 0, 4, 0)
        hlayout.setSpacing(4)

        btn_style = (
            "QPushButton { padding: 2px 12px; font-size: 12px; border-radius: 3px; }"
        )

        # 武将信息按钮组
        self._info_edit_btn = QPushButton("修改")
        self._info_edit_btn.setStyleSheet(btn_style + "background: #e8f4e8; color: #2e7d32;")
        self._info_edit_btn.clicked.connect(self._on_info_edit)
        hlayout.addWidget(self._info_edit_btn)

        self._info_delete_btn = QPushButton("删除")
        self._info_delete_btn.setStyleSheet(btn_style + "background: #fde8e8; color: #c62828;")
        self._info_delete_btn.clicked.connect(self._on_info_delete)
        hlayout.addWidget(self._info_delete_btn)

        # 攻略按钮组
        self._guide_edit_btn = QPushButton("修改")
        self._guide_edit_btn.setStyleSheet(btn_style + "background: #e8f4e8; color: #2e7d32;")
        self._guide_edit_btn.clicked.connect(self._on_guide_edit)
        hlayout.addWidget(self._guide_edit_btn)

        self._guide_delete_btn = QPushButton("删除")
        self._guide_delete_btn.setStyleSheet(btn_style + "background: #fde8e8; color: #c62828;")
        self._guide_delete_btn.clicked.connect(self._on_guide_delete)
        hlayout.addWidget(self._guide_delete_btn)

        # 初始隐藏攻略按钮组
        self._guide_edit_btn.hide()
        self._guide_delete_btn.hide()

        self._detail_tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
        self._detail_tabs.currentChanged.connect(self._on_tab_changed)

    def _on_tab_changed(self, index: int) -> None:
        """Tab 切换时切换对应的修改/删除按钮组"""
        if index == 0:  # 武将信息
            self._info_edit_btn.show()
            self._info_delete_btn.show()
            self._guide_edit_btn.hide()
            self._guide_delete_btn.hide()
        else:  # 攻略指南
            self._info_edit_btn.hide()
            self._info_delete_btn.hide()
            self._guide_edit_btn.show()
            self._guide_delete_btn.show()

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
        self._guide_layout = QVBoxLayout()
        layout.addLayout(self._guide_layout)
        self._guide_layout.addWidget(QLabel("请选择一个武将"))
        self._guide_layout.addStretch()

    # ---------------------------------------------------------------
    # 武将展示
    # ---------------------------------------------------------------

    def show_hero(self, hero_id: int) -> None:
        """展示指定武将的详细信息和攻略"""
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
            return

        self._info_edit_btn.setEnabled(True)
        self._info_delete_btn.setEnabled(True)
        self._guide_edit_btn.setEnabled(bool(guide))
        self._guide_delete_btn.setEnabled(bool(guide))

        self._update_info_tab(hero)
        self._update_guide_tab(guide)

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
        # 清空
        while self._skills_layout.count():
            item = self._skills_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

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
        while self._guide_layout.count():
            item = self._guide_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not guide:
            no_data = QLabel("暂无攻略数据")
            no_data.setStyleSheet("color: #a08060; font-size: 14px; padding: 20px;")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._guide_layout.addWidget(no_data)
            self._guide_layout.addStretch()
            return

        # 操作要点
        if guide.key_points:
            points_label = QLabel("<b>核心要点:</b>")
            self._guide_layout.addWidget(points_label)
            for point in guide.key_points:
                pl = QLabel(f"  {point}")
                pl.setWordWrap(True)
                self._guide_layout.addWidget(pl)

        # 新手提示
        if guide.tips_for_beginners:
            self._guide_layout.addWidget(QLabel(""))
            tips = QLabel(f"<b>新手提示:</b>\n{guide.tips_for_beginners}")
            tips.setWordWrap(True)
            self._guide_layout.addWidget(tips)

        # 克制 / 搭配
        if guide.counters:
            names = []
            for hid in guide.counters[:10]:
                h = self._hero_mgr.get_hero(hid)
                names.append(h.name if h else f"#{hid}")
            cl = QLabel(f"<b>被克制:</b>  {'、'.join(names)}")
            cl.setWordWrap(True)
            self._guide_layout.addWidget(cl)

        if guide.synergizes_with:
            names = []
            for hid in guide.synergizes_with[:10]:
                h = self._hero_mgr.get_hero(hid)
                names.append(h.name if h else f"#{hid}")
            sl = QLabel(f"<b>搭配推荐:</b>  {'、'.join(names)}")
            sl.setWordWrap(True)
            self._guide_layout.addWidget(sl)

        # 攻略正文（Markdown 渲染）
        if guide.description:
            self._guide_layout.addWidget(QLabel(""))
            desc_title = QLabel("<b>攻略详情:</b>")
            self._guide_layout.addWidget(desc_title)
            desc_browser = QTextBrowser()
            desc_browser.setHtml(self._markdown_to_html(guide.description))
            desc_browser.setOpenExternalLinks(False)
            self._guide_layout.addWidget(desc_browser)

    # ---------------------------------------------------------------
    # 武将信息 CRUD
    # ---------------------------------------------------------------

    def _on_info_edit(self) -> None:
        """打开编辑对话框修改武将信息"""
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
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除武将「{self._current_hero.name}」吗？\n"
            "该操作不可撤销，关联的攻略也将被删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._hero_mgr.delete_hero(self._current_hero.id)
            self._guide_mgr.delete_guide(self._current_hero.id)
            self._hero_mgr.save()
            self._guide_mgr.save()
            self._current_hero = None
            self._current_guide = None
            self._basic_info.setText("武将已删除，请选择其他武将")
            self._clear_skills()
            self._update_guide_tab(None)
            self.data_changed.emit()
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
                item.widget().deleteLater()


class HeroBrowser(QWidget):
    """武将浏览器主组件

    左侧列表 + 右侧详情面板，支持搜索和势力筛选。
    """

    def __init__(self, hero_manager: HeroManager, guide_manager: GuideManager, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 分割面板
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：武将列表
        self._list_panel = HeroListPanel(self._hero_mgr)
        splitter.addWidget(self._list_panel)

        # 右侧：详情面板
        self._detail_panel = HeroDetailPanel(self._hero_mgr, self._guide_mgr)
        splitter.addWidget(self._detail_panel)

        splitter.setSizes([280, 520])
        layout.addWidget(splitter, 1)

        # 连接信号
        self._list_panel.hero_selected.connect(self._detail_panel.show_hero)
        self._detail_panel.data_changed.connect(self.reload_data)

    def reload_data(self) -> None:
        """公有方法：重新加载武将列表数据"""
        self._list_panel.reload()