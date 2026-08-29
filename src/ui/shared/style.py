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

RANK_GOLD = "#9a6a00"
RANK_GOLD_SOFT = "#fff5cf"
RANK_SILVER = "#66717e"
RANK_SILVER_SOFT = "#edf1f5"
RANK_BRONZE = "#925b35"
RANK_BRONZE_SOFT = "#f8eadf"

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


def refresh_style(widget) -> None:
    """让运行时动态属性立即参与 QSS 匹配。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def set_style_property(widget, name: str, value: object) -> None:
    """设置参与 QSS 匹配的动态属性并立即刷新。"""
    widget.setProperty(name, value)
    refresh_style(widget)


def set_ui_role(widget, role: str) -> None:
    """设置按钮等交互控件的视觉语义。"""
    set_style_property(widget, UI_ROLE_PROPERTY, role)


def set_tone(widget, tone: str) -> None:
    """设置状态标签、通知条等展示控件的语义色调。"""
    set_style_property(widget, TONE_PROPERTY, tone)


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
QTabWidget#workspaceTabs::pane {{
    background-color: {CANVAS};
    border: none;
    border-radius: 0;
}}
QTabWidget#librarySectionTabs::pane {{
    background-color: {CANVAS};
    border: none;
    border-top: 1px solid {BORDER};
    border-radius: 0;
}}
QTabWidget#librarySectionTabs QTabBar::tab {{
    background-color: transparent;
    color: {MUTED_TEXT};
    padding: 7px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: {RADIUS_SM}px {RADIUS_SM}px 0 0;
    margin-right: 4px;
    font-weight: normal;
}}
QTabWidget#librarySectionTabs QTabBar::tab:hover:!selected {{
    background-color: {SUBTLE_SURFACE};
    color: {TEXT_PRIMARY};
}}
QTabWidget#librarySectionTabs QTabBar::tab:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
    border-bottom: 2px solid {PRIMARY};
    font-weight: bold;
}}
QWidget#heroListPane {{
    background-color: transparent;
}}
QLabel#libraryResultCount {{
    background-color: transparent;
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
    padding: 0 2px;
}}
QListWidget#heroList {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
}}
QListWidget#heroList::item {{
    min-height: 30px;
    padding: 0 8px;
    border-left: 3px solid transparent;
}}
QListWidget#heroList::item:hover:!selected {{
    background-color: {SUBTLE_SURFACE};
}}
QListWidget#heroList::item:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
    border-left-color: {PRIMARY};
}}
QFrame#heroIdentityBar {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#heroIdentityBar QLabel {{
    background-color: transparent;
}}
QLabel#heroIdentityName {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
}}
QLabel#heroIdentityMeta {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QToolButton#heroContextMoreButton {{
    min-width: {CONTROL_HEIGHT_DEFAULT}px;
    max-width: {CONTROL_HEIGHT_DEFAULT}px;
    padding: 0;
    font-size: 18px;
}}
QTabWidget#heroDetailTabs::pane {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 0 0 {RADIUS_SM}px {RADIUS_SM}px;
}}
QTabWidget#heroDetailTabs QTabBar::tab {{
    background-color: transparent;
    color: {MUTED_TEXT};
    padding: 7px 12px;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: {RADIUS_SM}px {RADIUS_SM}px 0 0;
    margin-right: 4px;
    font-weight: normal;
}}
QTabWidget#heroDetailTabs QTabBar::tab:hover:!selected {{
    background-color: {SUBTLE_SURFACE};
    color: {TEXT_PRIMARY};
}}
QTabWidget#heroDetailTabs QTabBar::tab:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
    border-bottom-color: {PRIMARY};
    font-weight: bold;
}}
QWidget#heroInfoView, QWidget#heroGuideView, QWidget#heroSynergyView {{
    background-color: {SURFACE};
}}
QWidget#heroSkillsContent, QWidget#heroGuideContent {{
    background-color: {SURFACE};
}}
QWidget#heroInfoView QLabel, QWidget#heroGuideView QLabel, QWidget#heroSynergyView QLabel {{
    background-color: transparent;
}}
QLabel#heroBasicInfo {{
    color: {TEXT_PRIMARY};
    padding: 4px 2px;
}}
QFrame#contentDivider {{
    border: none;
    border-top: 1px solid {BORDER};
}}
QFrame#heroSkillCard {{
    background-color: transparent;
    border: none;
    border-bottom: 1px solid {BORDER};
}}
QLabel#contentSectionTitle {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
    padding-top: {SPACE_SM}px;
}}
QLabel#contentItemTitle {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_MD + 1}px;
    font-weight: bold;
}}
QLabel#contentBody {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_MD}px;
}}
QLabel#contentMeta {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QLabel#heroSettlementBody {{
    color: {MUTED_TEXT};
    padding: 6px 8px;
    border-left: 2px solid {BORDER_STRONG};
}}
QLabel#libraryEmptyState {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_MD}px;
    padding: {SPACE_XL}px;
}}
QFrame#guideSummarySurface {{
    background-color: {SUBTLE_SURFACE};
    border: none;
    border-radius: {RADIUS_SM}px;
}}
QLabel#guideWarningTitle {{
    color: {WARNING};
    font-weight: bold;
    padding-top: {SPACE_XS}px;
}}
QLabel#guideNotice {{
    color: {TEXT_PRIMARY};
    background-color: {WARNING_SOFT};
    border-left: 3px solid {WARNING};
    padding: {SPACE_SM}px;
}}
QLabel#synergyResultCount {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
    font-weight: bold;
}}
QWidget#cardManagementPanel {{
    background-color: {CANVAS};
}}
QWidget#cardListPane, QWidget#cardDetailContent {{
    background-color: transparent;
}}
QToolButton#cardFilterResetButton, QToolButton#cardMoreButton {{
    min-width: {CONTROL_HEIGHT_DEFAULT}px;
    max-width: {CONTROL_HEIGHT_DEFAULT}px;
    padding: 0;
}}
QToolButton#cardMoreButton {{
    font-size: 18px;
}}
QListWidget#cardList {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
}}
QListWidget#cardList::item {{
    padding: 0;
    border-left: 3px solid transparent;
}}
QListWidget#cardList::item:hover:!selected:enabled {{
    background-color: {SUBTLE_SURFACE};
}}
QListWidget#cardList::item:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
    border-left-color: {PRIMARY};
}}
QListWidget#cardList::item:disabled {{
    background-color: {SUBTLE_SURFACE};
    color: {PRIMARY};
    border-left-color: transparent;
}}
QWidget#cardListItem, QWidget#cardListItem QLabel {{
    background-color: transparent;
}}
QLabel#cardListItemName {{
    color: {TEXT_PRIMARY};
    font-weight: bold;
}}
QLabel#cardListItemMeta {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_XS}px;
}}
QFrame#cardDetailSurface {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#cardDetailSurface QLabel {{
    background-color: transparent;
}}
QLabel#cardIdentityName {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_PAGE_TITLE}px;
    font-weight: bold;
}}
QLabel#cardReadonlyMeta {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QLabel#cardDetailSectionTitle {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_MD}px;
    font-weight: bold;
}}
QLabel#cardDescription {{
    color: {TEXT_PRIMARY};
    background-color: {PRIMARY_SOFT};
    border-left: 3px solid {PRIMARY};
    border-radius: {RADIUS_SM}px;
    padding: 9px 11px;
}}
QLabel#cardRuleDetail {{
    color: {TEXT_PRIMARY};
    padding: 2px 0;
}}
QLabel#cardAdjustmentSectionTitle {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
    padding-top: {SPACE_XS}px;
}}
QLabel#cardSchemaError {{
    color: {DANGER};
    background-color: {DANGER_SOFT};
    border-left: 3px solid {DANGER};
    padding: {SPACE_MD}px;
}}
QLabel#cardAdjustmentEmpty {{
    color: {MUTED_TEXT};
    padding: {SPACE_LG}px 2px;
}}
QFrame#cardAdjustmentField {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-left: 3px solid {PRIMARY};
    border-radius: {RADIUS_SM}px;
}}
QFrame#cardAdjustmentField[adjustmentKind="strengthen"] {{ border-left-color: {SUCCESS}; }}
QFrame#cardAdjustmentField[adjustmentKind="weaken"] {{ border-left-color: {WARNING}; }}
QFrame#cardAdjustmentField[adjustmentKind="historical"] {{ border-left-color: {BORDER_STRONG}; }}
QLabel#cardAdjustmentTitle {{
    color: {PRIMARY};
    font-weight: bold;
}}
QLabel#cardAdjustmentTitle[adjustmentKind="strengthen"] {{ color: {SUCCESS}; }}
QLabel#cardAdjustmentTitle[adjustmentKind="weaken"] {{ color: {WARNING}; }}
QLabel#cardAdjustmentTitle[adjustmentKind="historical"] {{ color: {MUTED_TEXT}; }}
QLabel#cardEffectRecord {{
    color: {MUTED_TEXT};
    background-color: {SUBTLE_SURFACE};
    border-left: 3px solid {BORDER_STRONG};
    border-radius: {RADIUS_SM}px;
    padding: 7px 9px;
}}
QLabel#cardEffectRecord[tone="success"] {{
    color: {SUCCESS};
    background-color: {SUCCESS_SOFT};
    border-left-color: {SUCCESS};
}}
QLabel#cardEffectRecord[tone="warning"] {{
    color: {WARNING};
    background-color: {WARNING_SOFT};
    border-left-color: {WARNING};
}}
QLabel#cardEffectRecord[tone="neutral"] {{
    color: {MUTED_TEXT};
    background-color: {SUBTLE_SURFACE};
    border-left-color: {BORDER_STRONG};
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
QProgressBar[tone="success"]::chunk {{ background-color: {SUCCESS}; }}
QProgressBar[tone="danger"] {{ border-color: {DANGER}; }}
QProgressBar[tone="danger"]::chunk {{ background-color: {DANGER}; }}
QLabel#progressStatusLabel {{
    color: {TEXT_PRIMARY};
    font-size: 14px;
    font-weight: bold;
}}
QLabel#progressDetailLabel {{ color: {MUTED_TEXT}; font-size: {FONT_SIZE_SM}px; }}
QLabel#progressErrorLabel {{ color: {DANGER}; font-weight: bold; }}
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
QWidget#pageActionBar {{
    background-color: transparent;
}}
QLabel#pageActionStatus {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QLabel#pageActionStatus[tone="info"] {{ color: {PRIMARY}; }}
QLabel#pageActionStatus[tone="success"] {{ color: {SUCCESS}; }}
QLabel#pageActionStatus[tone="warning"] {{ color: {WARNING}; }}
QLabel#pageActionStatus[tone="danger"] {{ color: {DANGER}; }}
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

/* 共享语义：区块标题 / 辅助说明 / 内容卡（知识库维护各面板与索引精化共用） */
QLabel#sectionTitle {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
    font-weight: bold;
    padding-top: {SPACE_XS}px;
}}
QLabel#metaText {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QFrame#panelCardSurface {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#panelCardSurface QTableWidget {{
    border: none;
}}
QFrame#panelCardSurface QLabel, QWidget#emptyState QLabel {{
    background-color: transparent;
}}

/* 页面内分区页签（下划线选中态，与资料库二级页签同视觉） */
QTabWidget#sectionTabs::pane {{
    background-color: {CANVAS};
    border: none;
    border-top: 1px solid {BORDER};
    border-radius: 0;
}}
QTabWidget#sectionTabs QTabBar::tab {{
    background-color: transparent;
    color: {MUTED_TEXT};
    padding: 7px 14px;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: {RADIUS_SM}px {RADIUS_SM}px 0 0;
    margin-right: 4px;
    font-weight: normal;
}}
QTabWidget#sectionTabs QTabBar::tab:hover:!selected {{
    background-color: {SUBTLE_SURFACE};
    color: {TEXT_PRIMARY};
}}
QTabWidget#sectionTabs QTabBar::tab:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
    border-bottom: 2px solid {PRIMARY};
    font-weight: bold;
}}

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

QFrame#recommendationCard {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#recommendationCard:hover {{ border-color: {PRIMARY}; }}
QFrame#recommendationCard[cardState="pending"],
QFrame#recommendationCard[cardState="unknown"] {{
    background-color: {SUBTLE_SURFACE};
    border-color: {BORDER_STRONG};
}}
QFrame#recommendationCard[rank="1"] {{ border: 2px solid {RANK_GOLD}; }}
QFrame#recommendationCard[rank="2"] {{ border: 2px solid {RANK_SILVER}; }}
QFrame#recommendationCard[rank="3"] {{ border: 2px solid {RANK_BRONZE}; }}
QWidget#recommendationPortrait {{
    background-color: {SUBTLE_SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
}}
QLabel#recommendationHeroName {{
    background-color: rgba(31, 41, 51, 176);
    color: white;
    padding: 4px 2px;
    font-size: {FONT_SIZE_MD}px;
    font-weight: bold;
}}
QLabel#recommendationPosition {{ color: {MUTED_TEXT}; font-size: {FONT_SIZE_SM}px; }}
QPushButton#recommendationIndex {{
    min-height: 24px;
    padding: 0;
    background-color: transparent;
    color: {PRIMARY};
    border: none;
    text-align: left;
    font-weight: bold;
}}
QPushButton#recommendationIndex:hover {{ color: {PRIMARY_HOVER}; background-color: transparent; }}
QLabel#recommendationPartner {{ color: {PRIMARY}; font-size: {FONT_SIZE_SM}px; font-weight: bold; }}
QLabel#recommendationWinRate {{ color: {TEXT_PRIMARY}; font-size: {FONT_SIZE_SM}px; font-weight: bold; }}
QLabel#recommendationSynergyItem {{ color: {MUTED_TEXT}; font-size: {FONT_SIZE_XS}px; }}
QToolButton#recommendationSkillButton {{ min-width: 26px; max-width: 26px; padding: 0; }}
QPushButton#recommendationGuideButton {{
    min-width: 76px;
    padding: 0 8px;
    color: {PRIMARY};
    background-color: {PRIMARY_SOFT};
    border-color: {PRIMARY};
    font-weight: bold;
}}
QPushButton#recommendationGuideButton:hover {{
    color: white;
    background-color: {PRIMARY};
    border-color: {PRIMARY};
}}
QLabel#recommendationRankBadge {{
    color: {MUTED_TEXT};
    background-color: {SUBTLE_SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 1px 5px;
    font-size: {FONT_SIZE_XS}px;
    font-weight: bold;
}}
QFrame#recommendationCard[rank="1"] QLabel#recommendationRankBadge {{
    color: {RANK_GOLD}; background-color: {RANK_GOLD_SOFT}; border-color: {RANK_GOLD};
}}
QFrame#recommendationCard[rank="2"] QLabel#recommendationRankBadge {{
    color: {RANK_SILVER}; background-color: {RANK_SILVER_SOFT}; border-color: {RANK_SILVER};
}}
QFrame#recommendationCard[rank="3"] QLabel#recommendationRankBadge {{
    color: {RANK_BRONZE}; background-color: {RANK_BRONZE_SOFT}; border-color: {RANK_BRONZE};
}}

QFrame#matchHeroCard {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-left: 3px solid {BORDER_STRONG};
    border-radius: {RADIUS_MD}px;
}}
QFrame#matchHeroCard[side="ally"] {{
    background-color: {PRIMARY_SOFT};
    border-left-color: {PRIMARY};
}}
QFrame#matchHeroCard[side="enemy"] {{
    background-color: {DANGER_SOFT};
    border-left-color: {DANGER};
}}
QFrame#matchHeroCard[cardState="pending"],
QFrame#matchHeroCard[cardState="unknown"] {{
    background-color: {SUBTLE_SURFACE};
    border-left-color: {BORDER_STRONG};
}}
QWidget#matchPortraitFrame {{
    background-color: {SUBTLE_SURFACE};
    border: 1px solid {BORDER};
}}
QLabel#matchPortrait[portraitState="empty"], QLabel#matchPortrait[portraitState="text"] {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_XS}px;
}}
QLabel#matchHeroNameOverlay {{
    background-color: rgba(31, 41, 51, 176);
    color: white;
    font-size: {FONT_SIZE_MD}px;
    font-weight: bold;
}}
QLabel#matchHeroPosition {{ color: {MUTED_TEXT}; font-size: {FONT_SIZE_SM}px; }}
QLabel#matchHeroWinRate {{ color: {PRIMARY}; font-size: {FONT_SIZE_SM}px; font-weight: bold; }}
QWidget#sideSegment {{
    background-color: {SUBTLE_SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
}}
QPushButton#matchSideOption {{
    min-height: 28px;
    padding: 0 5px;
    background-color: transparent;
    color: {MUTED_TEXT};
    border: none;
    border-radius: 0;
    font-size: {FONT_SIZE_XS}px;
    font-weight: bold;
}}
QPushButton#matchSideOption:hover {{ background-color: {SURFACE_HOVER}; }}
QPushButton#matchSideOption[side="ally"]:checked {{ background-color: {PRIMARY}; color: white; }}
QPushButton#matchSideOption[side="enemy"]:checked {{ background-color: {DANGER}; color: white; }}
QPushButton#matchSideOption[side="pending"]:checked {{ background-color: {BORDER_STRONG}; color: white; }}
QPushButton#matchLeaderButton, QPushButton#matchReplaceButton {{
    min-height: 26px;
    padding: 0 6px;
    font-size: {FONT_SIZE_XS}px;
}}
QToolButton#matchMoreButton {{ min-width: 30px; max-width: 30px; padding: 0; }}
QFrame#matchConfirmationArea {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QLabel#matchValidationText, QLabel#matchLineupHint, QLabel#matchHeroSnapshot,
QLabel#matchPendingText {{ color: {MUTED_TEXT}; font-size: {FONT_SIZE_SM}px; }}
QLabel#matchSectionTitle, QLabel#matchAnalysisSectionTitle {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
}}
QLabel#matchLineupGroupTitle {{ color: {MUTED_TEXT}; font-size: {FONT_SIZE_MD}px; font-weight: bold; }}
QLabel#matchLineupGroupTitle[side="ally"] {{ color: {PRIMARY}; }}
QLabel#matchLineupGroupTitle[side="enemy"] {{ color: {DANGER}; }}
QFrame#matchPriorityItem {{
    background-color: {PRIMARY_SOFT};
    border: 1px solid {BORDER};
    border-left: 3px solid {PRIMARY};
    border-radius: {RADIUS_SM}px;
}}
QLabel#matchPriorityNumber {{
    background-color: {PRIMARY};
    color: white;
    border-radius: 12px;
    font-weight: bold;
}}
QLabel#matchPriorityText {{ color: {TEXT_PRIMARY}; font-weight: bold; }}
QFrame#matchSummaryBlock, QFrame#matchGuideBlock, QFrame#matchDetailRow {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#matchSummaryBlock[side="ally"] {{ background-color: {PRIMARY_SOFT}; border-left: 3px solid {PRIMARY}; }}
QFrame#matchSummaryBlock[side="enemy"] {{ background-color: {DANGER_SOFT}; border-left: 3px solid {DANGER}; }}
QLabel#matchSummaryTitle, QLabel#matchGuideTitle {{ font-size: 14px; font-weight: bold; }}
QLabel#matchSummaryTitle[side="ally"] {{ color: {PRIMARY}; }}
QLabel#matchSummaryTitle[side="enemy"], QLabel#matchThreatText,
QLabel#matchStrategyText {{ color: {DANGER}; }}
QLabel#matchGuideMissing {{ color: {MUTED_TEXT}; }}
QLabel#matchGuideTips {{ color: {PRIMARY}; }}
QPushButton#matchMissingToggle, QPushButton#matchGuideDetailButton {{
    min-height: 26px;
    padding: 0 7px;
    font-size: {FONT_SIZE_XS}px;
}}
QFrame#noticeBanner[noticeRole="missingData"] {{
    background-color: {SUBTLE_SURFACE};
    border-left-color: {BORDER_STRONG};
}}

QFrame#dialogFooter {{
    background-color: {SURFACE};
    border-top: 1px solid {BORDER};
}}
QLabel#toastOverlay {{
    min-width: 220px;
    max-width: 520px;
    padding: 9px 14px;
    color: white;
    background-color: {TEXT_PRIMARY};
    border: 1px solid {TEXT_PRIMARY};
    border-radius: {RADIUS_MD}px;
    font-weight: bold;
}}
QLabel#toastOverlay[tone="success"] {{
    background-color: {SUCCESS};
    border-color: {SUCCESS};
}}
QLabel#toastOverlay[tone="warning"] {{
    background-color: {WARNING};
    border-color: {WARNING};
}}
QLabel#toastOverlay[tone="danger"] {{
    background-color: {DANGER};
    border-color: {DANGER};
}}

/* === 专属牌维护（对齐卡牌图鉴/武将资料） === */
QWidget#specialCardsPanel {{
    background-color: {CANVAS};
}}
QWidget#specialCardListPane, QWidget#specialCardDetailContent {{
    background-color: transparent;
}}
QListWidget#specialCardList {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
}}
QListWidget#specialCardList::item {{
    padding: 0;
    border-left: 3px solid transparent;
}}
QListWidget#specialCardList::item:hover:!selected {{
    background-color: {SUBTLE_SURFACE};
}}
QListWidget#specialCardList::item:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
    border-left-color: {PRIMARY};
}}
QWidget#specialCardListItem, QWidget#specialCardListItem QLabel {{
    background-color: transparent;
}}
QLabel#specialCardListItemName {{
    color: {TEXT_PRIMARY};
    font-weight: bold;
}}
QLabel#specialCardListItemMeta {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_XS}px;
}}
QFrame#specialCardDetailSurface {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#specialCardDetailSurface QLabel {{
    background-color: transparent;
}}
QLabel#specialCardFieldBody {{
    color: {TEXT_PRIMARY};
    background-color: {PRIMARY_SOFT};
    border-left: 3px solid {PRIMARY};
    border-radius: {RADIUS_SM}px;
    padding: 9px 11px;
}}
QLabel#specialCardFieldSingle {{
    color: {TEXT_PRIMARY};
    padding: 2px 0;
}}

/* === 知识库维护（对齐选将推荐/对局攻略工作台） === */
QWidget#ragMaintenancePanel {{
    background-color: transparent;
}}
QFrame#actionBarDivider {{
    border: none;
    border-left: 1px solid {BORDER};
    margin: 2px 4px;
}}
QPlainTextEdit#scriptLog {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 6px;
    font-family: Consolas, "Courier New", monospace;
    font-size: {FONT_SIZE_SM}px;
    color: {TEXT_PRIMARY};
}}

/* === 索引精化（对话框） === */
QWidget#indexRefineDialog {{
    background-color: {CANVAS};
}}
QFrame#indexRefineOverview {{
    background-color: {SUBTLE_SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QLabel#indexRefineOverviewText {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_SM}px;
}}
QLabel#indexRefineFilterLabel {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QFrame#indexRefineListPane, QFrame#indexRefineWorkPane {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QTableWidget#indexRefineTable {{
    background-color: {SURFACE};
    border: none;
    border-radius: {RADIUS_SM}px;
    gridline-color: {BORDER};
}}
QTableWidget#indexRefineTable::item {{
    padding: 6px 8px;
    border: none;
}}
QTableWidget#indexRefineTable::item:hover:!selected {{
    background-color: {SUBTLE_SURFACE};
}}
QTableWidget#indexRefineTable::item:selected {{
    background-color: {PRIMARY_SOFT};
    color: {PRIMARY};
}}
QTableWidget#indexRefineTable QHeaderView::section {{
    background-color: {SUBTLE_SURFACE};
    color: {TEXT_PRIMARY};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    font-weight: bold;
}}
QLabel#indexRefineItemTitle {{
    font-size: {FONT_SIZE_LG}px;
    font-weight: bold;
    color: {TEXT_PRIMARY};
}}
QLabel#indexRefineItemMeta {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
}}
QFrame#indexRefineSourceCard, QFrame#indexRefineFieldCard {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QFrame#indexRefineFieldCard[fieldState="llm"] {{
    border-left: 3px solid {PRIMARY};
    background-color: {PRIMARY_SOFT};
}}
QFrame#indexRefineFieldCard[fieldState="saved"] {{
    border-left: 3px solid {BORDER_STRONG};
    background-color: {SUBTLE_SURFACE};
}}
QFrame#indexRefineFieldCard[fieldState="manual"] {{
    border-left: 3px solid {SUCCESS};
    background-color: {SUCCESS_SOFT};
}}
QFrame#indexRefineFieldCard[fieldState="empty"] {{
    border-left: 3px solid {BORDER};
}}
QLabel#indexRefineFieldName {{
    font-weight: bold;
    color: {TEXT_PRIMARY};
}}
QPlainTextEdit#indexRefineSource, QPlainTextEdit#indexRefineFieldEditor {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px;
    padding: 6px;
}}
QPlainTextEdit#indexRefineSource {{
    font-size: {FONT_SIZE_SM}px;
}}

/* === 知识库维护：布局重排（左栏维护对象导航 + 折叠执行日志） === */
QFrame#maintenanceSourceNav {{
    background-color: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: {RADIUS_MD}px;
}}
QScrollArea#maintenanceNavScroll {{
    background-color: transparent;
    border: none;
}}
QLabel#maintenanceNavGroup, QLabel#maintenanceNavGroupCount {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
    font-weight: bold;
    background-color: transparent;
}}
QFrame#maintenanceNavItem {{
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: {RADIUS_SM}px;
}}
QFrame#maintenanceNavItem:hover {{
    background-color: {SUBTLE_SURFACE};
}}
QFrame#maintenanceNavItem[selected="true"] {{
    background-color: {PRIMARY_SOFT};
    border-left: 3px solid {PRIMARY};
}}
QLabel#maintenanceItemName {{
    color: {TEXT_PRIMARY};
    font-size: {FONT_SIZE_MD}px;
    background-color: transparent;
}}
QFrame#maintenanceNavItem[selected="true"] QLabel#maintenanceItemName {{
    color: {PRIMARY};
    font-weight: bold;
}}
QLabel#maintenanceStatusDot {{
    min-width: 8px;
    max-width: 8px;
    min-height: 8px;
    max-height: 8px;
    border-radius: 4px;
    background-color: {MUTED_TEXT};
}}
QLabel#maintenanceStatusDot[tone="warning"] {{ background-color: {WARNING}; }}
QLabel#maintenanceStatusDot[tone="danger"] {{ background-color: {DANGER}; }}
QLabel#maintenanceStatusText {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
    background-color: transparent;
}}
QLabel#maintenanceStatusText[tone="warning"] {{ color: {WARNING}; }}
QLabel#maintenanceStatusText[tone="danger"] {{ color: {DANGER}; }}
QToolButton#maintenanceRebuildButton {{
    min-width: 24px;
    max-width: 24px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    font-size: {FONT_SIZE_MD}px;
}}
QFrame#scriptLogCollapsed {{
    background-color: transparent;
    border: none;
}}
QLabel#scriptLogTitle {{
    font-weight: bold;
    background-color: transparent;
}}
QLabel#scriptLogMeta {{
    color: {MUTED_TEXT};
    font-size: {FONT_SIZE_SM}px;
    background-color: transparent;
}}
QPushButton#scriptLogToggleButton {{
    min-height: 22px;
    padding: 0 10px;
    font-size: {FONT_SIZE_SM}px;
}}"""
