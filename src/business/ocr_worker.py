"""串行执行模板匹配与 OCR 的后台 worker。"""

from __future__ import annotations

import logging
import queue
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from src.ocr.recognizer import GeneralRecognizer
from src.ocr.template_manager import TemplateManager

logger = logging.getLogger(__name__)
DEFAULT_SCREENSHOT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "screenshot_data"


@dataclass
class OcrTask:
    """传给 OCR worker 的不可变任务输入及其完成状态。"""

    image: object
    hero_names: tuple[str, ...]
    rois: tuple[tuple[int, ...], ...] | None
    template_name: str
    threshold: float
    recognize: bool = True
    match_template: bool = True
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    completed: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    result: dict | None = field(default=None, init=False)


class OcrWorker(QThread):
    """单线程队列，确保 PaddleOCR 仅在一个后台线程中使用。"""

    task_completed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tasks: queue.Queue[OcrTask | None] = queue.Queue()
        self._recognizer: GeneralRecognizer | None = None
        self._recognizer_signature: tuple | None = None

    def submit(self, task: OcrTask) -> None:
        """按提交顺序加入任务；调用方可通过 ``task.completed`` 等待结果。"""
        self._tasks.put(task)

    def shutdown(self, timeout_ms: int = 5_000) -> bool:
        """在当前识别结束后停止 worker。"""
        if not self.isRunning():
            return True
        self._tasks.put(None)
        if self.wait(timeout_ms):
            return True
        logger.warning("OCR worker 未能在 %d ms 内退出", timeout_ms)
        return False

    def run(self) -> None:
        while True:
            task = self._tasks.get()
            if task is None:
                return
            task.result = self._execute(task)
            task.completed.set()
            self.task_completed.emit(task)

    def _execute(self, task: OcrTask) -> dict:
        try:
            # 模板由 worker 自己按任务加载，避免与配置页的模板编辑共享可变实例。
            template_manager = TemplateManager(template_name=task.template_name)
            if task.match_template:
                if not template_manager.is_loaded:
                    return {"outcome": "template_missing"}
                matched, confidence = template_manager.match(task.image, task.threshold)
                if not matched:
                    return {
                        "outcome": "healthy_no_match",
                        "confidence": confidence,
                    }
                result = {"outcome": "matched", "confidence": confidence}
            else:
                result = {"outcome": "matched"}
            if not task.recognize:
                return result

            recognizer = self._get_recognizer(
                task.rois,
                task.hero_names,
                template_manager.reference_size,
            )
            results = recognizer.recognize(task.image)
            DEFAULT_SCREENSHOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
            GeneralRecognizer.save_results(results, DEFAULT_SCREENSHOT_DATA_DIR / "latest.json")
            result["ocr_results"] = results
            logger.debug("OCR 完成: %d 个武将识别", len([item for item in results if item.get("name")]))
            return result
        except Exception as exc:
            logger.error("OCR 执行异常: %s", exc)
            logger.debug(traceback.format_exc())
            return {"outcome": "retryable_ocr", "detail": str(exc)}

    def _get_recognizer(
        self,
        rois: tuple[tuple[int, ...], ...] | None,
        hero_names: tuple[str, ...],
        reference_size: tuple[int, int],
    ) -> GeneralRecognizer:
        signature = (rois, hero_names, reference_size)
        if self._recognizer is None or self._recognizer_signature != signature:
            self._recognizer = GeneralRecognizer(
                rois=[list(roi) for roi in rois] if rois else None,
                hero_names=list(hero_names),
                reference_size=reference_size,
            )
            self._recognizer_signature = signature
        return self._recognizer
