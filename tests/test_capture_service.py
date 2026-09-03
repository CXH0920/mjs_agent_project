"""CaptureService 连接状态测试。"""

from __future__ import annotations

from types import SimpleNamespace

from PIL import Image

from src.business.emulator.capture_service import CaptureService
from src.business.recognition.ocr_worker import OfficialImportTask


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
    service = CaptureService()
    completed: list[dict] = []
    service.capture_completed.connect(completed.append)
    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr("src.business.emulator.capture_service.save_image", lambda image, path: (True, ""))

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=False, perform_ocr=False,
    )

    assert service._ocr_worker is None
    assert completed[0]["ocr_results"] is None
    assert not completed[0]["ocr_matched"]
    service.shutdown()


def test_manual_ocr_skips_template_matching(monkeypatch, tmp_path) -> None:
    service = CaptureService()
    submitted: list[dict] = []
    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr("src.business.emulator.capture_service.save_image", lambda image, path: (True, ""))
    monkeypatch.setattr(
        service,
        "_queue_capture_ocr",
        lambda **kwargs: submitted.append(kwargs),
    )

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=True, perform_ocr=True,
    )

    assert submitted[0]["match_template"] is False
    assert submitted[0]["is_poll"] is False
    service.shutdown()


def test_capture_queues_ocr_copy_before_saving_image(monkeypatch, tmp_path) -> None:
    image = Image.new("RGB", (10, 20))

    service = CaptureService()
    service._config = {"mumu_ocr_enabled": True}
    events: list[str] = []
    submitted: dict = {}

    def queue(**kwargs):
        events.append("ocr")
        submitted.update(kwargs)
        return type("Task", (), {"task_id": "test-task"})()

    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr(service, "_queue_capture_ocr", queue)
    monkeypatch.setattr(
        "src.business.emulator.capture_service.save_image",
        lambda _image, _path: (events.append("save") is None, ""),
    )

    service._handle_capture_result(
        True, image, None, "hero_selection", force_ocr=False, perform_ocr=True,
    )
    service.shutdown()

    assert events[0] == "ocr"
    assert "save" in events
    assert submitted["image"] is not image


def test_capture_service_reports_ocr_warmup_states_and_allows_retry(monkeypatch) -> None:
    service = CaptureService()
    states: list[tuple[str, str]] = []
    service.ocr_warmup_state_changed.connect(lambda state, detail: states.append((state, detail)))
    worker = SimpleNamespace(warmup_model=lambda _names: True)
    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: worker)

    service.warmup_ocr_model(["曹操"])
    service._on_ocr_task_completed(SimpleNamespace(warmup=True, result={"outcome": "warmup_failed", "detail": "失败"}))
    service.warmup_ocr_model(["曹操"])
    service._on_ocr_task_completed(SimpleNamespace(warmup=True, result={"outcome": "warmed"}))

    assert states == [("warming", ""), ("failed", "失败"), ("warming", ""), ("ready", "")]
    assert service.ocr_warmup_state == "ready"


def test_capture_service_routes_official_import_through_shared_worker(monkeypatch) -> None:
    service = CaptureService()
    submitted: list[OfficialImportTask] = []
    worker = SimpleNamespace(submit=submitted.append)
    progress: list[tuple[str, int, int]] = []
    completed: list[list[dict]] = []
    failures: list[str] = []
    service.official_import_progress.connect(
        lambda status, current, total: progress.append((status, current, total))
    )
    service.official_import_completed.connect(completed.append)
    service.official_import_failed.connect(failures.append)
    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: worker)

    task = service.submit_official_import({"exile": ["page1.png", "page2.png"]})
    service._on_official_import_progress(task.task_id, "正在识别", 2, 10)
    task.result = {
        "outcome": "official_imported",
        "summaries": [{"name": "武将放逐数据", "records": 100}],
    }
    service._on_ocr_task_completed(task)

    assert submitted == [task]
    assert task.paths == {"exile": ("page1.png", "page2.png")}
    assert progress == [("正在等待 OCR 队列...", 0, 0), ("正在识别", 2, 10)]
    assert completed == [[{"name": "武将放逐数据", "records": 100}]]
    assert failures == []


def test_capture_service_rejects_overlapping_official_import(monkeypatch) -> None:
    service = CaptureService()
    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: SimpleNamespace(submit=lambda task: None))

    service.submit_official_import({"2v2": ["page1.png"]})

    try:
        service.submit_official_import({"exile": ["page1.png"]})
    except RuntimeError as exc:
        assert "已有官方榜单导入任务" in str(exc)
    else:
        raise AssertionError("应拒绝重叠的官方榜单导入任务")


def test_capture_service_returns_none_until_async_image_save_completes() -> None:
    from concurrent.futures import Future

    future: Future = Future()
    assert CaptureService._completed_save_path(future, "screenshot.png") is None
    future.set_result((True, ""))
    assert CaptureService._completed_save_path(future, "screenshot.png") == "screenshot.png"


def test_file_import_with_forced_ocr_skips_template_matching(monkeypatch, tmp_path) -> None:
    service = CaptureService()
    image_path = tmp_path / "match-guide.png"
    Image.new("RGB", (10, 20)).save(image_path)
    submitted: list[dict] = []
    monkeypatch.setattr(
        service,
        "_queue_capture_ocr",
        lambda **kwargs: submitted.append(kwargs),
    )

    service._execute_file_ocr(image_path, template_name="match_guide", force_ocr=True)

    assert submitted[0]["match_template"] is False
    assert submitted[0]["template_name"] == "match_guide"


def test_file_import_rejects_invalid_image_without_submitting_ocr(tmp_path) -> None:
    service = CaptureService()
    invalid_path = tmp_path / "invalid.png"
    invalid_path.write_bytes(b"not an image")
    failures: list[str] = []
    submitted: list[dict] = []
    service.capture_failed.connect(failures.append)
    service._queue_capture_ocr = lambda **kwargs: submitted.append(kwargs)

    service._execute_file_ocr(invalid_path)

    assert failures and "图片加载失败" in failures[0]
    assert submitted == []


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
