"""模拟器后台操作服务测试。"""

from __future__ import annotations

from threading import Event
from time import monotonic

from PySide6.QtWidgets import QApplication
from src.business.emulator.emulator_operation_service import EmulatorOperationService
from src.capture.emulator_discovery import EmulatorDeviceInfo


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
    devices = [EmulatorDeviceInfo("1", "实例", 16448, True)]
    monkeypatch.setattr("src.business.emulator.emulator_operation_service.discover_devices_with_status", lambda emulator_type="mumu": (devices, ""))
    service = EmulatorOperationService(_CaptureService())
    completed = Event()
    received: list[list[EmulatorDeviceInfo]] = []
    service.devices_refreshed.connect(
        lambda devices, _requested_type: (received.append(devices), completed.set()))

    service.refresh_devices()

    assert _wait(completed)
    assert received == [devices]
    service.shutdown()


def test_refresh_failure_is_reported_without_device_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.business.emulator.emulator_operation_service.discover_devices_with_status",
        lambda emulator_type="mumu": ([], "MuMuManager 查询失败（退出码 3221226505）"),
    )
    service = EmulatorOperationService(_CaptureService())
    completed = Event()
    errors: list[str] = []
    results: list[object] = []
    service.device_refresh_failed.connect(
        lambda message, _requested_type: (errors.append(message), completed.set()))
    service.devices_refreshed.connect(results.append)

    service.refresh_devices()

    assert _wait(completed)
    assert errors == ["MuMuManager 查询失败（退出码 3221226505）"]
    assert results == []
    service.shutdown()


def test_reprobe_adb_reports_path_without_device_refresh(monkeypatch) -> None:
    """R3：品牌切换静默重探只回传路径，不触发设备刷新。"""
    monkeypatch.setattr(
        "src.business.emulator.emulator_operation_service.resolve_adb_path",
        lambda emulator_type="mumu": "D:/tools/HD-Adb.exe",
    )
    service = EmulatorOperationService(_CaptureService())
    completed = Event()
    received: list[str] = []
    refreshed: list[object] = []
    service.adb_reprobed.connect(lambda path: (received.append(path), completed.set()))
    service.devices_refreshed.connect(refreshed.append)

    service.reprobe_adb("bluestacks")

    assert _wait(completed)
    assert received == ["D:/tools/HD-Adb.exe"]
    assert refreshed == []
    service.shutdown()


def test_duplicate_refresh_submissions_are_coalesced(monkeypatch) -> None:
    """同键（同品牌）重复提交在执行中直接丢弃，不排队累积（逍遥慢探测连点场景）。"""
    started = Event()
    release = Event()
    calls: list[str] = []

    def slow_probe(emulator_type: str = "mumu"):
        calls.append(emulator_type)
        started.set()
        release.wait(2)
        return [], ""

    monkeypatch.setattr(
        "src.business.emulator.emulator_operation_service.discover_devices_with_status", slow_probe)
    service = EmulatorOperationService(_CaptureService())
    completed = Event()
    service.devices_refreshed.connect(lambda devices, _requested_type: completed.set())

    service.refresh_devices("memu")
    assert _wait(started)
    service.refresh_devices("memu")  # 执行中 → 丢弃
    service.refresh_devices("memu")  # 执行中 → 丢弃
    release.set()

    assert _wait(completed)
    assert calls == ["memu"]
    service.shutdown()


def test_refresh_submissions_with_different_brands_both_run(monkeypatch) -> None:
    """不同键（不同品牌）提交互不去重，各自执行并回传结果。"""
    calls: list[str] = []

    def probe(emulator_type: str = "mumu"):
        calls.append(emulator_type)
        return [], ""

    monkeypatch.setattr(
        "src.business.emulator.emulator_operation_service.discover_devices_with_status", probe)
    service = EmulatorOperationService(_CaptureService())
    # 等第二次回传再断言：单线程执行器串行执行两个任务，只等首次回传就断言
    # 会与第二个任务的执行竞态（CI 慢机上曾输掉竞态误报失败）
    refreshed_count: list[int] = []
    both_completed = Event()

    def _on_refreshed(_devices, _requested_type) -> None:
        refreshed_count.append(1)
        if len(refreshed_count) == 2:
            both_completed.set()

    service.devices_refreshed.connect(_on_refreshed)

    service.refresh_devices("mumu")
    service.refresh_devices("bluestacks")

    assert _wait(both_completed)
    assert calls == ["mumu", "bluestacks"]
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
