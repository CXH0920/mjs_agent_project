"""武将详情页的三个独立展示视图。"""

from __future__ import annotations

import mistune

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, SynergyScore
from src.data.synergy_manager import SynergyManager
from src.ui.shared.widgets import FlowLayout


class HeroInfoView(QWidget):
    """展示武将基础属性和技能。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._basic_info = QLabel("请选择一个武将")
        self._basic_info.setObjectName("heroBasicInfo")
        self._basic_info.setWordWrap(True)
        self._basic_info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._basic_info)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        skills_widget = QWidget()
        self._skills_layout = QVBoxLayout(skills_widget)
        self._skills_layout.setContentsMargins(0, 4, 0, 4)
        scroll.setWidget(skills_widget)
        layout.addWidget(scroll, 1)

    def show_hero(self, hero: Hero) -> None:
        """更新基础信息和技能。"""
        gender_cn = "男" if hero.gender.value == "男" else "女"
        star_filled = "&#9733;" * hero.difficulty.value
        star_empty = "&#9734;" * (5 - hero.difficulty.value)
        star_display = f"{star_filled}{star_empty}"
        self._basic_info.setText(f"""
        <div style="font-size:14px; font-weight:bold; color:#2c3e50; margin-bottom:4px;">
            基础属性 <span style="font-size:12px; font-weight:normal; color:#78909c;">· 资料更新：{hero.last_updated or '未记录'}</span>
        </div>
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
            <td>{hero.max_hp}</td>
            <td><b>手牌</b></td>
            <td>{hero.max_hand}</td>
        </tr>
        </table>
        """)
        self._update_skills(hero)

    def show_missing(self, hero_id: int) -> None:
        self._basic_info.setText(f"武将 #{hero_id} 未找到")

    def show_deleted(self) -> None:
        self._basic_info.setText("武将已删除，请选择其他武将")
        self._clear_skills()

    def _update_skills(self, hero: Hero) -> None:
        self._clear_skills()
        if not hero.skills:
            self._skills_layout.addWidget(QLabel("无技能"))
            self._skills_layout.addStretch()
            return

        for skill in hero.skills:
            frame = QFrame()
            frame.setObjectName("heroSkillCard")
            frame.setFrameShape(QFrame.Shape.StyledPanel)
            skill_layout = QVBoxLayout(frame)
            skill_layout.setContentsMargins(8, 6, 8, 6)

            name_label = QLabel(f"<b>{skill.name}</b>")
            name_label.setStyleSheet("font-size: 14px;")
            skill_layout.addWidget(name_label)

            desc_label = QLabel(skill.description)
            desc_label.setWordWrap(True)
            skill_layout.addWidget(desc_label)

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
                settle_label.setStyleSheet(
                    "color: #666; padding-left: 8px; border-left: 2px solid #ddd;"
                )
                settle_label.setVisible(False)
                toggle.toggled.connect(
                    lambda checked, label=settle_label, button=toggle: (
                        label.setVisible(checked),
                        button.setText("▾ 收起结算" if checked else "▸ 展开结算"),
                    )
                )
                skill_layout.addWidget(toggle)
                skill_layout.addWidget(settle_label)

            self._skills_layout.addWidget(frame)
        self._skills_layout.addStretch()

    def _clear_skills(self) -> None:
        while self._skills_layout.count():
            item = self._skills_layout.takeAt(0)
            widget = item.widget()
            if widget:
                # 模态提示框会进入嵌套事件循环，先隐藏可避免延迟删除期间残留绘制。
                widget.hide()
                widget.deleteLater()


class HeroGuideSummaryView(QWidget):
    """展示攻略摘要并发出详情或关联武将请求。"""

    hero_requested = Signal(int)
    detail_requested = Signal()

    def __init__(self, hero_manager: HeroManager, parent=None) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self._guide_layout = QVBoxLayout(content)
        self._guide_layout.setContentsMargins(4, 4, 4, 4)
        self._guide_layout.setSpacing(10)
        area.setWidget(content)
        layout.addWidget(area, 1)
        self.show_guide(None)

    def show_guide(self, guide: HeroGuide | None) -> None:
        """更新攻略摘要；完整正文由外层打开详情窗口。"""
        while self._guide_layout.count():
            item = self._guide_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if not guide:
            no_data = QLabel("暂无攻略数据")
            no_data.setStyleSheet("color: #a08060; font-size: 14px; padding: 20px;")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._guide_layout.addWidget(no_data)
            self._guide_layout.addStretch()
            return

        self._add_quick_summary(guide)
        if guide.tips_for_beginners:
            self._add_section_title("新手提醒")
            tips = QLabel(guide.tips_for_beginners)
            tips.setWordWrap(True)
            tips.setStyleSheet(
                "background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 8px;"
            )
            self._guide_layout.addWidget(tips)

        if guide.weak_against_type or guide.strong_against_type or guide.synergizes_with:
            self._add_section_title("对局关系")
            self._add_type_tags("需谨慎的对手类型", guide.weak_against_type, "#fde8e8", "#a12622")
            self._add_type_tags("有利对手类型", guide.strong_against_type, "#e8f4e8", "#176b36")
        if guide.synergizes_with:
            self._add_relation_tags("搭配推荐", guide.synergizes_with, "#e8f4e8", "#2e7d32")

        self._add_section_title("完整攻略")
        description_hint = QLabel(
            "查看开局、中局、残局等完整说明。" if guide.description else "暂无完整攻略正文。"
        )
        description_hint.setStyleSheet("color: #65758b;")
        self._guide_layout.addWidget(description_hint)
        detail_button = QPushButton("阅读完整攻略")
        detail_button.setEnabled(bool(guide.description))
        detail_button.setStyleSheet("QPushButton { padding: 4px 12px; font-size: 12px; }")
        detail_button.clicked.connect(self.detail_requested.emit)
        self._guide_layout.addWidget(detail_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._guide_layout.addStretch()

    def _add_quick_summary(self, guide: HeroGuide) -> None:
        summary = QFrame()
        summary.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #dce6f0; border-radius: 6px; }"
        )
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(6)

        title = QLabel("核心建议")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #2c3e50;")
        summary_layout.addWidget(title)
        if guide.key_points:
            for point in guide.key_points[:3]:
                label = QLabel(f"• {point}")
                label.setWordWrap(True)
                summary_layout.addWidget(label)
        else:
            empty = QLabel("暂无核心要点")
            empty.setStyleSheet("color: #65758b;")
            summary_layout.addWidget(empty)

        if guide.counter_strategy:
            strategy_title = QLabel("面对该武将的应对")
            strategy_title.setStyleSheet("font-weight: bold; color: #8a5a00; padding-top: 2px;")
            summary_layout.addWidget(strategy_title)
            strategy = QLabel(guide.counter_strategy)
            strategy.setWordWrap(True)
            strategy.setStyleSheet(
                "background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 7px;"
            )
            summary_layout.addWidget(strategy)
        self._guide_layout.addWidget(summary)

    def _add_section_title(self, title: str) -> None:
        label = QLabel(title)
        label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #357abd; padding-top: 6px;"
        )
        self._guide_layout.addWidget(label)

    def _add_type_tags(
        self,
        title: str,
        types: list[str],
        background: str,
        foreground: str,
    ) -> None:
        if not types:
            return
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #65758b; font-size: 12px;")
        self._guide_layout.addWidget(title_label)
        tags = QWidget()
        flow = FlowLayout(tags, spacing=5)
        for hero_type in types:
            label = QLabel(hero_type)
            label.setStyleSheet(
                f"background-color: {background}; color: {foreground}; border: 1px solid {foreground}; "
                "border-radius: 10px; padding: 3px 8px; font-size: 12px;"
            )
            flow.addWidget(label)
        self._guide_layout.addWidget(tags)

    def _add_relation_tags(
        self,
        title: str,
        hero_ids: list[int],
        background: str,
        foreground: str,
    ) -> None:
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #65758b; font-size: 12px;")
        self._guide_layout.addWidget(title_label)
        tags = QWidget()
        flow = FlowLayout(tags, spacing=5)
        for hero_id in hero_ids:
            hero = self._hero_mgr.get_hero(hero_id)
            button = QPushButton(hero.name if hero else f"#{hero_id}")
            button.setFixedHeight(28)
            button.setToolTip(hero.name if hero else f"#{hero_id}")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ background-color: {background}; color: {foreground}; "
                f"border: 1px solid {foreground}; border-radius: 10px; padding: 3px 8px; "
                "font-size: 12px; font-weight: normal; }"
                f"QPushButton:hover {{ background-color: {foreground}; color: white; }}"
            )
            button.clicked.connect(
                lambda checked=False, target=hero_id: self.hero_requested.emit(target)
            )
            flow.addWidget(button)
        self._guide_layout.addWidget(tags)


class HeroSynergyView(QWidget):
    """展示、筛选并选择当前武将的相性记录。"""

    selection_changed = Signal()
    edit_requested = Signal()

    def __init__(
        self,
        hero_manager: HeroManager,
        synergy_manager: SynergyManager,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._synergy_mgr = synergy_manager
        self._current_hero: Hero | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._context_label = QLabel("请选择一个武将")
        self._context_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self._context_label)

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("关联武将:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索名称或 ID")
        self._search_edit.textChanged.connect(self.refresh)
        filter_layout.addWidget(self._search_edit, 1)
        filter_layout.addWidget(QLabel("总评:"))
        self._rating_combo = QComboBox()
        self._rating_combo.addItems(["全部", "S", "A", "B", "C", "D"])
        self._rating_combo.currentTextChanged.connect(self.refresh)
        filter_layout.addWidget(self._rating_combo)
        reset_btn = QPushButton("重置")
        reset_btn.clicked.connect(self._reset_filters)
        filter_layout.addWidget(reset_btn)
        layout.addLayout(filter_layout)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            "搭配武将", "综合评分", "总评", "配合上限", "配合稳定性", "环境适应力", "相性说明",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.itemSelectionChanged.connect(self.selection_changed.emit)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        header = self._table.horizontalHeader()
        header.setStretchLastSection(True)
        for column, width in enumerate((150, 76, 52, 82, 92, 92)):
            self._table.setColumnWidth(column, width)
        layout.addWidget(self._table, 1)

    def show_hero(self, hero: Hero | None) -> None:
        self._current_hero = hero
        self.refresh()

    def refresh(self) -> None:
        hero = self._current_hero
        self._table.setRowCount(0)
        if not hero:
            self._context_label.setText("请选择一个武将")
            self.selection_changed.emit()
            return

        synergies = self._synergy_mgr.list_synergies_for_hero(hero.id)
        self._context_label.setText(
            f"当前武将：{hero.name}（#{hero.id}）｜共 {len(synergies)} 条相性记录"
        )
        search_text = self._search_edit.text().strip().lower()
        rating = self._rating_combo.currentText()
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
        self._table.setRowCount(len(rows))
        for row, (synergy, partner) in enumerate(rows):
            partner_id = synergy.hero_b_id if synergy.hero_a_id == hero.id else synergy.hero_a_id
            cells = [
                partner.name if partner else f"#{partner_id}",
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
                        Qt.ItemDataRole.UserRole, (synergy.hero_a_id, synergy.hero_b_id),
                    )
                if column == 6:
                    item.setToolTip(synergy.description)
                self._table.setItem(row, column, item)
        self.selection_changed.emit()

    def selected_synergy(self) -> SynergyScore | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        ids = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self._synergy_mgr.get_synergy(*ids) if ids else None

    def _reset_filters(self) -> None:
        self._search_edit.clear()
        self._rating_combo.setCurrentText("全部")

    def _on_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 6:
            self._show_description(item.row())
        else:
            self.edit_requested.emit()

    def _show_description(self, row: int) -> None:
        item = self._table.item(row, 0)
        ids = item.data(Qt.ItemDataRole.UserRole) if item else None
        synergy = self._synergy_mgr.get_synergy(*ids) if ids else None
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
        browser.setHtml(mistune.html(synergy.description))
        browser.setOpenExternalLinks(False)
        layout.addWidget(browser, 1)
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dialog.accept)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)
        dialog.exec()
