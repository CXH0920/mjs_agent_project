"""MuMu 配置对话框的独立视图区块。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
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


class MumuDeviceSection(QFrame):
    """设备连接视图，只负责控件和用户操作信号。"""

    browse_requested = Signal()
    detect_requested = Signal()
    refresh_requested = Signal()
    connect_requested = Signal()
    test_requested = Signal()
    device_changed = Signal(int)
    device_activated = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("mumuDevicePage")
        layout = QGridLayout(self)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(12)

        title = QLabel("设备与连接")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        helper = QLabel("选择正在运行的模拟器实例，并配置用于连接的 ADB。")
        helper.setStyleSheet("color: #65758b; font-size: 12px;")
        layout.addWidget(title, 0, 0, 1, 4)
        layout.addWidget(helper, 1, 0, 1, 4)

        self.device_combo = QComboBox()
        self.device_combo.currentIndexChanged.connect(self.device_changed)
        self.device_combo.activated.connect(self.device_activated)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.setFixedWidth(72)
        self.refresh_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        self.refresh_button.clicked.connect(self.refresh_requested)
        layout.addWidget(QLabel("当前设备"), 2, 0)
        layout.addWidget(self.device_combo, 2, 1, 1, 2)
        layout.addWidget(self.refresh_button, 2, 3)

        self.port_label = QLabel("(自动探测)")
        self.port_label.setStyleSheet("color: #4a6a8a;")
        self.instance_status_label = QLabel("● 实例：未探测")
        self.instance_status_label.setStyleSheet("color: #65758b; font-size: 12px;")
        device_meta = QHBoxLayout()
        device_meta.addWidget(QLabel("ADB 端口"))
        device_meta.addWidget(self.port_label)
        device_meta.addSpacing(16)
        device_meta.addWidget(self.instance_status_label)
        device_meta.addStretch()
        layout.addLayout(device_meta, 3, 1, 1, 3)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #dce3ea;")
        layout.addWidget(divider, 4, 0, 1, 4)

        self.adb_path_edit = QLineEdit()
        self.adb_path_edit.setReadOnly(True)
        self.adb_path_edit.setPlaceholderText("请选择 adb.exe")
        self.adb_path_edit.setStyleSheet(
            "QLineEdit { border: 1px solid #c8d0d8; padding: 5px 8px; "
            "background-color: #fafbfc; border-radius: 4px; }"
        )
        browse_button = QPushButton("浏览")
        browse_button.setFixedWidth(72)
        browse_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        browse_button.clicked.connect(self.browse_requested)
        self.detect_button = QPushButton("自动探测")
        self.detect_button.setFixedWidth(88)
        self.detect_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        self.detect_button.clicked.connect(self.detect_requested)
        layout.addWidget(QLabel("ADB 路径"), 5, 0)
        layout.addWidget(self.adb_path_edit, 5, 1)
        layout.addWidget(browse_button, 5, 2)
        layout.addWidget(self.detect_button, 5, 3)

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
        layout.addLayout(action_row, 6, 0, 1, 4)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(7, 1)


class MumuTemplateSection(QFrame):
    """OCR 模板状态和即时操作视图。"""

    hero_select_requested = Signal()
    hero_make_requested = Signal()
    match_guide_select_requested = Signal()
    match_guide_make_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._workflow_layout = QGridLayout(self)
        self._workflow_layout.setContentsMargins(0, 0, 0, 0)
        self._workflow_layout.setHorizontalSpacing(12)
        self._workflow_layout.setVerticalSpacing(12)
        self._hero_box, hero_controls, self._hero_parameter_layout = self._template_box("武将选择识别", True)
        self._match_box, match_controls, self._match_parameter_layout = self._template_box("对局攻略识别", False)
        self._compact_layout: bool | None = None
        self._set_compact_layout(True)

        self.hero_status_icon, self.hero_status_label, self.hero_select_button, self.hero_make_button = hero_controls
        (
            self.match_guide_status_icon,
            self.match_guide_status_label,
            self.match_guide_select_button,
            self.match_guide_make_button,
        ) = match_controls

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._set_compact_layout(event.size().width() < 680)

    def _set_compact_layout(self, compact: bool) -> None:
        if compact == self._compact_layout:
            return
        self._compact_layout = compact
        self._workflow_layout.removeWidget(self._hero_box)
        self._workflow_layout.removeWidget(self._match_box)
        self._workflow_layout.addWidget(self._hero_box, 0, 0)
        self._workflow_layout.addWidget(self._match_box, 1 if compact else 0, 0 if compact else 1)
        self._workflow_layout.setColumnStretch(0, 1)
        self._workflow_layout.setColumnStretch(1, 0 if compact else 1)

    def _template_box(
        self, title: str, is_hero: bool,
    ) -> tuple[QFrame, tuple[QLabel, QLabel, QPushButton, QPushButton], QGridLayout]:
        box = QFrame()
        box.setObjectName("recognitionWorkflow")
        box.setStyleSheet(
            "QFrame#recognitionWorkflow { background: #ffffff; border: 1px solid #d3dde7; "
            "border-radius: 6px; }"
        )
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(14, 12, 14, 14)
        box_layout.setSpacing(9)
        heading = QLabel(title)
        heading.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        box_layout.addWidget(heading)
        status_row = QHBoxLayout()
        status_icon = QLabel("○")
        status_icon.setStyleSheet("color: #888; font-size: 16px;")
        status_label = QLabel("未设定")
        status_label.setStyleSheet("color: #888; font-size: 13px;")
        status_row.addWidget(status_icon)
        status_row.addWidget(status_label, 1)
        box_layout.addLayout(status_row)

        button_row = QHBoxLayout()
        select_button = QPushButton("选择模板")
        make_button = QPushButton("制作模板")
        select_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        make_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
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

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #e1e7ed;")
        box_layout.addWidget(divider)
        parameter_layout = QGridLayout()
        parameter_layout.setHorizontalSpacing(8)
        parameter_layout.setVerticalSpacing(8)
        box_layout.addLayout(parameter_layout)
        return box, (status_icon, status_label, select_button, make_button), parameter_layout

    def attach_parameters(
        self,
        hero_threshold: QDoubleSpinBox,
        hero_cooldown: QSpinBox,
        hero_roi_row: QHBoxLayout,
        match_threshold: QDoubleSpinBox,
        match_roi_row: QHBoxLayout,
    ) -> None:
        """把每项识别参数放回对应的任务面板，避免跨区查找。"""
        self._hero_parameter_layout.addWidget(QLabel("匹配阈值"), 0, 0)
        self._hero_parameter_layout.addWidget(hero_threshold, 0, 1)
        self._hero_parameter_layout.addWidget(QLabel("选择冷却"), 1, 0)
        self._hero_parameter_layout.addWidget(hero_cooldown, 1, 1)
        self._hero_parameter_layout.addWidget(QLabel("识别区域"), 2, 0)
        self._hero_parameter_layout.addLayout(hero_roi_row, 2, 1)
        self._hero_parameter_layout.setColumnStretch(1, 1)

        self._match_parameter_layout.addWidget(QLabel("匹配阈值"), 0, 0)
        self._match_parameter_layout.addWidget(match_threshold, 0, 1)
        self._match_parameter_layout.addWidget(QLabel("识别区域"), 1, 0)
        self._match_parameter_layout.addLayout(match_roi_row, 1, 1)
        self._match_parameter_layout.setColumnStretch(1, 1)


class MumuOcrPollingSection(QFrame):
    """OCR 开关、轮询参数和识别区域视图。"""

    poll_mode_changed = Signal(bool)
    resume_requested = Signal()
    roi_capture_requested = Signal(str)
    roi_image_requested = Signal(str)
    roi_reset_requested = Signal(str)

    def __init__(self, template_section: MumuTemplateSection, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("mumuRecognitionPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(12)

        title = QLabel("识别与自动化")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        helper = QLabel("分别维护两种识别任务的模板、匹配参数和识别区域。")
        helper.setStyleSheet("color: #65758b; font-size: 12px;")
        layout.addWidget(title)
        layout.addWidget(helper)

        automation = QFrame()
        automation.setObjectName("recognitionAutomation")
        automation.setStyleSheet(
            "QFrame#recognitionAutomation { background: #ffffff; border: 1px solid #d3dde7; "
            "border-radius: 6px; }"
        )
        switch_grid = QGridLayout(automation)
        switch_grid.setContentsMargins(14, 10, 14, 10)
        switch_grid.setHorizontalSpacing(12)
        switch_grid.setVerticalSpacing(8)
        self.ocr_enabled_check = QCheckBox("启用 OCR 识别")
        self.poll_mode_check = QCheckBox("持续轮询")
        self.auto_switch_tab_check = QCheckBox("识别后自动跳转到结果页面")
        self.poll_mode_check.toggled.connect(self.poll_mode_changed)
        switch_grid.addWidget(self.ocr_enabled_check, 0, 0)
        switch_grid.addWidget(self.poll_mode_check, 0, 1)
        switch_grid.addWidget(QLabel("检测间隔"), 0, 2)
        self.poll_interval_spin = QSpinBox()
        self.poll_interval_spin.setRange(1, 60)
        self.poll_interval_spin.setSuffix(" 秒")
        self.poll_interval_spin.setFixedWidth(80)
        switch_grid.addWidget(self.poll_interval_spin, 0, 3)
        switch_grid.addWidget(self.auto_switch_tab_check, 1, 0, 1, 2)
        self.resume_button = QPushButton("恢复轮询")
        self.resume_button.setFixedWidth(80)
        self.resume_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        self.resume_button.clicked.connect(self.resume_requested)
        switch_grid.addWidget(self.resume_button, 1, 3)
        switch_grid.setColumnStretch(1, 1)
        layout.addWidget(automation)

        self.threshold_spin = self._make_threshold_spin()
        self.match_guide_threshold_spin = self._make_threshold_spin()
        self.hero_cooldown_spin = self._make_cooldown_spin()
        hero_roi_row, hero_buttons = self._roi_button_row("hero_selection")
        match_roi_row, match_buttons = self._roi_button_row("match_guide")
        template_section.attach_parameters(
            self.threshold_spin,
            self.hero_cooldown_spin,
            hero_roi_row,
            self.match_guide_threshold_spin,
            match_roi_row,
        )
        layout.addWidget(template_section)
        layout.addStretch(1)

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
        capture_button.setStyleSheet(PRIMARY_BUTTON_STYLE)
        image_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
        reset_button.setStyleSheet(OUTLINE_BUTTON_STYLE)
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
