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

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from src.business.emulator.capture_service import CaptureService
from src.business.emulator.emulator_operation_service import EmulatorOperationService
from src.business.emulator.mumu_config_coordinator import MumuConfigCoordinator
from src.business.recognition.ocr_service import OcrService
from src.ui.configuration.mumu_device_page import MumuDevicePage
from src.ui.configuration.mumu_recognition_page import MumuRecognitionPage
from src.ui.shared.widgets import DialogFooter, PageHeader, close_after_toast

logger = logging.getLogger(__name__)


class MumuConfigDialog(QDialog):
    """模拟器配置对话框"""

    def __init__(
        self,
        config: dict,
        capture_service: CaptureService,
        ocr_service: OcrService,
        operation_service: EmulatorOperationService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._coordinator = MumuConfigCoordinator(
            config,
            self._capture_service,
            self._ocr_service,
            operation_service,
            self,
        )
        self._operation_service = self._coordinator.operation_service

        self.setWindowTitle("模拟器配置")
        self.setMinimumWidth(760)
        self.setMinimumHeight(620)
        self.resize(900, 680)
        self._setup_ui()
        self._coordinator.connection_state_changed.connect(self._on_connection_changed)
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
            "border-left: 3px solid #2f6ea5; font-weight: bold; }"
            "QListWidget#mumuConfigNavigation::item:hover:!selected { background: #e5ebf1; }"
        )
        body.addWidget(self._page_nav)
        self._page_stack = QStackedWidget()
        body.addWidget(self._page_stack, 1)

        self._device_page = MumuDevicePage(self._coordinator, self)
        self._device_page.ui_refresh_requested.connect(self._update_ui)
        device_scroll = self._page_scroll(self._device_page)
        self._page_stack.addWidget(device_scroll)

        self._recognition_page = MumuRecognitionPage(self._coordinator, self)
        self._recognition_page.ui_refresh_requested.connect(self._update_ui)
        recognition_scroll = self._page_scroll(self._recognition_page)
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
        should_auto_detect = not self._config.get("mumu_adb_path", "")

        self._device_page.load_config(self._config)
        self._recognition_page.load_config(self._config)

        self._device_page.sync_capture_config()
        self._device_page.refresh_devices()

        self._update_ui()
        if should_auto_detect:
            self._device_page.start_detect()

    # ────────────────────────────────────────────────
    # ADB 连接管理（设备页内，见 mumu_device_page.py）
    # ────────────────────────────────────────────────

    def _on_connection_changed(self, _state: str, _detail: str) -> None:
        """共享 CaptureService 会话变化时刷新配置页。"""
        self._update_ui()

    def _on_operation_failed(self, operation: str, message: str) -> None:
        """恢复异常中断的后台操作对应控件，并统一弹出失败提示。"""
        self._device_page.restore_after_operation(operation)
        QMessageBox.warning(self, "模拟器操作失败", message)

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
        self._device_page.update_state(state)
        self._recognition_page.update_state(state)

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
            raw_path, device, selected_explicitly = self._device_page.collect_device_draft()
            form_values = {"mumu_adb_path": raw_path}
            form_values.update(self._recognition_page.collect_parameters())
            _, error = self._coordinator.save_config(form_values, device, selected_explicitly)
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

    def done(self, result: int) -> None:
        """关闭时停止接收后台操作结果，避免更新已关闭的对话框。"""
        self._coordinator.shutdown()
        super().done(result)
