"""武将详情页的三个独立展示视图。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.data.combo_manager import ComboManager
from src.data.combo_seats import format_seats
from src.data.hero_manager import HeroManager
from src.ui.shared.markdown_renderer import render_markdown
from src.data.models import Combo, Hero, HeroGuide, SynergyScore
from src.data.synergy_manager import SynergyManager
from src.ui.shared.widgets import DialogFooter, FlowLayout, PageHeader
from src.ui.shared.style import ROLE_GHOST, ROLE_SECONDARY, set_ui_role


class HeroInfoView(QWidget):
    """展示武将基础属性和技能。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("heroInfoView")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._basic_info = QLabel("请选择一个武将")
        self._basic_info.setObjectName("heroBasicInfo")
        self._basic_info.setWordWrap(True)
        self._basic_info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._basic_info)

        line = QFrame()
        line.setObjectName("contentDivider")
        line.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(line)

        scroll = QScrollArea()
        scroll.setObjectName("heroSkillScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        skills_widget = QWidget()
        skills_widget.setObjectName("heroSkillsContent")
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
        self._basic_info.setText(
            f"<div><b>基础属性</b> · 资料更新：{hero.last_updated or '未记录'}</div>"
            f"<p><b>定位：</b>{hero.position or '未设置'}　<b>难度：</b>{star_display}</p>"
            f"<p><b>势力：</b>{hero.faction or '未设置'}　<b>性别：</b>{gender_cn}　"
            f"<b>体力：</b>{hero.max_hp}　<b>手牌：</b>{hero.max_hand}</p>"
        )
        self._update_skills(hero)

    def show_missing(self, hero_id: int) -> None:
        self._basic_info.setText(f"武将 #{hero_id} 未找到")

    def show_deleted(self) -> None:
        self._basic_info.setText("武将已删除，请选择其他武将")
        self._clear_skills()

    def _update_skills(self, hero: Hero) -> None:
        self._clear_skills()
        if not hero.skills:
            empty = QLabel("暂无技能资料")
            empty.setObjectName("libraryEmptyState")
            self._skills_layout.addWidget(empty)
            self._skills_layout.addStretch()
            return

        for skill in hero.skills:
            frame = QFrame()
            frame.setObjectName("heroSkillCard")
            skill_layout = QVBoxLayout(frame)
            skill_layout.setContentsMargins(0, 8, 0, 10)
            skill_layout.setSpacing(6)

            name_label = QLabel(skill.name)
            name_label.setObjectName("contentItemTitle")
            name_label.setWordWrap(True)
            skill_layout.addWidget(name_label)

            desc_label = QLabel(skill.description)
            desc_label.setObjectName("contentBody")
            desc_label.setWordWrap(True)
            skill_layout.addWidget(desc_label)

            if skill.settlement:
                toggle = QPushButton("▸ 展开结算")
                toggle.setObjectName("heroSettlementToggle")
                toggle.setCheckable(True)
                set_ui_role(toggle, ROLE_GHOST)
                settle_label = QLabel(skill.settlement)
                settle_label.setObjectName("heroSettlementBody")
                settle_label.setWordWrap(True)
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
        self.setObjectName("heroGuideView")
        self._hero_mgr = hero_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        area = QScrollArea()
        area.setObjectName("heroGuideScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("heroGuideContent")
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
            no_data.setObjectName("libraryEmptyState")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._guide_layout.addWidget(no_data)
            self._guide_layout.addStretch()
            return

        self._add_quick_summary(guide)
        if guide.tips_for_beginners:
            self._add_section_title("新手提醒")
            tips = QLabel(guide.tips_for_beginners)
            tips.setObjectName("guideNotice")
            tips.setWordWrap(True)
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
        description_hint.setObjectName("contentBody")
        description_hint.setWordWrap(True)
        self._guide_layout.addWidget(description_hint)
        detail_button = QPushButton("阅读完整攻略")
        detail_button.setEnabled(bool(guide.description))
        set_ui_role(detail_button, ROLE_SECONDARY)
        detail_button.clicked.connect(self.detail_requested.emit)
        self._guide_layout.addWidget(detail_button, 0, Qt.AlignmentFlag.AlignLeft)
        self._guide_layout.addStretch()

    def _add_quick_summary(self, guide: HeroGuide) -> None:
        summary = QFrame()
        summary.setObjectName("guideSummarySurface")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(6)

        title = QLabel("核心建议")
        title.setObjectName("contentItemTitle")
        summary_layout.addWidget(title)
        if guide.key_points:
            for point in guide.key_points[:3]:
                label = QLabel(f"• {point}")
                label.setObjectName("contentBody")
                label.setWordWrap(True)
                summary_layout.addWidget(label)
        else:
            empty = QLabel("暂无核心要点")
            empty.setObjectName("contentBody")
            summary_layout.addWidget(empty)

        if guide.counter_strategy:
            strategy_title = QLabel("面对该武将的应对")
            strategy_title.setObjectName("guideWarningTitle")
            summary_layout.addWidget(strategy_title)
            strategy = QLabel(guide.counter_strategy)
            strategy.setObjectName("guideNotice")
            strategy.setWordWrap(True)
            summary_layout.addWidget(strategy)
        self._guide_layout.addWidget(summary)

    def _add_section_title(self, title: str) -> None:
        label = QLabel(title)
        label.setObjectName("contentSectionTitle")
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
        title_label.setObjectName("contentMeta")
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
        title_label.setObjectName("contentMeta")
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
    """展示、筛选并选择当前武将的相性记录。

    数据源为 AI 相性评分与实战配队（combos）的并集：
    有实战配队但未生成 AI 评分的配对也显示（综合评分列标"未生成"）。
    """

    selection_changed = Signal()
    edit_requested = Signal()

    def __init__(
        self,
        hero_manager: HeroManager,
        synergy_manager: SynergyManager,
        combo_manager: ComboManager | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("heroSynergyView")
        self._hero_mgr = hero_manager
        self._synergy_mgr = synergy_manager
        self._combo_mgr = combo_manager
        if self._combo_mgr is not None:
            self._combo_mgr.load()
        self._current_hero: Hero | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._context_label = QLabel("请选择一个武将")
        self._context_label.setObjectName("synergyResultCount")
        self._context_label.setWordWrap(True)
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
        filter_layout.addWidget(QLabel("来源:"))
        self._source_combo = QComboBox()
        self._source_combo.addItems(["全部", "有实战配队", "未生成 AI 评分"])
        self._source_combo.currentTextChanged.connect(self.refresh)
        filter_layout.addWidget(self._source_combo)
        self._reset_button = QToolButton()
        self._reset_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton)
        )
        self._reset_button.setToolTip("重置相性筛选")
        self._reset_button.setAccessibleName("重置相性筛选")
        self._reset_button.clicked.connect(self._reset_filters)
        filter_layout.addWidget(self._reset_button)
        layout.addLayout(filter_layout)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            "搭配武将", "综合评分", "总评", "实战评级", "实战座次", "相性说明",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.itemSelectionChanged.connect(self.selection_changed.emit)
        self._table.itemDoubleClicked.connect(self._on_double_clicked)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
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
        search_text = self._search_edit.text().strip().lower()
        rating = self._rating_combo.currentText()
        source = self._source_combo.currentText()

        # 行来源：AI 相性 ∪ 实战配队，同配对合并为一行（实战评级/座次随行展示）；
        # 同一配对可能有多个座次变体，逐条成行
        combos_by_pair: dict[tuple[int, int], list[Combo]] = {}
        if self._combo_mgr is not None:
            for combo in self._combo_mgr.list_combos_for_hero(hero.id):
                combos_by_pair.setdefault(
                    tuple(sorted((combo.hero1_id, combo.hero2_id))), []
                ).append(combo)

        rows: list[tuple[SynergyScore | None, Combo | None, Hero | None, int]] = []
        for synergy in synergies:
            pair = tuple(sorted((synergy.hero_a_id, synergy.hero_b_id)))
            partner_id = synergy.hero_b_id if synergy.hero_a_id == hero.id else synergy.hero_a_id
            partner = self._hero_mgr.get_hero(partner_id)
            combos = combos_by_pair.pop(pair, [])
            if not combos:
                combos = [None]
            for combo in combos:
                rows.append((synergy, combo, partner, partner_id))
        for combos in combos_by_pair.values():
            for combo in combos:
                partner_id = combo.hero2_id if combo.hero1_id == hero.id else combo.hero1_id
                rows.append((None, combo, self._hero_mgr.get_hero(partner_id), partner_id))

        filtered: list[tuple[SynergyScore | None, Combo | None, Hero | None, int]] = []
        for synergy, combo, partner, partner_id in rows:
            partner_text = f"{partner.name} #{partner_id}" if partner else f"#{partner_id}"
            if search_text and search_text not in partner_text.lower():
                continue
            if rating != "全部" and (synergy is None or synergy.synergy_rating != rating):
                continue
            if source == "有实战配队" and combo is None:
                continue
            if source == "未生成 AI 评分" and synergy is not None:
                continue
            filtered.append((synergy, combo, partner, partner_id))

        def sort_key(item: tuple) -> tuple:
            synergy, combo, partner, partner_id = item
            partner_name = partner.name if partner else str(partner_id)
            if synergy is not None:
                return (0, -synergy.score, partner_name)
            return (1, -combo.rating, partner_name)

        filtered.sort(key=sort_key)
        self._context_label.setText(
            f"{hero.name}（#{hero.id}） · 显示 {len(filtered)} / 共 {len(rows)} 条（含实战配队）"
        )
        self._table.setRowCount(len(filtered))
        for row, (synergy, combo, partner, partner_id) in enumerate(filtered):
            note_text = combo.note if combo else ""
            cells = [
                partner.name if partner else f"#{partner_id}",
                str(synergy.score) if synergy else "未生成",
                synergy.synergy_rating if synergy else "--",
                str(combo.rating) if combo else "--",
                (
                    f"{combo.hero1_name}[{format_seats(combo.hero1_seats)}] "
                    f"+ {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
                ) if combo else "--",
                (
                    synergy.description.replace("\n", " ") if synergy
                    else note_text.replace("\n", " ")
                ),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column == 0:
                    pair_ids = (
                        (synergy.hero_a_id, synergy.hero_b_id) if synergy
                        else (combo.hero1_id, combo.hero2_id)
                    )
                    item.setData(Qt.ItemDataRole.UserRole, pair_ids)
                if column == 1 and synergy is None:
                    item.setForeground(Qt.GlobalColor.gray)
                if column == 4 and note_text:
                    item.setToolTip(note_text)
                if column == 5:
                    item.setToolTip(synergy.description if synergy else note_text)
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
        self._source_combo.setCurrentText("全部")

    def _on_double_clicked(self, item: QTableWidgetItem) -> None:
        if item.column() == 5:
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
        layout.addWidget(PageHeader(
            "相性说明",
            f"{first_name} 与 {second_name}",
        ))
        browser = QTextBrowser()
        browser.setObjectName("synergyDescriptionBody")
        browser.setHtml(render_markdown(synergy.description))
        browser.setOpenExternalLinks(False)
        layout.addWidget(browser, 1)
        footer = DialogFooter(
            accept_text="关闭",
            accept_role=ROLE_SECONDARY,
            show_cancel=False,
        )
        footer.accepted.connect(dialog.accept)
        layout.addWidget(footer)
        dialog.exec()
