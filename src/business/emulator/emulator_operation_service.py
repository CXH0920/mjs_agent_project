"""MuMu 模拟器配置页的后台操作服务。"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, TypeVar

from PySide6.QtCore import QObject, Signal

from src.business.emulator.capture_service import CaptureService
from src.capture.adb_screen import AdbCapture
from src.capture.prober import probe_all_devices_with_status, probe_mumu_adb, test_adb_path

logger = logging.getLogger(__name__)

_Result = TypeVar("_Result")


class EmulatorOperationService(QObject):
    """串行执行 ADB 探测、连接、测试和模板截图等耗时操作。"""

    adb_detected = Signal(bool, str, str)  # (success, adb_path, message)
    devices_refreshed = Signal(object)  # list[MuMuDeviceInfo]
    device_refresh_failed = Signal(str)
    connection_finished = Signal(bool, str)
    disconnection_finished = Signal(bool, str)
    device_tested = Signal(bool, str, str)  # (success, target, message)
    screenshot_ready = Signal(str, object)  # (template_name, PIL.Image)
    screenshot_failed = Signal(str, str)  # (template_name, message)
    operation_failed = Signal(str, str)  # (operation, message)

    def __init__(self, capture_service: CaptureService, parent=None) -> None:
        super().__init__(parent)
        self._capture_service = capture_service
        self._probe_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emulator-probe")
        self._adb_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emulator-adb")
        self._closed = False

    def detect_adb(self) -> None:
        def work() -> tuple[bool, str, str]:
            adb_path = probe_mumu_adb()
            if not adb_path:
                return False, "", "未找到 ADB，请手动设置路径"
            ok, message = test_adb_path(adb_path)
            return ok, adb_path, message

        self._submit(self._probe_executor, "detect_adb", work, lambda result: self.adb_detected.emit(*result))

    def refresh_devices(self) -> None:
        def completed(result: tuple[list, str]) -> None:
            devices, error = result
            if error:
                self.device_refresh_failed.emit(error)
            else:
                self.devices_refreshed.emit(devices)

        self._submit(self._probe_executor, "refresh_devices", probe_all_devices_with_status, completed)

    def connect(self) -> None:
        self._submit(
            self._adb_executor,
            "connect",
            self._capture_service.connect_emulator,
            lambda result: self.connection_finished.emit(*result),
        )

    def disconnect(self) -> None:
        self._submit(
            self._adb_executor,
            "disconnect",
            self._capture_service.disconnect_emulator,
            lambda result: self.disconnection_finished.emit(*result),
        )

    def test_device(self, adb_path: str, port: int) -> None:
        target = f"127.0.0.1:{port}"

        def work() -> tuple[bool, str, str]:
            capture = AdbCapture(adb_path=adb_path, adb_port=port)
            try:
                ok, message = capture.connect()
                if not ok:
                    return False, target, message
                ok, message = capture.check_device()
                return ok, target, message
            finally:
                if capture.connected:
                    capture.disconnect()

        self._submit(self._adb_executor, "test_device", work, lambda result: self.device_tested.emit(*result))

    def capture_template_screenshot(self, template_name: str) -> None:
        def work() -> tuple[bool, object]:
            return self._capture_service.capture_screenshot()

        def completed(result: tuple[bool, object]) -> None:
            ok, payload = result
            if ok:
                self.screenshot_ready.emit(template_name, payload)
            else:
                self.screenshot_failed.emit(template_name, str(payload))

        self._submit(self._adb_executor, f"capture_template:{template_name}", work, completed)

    def shutdown(self) -> None:
        """停止接收新任务，应用退出时不再向已销毁的 UI 发射结果。"""
        self._closed = True
        self._probe_executor.shutdown(wait=False, cancel_futures=True)
        self._adb_executor.shutdown(wait=False, cancel_futures=True)

    def _submit(
        self,
        executor: ThreadPoolExecutor,
        operation: str,
        work: Callable[[], _Result],
        completed: Callable[[_Result], None],
    ) -> None:
        if self._closed:
            return
        future = executor.submit(work)
        future.add_done_callback(lambda item: self._complete(operation, item, completed))

    def _complete(
        self,
        operation: str,
        future: Future[_Result],
        completed: Callable[[_Result], None],
    ) -> None:
        if self._closed:
            return
        try:
            completed(future.result())
        except Exception as exc:
            logger.exception("模拟器后台操作异常")
            self.operation_failed.emit(operation, str(exc))
