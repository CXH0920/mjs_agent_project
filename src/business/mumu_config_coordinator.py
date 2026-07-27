"""模拟器配置的状态与服务协调。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.business.capture_service import CaptureService
from src.business.emulator_operation_service import EmulatorOperationService
from src.business.ocr_service import OcrService
from src.capture.prober import MuMuDeviceInfo
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout


@dataclass(frozen=True)
class TemplateStatus:
    """模板的当前加载状态，仅供配置视图渲染。"""

    loaded: bool
    path: Path | None = None


class MumuConfigCoordinator(QObject):
    """协调模拟器配置草稿、共享服务和后台操作，不依赖具体控件。"""

    adb_detected = Signal(bool, str, str)
    devices_changed = Signal(object)
    device_refresh_failed = Signal(str)
    connection_finished = Signal(bool, str)
    disconnection_finished = Signal(bool, str)
    device_tested = Signal(bool, str, str)
    connection_state_changed = Signal(str, str)
    template_screenshot_ready = Signal(str, object)
    template_screenshot_failed = Signal(str, str)
    template_capture_finished = Signal(str)
    roi_layout_screenshot_ready = Signal(str, object)
    roi_layout_screenshot_failed = Signal(str, str)
    roi_layout_capture_finished = Signal(str)
    operation_failed = Signal(str, str)

    def __init__(
        self,
        config: dict,
        capture_service: CaptureService,
        ocr_service: OcrService,
        operation_service: EmulatorOperationService | None = None,
        parent=None,
        roi_config: OcrRoiConfig | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = dict(config)
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._operation_service = operation_service or EmulatorOperationService(
            capture_service,
            self,
        )
        self._devices: list[MuMuDeviceInfo] = []
        self._template_captures_in_progress: set[str] = set()
        self._roi_layout_captures_in_progress: set[str] = set()
        if roi_config is not None:
            self._roi_config = roi_config
        elif hasattr(capture_service, "roi_config"):
            self._roi_config = capture_service.roi_config
        else:
            self._roi_config = OcrRoiConfig()

        self._capture_service.connection_changed.connect(self.connection_state_changed.emit)
        self._operation_service.adb_detected.connect(self._on_adb_detected)
        self._operation_service.devices_refreshed.connect(self._on_devices_refreshed)
        self._operation_service.device_refresh_failed.connect(self.device_refresh_failed.emit)
        self._operation_service.connection_finished.connect(self.connection_finished.emit)
        self._operation_service.disconnection_finished.connect(self.disconnection_finished.emit)
        self._operation_service.device_tested.connect(self.device_tested.emit)
        self._operation_service.screenshot_ready.connect(self._on_screenshot_ready)
        self._operation_service.screenshot_failed.connect(self._on_screenshot_failed)
        self._operation_service.operation_failed.connect(self._on_operation_failed)

        self.sync_capture_config()

    @property
    def config(self) -> dict:
        """返回当前配置草稿的副本。"""
        return dict(self._config)

    @property
    def devices(self) -> tuple[MuMuDeviceInfo, ...]:
        """返回最近一次成功探测到的设备。"""
        return tuple(self._devices)

    @property
    def operation_service(self) -> EmulatorOperationService:
        """保留后台服务公开入口，便于现有调用方注入和测试。"""
        return self._operation_service

    @property
    def connection_state(self) -> tuple[str, str]:
        return self._capture_service.connection_state

    @property
    def capture(self):
        return self._capture_service.capture

    def sync_capture_config(self) -> None:
        """将当前草稿的 ADB 配置应用到共享截图会话。"""
        self._capture_service.update_config(self._config)

    def update_adb_path(self, adb_path: str) -> None:
        """更新编辑中的 ADB 路径，并重建需要的共享会话。"""
        self._config["mumu_adb_path"] = adb_path
        self.sync_capture_config()

    def detect_adb(self) -> None:
        self._operation_service.detect_adb()

    def refresh_devices(self) -> None:
        self._operation_service.refresh_devices()

    def connect(
        self,
        device: MuMuDeviceInfo | None,
        selected_explicitly: bool,
    ) -> str:
        """连接当前选择的设备，返回无法发起连接时的提示。"""
        error = self._device_selection_error(selected_explicitly)
        if error:
            return error
        if selected_explicitly and device and device.adb_port:
            self._capture_service.set_target_port(device.adb_port)
        self._operation_service.connect()
        return ""

    def disconnect(self) -> None:
        self._operation_service.disconnect()

    def test_device(self, adb_path: str, device: MuMuDeviceInfo | None) -> str:
        """测试指定设备，不修改共享会话；失败前置条件返回提示。"""
        if not adb_path:
            return "请先配置 ADB 路径。"
        if not device or not device.adb_port:
            return "请选择一个带有效 ADB 端口的实例后再测试。"
        self._operation_service.test_device(adb_path, device.adb_port)
        return ""

    def start_template_capture(self, template_name: str) -> bool:
        """开始模板截图，同一模板的重复请求会被忽略。"""
        if template_name in self._template_captures_in_progress:
            return False
        self._template_captures_in_progress.add(template_name)
        try:
            self._operation_service.capture_template_screenshot(template_name)
        except Exception:
            self.finish_template_capture(template_name)
            raise
        return True

    def finish_template_capture(self, template_name: str) -> None:
        """结束截图后的 ROI 选择和模板写入生命周期。"""
        if template_name in self._template_captures_in_progress:
            self._template_captures_in_progress.remove(template_name)
            self.template_capture_finished.emit(template_name)

    def is_template_capture_in_progress(self, template_name: str) -> bool:
        return template_name in self._template_captures_in_progress

    def start_roi_layout_capture(self, page_type: str) -> bool:
        """获取当前页面截图，用于编辑该页面的全部 OCR 识别区域。"""
        self._roi_config.layout_for(page_type)
        if page_type in self._roi_layout_captures_in_progress:
            return False
        self._roi_layout_captures_in_progress.add(page_type)
        try:
            self._operation_service.capture_template_screenshot(f"roi_layout:{page_type}")
        except Exception:
            self.finish_roi_layout_capture(page_type)
            raise
        return True

    def finish_roi_layout_capture(self, page_type: str) -> None:
        if page_type in self._roi_layout_captures_in_progress:
            self._roi_layout_captures_in_progress.remove(page_type)
            self.roi_layout_capture_finished.emit(page_type)

    def is_roi_layout_capture_in_progress(self, page_type: str) -> bool:
        return page_type in self._roi_layout_captures_in_progress

    def roi_layout(self, page_type: str) -> OcrRoiLayout:
        return self._roi_config.layout_for(page_type)

    def save_roi_layout(self, page_type: str, layout: OcrRoiLayout) -> None:
        self._roi_config.save_layout(page_type, layout)

    def reset_roi_layout(self, page_type: str) -> None:
        self._roi_config.reset_layout(page_type)

    def template_status(self, template_name: str = "hero_selection") -> TemplateStatus:
        """读取模板状态，供视图决定文本与颜色。"""
        if not self._ocr_service.is_template_loaded(template_name):
            return TemplateStatus(False)
        return TemplateStatus(True, Path(self._ocr_service.template_path(template_name)))

    def select_template(self, file_path: str, template_name: str = "hero_selection") -> TemplateStatus:
        """加载用户选择的模板文件。"""
        self._ocr_service.select_template(file_path, template_name)
        return self.template_status(template_name)

    def create_template(self, image, roi: tuple[int, int, int, int], template_name: str) -> TemplateStatus:
        """保存经 UI 框选后的模板区域。"""
        self._ocr_service.create_template(image, roi, template_name)
        return self.template_status(template_name)

    def resume_poll(self) -> bool:
        """仅在轮询暂停时恢复。"""
        if self._ocr_service.poll_state != "paused":
            return False
        self._ocr_service.resume_poll()
        return True

    def poll_is_paused(self) -> bool:
        return self._ocr_service.poll_state == "paused"

    def save_config(
        self,
        form_values: dict,
        device: MuMuDeviceInfo | None,
        selected_explicitly: bool,
    ) -> tuple[dict | None, str]:
        """合并表单配置并校验多实例选择，成功时更新配置草稿。"""
        error = self._device_selection_error(selected_explicitly)
        if error:
            return None, error

        config = dict(self._config)
        config.update(form_values)
        configured_port = self._config.get("mumu_adb_port", 0)
        if selected_explicitly and device and device.adb_port:
            config["mumu_adb_port"] = device.adb_port
        elif configured_port == 0:
            config["mumu_adb_port"] = 0
        elif device and device.adb_port:
            config["mumu_adb_port"] = device.adb_port
        self._config = config
        return self.config, ""

    def shutdown(self) -> None:
        self._operation_service.shutdown()

    def _on_adb_detected(self, success: bool, adb_path: str, message: str) -> None:
        if success:
            self.update_adb_path(adb_path)
            self.refresh_devices()
        self.adb_detected.emit(success, adb_path, message)

    def _on_devices_refreshed(self, devices: list[MuMuDeviceInfo]) -> None:
        self._devices = list(devices)
        self.devices_changed.emit(self.devices)

    def _on_screenshot_ready(self, request_name: str, image) -> None:
        if request_name.startswith("roi_layout:"):
            self.roi_layout_screenshot_ready.emit(request_name.split(":", 1)[1], image)
            return
        self.template_screenshot_ready.emit(request_name, image)

    def _on_screenshot_failed(self, request_name: str, message: str) -> None:
        if request_name.startswith("roi_layout:"):
            page_type = request_name.split(":", 1)[1]
            self.finish_roi_layout_capture(page_type)
            self.roi_layout_screenshot_failed.emit(page_type, message)
            return
        template_name = request_name
        self.finish_template_capture(template_name)
        self.template_screenshot_failed.emit(template_name, message)

    def _on_operation_failed(self, operation: str, message: str) -> None:
        if operation.startswith("capture_template:"):
            request_name = operation.split(":", 1)[1]
            if request_name.startswith("roi_layout:"):
                self.finish_roi_layout_capture(request_name.split(":", 1)[1])
            else:
                self.finish_template_capture(request_name)
        self.operation_failed.emit(operation, message)

    def _device_selection_error(self, selected_explicitly: bool) -> str:
        running_devices = [device for device in self._devices if device.is_running and device.adb_port]
        if self._config.get("mumu_adb_port", 0) == 0 and len(running_devices) > 1 and not selected_explicitly:
            return "检测到多个运行中的 MuMu 实例，请先选择要使用的实例。"
        return ""
