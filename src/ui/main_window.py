"""

名将杀 Agent - 主窗口框架



提供菜单栏、Tab 切换、状态栏和应用主框架。

"""



from __future__ import annotations



import logging

import sys

from pathlib import Path



from PySide6.QtCore import Qt, QProcess

from PySide6.QtGui import QAction

from PySide6.QtWidgets import (

    QLabel,

    QMainWindow,

    QMessageBox,

    QStatusBar,

    QTabWidget,

    QVBoxLayout,

    QWidget,

    QDialog,
    QGridLayout,
    QCheckBox,
    QLineEdit,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
)



from src.data.manager import DataManager

from src.ui.hero_browser import HeroBrowser

from src.ui.settings_dialog import SettingsDialog


# 默认数据路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"



class MainWindow(QMainWindow):

    """主窗口



    初始化时自动加载数据，显示武将浏览和选将推荐 Tab。

    """



    def __init__(self, data_manager: Optional[DataManager] = None):

        super().__init__()

        self._dm = data_manager or DataManager(

            heroes_file=DEFAULT_HEROES_FILE,

            synergies_file=DEFAULT_SYNERGIES_FILE,

            guides_file=DEFAULT_GUIDES_FILE,

        )



        self.setWindowTitle("名将杀 Agent")

        self.setMinimumSize(960, 640)

        self.resize(1100, 720)



        self._setup_menu()

        self._load_data()

        self._setup_ui()

        self._setup_status_bar()

        self._update_status()



    # ---------------------------------------------------------------

    # 菜单栏

    # ---------------------------------------------------------------



    def _setup_menu(self) -> None:

        """构建菜单栏"""

        bar = self.menuBar()



        # 文件菜单

        file_menu = bar.addMenu("文件")

        exit_action = QAction("退出", self)

        exit_action.setShortcut("Ctrl+Q")

        exit_action.triggered.connect(self.close)

        file_menu.addAction(exit_action)



        # 工具菜单

        tools_menu = bar.addMenu("工具")

        config_action = QAction("API 配置", self)

        config_action.triggered.connect(self._open_settings)

        tools_menu.addAction(config_action)



        # 数据菜单

        data_menu = bar.addMenu("数据")

        reload_action = QAction("重新加载数据", self)

        reload_action.setShortcut("F5")

        reload_action.triggered.connect(self._reload_data)

        data_menu.addAction(reload_action)

        # 武将获取子菜单
        fetch_menu = data_menu.addMenu("武将获取")

        fetch_all_action = QAction("全量获取", self)
        fetch_all_action.triggered.connect(self._fetch_all_heroes)
        fetch_menu.addAction(fetch_all_action)

        fetch_inc_action = QAction("增量获取", self)
        fetch_inc_action.triggered.connect(self._fetch_incremental)
        fetch_menu.addAction(fetch_inc_action)

        fetch_spec_action = QAction("指定获取", self)
        fetch_spec_action.triggered.connect(self._fetch_specific)
        fetch_menu.addAction(fetch_spec_action)



        # 帮助菜单

        help_menu = bar.addMenu("帮助")

        about_action = QAction("关于", self)

        about_action.triggered.connect(self._show_about)

        help_menu.addAction(about_action)



    # ---------------------------------------------------------------

    # UI 构建

    # ---------------------------------------------------------------



    def _setup_ui(self) -> None:

        """构建中央控件"""

        central = QWidget()

        self.setCentralWidget(central)



        layout = QVBoxLayout(central)

        layout.setContentsMargins(4, 4, 4, 4)



        self._tabs = QTabWidget()

        self._tabs.setDocumentMode(True)



        # Tab 1: 武将浏览

        self._hero_browser = HeroBrowser(self._dm)

        self._tabs.addTab(self._hero_browser, "武将浏览")



        # Tab 2: 选将推荐（占位）

        placeholder = QWidget()

        placeholder_layout = QVBoxLayout(placeholder)

        label = QLabel("选将推荐功能将在后续版本中实现")

        label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label.setStyleSheet("color: gray; font-size: 16px;")

        placeholder_layout.addWidget(label)

        self._tabs.addTab(placeholder, "选将推荐")



        layout.addWidget(self._tabs, 1)



    def _setup_status_bar(self) -> None:

        """构建状态栏"""

        bar = QStatusBar()

        self._status_label = QLabel("正在加载数据...")

        bar.addWidget(self._status_label, 1)

        self.setStatusBar(bar)



    # ---------------------------------------------------------------

    # 数据加载

    # ---------------------------------------------------------------



    def _load_data(self) -> None:

        """加载所有数据"""

        try:

            self._dm.load_all()

        except Exception as e:

            logger.exception("数据加载失败")

            QMessageBox.warning(

                self, "数据加载失败",

                f"无法加载数据文件:\\n{e}"

                "\\n\\n请确保 data/ 目录下存在 heroes.json 文件。"

            )



    def _reload_data(self) -> None:

        """重新加载数据"""

        self._load_data()

        self._update_status()

        # 刷新武将浏览器

        if hasattr(self, '_hero_browser'):

            # 重新初始化列表面板
            self._hero_browser._list_panel._load_heroes()
        QMessageBox.information(self, "已刷新", "数据已重新加载")




    def _toggle_all_factions(self, btn: QPushButton) -> None:
        """全选 / 取消全选 势力复选框"""
        check = btn.text() == "全部选中"
        for cb in self._faction_checkboxes:
            cb.blockSignals(True)
            cb.setChecked(check)
            cb.blockSignals(False)
        btn.setText("取消全选" if check else "全部选中")
        # 触发过滤
        if hasattr(self, '_hero_browser'):
            pass

    def _fetch_specific(self) -> None:
        """指定获取武将"""
        all_heroes = sorted(self._dm.list_heroes(), key=lambda h: h.id)
        factions = self._dm.list_factions()

        dialog = QDialog(self)
        dialog.setWindowTitle("选择武将")
        dialog.setMinimumWidth(420)
        dialog.setMinimumHeight(500)
        layout = QVBoxLayout(dialog)

        # 武将名称搜索
        search_input = QLineEdit()
        search_input.setPlaceholderText("搜索武将名称...")
        layout.addWidget(search_input)

        # 势力筛选（复选框）
        faction_layout = QGridLayout()
        faction_layout.setSpacing(6)
        self._faction_checkboxes: list[QCheckBox] = []
        for idx, f in enumerate(factions):
            cb = QCheckBox(f)
            cb.setChecked(True)
            row, col = divmod(idx, 10)
            faction_layout.addWidget(cb, row, col)
            self._faction_checkboxes.append(cb)

        # 全选/取消全选 按钮放在最后一行
        toggle_btn = QPushButton("全部选中")
        toggle_btn.clicked.connect(lambda: self._toggle_all_factions(toggle_btn))
        row_count = (len(factions) - 1) // 10 + 1
        faction_layout.addWidget(toggle_btn, row_count, 0, 1, 3)

        layout.addLayout(faction_layout)
        count_label = QLabel()
        layout.addWidget(count_label)

        # 武将列表（多选）
        list_widget = QListWidget()
        list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        layout.addWidget(list_widget, 1)

        # 过滤逻辑
        def _apply_filter() -> None:
            keyword = search_input.text().strip()
            selected_factions = {cb.text() for cb in self._faction_checkboxes if cb.isChecked()}

            filtered = []
            for hero in all_heroes:
                if hero.faction not in selected_factions:
                    continue
                if keyword and keyword not in hero.name:
                    continue
                filtered.append(hero)

            list_widget.blockSignals(True)
            list_widget.clear()
            for hero in filtered:
                item = QListWidgetItem(f"{hero.name}  [{hero.faction}]")
                item.setData(Qt.ItemDataRole.UserRole, hero.id)
                list_widget.addItem(item)
            list_widget.blockSignals(False)
            count_label.setText(f"已筛选: {len(filtered)} / {len(all_heroes)} 个武将")

        # 连接信号
        search_input.textChanged.connect(_apply_filter)
        for cb in self._faction_checkboxes:
            cb.toggled.connect(_apply_filter)

        _apply_filter()

        # 按钮
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        cancel_btn.clicked.connect(dialog.reject)
        ok_btn.clicked.connect(dialog.accept)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_ids = []
        for item in list_widget.selectedItems():
            hid = item.data(Qt.ItemDataRole.UserRole)
            selected_ids.append(str(hid))

        if not selected_ids:
            return

        self._status_label.setText("正在采集指定武将...")

        ids_str = ",".join(selected_ids)
        self._fetch_proc = QProcess(self)
        self._fetch_proc.finished.connect(self._on_fetch_finished)
        self._fetch_proc.errorOccurred.connect(self._on_fetch_error)
        self._fetch_proc.start(sys.executable, ["-m", "src.scraper.incremental", "--hero-id", ids_str])
    def _fetch_incremental(self) -> None:
        """增量获取武将数据"""
        reply = QMessageBox.question(
            self,
            "确认操作",
            "是否增量获取武将数据？\n仅爬取本地还未拥有的武将并追加写入。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._status_label.setText("正在增量采集武将数据...")

        self._fetch_proc = QProcess(self)
        self._fetch_proc.finished.connect(self._on_fetch_finished)
        self._fetch_proc.errorOccurred.connect(self._on_fetch_error)
        self._fetch_proc.start(sys.executable, ["-m", "src.scraper.incremental", "--incremental"])

    def _fetch_all_heroes(self) -> None:
        """全量获取武将数据"""
        reply = QMessageBox.question(
            self,
            "确认操作",
            "是否全量获取武将数据？\n此操作将从官网重新采集所有武将信息。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._status_label.setText("正在采集武将数据...")

        self._fetch_proc = QProcess(self)
        self._fetch_proc.finished.connect(self._on_fetch_finished)
        self._fetch_proc.errorOccurred.connect(self._on_fetch_error)
        self._fetch_proc.start(sys.executable, ["-m", "src.scraper.official"])

    def _on_fetch_finished(self, exit_code: int) -> None:
        """采集完成回调"""
        if exit_code == 0:
            self._status_label.setText("武将数据采集完成")
            QMessageBox.information(self, "提示", "武将数据已采集完成\n请通过 数据 > 重新加载数据 刷新")
        else:
            self._status_label.setText("武将数据采集失败")

    def _on_fetch_error(self, error: QProcess.ProcessError) -> None:
        """采集出错回调"""
        self._status_label.setText("采集出错")
        QMessageBox.warning(self, "采集失败", f"武将数据采集失败\n{self._fetch_proc.errorString()}")

    def _update_status(self) -> None:

        """更新状态栏显示"""

        heroes = len(self._dm.list_heroes())

        synergies = len(self._dm.list_synergies())

        guides = len(self._dm.list_guides())

        self._status_label.setText(

            f"武将: {heroes}  |  相性: {synergies}  |  攻略: {guides}"

        )



    # ---------------------------------------------------------------

    # 对话框

    # ---------------------------------------------------------------



    def _open_settings(self) -> None:

        """打开 API 配置对话框"""

        dialog = SettingsDialog(parent=self)

        dialog.exec()



    def _show_about(self) -> None:

        """显示关于对话框"""

        QMessageBox.about(

            self, "关于 名将杀 Agent",

            "名将杀 Agent v0.1.0\\n\\n"

            "名将杀桌面辅助工具\\n"

            "面向名将杀手游的轻度玩家\\n\\n"

            "技术栈: PySide6 + Pydantic + httpx\\n"

            "数据来源: 游戏官网 + DeepSeek API"

        )

