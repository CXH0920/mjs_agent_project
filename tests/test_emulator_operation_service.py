"""模拟器后台操作服务测试。"""

from __future__ import annotations

from threading import Event
from time import monotonic

from PySide6.QtWidgets import QApplication

from src.business.emulator.emulator_operation_service import EmulatorOperationService
from src.capture.prober import MuMuDeviceInfo


class _CaptureService:
    def __init__(self) -> None:
        self.connected = 0

    def connect_emulator(self) -> tuple[bool, str]:
        self.connected += 1
        return False, "设备离线"

    def disconnect_emulator(self) -> tuple[bool, str]:
        return True, "已断开"

    def capture_screenshot(self) -> tuple[bool, object]:
        return True, "image"


def _wait(event: Event) -> bool:
    app = QApplication.instance() or QApplication([])
    deadline = monotonic() + 2
    while monotonic() < deadline:
        app.processEvents()
        if event.wait(0.01):
            app.processEvents()
            return True
    return False


def test_refresh_devices_runs_through_operation_service(monkeypatch) -> None:
    devices = [MuMuDeviceInfo("1", "实例", 16448, True)]
    monkeypatch.setattr("src.business.emulator.emulator_operation_service.probe_all_devices_with_status", lambda: (devices, ""))
    service = EmulatorOperationService(_CaptureService())
    completed = Event()
    received: list[list[MuMuDeviceInfo]] = []
    service.devices_refreshed.connect(lambda result: (received.append(result), completed.set()))

    service.refresh_devices()

    assert _wait(completed)
    assert received == [devices]
    service.shutdown()


def test_refresh_failure_is_reported_without_device_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.business.emulator.emulator_operation_service.probe_all_devices_with_status",
        lambda: ([], "MuMuManager 查询失败（退出码 3221226505）"),
    )
    service = EmulatorOperationService(_CaptureService())
    completed = Event()
    errors: list[str] = []
    results: list[object] = []
    service.device_refresh_failed.connect(lambda message: (errors.append(message), completed.set()))
    service.devices_refreshed.connect(results.append)

    service.refresh_devices()

    assert _wait(completed)
    assert errors == ["MuMuManager 查询失败（退出码 3221226505）"]
    assert results == []
    service.shutdown()


def test_connection_failure_and_template_capture_are_reported() -> None:
    service = EmulatorOperationService(_CaptureService())
    connection_completed = Event()
    screenshot_completed = Event()
    connection_results: list[tuple[bool, str]] = []
    screenshots: list[tuple[str, object]] = []
    errors: list[tuple[str, str]] = []
    service.connection_finished.connect(lambda ok, message: (connection_results.append((ok, message)), connection_completed.set()))
    service.screenshot_ready.connect(lambda name, image: (screenshots.append((name, image)), screenshot_completed.set()))
    service.operation_failed.connect(lambda operation, message: errors.append((operation, message)))

    service.connect()
    assert _wait(connection_completed)

    service.capture_template_screenshot("match_guide")
    assert _wait(screenshot_completed)
    assert connection_results == [(False, "设备离线")]
    assert screenshots == [("match_guide", "image")]
    assert errors == []
    service.shutdown()
