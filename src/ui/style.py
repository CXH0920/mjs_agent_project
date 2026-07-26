"""
名将杀 Agent - UI 样式表

统一定义应用全局样式和颜色方案。
"""

# 语义色 token：页面组件优先复用这些值，动态势力色等例外可局部计算。
PRIMARY = "#4a90d9"
PRIMARY_HOVER = "#357abd"
SUCCESS = "#176b36"
WARNING = "#8a5a00"
DANGER = "#a12622"
TEXT_PRIMARY = "#2c3e50"
MUTED_TEXT = "#65758b"
SURFACE = "#ffffff"
SUBTLE_SURFACE = "#eef2f6"
BORDER = "#b0c4de"

# 页面级标题和顶部操作栏：选将推荐、对局攻略等工作台页面共用。
PAGE_TITLE_STYLE = f"font-size: 18px; font-weight: bold; color: {TEXT_PRIMARY}; padding: 4px 0;"
HEADER_PRIMARY_BUTTON_STYLE = "padding: 4px 14px; font-size: 12px;"
HEADER_SECONDARY_BUTTON_STYLE = (
    f"QPushButton {{ background-color: {SURFACE}; color: {PRIMARY}; border: 1px solid {BORDER}; "
    "border-radius: 4px; padding: 4px 14px; font-size: 12px; }"
    f"QPushButton:hover {{ background-color: {SUBTLE_SURFACE}; }}"
)


GLOBAL_STYLE = """
/* === 全局 === */
QMainWindow, QDialog, QWidget {
    background-color: #f0f4f8;
    color: #2c3e50;
    font-family: "Microsoft YaHei UI", "微软雅黑", "SimHei", sans-serif;
    font-size: 13px;
}

/* === 菜单栏 === */
QMenuBar {
    background-color: #4a90d9;
    color: white;
    padding: 2px 0;
    font-size: 13px;
    font-weight: bold;
}
QMenuBar::item {
    padding: 6px 14px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #357abd;
}
QMenu {
    background-color: #f8faff;
    color: #2c3e50;
    border: 1px solid #b0c4de;
    padding: 4px 0;
}
QMenu::item {
    padding: 6px 28px 6px 20px;
    border-radius: 3px;
}
QMenu::item:selected {
    background-color: #4a90d9;
    color: white;
}
QMenu::separator {
    height: 1px;
    background-color: #b0c4de;
    margin: 4px 10px;
}

/* === 标签页 === */
QTabWidget::pane {
    border: 1px solid #b0c4de;
    border-top: none;
    background-color: #f0f4f8;
    border-radius: 0 0 6px 6px;
}
QTabBar::tab {
    background-color: #dce6f0;
    color: #4a6a8a;
    padding: 8px 20px;
    border: 1px solid #b0c4de;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    margin-right: 2px;
    font-size: 13px;
    font-weight: bold;
}
QTabBar::tab:selected {
    background-color: #4a90d9;
    color: white;
    border-color: #4a90d9;
}

/* === 状态栏 === */
QStatusBar {
    background-color: #dce6f0;
    border-top: 1px solid #b0c4de;
    color: #4a6a8a;
    font-size: 12px;
    padding: 2px 8px;
}

/* === 全局按钮（默认蓝） === */
QPushButton {
    background-color: #4a90d9;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: bold;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #357abd;
}
QPushButton:pressed {
    background-color: #2a6cb5;
}
QPushButton:disabled {
    background-color: #b0c4de;
    color: #dce6f0;
}

/* === 输入框 === */
QLineEdit {
    border: 1px solid #b0c4de;
    border-radius: 4px;
    padding: 5px 8px;
    background-color: white;
    color: #2c3e50;
    selection-background-color: #4a90d9;
}
QLineEdit:focus {
    border-color: #4a90d9;
}

/* === 下拉框 === */
QComboBox {
    border: 1px solid #b0c4de;
    border-radius: 4px;
    padding: 4px 8px;
    background-color: white;
    color: #2c3e50;
    min-height: 22px;
}
QComboBox:focus {
    border-color: #4a90d9;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: white;
    color: #2c3e50;
    selection-background-color: #4a90d9;
    border: 1px solid #b0c4de;
}

/* === 列表控件 === */
QListWidget {
    border: 1px solid #b0c4de;
    border-radius: 4px;
    background-color: white;
    color: #2c3e50;
    outline: none;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 3px;
}
QListWidget::item:selected {
    background-color: #4a90d9;
    color: white;
}
QListWidget::item:hover:!selected {
    background-color: #e8f0fe;
}

/* === 复选框 === */
QCheckBox {
    spacing: 6px;
    color: #2c3e50;
    font-size: 12px;
}
QCheckBox::indicator {
    width: 15px;
    height: 15px;
}

/* === 滚动条 === */
QScrollArea, QScrollBar:vertical {
    background-color: transparent;
}
QScrollBar:vertical {
    width: 10px;
    border: none;
    background-color: #dce6f0;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background-color: #b0c4de;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background-color: #7a9bb5;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* === 进度条 === */
QProgressBar {
    border: 1px solid #b0c4de;
    border-radius: 4px;
    text-align: center;
    background-color: #dce6f0;
    color: #2c3e50;
    font-size: 12px;
    height: 22px;
}
QProgressBar::chunk {
    background-color: #4a90d9;
    border-radius: 3px;
}

/* === 分组框 === */
QGroupBox {
    border: 1px solid #b0c4de;
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 8px 8px;
    font-weight: bold;
    color: #4a6a8a;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #4a90d9;
}

/* === 微调框 === */
QSpinBox {
    border: 1px solid #b0c4de;
    border-radius: 4px;
    padding: 4px 6px;
    background-color: white;
    color: #2c3e50;
    min-height: 22px;
}
QSpinBox:focus {
    border-color: #4a90d9;
}

/* === 文本框（只读/富文本） === */
QTextBrowser {
    background-color: white;
    border: 1px solid #b0c4de;
    border-radius: 4px;
    padding: 6px;
    color: #2c3e50;
}
"""
