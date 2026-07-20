"""
名将杀 Agent - 模拟器配置对话框

提供完整的模拟器（MuMu）ADB 连接管理和 OCR 模板配置。
位于 配置 → 模拟器配置 菜单入口。

功能：
  1. 连接管理 — 自动探测 ADB 路径和端口，多设备下拉切换，一键连接/断开，状态监控
  2. 模板管理 — 制作模板（截图+框选），选择模板，打开模板目录，状态显示
  3. OCR 配置 — 启用开关，匹配阈值
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QSignalBlocker, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QGridLayout,
)

from src.capture.adb_screen import AdbCapture
from src.capture.image_utils import pil_to_qpixmap
from src.capture.prober import probe_all_devices, probe_mumu_adb, test_adb_path, MuMuDeviceInfo

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_TEMPLATE_DIR = PROJECT_ROOT / "templates"


class MumuConfigDialog(QDialog):
    """模拟器配置对话框"""

    def __init__(self, config: dict, capture_service=None, ocr_service=None, parent=None):
        super().__init__(parent)
        self._config = dict(config)
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._capture: AdbCapture | None = capture_service.capture if capture_service else None
        self._devices: list[MuMuDeviceInfo] = []
        self._device_selected_explicitly = False
        self._template_path: str | None = None
        self._roi: tuple[int, int, int, int] | None = None
        self._screenshot_pixmap: QPixmap | None = None

        self.setWindowTitle("模拟器配置")
        self.setMinimumWidth(760)
        self.setMinimumHeight(620)
        self.resize(820, 680)
        self._setup_ui()
        if self._capture_service:
            self._capture_service.connection_changed.connect(self._on_connection_changed)
        self._load_config()

    def _setup_ui(self) -> None:
        """按设备、模板、参数三张卡片构建配置界面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(16)

        primary_style = (
            "QPushButton { background-color: #438ed3; color: white; border: none; "
            "border-radius: 4px; padding: 5px 10px; }"
            "QPushButton:hover { background-color: #347dc0; }"
            "QPushButton:disabled { background-color: #c8d4df; color: #f7f9fb; }"
        )
        outline_style = (
            "QPushButton { background-color: transparent; color: #3578b7; "
            "border: 1px solid #8bb8df; border-radius: 4px; padding: 5px 10px; }"
            "QPushButton:hover { background-color: #eaf4fd; }"
        )

        # 设备连接卡片：连接、探测和刷新属于即时操作。
        device_card = QGroupBox("🔗 设备连接")
        device_grid = QGridLayout(device_card)
        device_grid.setContentsMargins(12, 12, 12, 12)
        device_grid.setHorizontalSpacing(8)
        device_grid.setVerticalSpacing(8)

        self._adb_path_edit = QLineEdit()
        self._adb_path_edit.setReadOnly(True)
        self._adb_path_edit.setMinimumWidth(400)
        self._adb_path_edit.setPlaceholderText("请选择 adb.exe")
        self._adb_path_edit.setStyleSheet("QLineEdit { border: 1px solid #c8d0d8; padding: 5px 8px; background-color: #fafbfc; border-radius: 4px; }")
        browse_btn = QPushButton("浏览")
        browse_btn.setFixedWidth(80)
        browse_btn.setStyleSheet(outline_style)
        browse_btn.clicked.connect(self._browse_adb)
        detect_btn = QPushButton("自动探测")
        detect_btn.setFixedWidth(80)
        detect_btn.setStyleSheet(outline_style)
        detect_btn.clicked.connect(self._on_auto_detect)
        device_grid.addWidget(QLabel("ADB 路径"), 0, 0)
        device_grid.addWidget(self._adb_path_edit, 0, 1)
        device_grid.addWidget(browse_btn, 0, 2)
        device_grid.addWidget(detect_btn, 0, 3)

        self._device_combo = QComboBox()
        self._device_combo.setMinimumWidth(400)
        self._device_combo.currentIndexChanged.connect(self._on_device_changed)
        self._device_combo.activated.connect(self._on_device_activated)
        refresh_btn = QPushButton("刷新")
        refresh_btn.setFixedWidth(60)
        refresh_btn.setStyleSheet(outline_style)
        refresh_btn.clicked.connect(self._on_refresh_devices)
        device_grid.addWidget(QLabel("目标设备"), 1, 0)
        device_grid.addWidget(self._device_combo, 1, 1, 1, 2)
        device_grid.addWidget(refresh_btn, 1, 3)

        self._port_label = QLabel("(自动探测)")
        self._port_label.setStyleSheet("color: #555;")
        device_grid.addWidget(QLabel("ADB 端口"), 2, 0)
        device_grid.addWidget(self._port_label, 2, 1)

        action_row = QHBoxLayout()
        self._connect_btn = QPushButton("连接")
        self._connect_btn.setFixedWidth(100)
        self._connect_btn.setStyleSheet(primary_style)
        self._connect_btn.clicked.connect(self._on_connect_toggle)
        self._test_device_btn = QPushButton("测试连接")
        self._test_device_btn.setFixedWidth(100)
        self._test_device_btn.setStyleSheet(outline_style)
        self._test_device_btn.clicked.connect(self._on_test_selected_device)
        action_row.addStretch()
        action_row.addWidget(self._connect_btn)
        action_row.addWidget(self._test_device_btn)
        device_grid.addLayout(action_row, 2, 2, 1, 2)

        self._instance_status_label = QLabel("● 实例：未探测")
        self._status_label = QLabel("● ADB：未配置")
        for status_label in (self._instance_status_label, self._status_label):
            status_label.setStyleSheet("color: #777; font-size: 12px;")
        status_row = QHBoxLayout()
        status_row.addStretch()
        status_row.addWidget(self._instance_status_label)
        status_row.addSpacing(16)
        status_row.addWidget(self._status_label)
        device_grid.addLayout(status_row, 3, 0, 1, 4)
        device_grid.setColumnStretch(1, 1)
        layout.addWidget(device_card)

        # 模板管理卡片：选择/制作立即生效，不依赖底部保存。
        template_card = QGroupBox("🖼️ 识别模板管理")
        template_grid = QGridLayout(template_card)
        template_grid.setContentsMargins(12, 12, 12, 12)
        template_grid.setHorizontalSpacing(12)
        template_grid.setVerticalSpacing(8)
        template_grid.addWidget(self._template_box("武将模板", "hero"), 0, 0)
        template_grid.addWidget(self._template_box("对局攻略模板", "match_guide"), 0, 1)
        template_grid.setColumnStretch(0, 1)
        template_grid.setColumnStretch(1, 1)
        layout.addWidget(template_card)

        # 识别参数卡片：仅此区域需要点击保存。
        parameter_card = QGroupBox("⚙️ 识别参数")
        parameter_layout = QVBoxLayout(parameter_card)
        parameter_layout.setContentsMargins(12, 12, 12, 12)
        switch_row = QHBoxLayout()
        self._ocr_enabled_check = QCheckBox("启用武将识别")
        self._poll_mode_check = QCheckBox("持续轮询")
        self._poll_mode_check.toggled.connect(self._update_parameter_controls)
        switch_row.addWidget(self._ocr_enabled_check)
        switch_row.addSpacing(16)
        switch_row.addWidget(self._poll_mode_check)
        switch_row.addWidget(QLabel("检测间隔"))
        self._poll_interval_spin = QSpinBox()
        self._poll_interval_spin.setRange(1, 60)
        self._poll_interval_spin.setSuffix(" 秒")
        self._poll_interval_spin.setFixedWidth(80)
        switch_row.addWidget(self._poll_interval_spin)
        self._resume_poll_btn = QPushButton("恢复轮询")
        self._resume_poll_btn.setFixedWidth(80)
        self._resume_poll_btn.setStyleSheet(primary_style)
        self._resume_poll_btn.clicked.connect(self._on_resume_poll)
        switch_row.addWidget(self._resume_poll_btn)
        switch_row.addStretch()
        parameter_layout.addLayout(switch_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Sunken)
        parameter_layout.addWidget(divider)

        parameter_grid = QGridLayout()
        parameter_grid.setHorizontalSpacing(24)
        parameter_grid.setVerticalSpacing(8)
        parameter_grid.addWidget(QLabel("武将识别"), 0, 0)
        parameter_grid.addWidget(QLabel("对局攻略识别"), 0, 2)
        self._threshold_spin = self._make_threshold_spin()
        self._match_guide_threshold_spin = self._make_threshold_spin()
        self._hero_cooldown_spin = self._make_cooldown_spin()
        self._match_guide_cooldown_spin = self._make_cooldown_spin()
        parameter_grid.addWidget(QLabel("匹配阈值"), 1, 0)
        parameter_grid.addWidget(self._threshold_spin, 1, 1)
        parameter_grid.addWidget(QLabel("匹配阈值"), 1, 2)
        parameter_grid.addWidget(self._match_guide_threshold_spin, 1, 3)
        parameter_grid.addWidget(QLabel("选择冷却"), 2, 0)
        parameter_grid.addWidget(self._hero_cooldown_spin, 2, 1)
        parameter_grid.addWidget(QLabel("触发冷却"), 2, 2)
        parameter_grid.addWidget(self._match_guide_cooldown_spin, 2, 3)
        parameter_grid.setColumnStretch(1, 1)
        parameter_grid.setColumnStretch(3, 1)
        parameter_layout.addLayout(parameter_grid)
        layout.addWidget(parameter_card)
        layout.addStretch(1)

        # 底部操作栏：保存写入参数，取消不保存并关闭窗口。
        footer = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(80)
        cancel_btn.setStyleSheet(outline_style)
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("保存")
        save_btn.setFixedWidth(80)
        save_btn.setStyleSheet(primary_style)
        save_btn.clicked.connect(self._on_save)
        footer.addStretch()
        footer.addWidget(save_btn)
        footer.addWidget(cancel_btn)
        layout.addLayout(footer)

    def _template_box(self, title: str, template_name: str) -> QGroupBox:
        """创建单个模板卡片，保留旧控件属性供现有槽函数使用。"""
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
        select_btn = QPushButton("📁选择模板")
        select_btn.setFixedWidth(90)
        make_btn = QPushButton("制作模板...")
        make_btn.setFixedWidth(90)
        for button in (select_btn, make_btn):
            button.setStyleSheet(
                "QPushButton { background-color: #438ed3; color: white; border: none; "
                "border-radius: 4px; padding: 5px 8px; }"
                "QPushButton:hover { background-color: #347dc0; }"
                "QPushButton:disabled { background-color: #c8d4df; color: #f7f9fb; }"
            )
        if template_name == "hero":
            self._template_status_icon = status_icon
            self._template_status_label = status_label
            self._select_template_btn = select_btn
            self._make_template_btn = make_btn
            select_btn.clicked.connect(self._on_select_template)
            make_btn.clicked.connect(self._on_make_template)
        else:
            self._match_guide_status_label = status_label
            self._select_match_guide_template_btn = select_btn
            self._make_match_guide_template_btn = make_btn
            select_btn.clicked.connect(self._on_select_match_guide_template)
            make_btn.clicked.connect(self._on_make_match_guide_template)
        button_row.addWidget(select_btn)
        button_row.addWidget(make_btn)
        button_row.addStretch()
        box_layout.addLayout(button_row)
        return box

    @staticmethod
    def _make_threshold_spin() -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0.1, 1.0)
        spin.setSingleStep(0.05)
        spin.setDecimals(2)
        spin.setFixedWidth(100)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        MumuConfigDialog._style_parameter_spin(spin)
        return spin

    @staticmethod
    def _make_cooldown_spin() -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(1, 3600)
        spin.setSuffix(" 秒")
        spin.setFixedWidth(100)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        MumuConfigDialog._style_parameter_spin(spin)
        return spin

    @staticmethod
    def _style_parameter_spin(spin: QSpinBox) -> None:
        """统一阈值和冷却输入控件的视觉样式。"""
        spin.setStyleSheet(
            "QSpinBox, QDoubleSpinBox { "
            "border: 1px solid #c8d0d8; border-radius: 4px; "
            "padding: 4px 8px; background-color: #fafbfc; "
            "color: #2c3e50; }"
            "QSpinBox:hover, QDoubleSpinBox:hover { border-color: #7fb1dc; }"
            "QSpinBox:focus, QDoubleSpinBox:focus { "
            "border: 1px solid #438ed3; background-color: #ffffff; }"
        )

    # ────────────────────────────────────────────────
    # 加载配置
    # ────────────────────────────────────────────────

    def _load_config(self) -> None:
        """从配置加载当前值"""
        adb_path = self._config.get("mumu_adb_path", "")
        if not adb_path:
            detected = probe_mumu_adb()
            if detected:
                adb_path = detected

        self._adb_path_edit.setText(adb_path or "(未设置，点击「自动探测」)")
        self._adb_path_edit.setProperty("raw_path", adb_path)

        adb_port = self._config.get("mumu_adb_port", 0)
        self._port_label.setText(str(adb_port) if adb_port else "(自动探测)")

        self._ocr_enabled_check.setChecked(self._config.get("mumu_ocr_enabled", False))
        self._poll_mode_check.setChecked(self._config.get("mumu_ocr_poll_mode", False))
        self._poll_interval_spin.setValue(self._config.get("mumu_ocr_poll_interval", 2))
        self._threshold_spin.setValue(self._config.get("mumu_hero_selection_threshold", self._config.get("mumu_ocr_match_threshold", 0.8)))
        self._match_guide_threshold_spin.setValue(self._config.get("mumu_match_guide_threshold", 0.8))
        self._hero_cooldown_spin.setValue(self._config.get("mumu_hero_selection_cooldown", 180))
        self._match_guide_cooldown_spin.setValue(self._config.get("mumu_match_guide_cooldown", 5))

        # 创建 AdbCapture 实例（复用已有的连接）
        if self._capture_service and self._capture_service.capture:
            self._capture = self._capture_service.capture
        elif adb_path:
            self._capture = AdbCapture(adb_path=adb_path, adb_port=adb_port)

        # 刷新设备列表
        self._on_refresh_devices()

        # 检查模板状态
        self._refresh_template_status()
        self._refresh_match_guide_template_status()
        self._update_ui()

    def _refresh_template_status(self) -> None:
        """更新模板状态显示"""
        from src.ocr.ocr_loader import get_template_manager
        tm = get_template_manager()
        if tm.is_loaded:
            self._template_status_icon.setText("●")
            self._template_status_icon.setStyleSheet("color: #27ae60; font-size: 16px;")
            self._template_status_label.setText(f"已加载: {tm.template_path.name}")
            self._template_status_label.setStyleSheet("color: #27ae60; font-size: 13px;")
        else:
            self._template_status_icon.setText("○")
            self._template_status_icon.setStyleSheet("color: #888; font-size: 16px;")
            self._template_status_label.setText("未设定")
            self._template_status_label.setStyleSheet("color: #888; font-size: 13px;")

    def _refresh_match_guide_template_status(self) -> None:
        """更新对局攻略模板状态显示。"""
        from src.ocr.ocr_loader import get_template_manager
        tm = get_template_manager("match_guide")
        if tm.is_loaded:
            self._match_guide_status_label.setText(f"对局攻略模板：已加载 {tm.template_path.name}")
            self._match_guide_status_label.setStyleSheet("color: #27ae60; font-size: 13px;")
        else:
            self._match_guide_status_label.setText("对局攻略模板：未设定")
            self._match_guide_status_label.setStyleSheet("color: #888; font-size: 13px;")

    # ────────────────────────────────────────────────
    # ADB 连接管理
    # ────────────────────────────────────────────────

    def _on_auto_detect(self) -> None:
        """自动探测 ADB 路径和端口"""
        logger.info("开始自动探测 ADB...")

        # 探测 adb 路径
        adb_path = probe_mumu_adb()
        if adb_path:
            ok, msg = test_adb_path(adb_path)
            if ok:
                self._adb_path_edit.setText(adb_path)
                self._adb_path_edit.setProperty("raw_path", adb_path)
                self._adb_path_edit.setStyleSheet(
                    "border: 1px solid #27ae60; padding: 4px 8px; background-color: #f0faf0; border-radius: 3px;"
                )

                # 让共享服务使用当前草稿配置，连接状态由它统一发布
                self._sync_capture_service_config()
                self._on_refresh_devices()

                QMessageBox.information(self, "自动探测", f"找到 ADB:\n{adb_path}\n{msg}")
            else:
                self._adb_path_edit.setStyleSheet(
                    "border: 1px solid #e74c3c; padding: 4px 8px; background-color: #fdf0ef; border-radius: 3px;"
                )
                QMessageBox.warning(self, "自动探测", f"ADB 文件存在但验证失败:\n{msg}")
        else:
            self._adb_path_edit.setStyleSheet(
                "border: 1px solid #e74c3c; padding: 4px 8px; background-color: #fdf0ef; border-radius: 3px;"
            )
            QMessageBox.warning(self, "自动探测", "未找到 ADB，请手动设置路径")

    def _browse_adb(self) -> None:
        """弹出文件选择对话框选择 adb.exe"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ADB 可执行文件", "",
            "adb (*.exe);;所有文件 (*.*)"
        )
        if path:
            self._adb_path_edit.setText(path)
            self._adb_path_edit.setProperty("raw_path", path)
            self._adb_path_edit.setStyleSheet(
                "border: 1px solid #ccc; padding: 4px 8px; background-color: #f9f9f9; border-radius: 3px;"
            )
            self._sync_capture_service_config()
            self._on_refresh_devices()

    def _sync_capture_service_config(self) -> None:
        """将当前编辑中的 ADB 配置同步到共享截图服务。"""
        if not self._capture_service:
            return
        if self._capture_service:
            config = dict(self._config)
            config["mumu_adb_path"] = self._adb_path_edit.property("raw_path") or ""
            self._capture_service.update_config(config)
            self._capture = self._capture_service.capture
        else:
            self._capture = AdbCapture(
                adb_path=self._adb_path_edit.property("raw_path") or "",
                adb_port=self._config.get("mumu_adb_port", 0),
            )

    def _on_refresh_devices(self) -> None:
        """刷新设备列表，并避免在多实例时擅自选择目标。"""
        configured_port = self._config.get("mumu_adb_port", 0)
        self._devices = probe_all_devices()
        running_devices = [device for device in self._devices if device.is_running and device.adb_port]
        self._device_selected_explicitly = False

        with QSignalBlocker(self._device_combo):
            self._device_combo.clear()
            if not self._devices:
                self._device_combo.addItem("(未探测到设备)")
                self._device_combo.setEnabled(False)
                self._instance_status_label.setText("● 实例：未探测到")
                self._port_label.setText("(自动探测)")
                self._update_ui()
                return

            self._device_combo.setEnabled(True)
            for device in self._devices:
                running_text = "运行中" if device.is_running else "未运行"
                label = f"[{device.index}] {device.name}（{running_text}）"
                if device.adb_port:
                    label += f"  (端口:{device.adb_port})"
                self._device_combo.addItem(label, userData=device)

            target_index = next(
                (
                    index for index in range(self._device_combo.count())
                    if (device := self._device_combo.itemData(index)) and device.adb_port == configured_port
                ),
                -1,
            ) if configured_port else -1

            if target_index >= 0:
                self._device_combo.setCurrentIndex(target_index)
            elif configured_port == 0 and len(running_devices) == 1:
                target_index = next(
                    index for index in range(self._device_combo.count())
                    if self._device_combo.itemData(index) is running_devices[0]
                )
                self._device_combo.setCurrentIndex(target_index)
            elif configured_port == 0 and len(running_devices) > 1:
                self._device_combo.insertItem(0, "请选择运行中的实例", userData=None)
                self._device_combo.setCurrentIndex(0)
            else:
                self._device_combo.setCurrentIndex(0)

        self._on_device_changed(self._device_combo.currentIndex())
        self._update_ui()

    def _on_device_activated(self, index: int) -> None:
        """记录用户对实例的显式选择。"""
        if self._device_combo.itemData(index):
            self._device_selected_explicitly = True

    def _on_device_changed(self, index: int) -> None:
        """设备下拉选择变化"""
        if index < 0 or not self._devices:
            return

        device = self._device_combo.itemData(index)
        if device and device.adb_port:
            self._port_label.setText(str(device.adb_port))
            state = "运行中" if device.is_running else "未运行"
            self._instance_status_label.setText(f"● 实例：{state}")
        else:
            self._port_label.setText("(自动探测)")
            self._instance_status_label.setText("● 实例：未探测到")
        self._update_ui()

    def _on_connection_changed(self, _state: str, _detail: str) -> None:
        """共享 CaptureService 会话变化时刷新配置页。"""
        self._capture = self._capture_service.capture if self._capture_service else None
        self._update_ui()

    def _on_connect_toggle(self) -> None:
        """连接/断开切换"""
        if self._capture and self._capture.connected:
            self._disconnect_emulator()
        else:
            self._connect_emulator()

    def _connect_emulator(self) -> None:
        """连接选中的模拟器。"""
        if not self._capture_service:
            QMessageBox.warning(self, "连接失败", "截图服务不可用")
            return
        device = self._device_combo.currentData()
        running_devices = [item for item in self._devices if item.is_running and item.adb_port]
        if self._config.get("mumu_adb_port", 0) == 0 and len(running_devices) > 1 and not self._device_selected_explicitly:
            QMessageBox.warning(self, "请选择设备", "检测到多个运行中的 MuMu 实例，请先选择要连接的实例。")
            return
        if self._device_selected_explicitly and device and device.adb_port:
            self._capture_service.set_target_port(device.adb_port)
            self._capture = self._capture_service.capture

        self._connect_btn.setEnabled(False)
        QTimer.singleShot(50, self._do_connect)

    def _do_connect(self) -> None:
        """实际执行连接。"""
        if not self._capture_service:
            self._connect_btn.setEnabled(True)
            return
        ok, message = self._capture_service.connect_emulator()
        if not ok:
            QMessageBox.warning(self, "连接失败", message)
        self._connect_btn.setEnabled(True)
        self._update_ui()

    def _disconnect_emulator(self) -> None:
        """断开模拟器。"""
        if self._capture_service:
            self._capture_service.disconnect_emulator()
        self._update_ui()

    def _on_test_selected_device(self) -> None:
        """测试当前选择的设备连通性，不改变共享会话或配置。"""
        adb_path = self._adb_path_edit.property("raw_path") or ""
        device = self._device_combo.currentData()
        if not adb_path:
            QMessageBox.warning(self, "设备测试", "请先配置 ADB 路径。")
            return
        if not device or not device.adb_port:
            QMessageBox.warning(self, "设备测试", "请选择一个带有效 ADB 端口的实例后再测试。")
            return

        target = f"127.0.0.1:{device.adb_port}"
        self._test_device_btn.setEnabled(False)
        self._test_device_btn.setText("测试中...")
        QTimer.singleShot(0, lambda: self._do_test_selected_device(adb_path, device.adb_port, target))

    def _do_test_selected_device(self, adb_path: str, port: int, target: str) -> None:
        """执行精确目标的 connect + get-state 测试。"""
        try:
            shared = self._capture_service.capture if self._capture_service else None
            if shared and shared.connected and shared.device_serial == target:
                capture = shared
            else:
                capture = AdbCapture(adb_path=adb_path, adb_port=port)
            ok, message = capture.connect()
            if ok:
                ok, state = capture.check_device()
                message = state if ok else state
            if ok:
                QMessageBox.information(self, "设备测试成功", f"已连接到所选设备：\n{target}\n设备状态：device")
            else:
                QMessageBox.warning(self, "设备测试失败", f"无法连接所选设备 {target}：\n{message}")
        finally:
            self._test_device_btn.setEnabled(True)
            self._test_device_btn.setText("测试所选设备")
            self._update_ui()

    def _on_resume_poll(self) -> None:
        """恢复已暂停的 OCR 轮询。"""
        if self._ocr_service and self._ocr_service.poll_state == "paused":
            self._ocr_service.resume_poll()
            self._update_ui()


    def _on_make_template(self) -> None:
        """制作模板：截图 → 框选 → 保存"""
        if not self._capture or not self._capture.connected:
            # 尝试先连接
            if self._capture:
                ok, msg = self._capture.connect()
                if not ok:
                    QMessageBox.warning(self, "制作模板", f"请先连接模拟器\n{msg}")
                    return
            else:
                QMessageBox.warning(self, "制作模板", "请先配置 ADB 并连接模拟器")
                return

        self._make_template_btn.setEnabled(False)
        self._make_template_btn.setText("正在截图...")
        QTimer.singleShot(50, self._do_make_template)

    def _do_make_template(self) -> None:
        """实际执行模板制作"""
        try:
            ok, result = self._capture.screencap_full()
            if not ok:
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                QMessageBox.warning(self, "制作模板", f"截图失败:\n{result}")
                return

            image = result

            # 转为 QPixmap 显示
            pixmap = pil_to_qpixmap(image)
            if pixmap.isNull():
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                QMessageBox.warning(self, "制作模板", "图像转换失败")
                return

            # 打开框选对话框
            from src.ui.roi_selector import RoiSelectorDialog
            dialog = RoiSelectorDialog(pixmap, title="框选模板区域（如页面标题或按钮）", parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                return

            roi = dialog.get_roi()
            if not roi:
                self._make_template_btn.setEnabled(True)
                self._make_template_btn.setText("🎯 制作模板")
                return

            # 保存模板
            from src.ocr.ocr_loader import get_template_manager
            tm = get_template_manager()
            tm.set_template(image, roi)

            self._refresh_template_status()
            QMessageBox.information(
                self, "模板已保存",
                f"模板已保存到:\n{tm.template_path}\n\n"
                f"ROI: ({roi[0]}, {roi[1]})  {roi[2]}×{roi[3]}"
            )

        except Exception as e:
            logger.error("制作模板异常: %s", e)
            logger.debug(traceback.format_exc())
            QMessageBox.warning(self, "制作模板", f"制作模板时出错:\n{e}")

        self._make_template_btn.setEnabled(True)
        self._make_template_btn.setText("🎯 制作模板")

    def _on_select_template(self) -> None:
        """选择模板文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模板图片", str(DEFAULT_TEMPLATE_DIR),
            "图片 (*.png *.jpg *.jpeg)"
        )
        if path:
            from src.ocr.ocr_loader import get_template_manager
            import shutil

            tm = get_template_manager()
            tm.template_path.parent.mkdir(parents=True, exist_ok=True)

            src = Path(path)
            dst = tm.template_path
            if src.resolve() != dst.resolve():
                shutil.copy2(str(src), str(dst))
                # 外部模板通常没有本项目的参考尺寸元数据，不能沿用旧模板的尺寸。
                metadata_path = dst.with_suffix(".json")
                if metadata_path.exists():
                    try:
                        metadata_path.unlink()
                    except OSError as exc:
                        logger.warning("旧模板元数据清理失败: %s", exc)

            tm.reload()
            self._template_path = str(dst)
            self._refresh_template_status()

    def _on_make_match_guide_template(self) -> None:
        """截图并制作独立的对局攻略模板。"""
        if not self._capture or not self._capture.connected:
            if self._capture:
                ok, msg = self._capture.connect()
                if not ok:
                    QMessageBox.warning(self, "制作对局攻略模板", f"请先连接模拟器\n{msg}")
                    return
            else:
                QMessageBox.warning(self, "制作对局攻略模板", "请先配置 ADB 并连接模拟器")
                return

        self._make_match_guide_template_btn.setEnabled(False)
        self._make_match_guide_template_btn.setText("正在截图...")
        QTimer.singleShot(50, self._do_make_match_guide_template)

    def _do_make_match_guide_template(self) -> None:
        """实际保存对局攻略模板。"""
        try:
            ok, result = self._capture.screencap_full()
            if not ok:
                QMessageBox.warning(self, "制作对局攻略模板", f"截图失败:\n{result}")
                return

            pixmap = pil_to_qpixmap(result)
            if pixmap.isNull():
                QMessageBox.warning(self, "制作对局攻略模板", "图像转换失败")
                return

            from src.ui.roi_selector import RoiSelectorDialog
            dialog = RoiSelectorDialog(pixmap, title="框选对局攻略页面模板区域", parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            roi = dialog.get_roi()
            if not roi:
                return

            from src.ocr.ocr_loader import get_template_manager
            tm = get_template_manager("match_guide")
            tm.set_template(result, roi)
            self._refresh_match_guide_template_status()
            QMessageBox.information(self, "模板已保存", f"模板已保存到:\n{tm.template_path}")
        except Exception as exc:
            logger.exception("制作对局攻略模板异常")
            QMessageBox.warning(self, "制作对局攻略模板", f"制作模板时出错:\n{exc}")
        finally:
            self._make_match_guide_template_btn.setEnabled(True)
            self._make_match_guide_template_btn.setText("制作对局攻略模板")

    def _on_select_match_guide_template(self) -> None:
        """选择并保存对局攻略模板文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择对局攻略模板图片", str(DEFAULT_TEMPLATE_DIR),
            "图片 (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            from src.ocr.ocr_loader import get_template_manager
            import shutil
            tm = get_template_manager("match_guide")
            tm.template_path.parent.mkdir(parents=True, exist_ok=True)
            src = Path(path)
            dst = tm.template_path
            if src.resolve() != dst.resolve():
                shutil.copy2(str(src), str(dst))
                metadata_path = dst.with_suffix(".json")
                if metadata_path.exists():
                    metadata_path.unlink()
            tm.reload()
            self._refresh_match_guide_template_status()
        except Exception as exc:
            logger.exception("选择对局攻略模板异常")
            QMessageBox.warning(self, "选择对局攻略模板", f"加载模板时出错:\n{exc}")



    # ────────────────────────────────────────────────
    # 连接测试
    # ────────────────────────────────────────────────

    def _test_connection(self) -> None:
        """测试 ADB 连接"""
        adb_path = self._adb_path_edit.property("raw_path") or ""
        if adb_path:
            ok, msg = test_adb_path(adb_path)
            if ok:
                QMessageBox.information(self, "连接测试", f"ADB 验证成功\n{msg}")
            else:
                QMessageBox.warning(self, "连接测试", f"ADB 验证失败\n{msg}")
        else:
            QMessageBox.warning(self, "连接测试", "请先配置 ADB 路径")

    # ────────────────────────────────────────────────
    # UI 更新
    # ────────────────────────────────────────────────

    def _update_ui(self) -> None:
        """根据实例运行状态与真实 ADB 会话状态更新控件。"""
        if self._capture_service:
            state, detail = self._capture_service.connection_state
        elif self._capture:
            state, detail = ("connected", self._capture.device_serial) if self._capture.connected else ("disconnected", "")
        else:
            state, detail = "unconfigured", ""

        states = {
            "unconfigured": ("ADB 状态: 未配置", "#888"),
            "disconnected": ("ADB 状态: 未连接", "#888"),
            "connecting": ("ADB 状态: 连接中...", "#f39c12"),
            "connected": (f"ADB 状态: 已连接 ({detail})", "#27ae60"),
            "offline": ("ADB 状态: 设备离线", "#e74c3c"),
        }
        text, color = states.get(state, states["disconnected"])
        self._status_label.setText(f"● ADB：{text.replace('ADB 状态: ', '')}")
        self._status_label.setToolTip(detail)
        self._status_label.setStyleSheet(f"color: {color}; font-size: 12px; padding: 2px 0;")
        self._connect_btn.setText("断开" if state == "connected" else "连接")
        self._connect_btn.setEnabled(state != "connecting")
        # 制作模板流程本身会在未连接时尝试建立 ADB 会话，不能只按
        # connected 状态禁用按钮，否则用户无法从模板按钮触发自动连接。
        can_make_template = self._capture is not None and state != "connecting"
        self._make_template_btn.setEnabled(can_make_template)
        self._make_match_guide_template_btn.setEnabled(can_make_template)
        self._select_template_btn.setEnabled(state != "connecting")
        self._select_match_guide_template_btn.setEnabled(state != "connecting")
        self._resume_poll_btn.setEnabled(
            self._poll_mode_check.isChecked()
            and self._ocr_service is not None
            and self._ocr_service.poll_state == "paused"
        )
        self._test_device_btn.setEnabled(self._device_combo.currentData() is not None)
        self._update_parameter_controls()

    def _update_parameter_controls(self) -> None:
        """持续轮询关闭时，禁用只与轮询相关的控件。"""
        polling_enabled = self._poll_mode_check.isChecked()
        self._poll_interval_spin.setEnabled(polling_enabled)
        self._resume_poll_btn.setEnabled(
            polling_enabled
            and self._ocr_service is not None
            and self._ocr_service.poll_state == "paused"
        )

    def _show_save_toast(self) -> None:
        """在关闭对话框前给出短暂的保存反馈。"""
        toast = QLabel("✓ 识别参数已保存", self)
        toast.setStyleSheet(
            "QLabel { color: white; background-color: #2f855a; "
            "border-radius: 4px; padding: 6px 12px; }"
        )
        toast.adjustSize()
        toast.move(
            max(0, (self.width() - toast.width()) // 2),
            max(0, self.height() - toast.height() - 48),
        )
        toast.show()

        def finish_save() -> None:
            toast.deleteLater()
            self.accept()

        QTimer.singleShot(300, finish_save)

    # ────────────────────────────────────────────────
    # 保存
    # ────────────────────────────────────────────────

    def _on_save(self) -> None:
        """保存配置"""
        try:
            raw_path = self._adb_path_edit.property("raw_path") or ""
            self._config["mumu_adb_path"] = raw_path

            configured_port = self._config.get("mumu_adb_port", 0)
            device = self._device_combo.currentData()
            running_devices = [item for item in self._devices if item.is_running and item.adb_port]
            if configured_port == 0 and len(running_devices) > 1 and not self._device_selected_explicitly:
                QMessageBox.warning(self, "请选择设备", "检测到多个运行中的 MuMu 实例，请选择要使用的实例后再保存。")
                return
            if self._device_selected_explicitly and device and device.adb_port:
                self._config["mumu_adb_port"] = device.adb_port
            elif configured_port == 0:
                self._config["mumu_adb_port"] = 0
            elif device and device.adb_port:
                self._config["mumu_adb_port"] = device.adb_port

            self._config["mumu_ocr_enabled"] = self._ocr_enabled_check.isChecked()
            self._config["mumu_ocr_poll_mode"] = self._poll_mode_check.isChecked()
            self._config["mumu_ocr_poll_interval"] = self._poll_interval_spin.value()
            self._config["mumu_ocr_match_threshold"] = round(self._threshold_spin.value(), 2)
            self._config["mumu_hero_selection_threshold"] = round(self._threshold_spin.value(), 2)
            self._config["mumu_match_guide_threshold"] = round(self._match_guide_threshold_spin.value(), 2)
            self._config["mumu_hero_selection_cooldown"] = self._hero_cooldown_spin.value()
            self._config["mumu_match_guide_cooldown"] = self._match_guide_cooldown_spin.value()
            self._show_save_toast()
        except Exception as e:
            logger.exception("保存模拟器配置失败")
            QMessageBox.critical(self, "保存失败", f"保存配置时出错:\n{e}")

    def get_config(self) -> dict:
        """获取用户修改后的配置"""
        return self._config

    def get_connected(self) -> bool:
        """是否已连接"""
        return self._capture.connected if self._capture else False
