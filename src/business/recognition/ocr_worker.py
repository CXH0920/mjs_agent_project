"""串行执行模板匹配与 OCR 的后台 worker。"""

from __future__ import annotations

import atexit
import logging
import os
import queue
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from src.config.env import PROJECT_ROOT
from src.ocr.recognizer import GeneralRecognizer
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout, OcrRoiSlot
from src.ocr.template_manager import TemplateManager

logger = logging.getLogger(__name__)
DEFAULT_SCREENSHOT_DATA_DIR = PROJECT_ROOT / "screenshot_data"

# 已通知停止但仍在收尾的 worker，防止 QThread 对象在运行中被提前销毁。
_RETIRED_WORKERS: list["OcrWorker"] = []


def _drain_retired_workers() -> None:
    """进程退出前等待退役 worker 结束，避免运行中的 QThread 被销毁。

    15 秒仍未退出时强制结束进程，避免进程挂起（退出路径无关键写操作）。
    """
    for worker in _RETIRED_WORKERS:
        if worker.isRunning() and not worker.wait(15_000):
            logger.error("退役 OCR worker 15 秒未退出，强制结束进程")
            os._exit(1)


atexit.register(_drain_retired_workers)


@dataclass
class OcrTask:
    """传给 OCR worker 的不可变任务输入及其完成状态。"""

    image: object
    hero_names: tuple[str, ...]
    rois: tuple[tuple[int, ...], ...] | None
    template_name: str
    threshold: float
    roi_layout: OcrRoiLayout | None = None
    recognize: bool = True
    match_template: bool = True
    fallback_on_template_miss: bool = False
    warmup: bool = False
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    completed: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    result: dict | None = field(default=None, init=False)


@dataclass
class OfficialImportTask:
    """在唯一 OCR worker 中执行的整批官方榜单导入任务。"""

    paths: dict[str, tuple[str, ...]]
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    completed: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    result: dict | None = field(default=None, init=False)


class OcrWorker(QThread):
    """单线程队列，确保 PaddleOCR 仅在一个后台线程中使用。"""

    task_completed = Signal(object)
    official_progress = Signal(str, str, int, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._tasks: queue.Queue[OcrTask | OfficialImportTask | None] = queue.Queue()
        self._cancel_event = threading.Event()
        self._current_task_kind: str | None = None
        self._recognizer: GeneralRecognizer | None = None
        self._recognizer_signature: tuple | None = None
        self._ocr_engine = None
        self._rare_char_ocr_engine = None
        self._warmup_queued = False

    def submit(self, task: OcrTask | OfficialImportTask) -> None:
        """按提交顺序加入任务；调用方可通过 ``task.completed`` 等待结果。"""
        self._tasks.put(task)

    def warmup_model(self, hero_names: list[str] | None = None) -> OcrTask | None:
        """将模型、推理算子和词表特征预热任务加入当前串行队列，返回任务供调用方等待。"""
        if self._warmup_queued:
            return None
        self._warmup_queued = True
        task = OcrTask(
            image=None,
            hero_names=tuple(hero_names or ()),
            rois=None,
            template_name="hero_selection",
            threshold=0.0,
            match_template=False,
            warmup=True,
        )
        self.submit(task)
        return task

    def request_stop(self) -> None:
        """请求停止：置取消标记并投递哨兵任务，不阻塞调用方。"""
        self._cancel_event.set()
        self._tasks.put(None)

    def retire(self) -> None:
        """停止并放弃同步等待；线程由进程退出钩子负责收尾。

        预热在步骤间可取消（见 _warmup_model 的取消检查点）；若线程正卡在
        引擎加载这类不可中断的原生调用里，不再强杀线程，由退出钩子在
        15 秒等待后 os._exit 兜底（退出路径无关键写操作）。
        """
        self.request_stop()
        if self not in _RETIRED_WORKERS:
            _RETIRED_WORKERS.append(self)

    def shutdown(self, timeout_ms: int = 5_000) -> bool:
        """在当前识别结束后停止 worker；超时则转入退役列表，避免线程被提前销毁。"""
        if not self.isRunning():
            return True
        self.request_stop()
        if self.wait(timeout_ms):
            return True
        logger.warning("OCR worker 未能在 %d ms 内退出，转入后台退役等待", timeout_ms)
        self.retire()
        return False

    def run(self) -> None:
        while True:
            if self._cancel_event.is_set():
                self._drain_pending_tasks()
                return
            task = self._tasks.get()
            if task is None:
                self._drain_pending_tasks()
                return
            self._current_task_kind = (
                "official" if isinstance(task, OfficialImportTask)
                else "warmup" if task.warmup
                else "regular"
            )
            try:
                try:
                    if isinstance(task, OfficialImportTask):
                        task.result = self._execute_official_import(task)
                    else:
                        task.result = self._execute(task)
                        if task.warmup and task.result.get("outcome") == "warmup_failed":
                            self._warmup_queued = False
                except BaseException:
                    # _execute 内部已兜底 Exception，这里兜住漏网的 BaseException：
                    # completed 必须置位，否则等待方只能吃满 15~30 秒超时，
                    # capture_service 的 _pending_ocr_captures 还会连带泄漏图像引用
                    logger.exception("OCR 任务执行异常（%s）", type(task).__name__)
                    task.result = {"outcome": "failed", "detail": "worker 内部异常"}
                finally:
                    task.completed.set()
                    try:
                        self.task_completed.emit(task)
                    except Exception:
                        logger.exception("OCR 任务完成信号发送失败")
            finally:
                self._current_task_kind = None

    def _drain_pending_tasks(self) -> None:
        """停止时把队列中滞留的任务逐个置位并广播完成，等待方立即醒来而非吃满超时。

        停止之后再提交的任务同样不会被处理（submit 不校验取消态），属停止语义的一部分。
        """
        while True:
            try:
                task = self._tasks.get_nowait()
            except queue.Empty:
                return
            if task is None:
                continue  # 多次 request_stop 投递的哨兵
            task.result = {"outcome": "cancelled"}
            task.completed.set()
            try:
                self.task_completed.emit(task)
            except Exception:
                logger.exception("取消任务的完成信号发送失败")

    def _execute_official_import(self, task: OfficialImportTask) -> dict:
        """复用当前线程的 OCR 引擎，串行完成整批官方榜单导入。"""
        from src.business.recognition.official_data_import_service import OfficialDataImportService

        started = time.perf_counter()
        service = OfficialDataImportService(
            ocr_engine=self._ocr_engine,
            rare_char_ocr_engine=self._rare_char_ocr_engine,
        )
        selected_paths = [(key, paths) for key, paths in task.paths.items() if paths]
        summaries = []
        try:
            if not selected_paths:
                raise ValueError("未选择官方榜单图片")
            for index, (key, paths) in enumerate(selected_paths, start=1):
                display_names = {"2v2": "2v2数据", "peak": "巅峰赛数据"}
                name = display_names.get(key, "武将放逐数据")
                status = f"正在导入{name}（{index}/{len(selected_paths)}）"
                self.official_progress.emit(task.task_id, status, 0, 0)
                summaries.append(service.import_pages(
                    key,
                    [Path(path) for path in paths],
                    lambda current, total, text=status: self.official_progress.emit(
                        task.task_id, text, current, total,
                    ),
                    lambda text, prefix=status: self.official_progress.emit(
                        task.task_id, f"{prefix}：{text}", -1, -1,
                    ),
                ))
            logger.info(
                "官方榜单整批导入完成: groups=%d，耗时 %.1fms",
                len(summaries),
                (time.perf_counter() - started) * 1000,
            )
            return {"outcome": "official_imported", "summaries": summaries}
        except Exception as exc:
            logger.exception("官方榜单导入失败")
            return {"outcome": "official_import_failed", "detail": str(exc)}
        finally:
            self._ocr_engine = service.ocr_engine
            self._rare_char_ocr_engine = service.rare_char_ocr_engine

    def _execute(self, task: OcrTask) -> dict:
        if task.warmup:
            return self._warmup_model(task)

        task_started = time.perf_counter()
        template_load_ms = 0.0
        template_match_ms = 0.0
        recognition_ms = 0.0
        result_save_ms = 0.0
        recognizer_timing: dict[str, float] = {}
        template_confidence = 0.0
        template_scale = 0.0
        template_strategy = "not_run"
        try:
            # 模板由 worker 自己按任务加载，避免与配置页的模板编辑共享可变实例。
            template_load_started = time.perf_counter()
            template_manager = TemplateManager(template_name=task.template_name)
            template_load_ms = (time.perf_counter() - template_load_started) * 1000
            if task.match_template:
                if not template_manager.is_loaded:
                    self._log_timing(
                        task, task_started, outcome="template_missing", template_load_ms=template_load_ms,
                    )
                    return {"outcome": "template_missing"}
                template_match_started = time.perf_counter()
                matched, confidence = template_manager.match(task.image, task.threshold)
                template_match_ms = (time.perf_counter() - template_match_started) * 1000
                template_confidence = confidence
                template_scale = getattr(template_manager, "last_match_scale", 0.0)
                template_strategy = getattr(template_manager, "last_match_strategy", "unknown")
                if not matched:
                    if task.fallback_on_template_miss:
                        logger.debug(
                            "模板未命中，回退执行 OCR: %s (置信度 %.4f)",
                            task.template_name,
                            confidence,
                        )
                        result = {"outcome": "matched", "confidence": confidence}
                    else:
                        self._log_timing(
                            task,
                            task_started,
                            outcome="healthy_no_match",
                            template_load_ms=template_load_ms,
                            template_match_ms=template_match_ms,
                            template_confidence=template_confidence,
                            template_scale=template_scale,
                            template_strategy=template_strategy,
                        )
                        return {
                            "outcome": "healthy_no_match",
                            "confidence": confidence,
                        }
                result = {"outcome": "matched", "confidence": confidence}
            else:
                result = {"outcome": "matched"}
            if not task.recognize:
                self._log_timing(
                    task,
                    task_started,
                    outcome="matched",
                    template_load_ms=template_load_ms,
                    template_match_ms=template_match_ms,
                    template_confidence=template_confidence,
                    template_scale=template_scale,
                    template_strategy=template_strategy,
                )
                return result

            layout = task.roi_layout or OcrRoiConfig().layout_for(task.template_name)
            if task.rois is not None:
                layout = OcrRoiLayout(
                    layout.reference_size,
                    tuple(OcrRoiSlot(name_roi=tuple(roi)) for roi in task.rois),
                )
            recognizer = self._get_recognizer(layout, task.hero_names, task.template_name)
            recognition_started = time.perf_counter()
            results = recognizer.recognize(task.image)
            self._ocr_engine = recognizer.shared_engine()
            recognition_ms = (time.perf_counter() - recognition_started) * 1000
            recognizer_timing = getattr(recognizer, "timing_ms", {})
            result_save_started = time.perf_counter()
            DEFAULT_SCREENSHOT_DATA_DIR.mkdir(parents=True, exist_ok=True)
            GeneralRecognizer.save_results(results, DEFAULT_SCREENSHOT_DATA_DIR / "latest.json")
            result_save_ms = (time.perf_counter() - result_save_started) * 1000
            result["ocr_results"] = results
            self._log_timing(
                task,
                task_started,
                outcome="matched",
                template_load_ms=template_load_ms,
                template_match_ms=template_match_ms,
                template_confidence=template_confidence,
                template_scale=template_scale,
                template_strategy=template_strategy,
                recognition_ms=recognition_ms,
                result_save_ms=result_save_ms,
                recognizer_timing=recognizer_timing,
            )
            logger.debug("OCR 完成: %d 个武将识别", len([item for item in results if item.get("name")]))
            return result
        except Exception as exc:
            logger.error("OCR 执行异常: %s", exc)
            logger.debug(traceback.format_exc())
            return {"outcome": "retryable_ocr", "detail": str(exc)}

    def _warmup_model(self, task: OcrTask) -> dict:
        """在 worker 线程加载一次模型，供后续不同页面的识别器复用。

        各步骤间检查取消标记：应用关闭时尽快退出预热；只有引擎加载本身
        （Paddle 原生初始化）不可中断。
        """
        started = time.perf_counter()
        if self._cancel_event.is_set():
            return {"outcome": "cancelled"}
        try:
            recognizer = GeneralRecognizer(
                hero_names=list(task.hero_names), page_type="hero_selection",
            )
            if self._ocr_engine is not None:
                recognizer.adopt_engine(self._ocr_engine)
            # 先触发模型加载（惰性加载过程不可中断）
            recognizer.ensure_engine()
            if self._cancel_event.is_set():
                logger.info("OCR 预热已取消（应用正在关闭），跳过特征预热与推理")
                return {"outcome": "cancelled"}
            recognizer.warmup()
            self._ocr_engine = recognizer.shared_engine() or self._ocr_engine
            if self._cancel_event.is_set():
                logger.info("OCR 预热已取消（特征预热完成），跳过推理预热")
                return {"outcome": "cancelled"}
            recognizer.warmup_inference()
            logger.info("PaddleOCR 模型和推理预热完成，耗时 %.1fms", (time.perf_counter() - started) * 1000)
            return {"outcome": "warmed"}
        except Exception as exc:
            logger.warning("PaddleOCR 模型预热失败，首次识别将按需加载: %s", exc)
            logger.debug(traceback.format_exc())
            return {"outcome": "warmup_failed", "detail": str(exc)}

    @staticmethod
    def _log_timing(
        task: OcrTask,
        task_started: float,
        *,
        outcome: str,
        template_load_ms: float = 0.0,
        template_match_ms: float = 0.0,
        template_confidence: float = 0.0,
        template_scale: float = 0.0,
        template_strategy: str = "not_run",
        recognition_ms: float = 0.0,
        result_save_ms: float = 0.0,
        recognizer_timing: dict[str, float] | None = None,
    ) -> None:
        timings = recognizer_timing or {}
        logger.info(
            "OCR阶段耗时[%s/%s]: outcome=%s，模板置信度=%.4f，阈值=%.2f，缩放=%.4f，策略=%s，"
            "模板加载=%.1fms，模板匹配=%.1fms，模型初始化=%.1fms，"
            "名称预处理=%.1fms，名称OCR=%.1fms，名称纠错=%.1fms，"
            "阵营预处理=%.1fms，阵营OCR=%.1fms，结果落盘=%.1fms，识别合计=%.1fms，总计=%.1fms",
            task.template_name,
            task.task_id[:8],
            outcome,
            template_confidence,
            task.threshold,
            template_scale,
            template_strategy,
            template_load_ms,
            template_match_ms,
            timings.get("model_load", 0.0),
            timings.get("name_preprocess", 0.0),
            timings.get("name_ocr", 0.0),
            timings.get("name_correction", 0.0),
            timings.get("team_preprocess", 0.0),
            timings.get("team_ocr", 0.0),
            result_save_ms,
            recognition_ms,
            (time.perf_counter() - task_started) * 1000,
        )

    def _get_recognizer(
        self,
        layout: OcrRoiLayout,
        hero_names: tuple[str, ...],
        page_type: str,
    ) -> GeneralRecognizer:
        signature = (layout, hero_names, page_type)
        if self._recognizer is None or self._recognizer_signature != signature:
            self._recognizer = GeneralRecognizer(
                hero_names=list(hero_names),
                page_type=page_type,
                layout=layout,
            )
            if self._ocr_engine is not None:
                self._recognizer.adopt_engine(self._ocr_engine)
            self._recognizer_signature = signature
        return self._recognizer
