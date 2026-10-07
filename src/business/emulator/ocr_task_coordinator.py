"""OCR 任务协调器。

从 CaptureService 出仓的 OCR 编排职责：worker 生命周期（唯一创建点）、
模型预热状态机、识别任务的阈值/ROI 组装入队。
截图请求与结果的关联（pending 表）与完成分派仍属 CaptureService；
配置经 config_provider 晚绑定读取，阈值改动实时生效。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from PySide6.QtCore import QObject, Signal
from src.business.recognition.ocr_worker import OcrTask, OcrWorker
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout, OcrRoiSlot

logger = logging.getLogger(__name__)


def _image_pixel_size(image) -> tuple[int, int] | None:
    """读取图像像素尺寸 (宽, 高)；PIL Image 与 numpy 数组之外的类型返回 None。"""
    size = getattr(image, "size", None)
    if isinstance(size, tuple) and len(size) == 2:
        return int(size[0]), int(size[1])
    shape = getattr(image, "shape", None)
    if shape is not None and len(shape) >= 2:
        return int(shape[1]), int(shape[0])
    return None


class OcrTaskCoordinator(QObject):
    """OCR worker 生命周期、模型预热状态机与识别任务组装入队。"""

    warmup_state_changed = Signal(str, str)

    def __init__(
        self,
        parent=None,
        config_provider=None,
        roi_config: OcrRoiConfig | None = None,
        *,
        on_worker_created: Callable[[OcrWorker], None],
    ):
        super().__init__(parent)
        self._config_provider = config_provider
        self._roi_config = roi_config or OcrRoiConfig()
        self._worker: OcrWorker | None = None
        self._worker_lock = threading.Lock()
        self._on_worker_created = on_worker_created
        self._warmup_state = "idle"
        self._warmup_task = None

    @property
    def warmup_state(self) -> str:
        """返回 OCR 预热状态：idle、warming、ready 或 failed。"""
        return self._warmup_state

    def _set_warmup_state(self, state: str, detail: str = "") -> None:
        if (state, detail) == (self._warmup_state, ""):
            return
        self._warmup_state = state
        self.warmup_state_changed.emit(state, detail)

    def ensure_worker(self) -> OcrWorker:
        """返回唯一 OCR worker；首次调用时创建，同步回调接线后才 start。

        接线回调是普通同步调用而非 Qt 信号：跨线程发射的信号会退化为
        Queued 投递，宿主未接线 worker 即已开跑，官方导入完成回执丢失会
        把 OfficialImportGateway._pending 永久锁死。回调在 _worker_lock
        持有期间执行，Lock 不可重入——回调内禁止重入 ensure_worker 或
        调用 reset_recognizer_cache 等任何触发 worker 创建的接口，否则
        同线程重入会永久死锁且无任何日志。
        """
        if self._worker is None:
            with self._worker_lock:
                if self._worker is None:
                    worker = OcrWorker()
                    self._on_worker_created(worker)
                    worker.start()
                    self._worker = worker
        return self._worker

    def reset_recognizer_cache(self) -> None:
        """丢弃 OCR recognizer 缓存，使下次识别读到新的用户层白名单。"""
        self.ensure_worker().reset_recognizer_cache()

    def note_warmup_submitted(self, task) -> None:
        """记录预热任务提交并进入 warming 状态；task 为 None 表示已在队列。"""
        if task is not None:
            self._warmup_task = task
            self._set_warmup_state("warming")

    def on_warmup_completed(self, task) -> None:
        """预热任务完成回执：更新预热状态机。"""
        result = task.result or {"outcome": "warmup_failed"}
        if result.get("outcome") == "warmed":
            self._set_warmup_state("ready")
        else:
            self._set_warmup_state("failed", result.get("detail", "未知错误"))

    def wait_warmup(self, timeout_ms: int = 15_000) -> bool:
        """阻塞等待启动阶段预热完成；超时或未启动预热时返回 False。

        供主窗口显示前的启动画面阶段调用：OCR 引擎初始化期间会长时间持有
        Python GIL，若在事件循环运行后再预热会卡住界面，因此放在显示前完成。
        """
        task = self._warmup_task
        if task is None:
            return False
        if not task.completed.wait(timeout_ms / 1000):
            logger.warning("OCR 预热等待超时，主窗口将继续显示")
            return False
        self._warmup_task = None
        return bool(task.result and task.result.get("outcome") == "warmed")

    def build_task(
        self,
        image,
        hero_names: list[str] | None = None,
        template_name: str = "hero_selection",
        recognize: bool = True,
        rois: list[list[int]] | None = None,
        match_template: bool = True,
        fallback_on_template_miss: bool = False,
        allow_result_reuse: bool = False,
    ) -> OcrTask:
        """组装识别任务（阈值/ROI 布局）；入队由宿主经 worker 提供者完成。"""
        config = self._config_provider()
        threshold_key = (
            "mumu_match_guide_threshold"
            if template_name == "match_guide"
            else "mumu_hero_selection_threshold"
        )
        layout = self._roi_config.layout_for(template_name)
        if rois is not None:
            # 调用方派生的 rois 与 image 同一像素空间（如巅峰 watcher 按当帧
            # 卡位派生名称条），参考尺寸取 image 实际尺寸使缩放比恒为 1，
            # 不再叠加模板页参考尺寸的二次缩放
            layout = OcrRoiLayout(
                _image_pixel_size(image) or layout.reference_size,
                tuple(OcrRoiSlot(name_roi=tuple(roi)) for roi in rois),
            )
        task = OcrTask(
            image=image,
            hero_names=tuple(hero_names or ()),
            rois=None,
            template_name=template_name,
            threshold=config.get(
                threshold_key,
                config.get("mumu_ocr_match_threshold", 0.8),
            ),
            roi_layout=layout,
            recognize=recognize,
            match_template=match_template,
            fallback_on_template_miss=fallback_on_template_miss,
            allow_result_reuse=allow_result_reuse,
        )
        return task

    def shutdown(self) -> None:
        """退役 OCR worker：仅通知停止并立即返回（不在 GUI 线程同步等待），
        线程由 OcrWorker 的退役列表持有并在进程退出前收尾，避免窗口卡死。"""
        if self._worker is not None:
            self._worker.retire()
            self._worker = None
