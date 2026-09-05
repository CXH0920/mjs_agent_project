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

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.business.emulator.capture_service import CaptureService
from src.business.emulator.emulator_operation_service import EmulatorOperationService
from src.business.emulator.mumu_config_coordinator import MumuConfigCoordinator
from src.business.recognition.ocr_service import OcrService
from src.capture.image_validation import load_local_image
from src.capture.image_utils import pil_to_qpixmap
from src.capture.prober import MuMuDeviceInfo
from src.config.env import BUNDLE_ROOT, SCREENSHOTS_DIR
from src.ui.configuration.mumu_config_sections import (
    MumuDeviceSection,
    MumuOcrPollingSection,
    MumuTemplateSection,
)
from src.ui.shared.widgets import close_after_toast, DialogFooter, PageHeader, show_toast

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE_DIR = BUNDLE_ROOT / "templates"
DEFAULT_SCREENSHOTS_DIR = SCREENSHOTS_DIR


class MumuConfigDialog(QDialog):
    """模拟器配置对话框"""

    def __init__(
        self,
        config: dict,
        capture_service: CaptureService | None = None,
        ocr_service: OcrService | None = None,
        operation_service: EmulatorOperationService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._capture_service = capture_service or CaptureService(self)
        self._ocr_service = ocr_service or OcrService(self)
        self._coordinator = MumuConfigCoordinator(
            config,
            self._capture_service,
            self._ocr_service,
            operation_service,
            self,
        )
        self._operation_service = self._coordinator.operation_service
        self._device_selected_explicitly = False

        self.setWindowTitle("模拟器配置")
        self.setMinimumWidth(760)
        self.setMinimumHeight(620)
        self.resize(900, 680)
        self._setup_ui()
        self._coordinator.connection_state_changed.connect(self._on_connection_changed)
        self._coordinator.adb_detected.connect(self._on_adb_detected)
        self._coordinator.devices_changed.connect(self._on_devices_refreshed)
        self._coordinator.device_refresh_failed.connect(self._on_device_refresh_failed)
        self._coordinator.connection_finished.connect(self._on_connection_finished)
        self._coordinator.disconnection_finished.connect(self._on_disconnection_finished)
        self._coordinator.device_tested.connect(self._on_device_tested)
        self._coordinator.template_screenshot_ready.connect(self._on_template_screenshot_ready)
        self._coordinator.template_screenshot_failed.connect(self._on_template_screenshot_failed)
        self._coordinator.template_capture_finished.connect(self._restore_template_button)
        self._coordinator.roi_layout_screenshot_ready.connect(self._on_roi_layout_screenshot_ready)
        self._coordinator.roi_layout_screenshot_failed.connect(self._on_roi_layout_screenshot_failed)
        self._coordinator.roi_layout_capture_finished.connect(self._restore_roi_layout_button)
        self._coordinator.operation_failed.connect(self._on_operation_failed)
        self._load_config()

    @property
    def _config(self) -> dict:
        """兼容现有调用方读取对话框配置草稿。"""
        return self._coordinator.config

    def _setup_ui(self) -> None:
        """按设备连接和识别自动化两个任务页构建配置界面。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = PageHeader("模拟器配置", "管理设备连接、识别模板与自动化参数")
        header.setContentsMargins(18, 12, 18, 12)
        self._status_label = QLabel("● ADB：未配置")
        header.actions_layout.addWidget(self._status_label)
        layout.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self._page_nav = QListWidget()
        self._page_nav.setObjectName("mumuConfigNavigation")
        self._page_nav.setFixedWidth(158)
        self._page_nav.addItems(["设备与连接", "识别与自动化"])
        self._page_nav.setStyleSheet(
            "QListWidget#mumuConfigNavigation { background: #eef2f6; border: none; "
            "border-right: 1px solid #d3dde7; padding: 10px 7px; }"
            "QListWidget#mumuConfigNavigation::item { color: #4a6a8a; border-radius: 4px; "
            "padding: 10px 12px; margin-bottom: 3px; }"
            "QListWidget#mumuConfigNavigation::item:selected { background: #dceeff; color: #357abd; "
            "border-left: 3px solid #4a90d9; font-weight: bold; }"
            "QListWidget#mumuConfigNavigation::item:hover:!selected { background: #e5ebf1; }"
        )
        body.addWidget(self._page_nav)
        self._page_stack = QStackedWidget()
        body.addWidget(self._page_stack, 1)

        self._device_section = MumuDeviceSection(self)
        self._device_section.browse_requested.connect(self._browse_adb)
        self._device_section.detect_requested.connect(self._on_auto_detect)
        self._device_section.refresh_requested.connect(self._on_refresh_devices)
        self._device_section.connect_requested.connect(self._on_connect_toggle)
        self._device_section.test_requested.connect(self._on_test_selected_device)
        self._device_section.device_changed.connect(self._on_device_changed)
        self._device_section.device_activated.connect(self._on_device_activated)
        self._adb_path_edit = self._device_section.adb_path_edit
        self._detect_btn = self._device_section.detect_button
        self._device_combo = self._device_section.device_combo
        self._refresh_devices_btn = self._device_section.refresh_button
        self._port_label = self._device_section.port_label
        self._connect_btn = self._device_section.connect_button
        self._test_device_btn = self._device_section.test_button
        self._instance_status_label = self._device_section.instance_status_label
        device_scroll = self._page_scroll(self._device_section)
        self._page_stack.addWidget(device_scroll)

        self._template_section = MumuTemplateSection(self)
        self._template_section.hero_select_requested.connect(self._on_select_template)
        self._template_section.hero_make_requested.connect(self._on_make_template)
        self._template_section.match_guide_select_requested.connect(self._on_select_match_guide_template)
        self._template_section.match_guide_make_requested.connect(self._on_make_match_guide_template)
        self._template_status_icon = self._template_section.hero_status_icon
        self._template_status_label = self._template_section.hero_status_label
        self._select_template_btn = self._template_section.hero_select_button
        self._make_template_btn = self._template_section.hero_make_button
        self._match_guide_status_icon = self._template_section.match_guide_status_icon
        self._match_guide_status_label = self._template_section.match_guide_status_label
        self._select_match_guide_template_btn = self._template_section.match_guide_select_button
        self._make_match_guide_template_btn = self._template_section.match_guide_make_button

        self._ocr_polling_section = MumuOcrPollingSection(self._template_section, self)
        self._ocr_polling_section.poll_mode_changed.connect(self._update_parameter_controls)
        self._ocr_polling_section.resume_requested.connect(self._on_resume_poll)
        self._ocr_polling_section.roi_capture_requested.connect(self._start_roi_layout_capture)
        self._ocr_polling_section.roi_image_requested.connect(self._select_roi_layout_image)
        self._ocr_polling_section.roi_reset_requested.connect(self._reset_roi_layout)
        self._ocr_enabled_check = self._ocr_polling_section.ocr_enabled_check
        self._poll_mode_check = self._ocr_polling_section.poll_mode_check
        self._auto_switch_tab_check = self._ocr_polling_section.auto_switch_tab_check
        self._poll_interval_spin = self._ocr_polling_section.poll_interval_spin
        self._resume_poll_btn = self._ocr_polling_section.resume_button
        self._threshold_spin = self._ocr_polling_section.threshold_spin
        self._match_guide_threshold_spin = self._ocr_polling_section.match_guide_threshold_spin
        self._hero_cooldown_spin = self._ocr_polling_section.hero_cooldown_spin
        self._edit_hero_roi_capture_btn = self._ocr_polling_section.hero_roi_capture_button
        self._edit_hero_roi_image_btn = self._ocr_polling_section.hero_roi_image_button
        self._reset_hero_roi_btn = self._ocr_polling_section.hero_roi_reset_button
        self._edit_match_guide_roi_capture_btn = self._ocr_polling_section.match_guide_roi_capture_button
        self._edit_match_guide_roi_image_btn = self._ocr_polling_section.match_guide_roi_image_button
        self._reset_match_guide_roi_btn = self._ocr_polling_section.match_guide_roi_reset_button
        recognition_scroll = self._page_scroll(self._ocr_polling_section)
        self._page_stack.addWidget(recognition_scroll)
        self._page_nav.currentRowChanged.connect(self._page_stack.setCurrentIndex)
        self._page_nav.setCurrentRow(0)
        layout.addLayout(body, 1)

        self._footer = DialogFooter(accept_text="保存", cancel_text="取消")
        self._footer.accepted.connect(self._on_save)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    @staticmethod
    def _page_scroll(page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    # ────────────────────────────────────────────────
    # 加载配置
    # ────────────────────────────────────────────────

    def _load_config(self) -> None:
        """从配置加载当前值"""
        adb_path = self._config.get("mumu_adb_path", "")
        should_auto_detect = not adb_path

        self._adb_path_edit.setText(adb_path or "(未设置，点击「自动探测」)")
        self._adb_path_edit.setProperty("raw_path", adb_path)
        self._adb_path_edit.setToolTip(adb_path)

        adb_port = self._config.get("mumu_adb_port", 0)
        self._port_label.setText(str(adb_port) if adb_port else "(自动探测)")

        self._ocr_enabled_check.setChecked(self._config.get("mumu_ocr_enabled", False))
        self._poll_mode_check.setChecked(self._config.get("mumu_ocr_poll_mode", False))
        self._auto_switch_tab_check.setChecked(self._config.get("mumu_ocr_auto_switch_tab", False))
        # 配置出自 get_mumu_config() 全键字典（协调器持有），默认值以 env 层为唯一权威，直接取键
        self._poll_interval_spin.setValue(self._config["mumu_ocr_poll_interval"])
        self._threshold_spin.setValue(self._config["mumu_hero_selection_threshold"])
        self._match_guide_threshold_spin.setValue(self._config["mumu_match_guide_threshold"])
        self._hero_cooldown_spin.setValue(self._config["mumu_hero_selection_cooldown"])

        self._sync_capture_service_config()
        self._on_refresh_devices()

        # 检查模板状态
        self._refresh_template_status()
        self._refresh_match_guide_template_status()
        self._update_ui()
        if should_auto_detect:
            self._on_auto_detect()

    def _refresh_template_status(self) -> None:
        """更新模板状态显示"""
        self._apply_template_status(
            self._template_status_icon, self._template_status_label,
            self._coordinator.template_status(),
        )

    def _refresh_match_guide_template_status(self) -> None:
        """更新对局攻略模板状态显示。"""
        self._apply_template_status(
            self._match_guide_status_icon, self._match_guide_status_label,
            self._coordinator.template_status("match_guide"),
        )

    @staticmethod
    def _apply_template_status(icon: QLabel, label: QLabel, status) -> None:
        """把一份模板状态渲染到指定的图标/标签控件对（两组控件共用）。"""
        if status.loaded:
            icon.setText("●")
            icon.setStyleSheet("color: #27ae60; font-size: 16px;")
            label.setText(f"已加载：{status.path.name}")
            label.setToolTip(str(status.path))
            label.setStyleSheet("color: #27ae60; font-size: 13px;")
        else:
            icon.setText("○")
            icon.setStyleSheet("color: #888; font-size: 16px;")
            label.setText("未设定")
            label.setToolTip("")
            label.setStyleSheet("color: #888; font-size: 13px;")

    # ────────────────────────────────────────────────
    # ADB 连接管理
    # ────────────────────────────────────────────────

    def _on_auto_detect(self) -> None:
        """自动探测 ADB 路径和端口"""
        logger.info("开始自动探测 ADB...")
        self._detect_btn.setEnabled(False)
        self._detect_btn.setText("探测中...")
        self._coordinator.detect_adb()

    def _on_adb_detected(self, success: bool, adb_path: str, message: str) -> None:
        self._detect_btn.setEnabled(True)
        self._detect_btn.setText("自动探测")
        if not success:
            self._adb_path_edit.setStyleSheet(
                "border: 1px solid #e74c3c; padding: 4px 8px; background-color: #fdf0ef; border-radius: 3px;"
            )
            QMessageBox.warning(self, "自动探测", message)
            return

        self._adb_path_edit.setText(adb_path)
        self._adb_path_edit.setProperty("raw_path", adb_path)
        self._adb_path_edit.setToolTip(adb_path)
        self._adb_path_edit.setStyleSheet(
            "border: 1px solid #27ae60; padding: 4px 8px; background-color: #f0faf0; border-radius: 3px;"
        )
        show_toast(self, f"已找到 ADB：{adb_path}\n{message}", duration=3000)

    def _browse_adb(self) -> None:
        """弹出文件选择对话框选择 adb.exe"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ADB 可执行文件", "",
            "adb (*.exe);;所有文件 (*.*)"
        )
        if path:
            self._adb_path_edit.setText(path)
            self._adb_path_edit.setProperty("raw_path", path)
            self._adb_path_edit.setToolTip(path)
            self._adb_path_edit.setStyleSheet(
                "border: 1px solid #ccc; padding: 4px 8px; background-color: #f9f9f9; border-radius: 3px;"
            )
            self._coordinator.update_adb_path(path)
            self._on_refresh_devices()

    def _sync_capture_service_config(self) -> None:
        """将当前编辑中的 ADB 配置同步到共享截图服务。"""
        self._coordinator.update_adb_path(self._adb_path_edit.property("raw_path") or "")

    def _on_refresh_devices(self) -> None:
        """请求后台刷新设备列表。"""
        self._refresh_devices_btn.setEnabled(False)
        self._refresh_devices_btn.setText("刷新中...")
        self._coordinator.refresh_devices()

    def _on_devices_refreshed(self, devices: list[MuMuDeviceInfo]) -> None:
        """展示后台探测到的设备，并避免在多实例时擅自选择目标。"""
        self._refresh_devices_btn.setEnabled(True)
        self._refresh_devices_btn.setText("刷新")
        configured_port = self._config.get("mumu_adb_port", 0)
        devices = self._coordinator.devices
        running_devices = [device for device in devices if device.is_running and device.adb_port]
        self._device_selected_explicitly = False

        with QSignalBlocker(self._device_combo):
            self._device_combo.clear()
            if not devices:
                self._device_combo.addItem("(未探测到设备)")
                self._device_combo.setEnabled(False)
                self._set_instance_status("● 实例：未探测到")
                self._port_label.setText("(自动探测)")
                self._update_ui()
                return

            self._device_combo.setEnabled(True)
            for device in devices:
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

    def _on_device_refresh_failed(self, message: str) -> None:
        """探测失败时保留现有选择，避免瞬时错误抹掉设备状态。"""
        self._refresh_devices_btn.setEnabled(True)
        self._refresh_devices_btn.setText("刷新")
        self._refresh_devices_btn.setToolTip(message)
        if self._coordinator.devices:
            running = any(device.is_running for device in self._coordinator.devices)
            self._set_instance_status("● 实例：刷新失败（保留上次结果）", running=running)
        else:
            self._set_instance_status("● 实例：刷新失败")
        self._instance_status_label.setToolTip(message)
        self._update_ui()

    def _on_device_activated(self, index: int) -> None:
        """记录用户对实例的显式选择。"""
        if self._device_combo.itemData(index):
            self._device_selected_explicitly = True

    def _on_device_changed(self, index: int) -> None:
        """设备下拉选择变化"""
        if index < 0 or not self._coordinator.devices:
            return

        device = self._device_combo.itemData(index)
        if device and device.adb_port:
            self._port_label.setText(str(device.adb_port))
            state = "运行中" if device.is_running else "未运行"
            self._set_instance_status(f"● 实例：{state}", running=device.is_running)
        else:
            self._port_label.setText("(自动探测)")
            self._set_instance_status("● 实例：未探测到")
        self._update_ui()

    def _set_instance_status(self, text: str, *, running: bool = False) -> None:
        """更新实例状态文字及颜色，运行中的实例使用绿色强调。"""
        color = "#27ae60" if running else "#777"
        self._instance_status_label.setText(text)
        self._instance_status_label.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _on_connection_changed(self, _state: str, _detail: str) -> None:
        """共享 CaptureService 会话变化时刷新配置页。"""
        self._update_ui()

    def _on_connect_toggle(self) -> None:
        """连接/断开切换"""
        if self._coordinator.connection_state[0] == "connected":
            self._disconnect_emulator()
        else:
            self._connect_emulator()

    def _connect_emulator(self) -> None:
        """连接选中的模拟器。"""
        device = self._device_combo.currentData()
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("连接中...")
        error = self._coordinator.connect(device, self._device_selected_explicitly)
        if error:
            self._update_ui()
            QMessageBox.warning(self, "请选择设备", error)

    def _on_connection_finished(self, ok: bool, message: str) -> None:
        if not ok:
            QMessageBox.warning(self, "连接失败", message)
        self._connect_btn.setEnabled(True)
        self._update_ui()

    def _disconnect_emulator(self) -> None:
        """断开模拟器。"""
        self._connect_btn.setEnabled(False)
        self._connect_btn.setText("断开中...")
        self._coordinator.disconnect()

    def _on_disconnection_finished(self, _ok: bool, _message: str) -> None:
        self._connect_btn.setEnabled(True)
        self._update_ui()

    def _on_test_selected_device(self) -> None:
        """测试当前选择的设备连通性，不改变共享会话或配置。"""
        device = self._device_combo.currentData()
        error = self._coordinator.test_device(
            self._adb_path_edit.property("raw_path") or "",
            device,
        )
        if error:
            QMessageBox.warning(self, "设备测试", error)
            return

        self._test_device_btn.setEnabled(False)
        self._test_device_btn.setText("测试中...")

    def _on_device_tested(self, ok: bool, target: str, message: str) -> None:
        self._test_device_btn.setEnabled(True)
        self._test_device_btn.setText("测试连接")
        if ok:
            show_toast(self, f"设备测试成功：{target}\n{message}", duration=2500)
        else:
            QMessageBox.warning(self, "设备测试失败", f"无法连接所选设备 {target}：\n{message}")
        self._update_ui()

    def _on_resume_poll(self) -> None:
        """恢复已暂停的 OCR 轮询。"""
        if self._coordinator.resume_poll():
            self._update_ui()


    def _on_make_template(self) -> None:
        self._start_template_capture("hero_selection")

    def _on_select_template(self) -> None:
        """选择模板文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模板图片", str(DEFAULT_TEMPLATE_DIR),
            "图片 (*.png *.jpg *.jpeg)"
        )
        if not path:
            return
        try:
            self._coordinator.select_template(path)
            self._refresh_template_status()
        except Exception as exc:
            QMessageBox.warning(self, "选择模板", f"加载模板时出错:\n{exc}")

    def _on_make_match_guide_template(self) -> None:
        self._start_template_capture("match_guide")

    def _on_select_match_guide_template(self) -> None:
        """选择并保存对局攻略模板文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择对局攻略模板图片", str(DEFAULT_TEMPLATE_DIR),
            "图片 (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            self._coordinator.select_template(path, "match_guide")
            self._refresh_match_guide_template_status()
        except Exception as exc:
            QMessageBox.warning(self, "选择对局攻略模板", f"加载模板时出错:\n{exc}")

    def _start_template_capture(self, template_name: str) -> None:
        if not self._coordinator.start_template_capture(template_name):
            return
        button = self._template_button(template_name)
        button.setEnabled(False)
        button.setText("正在截图...")

    def _on_template_screenshot_ready(self, template_name: str, image) -> None:
        try:
            pixmap = pil_to_qpixmap(image)
            if pixmap.isNull():
                QMessageBox.warning(self, "制作模板", "图像转换失败")
                return

            from src.ui.configuration.roi_selector import RoiSelectorDialog

            title = "框选对局攻略页面模板区域" if template_name == "match_guide" else "框选模板区域（如页面标题或按钮）"
            dialog = RoiSelectorDialog(pixmap, title=title, parent=self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            roi = dialog.get_roi()
            if not roi:
                return

            self._coordinator.create_template(image, roi, template_name)
            self._refresh_template(template_name)
            template_path = self._coordinator.template_status(template_name).path
            message = f"模板已保存到:\n{template_path}"
            if template_name == "hero_selection":
                message += f"\n\nROI: ({roi[0]}, {roi[1]})  {roi[2]}×{roi[3]}"
            show_toast(self, message, duration=3000)
        except Exception as exc:
            logger.exception("制作模板异常")
            QMessageBox.warning(self, "制作模板", f"制作模板时出错:\n{exc}")
        finally:
            self._coordinator.finish_template_capture(template_name)

    def _on_template_screenshot_failed(self, template_name: str, message: str) -> None:
        QMessageBox.warning(self, "制作模板", f"截图失败:\n{message}")

    def _start_roi_layout_capture(self, page_type: str) -> None:
        if not self._coordinator.start_roi_layout_capture(page_type):
            return
        button = self._roi_layout_capture_button(page_type)
        button.setEnabled(False)
        button.setText("正在截图...")

    def _select_roi_layout_image(self, page_type: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择用于调整识别区域的截图",
            str(DEFAULT_SCREENSHOTS_DIR),
            "图片 (*.png *.jpg *.jpeg)",
        )
        if not path:
            return
        try:
            image = load_local_image(path)
            self._open_roi_layout_editor(page_type, image)
        except Exception as exc:
            logger.exception("读取 OCR ROI 截图失败")
            QMessageBox.warning(self, "编辑识别区域", f"无法读取图片:\n{exc}")

    def _on_roi_layout_screenshot_ready(self, page_type: str, image) -> None:
        try:
            self._open_roi_layout_editor(page_type, image)
        finally:
            self._coordinator.finish_roi_layout_capture(page_type)

    def _on_roi_layout_screenshot_failed(self, _page_type: str, message: str) -> None:
        QMessageBox.warning(self, "编辑识别区域", f"截图失败:\n{message}")

    def _open_roi_layout_editor(self, page_type: str, image) -> None:
        try:
            pixmap = pil_to_qpixmap(image)
            if pixmap.isNull():
                raise ValueError("图像转换失败")
            from src.ui.configuration.roi_selector import RoiLayoutEditorDialog

            dialog = RoiLayoutEditorDialog(
                pixmap,
                self._coordinator.roi_layout(page_type),
                page_type,
                self,
            )
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            self._coordinator.save_roi_layout(page_type, dialog.get_layout())
            show_toast(self, "识别区域已保存，将在下一次识别时生效。")
        except Exception as exc:
            logger.exception("保存 OCR ROI 配置失败")
            QMessageBox.warning(self, "编辑识别区域", f"保存识别区域时出错:\n{exc}")

    def _reset_roi_layout(self, page_type: str) -> None:
        page_name = "对局攻略" if page_type == "match_guide" else "选将推荐"
        if QMessageBox.question(
            self,
            "恢复默认识别区域",
            f"确定恢复{page_name}的默认识别区域吗？",
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self._coordinator.reset_roi_layout(page_type)
            show_toast(self, "默认识别区域已恢复，将在下一次识别时生效。")
        except Exception as exc:
            logger.exception("恢复默认 OCR ROI 配置失败")
            QMessageBox.warning(self, "恢复默认识别区域", f"恢复失败:\n{exc}")

    def _on_operation_failed(self, operation: str, message: str) -> None:
        """恢复异常中断的后台操作对应控件。"""
        if operation == "detect_adb":
            self._detect_btn.setEnabled(True)
            self._detect_btn.setText("自动探测")
        elif operation == "refresh_devices":
            self._refresh_devices_btn.setEnabled(True)
            self._refresh_devices_btn.setText("刷新")
        elif operation in {"connect", "disconnect"}:
            self._connect_btn.setEnabled(True)
            self._update_ui()
        elif operation == "test_device":
            self._test_device_btn.setEnabled(True)
            self._test_device_btn.setText("测试连接")
        QMessageBox.warning(self, "模拟器操作失败", message)

    def _template_button(self, template_name: str) -> QPushButton:
        return self._make_match_guide_template_btn if template_name == "match_guide" else self._make_template_btn

    def _roi_layout_capture_button(self, page_type: str) -> QPushButton:
        return (
            self._edit_match_guide_roi_capture_btn
            if page_type == "match_guide" else self._edit_hero_roi_capture_btn
        )

    def _restore_template_button(self, template_name: str) -> None:
        button = self._template_button(template_name)
        button.setEnabled(True)
        button.setText("制作模板")

    def _restore_roi_layout_button(self, page_type: str) -> None:
        button = self._roi_layout_capture_button(page_type)
        button.setEnabled(True)
        button.setText("截图编辑")

    def _refresh_template(self, template_name: str) -> None:
        if template_name == "match_guide":
            self._refresh_match_guide_template_status()
        else:
            self._refresh_template_status()

    # ────────────────────────────────────────────────
    # UI 更新
    # ────────────────────────────────────────────────

    def _update_ui(self) -> None:
        """根据实例运行状态与真实 ADB 会话状态更新控件。"""
        state, detail = self._coordinator.connection_state

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
        can_make_template = self._coordinator.capture is not None and state != "connecting"
        self._make_template_btn.setEnabled(
            can_make_template and not self._coordinator.is_template_capture_in_progress("hero_selection")
        )
        self._make_match_guide_template_btn.setEnabled(
            can_make_template and not self._coordinator.is_template_capture_in_progress("match_guide")
        )
        self._select_template_btn.setEnabled(state != "connecting")
        self._select_match_guide_template_btn.setEnabled(state != "connecting")
        can_capture_roi = self._coordinator.capture is not None and state != "connecting"
        self._edit_hero_roi_capture_btn.setEnabled(
            can_capture_roi and not self._coordinator.is_roi_layout_capture_in_progress("hero_selection")
        )
        self._edit_match_guide_roi_capture_btn.setEnabled(
            can_capture_roi and not self._coordinator.is_roi_layout_capture_in_progress("match_guide")
        )
        self._edit_hero_roi_image_btn.setEnabled(state != "connecting")
        self._edit_match_guide_roi_image_btn.setEnabled(state != "connecting")
        self._reset_hero_roi_btn.setEnabled(state != "connecting")
        self._reset_match_guide_roi_btn.setEnabled(state != "connecting")
        self._resume_poll_btn.setEnabled(
            self._poll_mode_check.isChecked()
            and self._coordinator.poll_is_paused()
        )
        self._test_device_btn.setEnabled(self._device_combo.currentData() is not None)
        self._update_parameter_controls()

    def _update_parameter_controls(self) -> None:
        """持续轮询关闭时，禁用只与轮询相关的控件。"""
        polling_enabled = self._poll_mode_check.isChecked()
        self._poll_interval_spin.setEnabled(polling_enabled)
        self._auto_switch_tab_check.setEnabled(polling_enabled)
        polling_paused = polling_enabled and self._coordinator.poll_is_paused()
        self._resume_poll_btn.setEnabled(polling_paused)
        self._resume_poll_btn.setVisible(polling_paused)

    def _show_save_toast(self) -> None:
        """在关闭对话框前给出短暂的保存反馈。"""
        close_after_toast(self, "识别参数已保存", 400)

    # ────────────────────────────────────────────────
    # 保存
    # ────────────────────────────────────────────────

    def _on_save(self) -> None:
        """保存配置"""
        self._footer.set_busy(True, "正在保存...")
        try:
            raw_path = self._adb_path_edit.property("raw_path") or ""
            device = self._device_combo.currentData()
            _, error = self._coordinator.save_config({
                "mumu_adb_path": raw_path,
                "mumu_ocr_enabled": self._ocr_enabled_check.isChecked(),
                "mumu_ocr_poll_mode": self._poll_mode_check.isChecked(),
                "mumu_ocr_auto_switch_tab": self._auto_switch_tab_check.isChecked(),
                "mumu_ocr_poll_interval": self._poll_interval_spin.value(),
                "mumu_ocr_match_threshold": round(self._threshold_spin.value(), 2),
                "mumu_hero_selection_threshold": round(self._threshold_spin.value(), 2),
                "mumu_match_guide_threshold": round(self._match_guide_threshold_spin.value(), 2),
                "mumu_hero_selection_cooldown": self._hero_cooldown_spin.value(),
            }, device, self._device_selected_explicitly)
            if error:
                self._footer.set_busy(False)
                QMessageBox.warning(self, "请选择设备", error)
                return
            self._show_save_toast()
        except Exception as e:
            self._footer.set_busy(False)
            logger.exception("保存模拟器配置失败")
            QMessageBox.critical(self, "保存失败", f"保存配置时出错:\n{e}")

    def get_config(self) -> dict:
        """获取用户修改后的配置"""
        return self._coordinator.config

    def get_connected(self) -> bool:
        """是否已连接"""
        return self._coordinator.connection_state[0] == "connected"

    def done(self, result: int) -> None:
        """关闭时停止接收后台操作结果，避免更新已关闭的对话框。"""
        self._coordinator.shutdown()
        super().done(result)
