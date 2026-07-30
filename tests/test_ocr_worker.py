"""OCR 单 worker 串行调度测试。"""

from __future__ import annotations

import threading
import time
import os
import logging
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.business.capture_service import CaptureService
from src.business.ocr_worker import OcrTask, OcrWorker
from src.business.ocr_service import OcrService


def test_ocr_worker_serializes_tasks_and_reuses_matching_recognizer(monkeypatch) -> None:
    calls: list[str] = []
    recognizer_inits: list[tuple] = []

    class FakeTemplateManager:
        is_loaded = True
        last_match_scale = 1.0
        last_match_strategy = "base_local"
        reference_size = (2560, 1440)

        def __init__(self, *, template_name: str) -> None:
            self.template_name = template_name

        def match(self, image, threshold: float):
            return True, threshold

    class FakeRecognizer:
        def __init__(self, hero_names, page_type, layout) -> None:
            recognizer_inits.append((hero_names, page_type, layout))

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
        def __init__(self, hero_names, page_type, layout) -> None:
            recognizer_inits.append((layout.reference_size, page_type))

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


def test_match_guide_template_miss_can_fall_back_to_ocr(monkeypatch) -> None:
    recognized: list[object] = []

    class FakeTemplateManager:
        is_loaded = True
        last_match_scale = 1.0
        last_match_strategy = "base_local"

        def __init__(self, *, template_name: str) -> None:
            pass

        def match(self, image, threshold: float):
            return False, 0.35

    class FakeRecognizer:
        timing_ms = {}

        def __init__(self, hero_names, page_type, layout) -> None:
            pass

        def recognize(self, image):
            recognized.append(image)
            return [{"index": 1, "name": "曹操", "confidence": 1.0}]

        @staticmethod
        def save_results(results, path) -> None:
            return None

    monkeypatch.setattr("src.business.ocr_worker.TemplateManager", FakeTemplateManager)
    monkeypatch.setattr("src.business.ocr_worker.GeneralRecognizer", FakeRecognizer)

    result = OcrWorker()._execute(OcrTask(
        image="image", hero_names=("曹操",), rois=None,
        template_name="match_guide", threshold=0.8, fallback_on_template_miss=True,
    ))

    assert result["outcome"] == "matched"
    assert result["ocr_results"][0]["name"] == "曹操"
    assert recognized == ["image"]


def test_ocr_worker_warmup_reuses_model_for_later_recognition(monkeypatch) -> None:
    engine = object()
    recognized_engines: list[object] = []
    inference_warmups: list[object] = []

    class FakeTemplateManager:
        is_loaded = True
        last_match_scale = 1.0
        last_match_strategy = "base_local"

        def __init__(self, *, template_name: str) -> None:
            pass

        def match(self, image, threshold: float):
            return True, threshold

    class FakeRecognizer:
        def __init__(self, hero_names, page_type, layout=None) -> None:
            self._ocr = None
            self.timing_ms = {}

        def warmup(self) -> None:
            self._ocr = engine

        def warmup_inference(self) -> None:
            inference_warmups.append(self._ocr)

        def recognize(self, image):
            recognized_engines.append(self._ocr)
            return []

        @staticmethod
        def save_results(results, path) -> None:
            return None

    monkeypatch.setattr("src.business.ocr_worker.TemplateManager", FakeTemplateManager)
    monkeypatch.setattr("src.business.ocr_worker.GeneralRecognizer", FakeRecognizer)

    worker = OcrWorker()
    warmup = OcrTask(
        image=None, hero_names=(), rois=None, template_name="hero_selection",
        threshold=0.0, match_template=False, warmup=True,
    )
    recognition = OcrTask(
        image="image", hero_names=("曹操",), rois=None, template_name="match_guide",
        threshold=0.8,
    )

    assert worker._execute(warmup) == {"outcome": "warmed"}
    assert worker._execute(recognition)["outcome"] == "matched"
    assert inference_warmups == [engine]
    assert recognized_engines == [engine]


def test_ocr_worker_logs_stage_timings(monkeypatch, caplog) -> None:
    class FakeTemplateManager:
        is_loaded = True
        last_match_scale = 1.0
        last_match_strategy = "base_local"

        def __init__(self, *, template_name: str) -> None:
            pass

        def match(self, image, threshold: float):
            return True, threshold

    class FakeRecognizer:
        timing_ms = {
            "model_load": 12.0,
            "name_preprocess": 2.0,
            "name_ocr": 5.0,
            "name_correction": 1.0,
        }

        def __init__(self, hero_names, page_type, layout) -> None:
            pass

        def recognize(self, image):
            return [{"index": 1, "name": "曹操", "confidence": 1.0}]

        @staticmethod
        def save_results(results, path) -> None:
            return None

    monkeypatch.setattr("src.business.ocr_worker.TemplateManager", FakeTemplateManager)
    monkeypatch.setattr("src.business.ocr_worker.GeneralRecognizer", FakeRecognizer)

    with caplog.at_level(logging.INFO, logger="src.business.ocr_worker"):
        result = OcrWorker()._execute(OcrTask(
            image="image", hero_names=("曹操",), rois=None,
            template_name="hero_selection", threshold=0.8,
        ))

    assert result["outcome"] == "matched"
    assert "OCR阶段耗时[hero_selection/" in caplog.text
    assert "outcome=matched" in caplog.text
    assert "模板置信度=0.8000" in caplog.text
    assert "策略=base_local" in caplog.text
    assert "模型初始化=12.0ms" in caplog.text
    assert "名称OCR=5.0ms" in caplog.text


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
    app = QApplication.instance() or QApplication([])

    class FakeTemplateManager:
        is_loaded = True
        reference_size = (2560, 1440)

        def __init__(self, *, template_name: str) -> None:
            self.template_name = template_name

        def match(self, image, threshold: float):
            return True, threshold

    class FakeRecognizer:
        def __init__(self, hero_names, page_type, layout) -> None:
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
