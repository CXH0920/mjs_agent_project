"""
名将杀 Agent - UI 样式表

统一定义应用全局样式和颜色方案。
"""

# 设计 token：动态势力色等业务颜色可局部计算，其余页面样式应复用这些值。
CANVAS = "#f4f6f8"
SURFACE = "#ffffff"
SUBTLE_SURFACE = "#edf1f5"
SURFACE_HOVER = "#e8eef4"
TEXT_PRIMARY = "#1f2933"
MUTED_TEXT = "#66717e"
BORDER = "#d7dee7"
BORDER_STRONG = "#aeb9c6"

PRIMARY = "#2f6ea5"
PRIMARY_HOVER = "#285f8f"
PRIMARY_PRESSED = "#214f77"
PRIMARY_SOFT = "#e8f1f8"
SUCCESS = "#26734d"
SUCCESS_SOFT = "#e8f4ed"
WARNING = "#a15c00"
WARNING_SOFT = "#fff3df"
DANGER = "#b23a3a"
DANGER_HOVER = "#983131"
DANGER_SOFT = "#faeaea"

FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "SimHei", sans-serif'
FONT_SIZE_XS = 11
FONT_SIZE_SM = 12
FONT_SIZE_MD = 13
FONT_SIZE_LG = 16
FONT_SIZE_PAGE_TITLE = 20

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 24
RADIUS_SM = 4
RADIUS_MD = 6
CONTROL_HEIGHT_COMPACT = 28
CONTROL_HEIGHT_DEFAULT = 32
CONTROL_HEIGHT_PRIMARY = 36
ICON_SIZE = 16

UI_ROLE_PROPERTY = "uiRole"
TONE_PROPERTY = "tone"
ROLE_PRIMARY = "primary"
ROLE_SECONDARY = "secondary"
ROLE_GHOST = "ghost"
ROLE_DANGER = "danger"
TONE_NEUTRAL = "neutral"
TONE_INFO = "info"
TONE_SUCCESS = "success"
TONE_WARNING = "warning"
TONE_DANGER = "danger"

# 页面级标题和顶部操作栏：选将推荐、对局攻略等工作台页面共用。
PAGE_TITLE_STYLE = (
    f"font-size: {FONT_SIZE_PAGE_TITLE}px; font-weight: bold; "
    f"color: {TEXT_PRIMARY}; padding: {SPACE_XS}px 0;"
)
HEADER_PRIMARY_BUTTON_STYLE = (
    f"QPushButton {{ background-color: {PRIMARY}; color: white; border: 1px solid {PRIMARY}; "
    f"border-radius: {RADIUS_SM}px; padding: {SPACE_XS}px 14px; "
    f"font-size: {FONT_SIZE_SM}px; font-weight: bold; }}"
    f"QPushButton:hover {{ background-color: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}"
    f"QPushButton:pressed {{ background-color: {PRIMARY_PRESSED}; border-color: {PRIMARY_PRESSED}; }}"
    f"QPushButton:disabled {{ background-color: {SUBTLE_SURFACE}; color: {BORDER_STRONG}; "
    f"border-color: {BORDER}; }}"
)
HEADER_SECONDARY_BUTTON_STYLE = (
    f"QPushButton {{ background-color: {SURFACE}; color: {PRIMARY}; border: 1px solid {BORDER}; "
    f"border-radius: {RADIUS_SM}px; padding: {SPACE_XS}px 14px; font-size: {FONT_SIZE_SM}px; }}"
    f"QPushButton:hover {{ background-color: {SUBTLE_SURFACE}; }}"
)


def _refresh_style(widget) -> None:
    """让运行时动态属性立即参与 QSS 匹配。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_ui_role(widget, role: str) -> None:
    """设置按钮等交互控件的视觉语义。"""
    widget.setProperty(UI_ROLE_PROPERTY, role)
    _refresh_style(widget)


def set_tone(widget, tone: str) -> None:
    """设置状态标签、通知条等展示控件的语义色调。"""
    widget.setProperty(TONE_PROPERTY, tone)
    _refresh_style(widget)


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

# 设计系统覆盖层放在旧规则之后，保证现有页面保持兼容，并允许页面逐步迁移到语义属性。
GLOBAL_STYLE += f"""
/* === Design system foundation === */
QMainWindow, QDialog {{
    background-color: {CANVAS};
    color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE_MD}px;
}}
QWidget {{
    color: {TEXT_PRIMARY};
    font-family: {FONT_FAMILY};
    font-size: {FONT_SIZE_MD}px;
}}
QMenuBar {{
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    border-bottom: 1px solid {BORDER};
    padding: 2px 4px;
    font-weight: normal;
}}
QMenuBar::item {{
    background: transparent;
    padding: 6px 12px;
    border-radius: {RADIUS_SM}px;
}}
QMenuBar::item:selected {{
    background-color: {SUBTLE_SURFACE};
    color: {PRIMARY};
}}
QMenu {{
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 7px 28px 7px 20px;
    border-radius: {RADIUS_SM}px;
}}
QMenu::item:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
}}
QMenu::separator {{
    height: 1px;
    background-color: {BORDER};
    margin: 4px 8px;
}}

QTabWidget::pane {{
    border: 1px solid {BORDER};
    border-top: none;
    background-color: {CANVAS};
    border-radius: 0 0 {RADIUS_MD}px {RADIUS_MD}px;
}}
QTabBar::tab {{
    background-color: {SUBTLE_SURFACE};
    color: {MUTED_TEXT};
    padding: 8px 18px;
    border: 1px solid {BORDER};
    border-bottom: none;
    border-radius: {RADIUS_MD}px {RADIUS_MD}px 0 0;
    margin-right: 2px;
    font-weight: normal;
}}
QTabBar::tab:hover:!selected {{
    background-color: {SURFACE_HOVER};
    color: {TEXT_PRIMARY};
}}
QTabBar::tab:selected {{
    background-color: {PRIMARY};
    color: white;
    border-color: {PRIMARY};
    font-weight: bold;
}}

QStatusBar {{
    background-color: {SURFACE};
    border-top: 1px solid {BORDER};
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
    padding: 2px 8px;
}}

QPushButton, QToolButton {{
    min-height: {CONTROL_HEIGHT_COMPACT}px;
    padding: 0 12px;
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    font-size: {FONT_SIZE_MD}px;
    font-weight: normal;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {SURFACE_HOVER};
    border-color: {PRIMARY};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {SUBTLE_SURFACE};
}}
QPushButton:focus, QToolButton:focus {{
    border: 2px solid {PRIMARY};
}}
QPushButton:disabled, QToolButton:disabled {{
    background-color: {SUBTLE_SURFACE};
    color: {BORDER_STRONG};
    border-color: {BORDER};
}}
QPushButton[uiRole="primary"], QToolButton[uiRole="primary"] {{
    background-color: {PRIMARY};
    color: white;
    border-color: {PRIMARY};
    font-weight: bold;
}}
QPushButton[uiRole="primary"]:hover, QToolButton[uiRole="primary"]:hover {{
    background-color: {PRIMARY_HOVER};
    border-color: {PRIMARY_HOVER};
}}
QPushButton[uiRole="primary"]:pressed, QToolButton[uiRole="primary"]:pressed {{
    background-color: {PRIMARY_PRESSED};
    border-color: {PRIMARY_PRESSED};
}}
QPushButton[uiRole="secondary"], QToolButton[uiRole="secondary"] {{
    background-color: {SURFACE};
    color: {PRIMARY};
    border-color: {BORDER_STRONG};
}}
QPushButton[uiRole="ghost"], QToolButton[uiRole="ghost"] {{
    background-color: transparent;
    color: {MUTED_TEXT};
    border-color: transparent;
}}
QPushButton[uiRole="ghost"]:hover, QToolButton[uiRole="ghost"]:hover {{
    background-color: {SUBTLE_SURFACE};
    color: {TEXT_PRIMARY};
}}
QPushButton[uiRole="danger"], QToolButton[uiRole="danger"] {{
    background-color: {DANGER};
    color: white;
    border-color: {DANGER};
    font-weight: bold;
}}
QPushButton[uiRole="danger"]:hover, QToolButton[uiRole="danger"]:hover {{
    background-color: {DANGER_HOVER};
    border-color: {DANGER_HOVER};
}}

QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    min-height: {CONTROL_HEIGHT_COMPACT}px;
    padding: 0 8px;
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    selection-background-color: {PRIMARY};
    selection-color: white;
}}
QTextEdit, QPlainTextEdit {{
    padding: 6px 8px;
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover,
QSpinBox:hover, QDoubleSpinBox:hover {{
    border-color: {PRIMARY};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{
    border: 2px solid {PRIMARY};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled,
QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background-color: {SUBTLE_SURFACE};
    color: {MUTED_TEXT};
    border-color: {BORDER};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    selection-background-color: {PRIMARY_SOFT};
    selection-color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
}}

QListWidget, QTreeWidget, QTableWidget {{
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    outline: none;
    alternate-background-color: {CANVAS};
}}
QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{
    background-color: {PRIMARY_SOFT};
    color: {TEXT_PRIMARY};
}}
QListWidget::item:hover:!selected, QTreeWidget::item:hover:!selected,
QTableWidget::item:hover:!selected {{
    background-color: {SUBTLE_SURFACE};
}}
QHeaderView::section {{
    background-color: {SUBTLE_SURFACE};
    color: {MUTED_TEXT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: bold;
}}

QScrollBar:vertical {{
    width: 10px;
    border: none;
    background-color: transparent;
}}
QScrollBar::handle:vertical {{
    background-color: {BORDER_STRONG};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {MUTED_TEXT};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QSplitter::handle {{
    background-color: {BORDER};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}

QProgressBar {{
    height: 22px;
    background-color: {SUBTLE_SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {PRIMARY};
    border-radius: 3px;
}}
QTextBrowser {{
    background-color: {SURFACE};
    color: {TEXT_PRIMARY};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 8px;
}}

QWidget#pageHeader {{
    background-color: transparent;
}}
QLabel#pageHeaderTitle {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_PAGE_TITLE}px;
    font-weight: bold;
}}
QLabel#pageHeaderSubtitle {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QWidget#emptyState {{
    background-color: transparent;
}}
QLabel#emptyStateTitle {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
}}
QLabel#emptyStateDescription {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_MD}px;
}}
QLabel#statusBadge {{
    padding: 2px 6px;
    border-radius: 3px;
    font-size: {FONT_SIZE_XS}px;
    font-weight: bold;
}}
QLabel#statusBadge[tone="neutral"] {{ color: {MUTED_TEXT}; background-color: {SUBTLE_SURFACE}; }}
QLabel#statusBadge[tone="info"] {{ color: {PRIMARY}; background-color: {PRIMARY_SOFT}; }}
QLabel#statusBadge[tone="success"] {{ color: {SUCCESS}; background-color: {SUCCESS_SOFT}; }}
QLabel#statusBadge[tone="warning"] {{ color: {WARNING}; background-color: {WARNING_SOFT}; }}
QLabel#statusBadge[tone="danger"] {{ color: {DANGER}; background-color: {DANGER_SOFT}; }}

QFrame#noticeBanner {{
    background-color: {PRIMARY_SOFT};
    border: 1px solid {BORDER};
    border-left: 3px solid {PRIMARY};
    border-radius: {RADIUS_SM}px;
}}
QFrame#noticeBanner[tone="success"] {{ background-color: {SUCCESS_SOFT}; border-left-color: {SUCCESS}; }}
QFrame#noticeBanner[tone="warning"] {{ background-color: {WARNING_SOFT}; border-left-color: {WARNING}; }}
QFrame#noticeBanner[tone="danger"] {{ background-color: {DANGER_SOFT}; border-left-color: {DANGER}; }}
QLabel#noticeBannerTitle {{ font-weight: bold; }}
QLabel#noticeBannerMessage {{ color: {MUTED_TEXT}; font-size: {FONT_SIZE_SM}px; }}

QFrame#dialogFooter {{
    background-color: {SURFACE};
    border-top: 1px solid {BORDER};
}}
"""
