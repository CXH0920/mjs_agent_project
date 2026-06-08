"""

名将杀 Agent - 主窗口框架



提供菜单栏、Tab 切换、状态栏和应用主框架。

"""



from __future__ import annotations



import logging

import sys

from pathlib import Path



from PySide6.QtCore import Qt

from PySide6.QtGui import QAction

from PySide6.QtWidgets import (

    QLabel,

    QMainWindow,

    QMessageBox,

    QStatusBar,

    QTabWidget,

    QVBoxLayout,

    QWidget,

)



from src.data.manager import DataManager

from src.ui.hero_browser import HeroBrowser

from src.ui.settings_dialog import SettingsDialog



logger = logging.getLogger(__name__)



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

