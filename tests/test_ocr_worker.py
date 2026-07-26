"""OCR 单 worker 串行调度测试。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from src.business.capture_service import CaptureService
from src.business.ocr_worker import OcrTask, OcrWorker
from src.business.ocr_service import OcrService


def test_ocr_worker_serializes_tasks_and_reuses_matching_recognizer(monkeypatch) -> None:
    calls: list[str] = []
    recognizer_inits: list[tuple] = []

    class FakeTemplateManager:
        is_loaded = True
        reference_size = (2560, 1440)

        def __init__(self, *, template_name: str) -> None:
            self.template_name = template_name

        def match(self, image, threshold: float):
            return True, threshold

    class FakeRecognizer:
        def __init__(self, rois, hero_names, reference_size, page_type) -> None:
            recognizer_inits.append((rois, hero_names, reference_size, page_type))

        def recognize(self, image):
            calls.append(image)
            return [{"index": 1, "name": image, "confidence": 1.0}]

        @staticmethod
        def save_results(results, path) -> None:
            return None

    monkeypatch.setattr("src.business.ocr_worker.TemplateManager", FakeTemplateManager)
    monkeypatch.setattr("src.business.ocr_worker.GeneralRecognizer", FakeRecognizer)

    worker = OcrWorker()
    first = OcrTask(
        image="first",
        hero_names=("曹操",),
        rois=((1, 2, 3, 4),),
        template_name="hero_selection",
        threshold=0.8,
    )
    second = OcrTask(
        image="second",
        hero_names=("曹操",),
        rois=((1, 2, 3, 4),),
        template_name="hero_selection",
        threshold=0.8,
    )

    worker.start()
    try:
        worker.submit(first)
        worker.submit(second)

        assert first.completed.wait(1)
        assert second.completed.wait(1)
    finally:
        worker.shutdown()

    assert calls == ["first", "second"]
    assert len(recognizer_inits) == 1
    assert first.result == {
        "outcome": "matched",
        "confidence": 0.8,
        "ocr_results": [{"index": 1, "name": "first", "confidence": 1.0}],
    }


def test_ocr_worker_keeps_default_roi_reference_independent_of_template(monkeypatch) -> None:
    recognizer_inits: list[tuple[int, int]] = []

    class FakeTemplateManager:
        is_loaded = True
        reference_size = (1920, 1080)

        def __init__(self, *, template_name: str) -> None:
            pass

        def match(self, image, threshold: float):
            return True, threshold

    class FakeRecognizer:
        def __init__(self, rois, hero_names, reference_size, page_type) -> None:
            recognizer_inits.append((reference_size, page_type))

        def recognize(self, image):
            return []

        @staticmethod
        def save_results(results, path) -> None:
            return None

    monkeypatch.setattr("src.business.ocr_worker.TemplateManager", FakeTemplateManager)
    monkeypatch.setattr("src.business.ocr_worker.GeneralRecognizer", FakeRecognizer)

    result = OcrWorker()._execute(OcrTask(
        image="image", hero_names=(), rois=None, template_name="match_guide", threshold=0.8,
    ))

    assert result["outcome"] == "matched"
    assert recognizer_inits == [((2560, 1440), "match_guide")]


def test_ocr_service_routes_direct_requests_to_injected_worker() -> None:
    submitted: list[tuple[object, dict]] = []
    task = SimpleNamespace(
        completed=threading.Event(),
        result={"outcome": "matched", "ocr_results": [{"name": "曹操"}]},
    )
    task.completed.set()

    def submit(image, **kwargs):
        submitted.append((image, kwargs))
        return task

    service = OcrService()
    service.set_hero_names(["曹操"])
    service.set_ocr_task_submitter(submit)

    assert service.run_ocr("image", rois=[[1, 2, 3, 4]]) == [{"name": "曹操"}]
    assert submitted == [(
        "image",
        {
            "hero_names": ["曹操"],
            "template_name": "hero_selection",
            "rois": [[1, 2, 3, 4]],
            "match_template": False,
        },
    )]


def test_capture_service_returns_worker_result_to_gui_thread(monkeypatch) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])

    class FakeTemplateManager:
        is_loaded = True
        reference_size = (2560, 1440)

        def __init__(self, *, template_name: str) -> None:
            self.template_name = template_name

        def match(self, image, threshold: float):
            return True, threshold

    class FakeRecognizer:
        def __init__(self, rois, hero_names, reference_size, page_type) -> None:
            pass

        def recognize(self, image):
            return [{"index": 1, "name": "曹操", "confidence": 1.0}]

        @staticmethod
        def save_results(results, path) -> None:
            return None

    monkeypatch.setattr("src.business.ocr_worker.TemplateManager", FakeTemplateManager)
    monkeypatch.setattr("src.business.ocr_worker.GeneralRecognizer", FakeRecognizer)

    service = CaptureService()
    completed: list[dict] = []
    service.capture_completed.connect(completed.append)
    service._queue_capture_ocr(
        image="image",
        save_path="image.png",
        hero_names=["曹操"],
        template_name="hero_selection",
    )

    deadline = time.monotonic() + 1
    while not completed and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    service.shutdown()

    assert completed == [{
        "image": "image",
        "save_path": "image.png",
        "ocr_results": [{"index": 1, "name": "曹操", "confidence": 1.0}],
        "ocr_matched": True,
    }]
