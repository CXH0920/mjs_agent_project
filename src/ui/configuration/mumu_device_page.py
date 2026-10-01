# -*- coding: utf-8 -*-
"""模拟器配置·设备与连接页（审计 G2 切片 4.1a）。

从 MumuConfigDialog 拆出：ADB 探测/设备刷新/连接管理/连通性测试的信号接线
与控件状态收敛到本页；对话框只负责页面装配、页头 ADB 总状态与保存编排。
控件来自 MumuDeviceSection（构建层），后台操作全部委托
MumuConfigCoordinator（业务层）。弹窗与 Toast 以 self.window()（对话框）
为父级，保持拆分前的呈现位置与共享 overlay 语义。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QVBoxLayout, QWidget
from src.business.emulator.mumu_config_coordinator import (
    MumuConfigCoordinator,
    MuMuDeviceInfo,
)
from src.ui.configuration.mumu_config_sections import MumuDeviceSection
from src.ui.shared.widgets import show_toast

logger = logging.getLogger(__name__)


class MumuDevicePage(QWidget):
    """「设备与连接」页：ADB 探测、设备列表、连接管理与连通性测试。"""

    ui_refresh_requested = Signal()

    def __init__(self, coordinator: MumuConfigCoordinator, parent=None) -> None:
        super().__init__(parent)
        self._coordinator = coordinator
        self._device_selected_explicitly = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.section = MumuDeviceSection(self)
        layout.addWidget(self.section)

        self.section.browse_requested.connect(self._browse_adb)
        self.section.detect_requested.connect(self._on_auto_detect)
        self.section.refresh_requested.connect(self._on_refresh_devices)
        self.section.connect_requested.connect(self._on_connect_toggle)
        self.section.test_requested.connect(self._on_test_selected_device)
        self.section.device_changed.connect(self._on_device_changed)
        self.section.device_activated.connect(self._on_device_activated)

        coordinator.adb_detected.connect(self._on_adb_detected)
        coordinator.devices_changed.connect(self._on_devices_refreshed)
        coordinator.device_refresh_failed.connect(self._on_device_refresh_failed)
        coordinator.connection_finished.connect(self._on_connection_finished)
        coordinator.disconnection_finished.connect(self._on_disconnection_finished)
        coordinator.device_tested.connect(self._on_device_tested)
        coordinator.operation_failed.connect(self._on_operation_failed)

    # Section 控件别名：本页方法与对话框外断言共用同一命名
    @property
    def adb_path_edit(self):
        return self.section.adb_path_edit

    @property
    def device_combo(self):
        return self.section.device_combo

    @property
    def port_label(self):
        return self.section.port_label

    @property
    def instance_status_label(self):
        return self.section.instance_status_label

    @property
    def detect_button(self):
        return self.section.detect_button

    @property
    def refresh_button(self):
        return self.section.refresh_button

    @property
    def connect_button(self):
        return self.section.connect_button

    @property
    def test_button(self):
        return self.section.test_button

    # ────────────────────────────────────────────────
    # 配置装载 / 状态渲染 / 草稿收集（对话框编排入口）
    # ────────────────────────────────────────────────

    def load_config(self, config: dict) -> None:
        """从配置加载 ADB 路径与端口显示。"""
        adb_path = config.get("mumu_adb_path", "")
        self.adb_path_edit.setText(adb_path or "(未设置，点击「自动探测」)")
        self.adb_path_edit.setProperty("raw_path", adb_path)
        self.adb_path_edit.setToolTip(adb_path)

        adb_port = config.get("mumu_adb_port", 0)
        self.port_label.setText(str(adb_port) if adb_port else "(自动探测)")

    def sync_capture_config(self) -> None:
        """将当前编辑中的 ADB 配置同步到共享截图服务。"""
        self._coordinator.update_adb_path(self.adb_path_edit.property("raw_path") or "")

    def start_detect(self) -> None:
        """触发一次 ADB 自动探测（对话框初载无配置时调用）。"""
        self._on_auto_detect()

    def refresh_devices(self) -> None:
        """请求后台刷新设备列表。"""
        self._on_refresh_devices()

    def update_state(self, state: str) -> None:
        """按 ADB 会话状态更新连接与测试控件。"""
        self.connect_button.setText("断开" if state == "connected" else "连接")
        self.connect_button.setEnabled(state != "connecting")
        self.test_button.setEnabled(self.device_combo.currentData() is not None)

    def collect_device_draft(self) -> tuple[str, MuMuDeviceInfo | None, bool]:
        """收集保存所需的设备侧草稿：(raw_path, device, selected_explicitly)。"""
        raw_path = self.adb_path_edit.property("raw_path") or ""
        return raw_path, self.device_combo.currentData(), self._device_selected_explicitly

    def restore_after_operation(self, operation: str) -> None:
        """恢复异常中断的设备侧后台操作对应控件（失败提示由对话框统一弹出）。"""
        if operation == "detect_adb":
            self.detect_button.setEnabled(True)
            self.detect_button.setText("自动探测")
        elif operation == "refresh_devices":
            self.refresh_button.setEnabled(True)
            self.refresh_button.setText("刷新")
        elif operation in {"connect", "disconnect"}:
            self.connect_button.setEnabled(True)
            self.ui_refresh_requested.emit()
        elif operation == "test_device":
            self.test_button.setEnabled(True)
            self.test_button.setText("测试连接")

    # ────────────────────────────────────────────────
    # ADB 连接管理
    # ────────────────────────────────────────────────

    def _on_auto_detect(self) -> None:
        """自动探测 ADB 路径和端口"""
        logger.info("开始自动探测 ADB...")
        self.detect_button.setEnabled(False)
        self.detect_button.setText("探测中...")
        self._coordinator.detect_adb()

    def _on_adb_detected(self, success: bool, adb_path: str, message: str) -> None:
        self.detect_button.setEnabled(True)
        self.detect_button.setText("自动探测")
        if not success:
            self.adb_path_edit.setStyleSheet(
                "border: 1px solid #e74c3c; padding: 4px 8px; background-color: #fdf0ef; border-radius: 3px;"
            )
            QMessageBox.warning(self.window(), "自动探测", message)
            return

        self.adb_path_edit.setText(adb_path)
        self.adb_path_edit.setProperty("raw_path", adb_path)
        self.adb_path_edit.setToolTip(adb_path)
        self.adb_path_edit.setStyleSheet(
            "border: 1px solid #27ae60; padding: 4px 8px; background-color: #f0faf0; border-radius: 3px;"
        )
        show_toast(self.window(), f"已找到 ADB：{adb_path}\n{message}", duration=3000)

    def _browse_adb(self) -> None:
        """弹出文件选择对话框选择 adb.exe"""
        path, _ = QFileDialog.getOpenFileName(
            self.window(), "选择 ADB 可执行文件", "",
            "adb (*.exe);;所有文件 (*.*)"
        )
        if path:
            self.adb_path_edit.setText(path)
            self.adb_path_edit.setProperty("raw_path", path)
            self.adb_path_edit.setToolTip(path)
            self.adb_path_edit.setStyleSheet(
                "border: 1px solid #ccc; padding: 4px 8px; background-color: #f9f9f9; border-radius: 3px;"
            )
            self._coordinator.update_adb_path(path)
            self._on_refresh_devices()

    def _on_refresh_devices(self) -> None:
        """请求后台刷新设备列表。"""
        self.refresh_button.setEnabled(False)
        self.refresh_button.setText("刷新中...")
        self._coordinator.refresh_devices()

    def _on_devices_refreshed(self, devices: list[MuMuDeviceInfo]) -> None:
        """展示后台探测到的设备，并避免在多实例时擅自选择目标。"""
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新")
        configured_port = self._coordinator.config.get("mumu_adb_port", 0)
        devices = self._coordinator.devices
        running_devices = [device for device in devices if device.is_running and device.adb_port]
        self._device_selected_explicitly = False

        with QSignalBlocker(self.device_combo):
            self.device_combo.clear()
            if not devices:
                self.device_combo.addItem("(未探测到设备)")
                self.device_combo.setEnabled(False)
                self._set_instance_status("● 实例：未探测到")
                self.port_label.setText("(自动探测)")
                self.ui_refresh_requested.emit()
                return

            self.device_combo.setEnabled(True)
            for device in devices:
                running_text = "运行中" if device.is_running else "未运行"
                label = f"[{device.index}] {device.name}（{running_text}）"
                if device.adb_port:
                    label += f"  (端口:{device.adb_port})"
                self.device_combo.addItem(label, userData=device)

            target_index = next(
                (
                    index for index in range(self.device_combo.count())
                    if (device := self.device_combo.itemData(index)) and device.adb_port == configured_port
                ),
                -1,
            ) if configured_port else -1

            if target_index >= 0:
                self.device_combo.setCurrentIndex(target_index)
            elif configured_port == 0 and len(running_devices) == 1:
                target_index = next(
                    index for index in range(self.device_combo.count())
                    if self.device_combo.itemData(index) is running_devices[0]
                )
                self.device_combo.setCurrentIndex(target_index)
            elif configured_port == 0 and len(running_devices) > 1:
                self.device_combo.insertItem(0, "请选择运行中的实例", userData=None)
                self.device_combo.setCurrentIndex(0)
            else:
                self.device_combo.setCurrentIndex(0)

        self._on_device_changed(self.device_combo.currentIndex())
        self.ui_refresh_requested.emit()

    def _on_device_refresh_failed(self, message: str) -> None:
        """探测失败时保留现有选择，避免瞬时错误抹掉设备状态。"""
        self.refresh_button.setEnabled(True)
        self.refresh_button.setText("刷新")
        self.refresh_button.setToolTip(message)
        if self._coordinator.devices:
            running = any(device.is_running for device in self._coordinator.devices)
            self._set_instance_status("● 实例：刷新失败（保留上次结果）", running=running)
        else:
            self._set_instance_status("● 实例：刷新失败")
        self.instance_status_label.setToolTip(message)
        self.ui_refresh_requested.emit()

    def _on_device_activated(self, index: int) -> None:
        """记录用户对实例的显式选择。"""
        if self.device_combo.itemData(index):
            self._device_selected_explicitly = True

    def _on_device_changed(self, index: int) -> None:
        """设备下拉选择变化"""
        if index < 0 or not self._coordinator.devices:
            return

        device = self.device_combo.itemData(index)
        if device and device.adb_port:
            self.port_label.setText(str(device.adb_port))
            state = "运行中" if device.is_running else "未运行"
            self._set_instance_status(f"● 实例：{state}", running=device.is_running)
        else:
            self.port_label.setText("(自动探测)")
            self._set_instance_status("● 实例：未探测到")
        self.ui_refresh_requested.emit()

    def _set_instance_status(self, text: str, *, running: bool = False) -> None:
        """更新实例状态文字及颜色，运行中的实例使用绿色强调。"""
        color = "#27ae60" if running else "#777"
        self.instance_status_label.setText(text)
        self.instance_status_label.setStyleSheet(f"color: {color}; font-size: 12px;")

    def _on_connect_toggle(self) -> None:
        """连接/断开切换"""
        if self._coordinator.connection_state[0] == "connected":
            self._disconnect_emulator()
        else:
            self._connect_emulator()

    def _connect_emulator(self) -> None:
        """连接选中的模拟器。"""
        device = self.device_combo.currentData()
        self.connect_button.setEnabled(False)
        self.connect_button.setText("连接中...")
        error = self._coordinator.connect(device, self._device_selected_explicitly)
        if error:
            self.ui_refresh_requested.emit()
            QMessageBox.warning(self.window(), "请选择设备", error)

    def _on_connection_finished(self, ok: bool, message: str) -> None:
        if not ok:
            QMessageBox.warning(self.window(), "连接失败", message)
        self.connect_button.setEnabled(True)
        self.ui_refresh_requested.emit()

    def _disconnect_emulator(self) -> None:
        """断开模拟器。"""
        self.connect_button.setEnabled(False)
        self.connect_button.setText("断开中...")
        self._coordinator.disconnect()

    def _on_disconnection_finished(self, _ok: bool, _message: str) -> None:
        self.connect_button.setEnabled(True)
        self.ui_refresh_requested.emit()

    def _on_test_selected_device(self) -> None:
        """测试当前选择的设备连通性，不改变共享会话或配置。"""
        device = self.device_combo.currentData()
        error = self._coordinator.test_device(
            self.adb_path_edit.property("raw_path") or "",
            device,
        )
        if error:
            QMessageBox.warning(self.window(), "设备测试", error)
            return

        self.test_button.setEnabled(False)
        self.test_button.setText("测试中...")

    def _on_device_tested(self, ok: bool, target: str, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
        if ok:
            show_toast(self.window(), f"设备测试成功：{target}\n{message}", duration=2500)
        else:
            QMessageBox.warning(self.window(), "设备测试失败", f"无法连接所选设备 {target}：\n{message}")
        self.ui_refresh_requested.emit()

    def _on_operation_failed(self, operation: str, _message: str) -> None:
        """仅处理设备侧操作的控件恢复；识别侧分支由识别页处理。"""
        if operation in {"detect_adb", "refresh_devices", "connect", "disconnect", "test_device"}:
            self.restore_after_operation(operation)
