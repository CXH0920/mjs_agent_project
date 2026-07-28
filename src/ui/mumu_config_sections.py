"""MuMu 配置对话框的独立视图区块。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

PRIMARY_BUTTON_STYLE = (
    "QPushButton { background-color: #438ed3; color: white; border: none; "
    "border-radius: 4px; padding: 5px 10px; }"
    "QPushButton:hover { background-color: #347dc0; }"
    "QPushButton:disabled { background-color: #c8d4df; color: #f7f9fb; }"
)
OUTLINE_BUTTON_STYLE = (
    "QPushButton { background-color: transparent; color: #3578b7; "
    "border: 1px solid #8bb8df; border-radius: 4px; padding: 5px 10px; }"
    "QPushButton:hover { background-color: #eaf4fd; }"
)


class MumuDeviceSection(QGroupBox):
    """设备连接视图，只负责控件和用户操作信号。"""

    browse_requested = Signal()
    detect_requested = Signal()
    refresh_requested = Signal()
    connect_requested = Signal()
    test_requested = Signal()
    device_changed = Signal(int)
    device_activated = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__("🔗 设备连接", parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(8)

        self.adb_path_edit = QLineEdit()
        self.adb_path_edit.setReadOnly(True)
        self.adb_path_edit.setMinimumWidth(400)
        self.adb_path_edit.setPlaceholderText("请选择 adb.exe")
        self.adb_path_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #c8d0d8; padding: 5px 8px; "
            "background-color: #fafbfc; border-radius: 4px; }"
        )
        browse_button = QPushButton("浏览")
        browse_button.setFixedWidth(80)
        browse_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        browse_button.clicked.connect(self.browse_requested)
        self.detect_button = QPushButton("自动探测")
        self.detect_button.setFixedWidth(80)
        self.detect_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        self.detect_button.clicked.connect(self.detect_requested)
        layout.addWidget(QLabel("ADB 路径"), 0, 0)
        layout.addWidget(self.adb_path_edit, 0, 1)
        layout.addWidget(browse_button, 0, 2)
        layout.addWidget(self.detect_button, 0, 3)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(400)
        self.device_combo.currentIndexChanged.connect(self.device_changed)
        self.device_combo.activated.connect(self.device_activated)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setFixedWidth(60)
        self.refresh_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        self.refresh_button.clicked.connect(self.refresh_requested)
        layout.addWidget(QLabel("目标设备"), 1, 0)
        layout.addWidget(self.device_combo, 1, 1, 1, 2)
        layout.addWidget(self.refresh_button, 1, 3)

        self.port_label = QLabel("(自动探测)")
        self.port_label.setStyleSheet("color: #555;")
        layout.addWidget(QLabel("ADB 端口"), 2, 0)
        layout.addWidget(self.port_label, 2, 1)

        action_row = QHBoxLayout()
        self.connect_button = QPushButton("连接")
        self.connect_button.setFixedWidth(100)
        self.connect_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.connect_button.clicked.connect(self.connect_requested)
        self.test_button = QPushButton("测试连接")
        self.test_button.setFixedWidth(100)
        self.test_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        self.test_button.clicked.connect(self.test_requested)
        action_row.addStretch()
        action_row.addWidget(self.connect_button)
        action_row.addWidget(self.test_button)
        layout.addLayout(action_row, 2, 2, 1, 2)

        self.instance_status_label = QLabel("● 实例：未探测")
        self.status_label = QLabel("● ADB：未配置")
        for status_label in (self.instance_status_label, self.status_label):
            status_label.setStyleSheet("color: #777; font-size: 12px;")
        status_row = QHBoxLayout()
        status_row.addStretch()
        status_row.addWidget(self.instance_status_label)
        status_row.addSpacing(16)
        status_row.addWidget(self.status_label)
        layout.addLayout(status_row, 3, 0, 1, 4)
        layout.setColumnStretch(1, 1)


class MumuTemplateSection(QGroupBox):
    """OCR 模板状态和即时操作视图。"""

    hero_select_requested = Signal()
    hero_make_requested = Signal()
    match_guide_select_requested = Signal()
    match_guide_make_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__("🖼️ 识别模板管理", parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(8)
        hero_box, hero_controls = self._template_box("武将识别模板", True)
        match_box, match_controls = self._template_box("对局攻略模板", False)
        layout.addWidget(hero_box, 0, 0)
        layout.addWidget(match_box, 0, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnStretch(1, 1)

        self.hero_status_icon, self.hero_status_label, self.hero_select_button, self.hero_make_button = hero_controls
        (
            self.match_guide_status_icon,
            self.match_guide_status_label,
            self.match_guide_select_button,
            self.match_guide_make_button,
        ) = match_controls

    def _template_box(self, title: str, is_hero: bool) -> tuple[QGroupBox, tuple[QLabel, QLabel, QPushButton, QPushButton]]:
        box = QGroupBox(title)
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 8, 8, 8)
        status_row = QHBoxLayout()
        status_icon = QLabel("○")
        status_icon.setStyleSheet("color: #888; font-size: 16px;")
        status_label = QLabel("未设定")
        status_label.setStyleSheet("color: #888; font-size: 13px;")
        status_row.addWidget(status_icon)
        status_row.addWidget(status_label, 1)
        box_layout.addLayout(status_row)

        button_row = QHBoxLayout()
        select_button = QPushButton("📁选择模板")
        select_button.setFixedWidth(90)
        make_button = QPushButton("🎯制作模板")
        make_button.setFixedWidth(90)
        for button in (select_button, make_button):
            button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        if is_hero:
            select_button.clicked.connect(self.hero_select_requested)
            make_button.clicked.connect(self.hero_make_requested)
        else:
            select_button.clicked.connect(self.match_guide_select_requested)
            make_button.clicked.connect(self.match_guide_make_requested)
        button_row.addWidget(select_button)
        button_row.addWidget(make_button)
        button_row.addStretch()
        box_layout.addLayout(button_row)
        return box, (status_icon, status_label, select_button, make_button)


class MumuOcrPollingSection(QGroupBox):
    """OCR 开关、轮询参数和识别区域视图。"""

    poll_mode_changed = Signal(bool)
    resume_requested = Signal()
    roi_capture_requested = Signal(str)
    roi_image_requested = Signal(str)
    roi_reset_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__("⚙️ 识别参数", parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        switch_row = QHBoxLayout()
        self.ocr_enabled_check = QCheckBox("启用武将识别")
        self.poll_mode_check = QCheckBox("持续轮询")
        self.auto_switch_tab_check = QCheckBox("识别后自动跳转到结果页面")
        self.poll_mode_check.toggled.connect(self.poll_mode_changed)
        switch_row.addWidget(self.ocr_enabled_check)
        switch_row.addSpacing(16)
        switch_row.addWidget(self.poll_mode_check)
        switch_row.addSpacing(16)
        switch_row.addWidget(self.auto_switch_tab_check)
        switch_row.addWidget(QLabel("检测间隔"))
        self.poll_interval_spin = QSpinBox()
        self.poll_interval_spin.setRange(1, 60)
        self.poll_interval_spin.setSuffix(" 秒")
        self.poll_interval_spin.setFixedWidth(80)
        switch_row.addWidget(self.poll_interval_spin)
        self.resume_button = QPushButton("恢复轮询")
        self.resume_button.setFixedWidth(80)
        self.resume_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.resume_button.clicked.connect(self.resume_requested)
        switch_row.addWidget(self.resume_button)
        switch_row.addStretch()
        layout.addLayout(switch_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(divider)

        parameter_grid = QGridLayout()
        parameter_grid.setHorizontalSpacing(24)
        parameter_grid.setVerticalSpacing(8)
        parameter_grid.addWidget(QLabel("武将识别"), 0, 0)
        parameter_grid.addWidget(QLabel("对局攻略识别"), 0, 2)
        self.threshold_spin = self._make_threshold_spin()
        self.match_guide_threshold_spin = self._make_threshold_spin()
        self.hero_cooldown_spin = self._make_cooldown_spin()
        parameter_grid.addWidget(QLabel("匹配阈值"), 1, 0)
        parameter_grid.addWidget(self.threshold_spin, 1, 1)
        parameter_grid.addWidget(QLabel("匹配阈值"), 1, 2)
        parameter_grid.addWidget(self.match_guide_threshold_spin, 1, 3)
        parameter_grid.addWidget(QLabel("选择冷却"), 2, 0)
        parameter_grid.addWidget(self.hero_cooldown_spin, 2, 1)
        parameter_grid.addWidget(QLabel("识别区域"), 3, 0)
        hero_roi_row, hero_buttons = self._roi_button_row("hero_selection")
        match_roi_row, match_buttons = self._roi_button_row("match_guide")
        parameter_grid.addLayout(hero_roi_row, 3, 1)
        parameter_grid.addWidget(QLabel("识别区域"), 3, 2)
        parameter_grid.addLayout(match_roi_row, 3, 3)
        parameter_grid.setColumnStretch(1, 1)
        parameter_grid.setColumnStretch(3, 1)
        layout.addLayout(parameter_grid)

        self.hero_roi_capture_button, self.hero_roi_image_button, self.hero_roi_reset_button = hero_buttons
        (
            self.match_guide_roi_capture_button,
            self.match_guide_roi_image_button,
            self.match_guide_roi_reset_button,
        ) = match_buttons

    def _roi_button_row(self, page_type: str) -> tuple[QHBoxLayout, tuple[QPushButton, QPushButton, QPushButton]]:
        row = QHBoxLayout()
        capture_button = QPushButton("截图编辑")
        image_button = QPushButton("图片编辑")
        reset_button = QPushButton("恢复默认")
        capture_button.clicked.connect(lambda: self.roi_capture_requested.emit(page_type))
        image_button.clicked.connect(lambda: self.roi_image_requested.emit(page_type))
        reset_button.clicked.connect(lambda: self.roi_reset_requested.emit(page_type))
        row.addWidget(capture_button)
        row.addWidget(image_button)
        row.addWidget(reset_button)
        return row, (capture_button, image_button, reset_button)

    @staticmethod
    def _make_threshold_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.1, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setFixedWidth(100)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        MumuOcrPollingSection._style_parameter_spin(spin)
        return spin

    @staticmethod
    def _make_cooldown_spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 3600)
        spin.setSuffix(" 秒")
        spin.setFixedWidth(100)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        MumuOcrPollingSection._style_parameter_spin(spin)
        return spin

    @staticmethod
    def _style_parameter_spin(spin: QSpinBox | QDoubleSpinBox) -> None:
        spin.setStyleSheet(
            "QSpinBox, QDoubleSpinBox { "
            "border: 1px solid #c8d0d8; border-radius: 4px; "
            "padding: 4px 8px; background-color: #fafbfc; "
            "color: #2c3e50; }"
            "QSpinBox:hover, QDoubleSpinBox:hover { border-color: #7fb1dc; }"
            "QSpinBox:focus, QDoubleSpinBox:focus { "
            "border: 1px solid #438ed3; background-color: #ffffff; }"
        )
