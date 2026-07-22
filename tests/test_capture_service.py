"""CaptureService 连接状态测试。"""

from __future__ import annotations

from PIL import Image

from src.business.capture_service import CaptureService


def test_config_change_discards_previous_connection() -> None:
    service = CaptureService()
    states: list[tuple[str, str]] = []
    service.connection_changed.connect(lambda state, detail: states.append((state, detail)))

    service.update_config({"mumu_adb_path": "adb-a.exe", "mumu_adb_port": 16448})
    service.capture._connected = True
    service.capture._device_serial = "127.0.0.1:16448"
    service.update_config({"mumu_adb_path": "adb-a.exe", "mumu_adb_port": 16416})

    assert not service.is_connected
    assert service.capture.device_serial == ""
    assert states[-1][0] == "disconnected"


def test_sync_connection_state_marks_offline() -> None:
    service = CaptureService()
    states: list[tuple[str, str]] = []
    service.connection_changed.connect(lambda state, detail: states.append((state, detail)))
    service.update_config({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448})
    service.capture._connected = False

    service.sync_connection_state("error: device offline")

    assert states[-1] == ("offline", "error: device offline")


def test_capture_can_skip_ocr_and_return_saved_image(monkeypatch, tmp_path) -> None:
    class FakeCapture:
        connected = True

        @staticmethod
        def screencap_full():
            return True, Image.new("RGB", (10, 20))

    service = CaptureService()
    service.capture = FakeCapture()
    completed: list[dict] = []
    service.capture_completed.connect(completed.append)
    monkeypatch.setattr("src.business.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr("src.business.capture_service.save_image", lambda image, path: (True, ""))

    service._execute_capture(perform_ocr=False)

    assert service._ocr_worker is None
    assert completed[0]["ocr_results"] is None
    assert not completed[0]["ocr_matched"]


def test_capture_screenshot_reuses_and_connects_shared_session() -> None:
    class FakeCapture:
        connected = False
        device_serial = "127.0.0.1:16448"

        def __init__(self) -> None:
            self.connect_calls = 0

        def connect(self):
            self.connect_calls += 1
            self.connected = True
            return True, "连接成功"

        @staticmethod
        def screencap_full():
            return True, Image.new("RGB", (10, 20))

    service = CaptureService()
    capture = FakeCapture()
    service.capture = capture

    ok, image = service.capture_screenshot()

    assert ok
    assert image.size == (10, 20)
    assert capture.connect_calls == 1
    assert service.connection_state == ("connected", "127.0.0.1:16448")
