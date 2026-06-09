"""

名将杀 Agent - 武将浏览器



提供武将列表浏览、搜索筛选、详情查看和攻略展示功能。

"""



from __future__ import annotations



import logging

from typing import Optional



from PySide6.QtCore import Qt, Signal, QSize

from PySide6.QtWidgets import (

    QComboBox,

    QFrame,

    QHBoxLayout,

    QLabel,

    QLineEdit,

    QListWidget,

    QListWidgetItem,

    QPushButton,

    QScrollArea,

    QSplitter,

    QStackedWidget,

    QTabWidget,

    QTextBrowser,

    QVBoxLayout,

    QWidget,

)



from src.data.manager import DataManager

from src.data.models import Hero, HeroGuide



logger = logging.getLogger(__name__)





class HeroListPanel(QWidget):

    """左侧武将列表面板



    包含搜索框、势力筛选和武将列表。

    """



    hero_selected = Signal(int)  # 发出武将 ID



    def __init__(self, data_manager: DataManager, parent=None):

        super().__init__(parent)

        self._dm = data_manager

        self._all_heroes: list[Hero] = []

        self._filtered_heroes: list[Hero] = []



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

        self._all_heroes = sorted(self._dm.list_heroes(), key=lambda h: h.id)



        # 填充势力筛选

        self._faction_combo.blockSignals(True)

        self._faction_combo.clear()

        self._faction_combo.addItem("全部")

        for faction in self._dm.list_factions():

            self._faction_combo.addItem(faction)

        self._faction_combo.blockSignals(False)



        self._apply_filters()



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

        """刷新列表显示"""

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

            self._list.setCurrentRow(0)



    def _on_selection_changed(self, row: int) -> None:

        """列表选中项变化"""

        if 0 <= row < len(self._filtered_heroes):

            hero_id = self._filtered_heroes[row].id

            self.hero_selected.emit(hero_id)





class HeroDetailPanel(QWidget):

    """武将详情面板"""



    def __init__(self, data_manager: DataManager, parent=None):

        super().__init__(parent)

        self._dm = data_manager

        self._current_hero: Optional[Hero] = None

        self._current_guide: Optional[HeroGuide] = None



        self._setup_ui()



    # ---------------------------------------------------------------

    # UI 构建

    # ---------------------------------------------------------------



    def _setup_ui(self) -> None:

        layout = QVBoxLayout(self)

        layout.setContentsMargins(0, 0, 0, 0)



        # 空状态提示

        self._empty_label = QLabel("请选择一个武将")

        self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._empty_label.setStyleSheet("color: gray; font-size: 16px;")



        # 详情标签页

        self._tabs = QTabWidget()

        self._tabs.setVisible(False)



        # Tab A — 基本信息

        self._info_tab = QWidget()

        self._setup_info_tab()

        self._tabs.addTab(self._info_tab, "基本信息")



        # Tab B — 攻略指南

        self._guide_tab = QWidget()

        self._setup_guide_tab()

        self._tabs.addTab(self._guide_tab, "攻略指南")



        # 堆叠容器

        self._stack = QStackedWidget()

        self._stack.addWidget(self._empty_label)   # index 0

        self._stack.addWidget(self._tabs)           # index 1

        layout.addWidget(self._stack)



    def _setup_info_tab(self) -> None:

        """基本信息标签页"""

        layout = QVBoxLayout(self._info_tab)



        # 武将概要

        self._summary = QLabel()

        self._summary.setWordWrap(True)

        self._summary.setStyleSheet("font-size: 14px;")

        layout.addWidget(self._summary)



        # 技能列表（滚动区）

        scroll = QScrollArea()

        scroll.setWidgetResizable(True)

        self._skills_widget = QWidget()

        self._skills_layout = QVBoxLayout(self._skills_widget)

        scroll.setWidget(self._skills_widget)

        layout.addWidget(scroll, 1)



    def _setup_guide_tab(self) -> None:

        """攻略指南标签页"""

        layout = QVBoxLayout(self._guide_tab)



        scroll = QScrollArea()

        scroll.setWidgetResizable(True)

        self._guide_widget = QWidget()

        self._guide_layout = QVBoxLayout(self._guide_widget)

        self._guide_layout.setContentsMargins(0, 0, 0, 5)
        scroll.setWidget(self._guide_widget)

        layout.addWidget(scroll, 1)



    # ---------------------------------------------------------------

    # 显示数据

    # ---------------------------------------------------------------



    def show_hero(self, hero_id: int) -> None:

        """显示指定武将的详情"""

        hero = self._dm.get_hero(hero_id)

        guide = self._dm.get_guide(hero_id)

        self._current_hero = hero

        self._current_guide = guide



        if not hero:

            self._empty_label.setText(f"未找到武将 (ID: {hero_id})")

            self._stack.setCurrentIndex(0)

            return



        self._update_info_tab(hero)

        self._update_guide_tab(guide)

        self._stack.setCurrentIndex(1)



    def _update_info_tab(self, hero: Hero) -> None:

        """更新基本信息"""

        # 摘要

        difficulty_stars = "★" * min(hero.difficulty, 5) + "☆" * (5 - min(hero.difficulty, 5))

        summary = (

            f"<b>{hero.name}</b>  {hero.title}"

            f"<br>势力: {hero.faction}  |  定位: {hero.position}"

            f"<br>体力: {hero.max_hp}  |  手牌: {hero.max_hand}  |  性别: {hero.gender.value}"

            f"<br>难度: {difficulty_stars}"

        )

        self._summary.setText(summary)



        # 技能列表

        while self._skills_layout.count():

            item = self._skills_layout.takeAt(0)

            if item.widget():

                item.widget().deleteLater()



        if not hero.skills:

            self._skills_layout.addWidget(QLabel("暂无技能数据"))

        else:

            for skill in hero.skills:

                frame = QFrame()

                frame.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)

                skill_layout = QVBoxLayout(frame)

                skill_layout.setContentsMargins(8, 4, 8, 4)



                name_label = QLabel(f"<b>{skill.name}</b>")

                name_label.setStyleSheet("font-size: 13px;")

                skill_layout.addWidget(name_label)



                if skill.description:

                    desc_label = QLabel(skill.description)

                    desc_label.setWordWrap(True)

                    desc_label.setStyleSheet("color: #333;")

                    skill_layout.addWidget(desc_label)



                if skill.settlement:

                    settle_label = QLabel(f"<i>结算: {skill.settlement}</i>")

                    settle_label.setWordWrap(True)

                    settle_label.setStyleSheet("color: #666; font-size: 11px;")

                    settle_label.setVisible(False)



                    toggle = QPushButton("展开结算详情")

                    toggle.setStyleSheet("font-size: 11px; max-width: 120px;")

                    toggle.clicked.connect(

                        lambda checked, s=settle_label, b=toggle: (

                            s.setVisible(not s.isVisible()),

                            b.setText("收起结算详情" if s.isVisible() else "展开结算详情")

                        )

                    )

                    skill_layout.addWidget(toggle)

                    skill_layout.addWidget(settle_label)



                self._skills_layout.addWidget(frame)



        self._skills_layout.addStretch()



    @staticmethod
    def _markdown_to_html(text: str) -> str:
        """将简单 Markdown 转换为 HTML"""
        import re
        html = text
        html = html.replace('&', '&amp;')
        html = html.replace('<', '&lt;')
        html = html.replace('>', '&gt;')
        html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
        html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
        html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
        html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
        html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html)
        html = re.sub(r'^\d+\.\s+(.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'^- (.+)$', r'<li>\1</li>', html, flags=re.MULTILINE)
        html = re.sub(r'^\s{2,}- (.+)$', r"<li style='margin-left:20px;'>\1</li>", html, flags=re.MULTILINE)
        html = re.sub(r'(<li>.*?</li>(?:\s*\n<li>.*?</li>)*)', r'<ul>\1</ul>', html, flags=re.DOTALL)
        html = html.replace('\\n', '<br>')
        for tag in ('h1', 'h2', 'h3', 'ul', '/ul'):
            html = re.sub(rf'<br>\s*(<{tag}>)', r'\1', html)
            html = re.sub(rf'(</{tag}>)\s*<br>', r'\1', html)
        return html

    def _update_guide_tab(self, guide: Optional[HeroGuide]) -> None:

        """更新攻略指南"""

        while self._guide_layout.count():

            item = self._guide_layout.takeAt(0)

            if item.widget():

                item.widget().deleteLater()



        if not guide:

            self._guide_layout.addWidget(QLabel("暂无攻略数据"))

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

            tips = QLabel(f"<b>新手提示:</b>\\n{guide.tips_for_beginners}")

            tips.setWordWrap(True)

            self._guide_layout.addWidget(tips)



        # 克制 / 搭配

        if guide.counters:

            names = []

            for hid in guide.counters[:10]:

                h = self._dm.get_hero(hid)

                names.append(h.name if h else f"#{hid}")

            cl = QLabel(f"<b>被克制:</b>  {'、'.join(names)}")

            cl.setWordWrap(True)

            self._guide_layout.addWidget(cl)



        if guide.synergizes_with:

            names = []

            for hid in guide.synergizes_with[:10]:

                h = self._dm.get_hero(hid)

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








class HeroBrowser(QWidget):

    """武将浏览器主组件



    左侧列表 + 右侧详情面板，支持搜索和势力筛选。

    """



    def __init__(self, data_manager: DataManager, parent=None):

        super().__init__(parent)

        self._dm = data_manager



        self._setup_ui()



    def _setup_ui(self) -> None:

        layout = QHBoxLayout(self)

        layout.setContentsMargins(0, 0, 0, 0)



        # 分割面板

        splitter = QSplitter(Qt.Orientation.Horizontal)



        # 左侧：武将列表

        self._list_panel = HeroListPanel(self._dm)

        splitter.addWidget(self._list_panel)



        # 右侧：详情面板

        self._detail_panel = HeroDetailPanel(self._dm)

        splitter.addWidget(self._detail_panel)



        splitter.setSizes([280, 520])

        layout.addWidget(splitter, 1)



        # 连接信号

        self._list_panel.hero_selected.connect(self._detail_panel.show_hero)

