"""模拟器配置协调器的行为测试。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.business.mumu_config_coordinator import MumuConfigCoordinator
from src.capture.prober import MuMuDeviceInfo


class _CaptureService(QObject):
    connection_changed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.connection_state = ("disconnected", "")
        self.capture = object()
        self.configs: list[dict] = []
        self.target_ports: list[int] = []

    def update_config(self, config: dict) -> None:
        self.configs.append(dict(config))

    def set_target_port(self, port: int) -> None:
        self.target_ports.append(port)


class _OcrService:
    poll_state = "stopped"

    def __init__(self, template_path: Path) -> None:
        self._template_path = template_path
        self.loaded: set[str] = set()
        self.created: list[tuple[str, tuple[int, int, int, int]]] = []
        self.selected: list[tuple[str, str]] = []
        self.resume_count = 0

    def is_template_loaded(self, template_name: str = "hero_selection") -> bool:
        return template_name in self.loaded

    def template_path(self, template_name: str = "hero_selection") -> Path:
        return self._template_path / f"{template_name}.png"

    def create_template(self, _image, roi: tuple[int, int, int, int], template_name: str) -> None:
        self.created.append((template_name, roi))
        self.loaded.add(template_name)

    def select_template(self, file_path: str, template_name: str = "hero_selection") -> None:
        self.selected.append((file_path, template_name))
        self.loaded.add(template_name)

    def resume_poll(self) -> None:
        self.resume_count += 1


class _OperationService(QObject):
    adb_detected = Signal(bool, str, str)
    devices_refreshed = Signal(object)
    device_refresh_failed = Signal(str)
    connection_finished = Signal(bool, str)
    disconnection_finished = Signal(bool, str)
    device_tested = Signal(bool, str, str)
    screenshot_ready = Signal(str, object)
    screenshot_failed = Signal(str, str)
    operation_failed = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.connect_count = 0
        self.refresh_count = 0
        self.template_requests: list[str] = []
        self.test_requests: list[tuple[str, int]] = []
        self.closed = False

    def detect_adb(self) -> None:
        pass

    def refresh_devices(self) -> None:
        self.refresh_count += 1

    def connect(self) -> None:
        self.connect_count += 1

    def disconnect(self) -> None:
        pass

    def test_device(self, path: str, port: int) -> None:
        self.test_requests.append((path, port))

    def capture_template_screenshot(self, template_name: str) -> None:
        self.template_requests.append(template_name)

    def shutdown(self) -> None:
        self.closed = True


def _coordinator(tmp_path: Path) -> tuple[MumuConfigCoordinator, _CaptureService, _OcrService, _OperationService]:
    capture = _CaptureService()
    ocr = _OcrService(tmp_path)
    operation = _OperationService()
    coordinator = MumuConfigCoordinator(
        {"mumu_adb_path": "adb.exe", "mumu_adb_port": 0},
        capture,
        ocr,
        operation,
    )
    return coordinator, capture, ocr, operation


def test_connect_and_save_require_explicit_multi_instance_selection(tmp_path: Path) -> None:
    coordinator, capture, _ocr, operation = _coordinator(tmp_path)
    devices = [
        MuMuDeviceInfo("1", "实例 A", 16448, True),
        MuMuDeviceInfo("2", "实例 B", 16416, True),
    ]
    operation.devices_refreshed.emit(devices)

    assert "多个运行中的 MuMu 实例" in coordinator.connect(None, False)
    config, error = coordinator.save_config({}, None, False)
    assert config is None
    assert "多个运行中的 MuMu 实例" in error

    assert coordinator.connect(devices[1], True) == ""
    config, error = coordinator.save_config({}, devices[1], True)

    assert error == ""
    assert capture.target_ports == [16416]
    assert operation.connect_count == 1
    assert config["mumu_adb_port"] == 16416


def test_template_lifecycle_and_service_delegation(tmp_path: Path) -> None:
    coordinator, _capture, ocr, operation = _coordinator(tmp_path)
    finished: list[str] = []
    coordinator.template_capture_finished.connect(finished.append)

    assert coordinator.start_template_capture("match_guide")
    assert not coordinator.start_template_capture("match_guide")
    assert coordinator.is_template_capture_in_progress("match_guide")
    assert operation.template_requests == ["match_guide"]

    coordinator.create_template(object(), (1, 2, 3, 4), "match_guide")
    coordinator.finish_template_capture("match_guide")
    coordinator.select_template("chosen.png")

    assert ocr.created == [("match_guide", (1, 2, 3, 4))]
    assert ocr.selected == [("chosen.png", "hero_selection")]
    assert coordinator.template_status("match_guide").loaded
    assert finished == ["match_guide"]
    assert not coordinator.is_template_capture_in_progress("match_guide")
