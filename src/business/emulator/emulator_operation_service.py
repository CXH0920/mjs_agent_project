"""模拟器后台操作服务（多品牌，品牌探测见 src/capture/probers）：仅提供屏幕读取能力。

本模块**故意不实现**以下功能，遵守项目法律红线（见 CLAUDE.md 与 AGENTS.md）：
- ADB 输入（tap、click、input）
- 自动点击、自动选将、自动战斗
- 游戏进程注入、内存修改、hook
- 反作弊绕过
任何请求此类功能的 Issue/PR 都将被拒绝合并。
如需扩展，请先更新 LICENSE（附加使用条款）、TERMS.md、CLAUDE.md、AGENTS.md，
并通过开发者本人审批。
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, TypeVar

from PySide6.QtCore import QObject, Signal
from src.business.emulator.capture_service import CaptureService
from src.business.recognition.session_guard import OneShotToken
from src.capture.adb_screen import AdbCapture
from src.capture.probers import (
    DEFAULT_EMULATOR_TYPE,
    probe_adb,
    probe_all_devices_with_status,
    test_adb_path,
)

logger = logging.getLogger(__name__)

_Result = TypeVar("_Result")


class EmulatorOperationService(QObject):
    """串行执行 ADB 探测、连接、测试和模板截图等耗时操作。"""

    adb_detected = Signal(bool, str, str)  # (success, adb_path, message)
    adb_reprobed = Signal(str)  # 品牌切换静默重探结果（空 = 未找到，沿用旧路径）
    devices_refreshed = Signal(object)  # list[EmulatorDeviceInfo]
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
        self._shutdown_token = OneShotToken()  # 关停一次性守卫（P0-3 收口）

    def detect_adb(self, emulator_type: str = DEFAULT_EMULATOR_TYPE) -> None:
        def work() -> tuple[bool, str, str]:
            adb_path = probe_adb(emulator_type)
            if not adb_path:
                return False, "", "未找到 ADB，请手动设置路径"
            ok, message = test_adb_path(adb_path)
            return ok, adb_path, message

        self._submit(self._probe_executor, "detect_adb", work, lambda result: self.adb_detected.emit(*result))

    def reprobe_adb(self, emulator_type: str = DEFAULT_EMULATOR_TYPE) -> None:
        """品牌切换后的静默 ADB 重探：只回传路径，不弹提示、不触发设备刷新。"""

        self._submit(
            self._probe_executor,
            "reprobe_adb",
            lambda: probe_adb(emulator_type),
            self.adb_reprobed.emit,
        )

    def refresh_devices(self, emulator_type: str = DEFAULT_EMULATOR_TYPE) -> None:
        def completed(result: tuple[list, str]) -> None:
            devices, error = result
            if error:
                self.device_refresh_failed.emit(error)
            else:
                self.devices_refreshed.emit(devices)

        self._submit(
            self._probe_executor,
            "refresh_devices",
            lambda: probe_all_devices_with_status(emulator_type),
            completed,
        )

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
                ok, message = capture.connect(validate_capture=True)
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
        self._shutdown_token.mark()
        self._probe_executor.shutdown(wait=False, cancel_futures=True)
        self._adb_executor.shutdown(wait=False, cancel_futures=True)

    def _submit(
        self,
        executor: ThreadPoolExecutor,
        operation: str,
        work: Callable[[], _Result],
        completed: Callable[[_Result], None],
    ) -> None:
        if self._shutdown_token.spent:
            return
        future = executor.submit(work)
        future.add_done_callback(lambda item: self._complete(operation, item, completed))

    def _complete(
        self,
        operation: str,
        future: Future[_Result],
        completed: Callable[[_Result], None],
    ) -> None:
        if self._shutdown_token.spent:
            return
        try:
            completed(future.result())
        except Exception as exc:
            logger.exception("模拟器后台操作异常")
            self.operation_failed.emit(operation, str(exc))
