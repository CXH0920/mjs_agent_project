"""
名将杀 Agent - UI 样式表

统一定义应用全局样式和颜色方案。
"""

GLOBAL_STYLE = """
/* === 全局 === */
QMainWindow, QDialog, QWidget {
    background-color: #f5f0e8;
    color: #3d2b1f;
    font-family: "Microsoft YaHei UI", "微软雅黑", "SimHei", sans-serif;
    font-size: 13px;
}

/* === 菜单栏 === */
QMenuBar {
    background-color: #8b0000;
    color: #fff5e6;
    padding: 2px 0;
    font-size: 13px;
    font-weight: bold;
}
QMenuBar::item {
    padding: 6px 14px;
    border-radius: 4px;
}
QMenuBar::item:selected {
    background-color: #a52a2a;
}
QMenu {
    background-color: #fff8f0;
    color: #3d2b1f;
    border: 1px solid #c0a080;
    padding: 4px 0;
}
QMenu::item {
    padding: 6px 28px 6px 20px;
    border-radius: 3px;
}
QMenu::item:selected {
    background-color: #d4a574;
    color: white;
}
QMenu::separator {
    height: 1px;
    background: #c0a080;
    margin: 4px 10px;
}

/* === 标签页 === */
QTabWidget::pane {
    border: 1px solid #c0a080;
    border-top: none;
    background-color: #f5f0e8;
    border-radius: 0 0 6px 6px;
}
QTabBar::tab {
    background-color: #e0d5c5;
    color: #5a4a3a;
    padding: 8px 20px;
    border: 1px solid #c0a080;
    border-bottom: none;
    border-radius: 6px 6px 0 0;
    margin-right: 2px;
    font-size: 13px;
    font-weight: bold;
}
QTabBar::tab:selected {
    background-color: #8b0000;
    color: #fff5e6;
    border-color: #8b0000;
}

/* === 状态栏 === */
QStatusBar {
    background-color: #e8ddd0;
    border-top: 1px solid #c0a080;
    color: #5a4a3a;
    font-size: 12px;
    padding: 2px 8px;
}

/* === 按钮 === */
QPushButton {
    background-color: #8b0000;
    color: white;
    border: none;
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 13px;
    font-weight: bold;
    min-height: 20px;
}
QPushButton:hover {
    background-color: #a52a2a;
}
QPushButton:pressed {
    background-color: #6b0000;
}
QPushButton:disabled {
    background-color: #c0a080;
    color: #e0d5c5;
}

/* === 输入框 === */
QLineEdit {
    border: 1px solid #c0a080;
    border-radius: 4px;
    padding: 5px 8px;
    background-color: #fffcf5;
    color: #3d2b1f;
    selection-background-color: #d4a574;
}
QLineEdit:focus {
    border-color: #8b0000;
}

/* === 下拉框 === */
QComboBox {
    border: 1px solid #c0a080;
    border-radius: 4px;
    padding: 4px 8px;
    background-color: #fffcf5;
    color: #3d2b1f;
    min-height: 22px;
}
QComboBox:focus {
    border-color: #8b0000;
}
QComboBox::drop-down {
    border: none;
    width: 24px;
}
QComboBox QAbstractItemView {
    background-color: #fffcf5;
    color: #3d2b1f;
    selection-background-color: #d4a574;
    border: 1px solid #c0a080;
}

/* === 列表控件 === */
QListWidget {
    border: 1px solid #c0a080;
    border-radius: 4px;
    background-color: #fffcf5;
    color: #3d2b1f;
    outline: none;
}
QListWidget::item {
    padding: 6px 8px;
    border-radius: 3px;
}
QListWidget::item:selected {
    background-color: #d4a574;
    color: white;
}
QListWidget::item:hover:!selected {
    background-color: #f0e6d8;
}

/* === 复选框 === */
QCheckBox {
    spacing: 6px;
    color: #3d2b1f;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #c0a080;
    border-radius: 3px;
    background-color: #fffcf5;
}
QCheckBox::indicator:checked {
    background-color: #8b0000;
    border-color: #8b0000;
}

/* === 滚动条 === */
QScrollArea, QScrollBar:vertical {
    background-color: transparent;
}
QScrollBar:vertical {
    width: 10px;
    border: none;
    background: #e8ddd0;
    border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: #c0a080;
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar::handle:vertical:hover {
    background: #a08060;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

/* === 进度条 === */
QProgressBar {
    border: 1px solid #c0a080;
    border-radius: 4px;
    text-align: center;
    background-color: #e8ddd0;
    color: #3d2b1f;
    font-size: 12px;
    height: 22px;
}
QProgressBar::chunk {
    background-color: #8b0000;
    border-radius: 3px;
}

/* === 分组框 === */
QGroupBox {
    border: 1px solid #c0a080;
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 8px 8px;
    font-weight: bold;
    color: #5a4a3a;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    color: #8b0000;
}

/* === 微调框 === */
QSpinBox {
    border: 1px solid #c0a080;
    border-radius: 4px;
    padding: 4px 6px;
    background-color: #fffcf5;
    color: #3d2b1f;
    min-height: 22px;
}
QSpinBox:focus {
    border-color: #8b0000;
}

/* === 文本框（只读/富文本） === */
QTextBrowser {
    background-color: #fffcf5;
    border: 1px solid #c0a080;
    border-radius: 4px;
    padding: 6px;
    color: #3d2b1f;
}
"""
