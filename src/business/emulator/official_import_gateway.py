"""官方榜单导入网关。

从 CaptureService 出仓的导入职责：把整批官方榜单图片作为单个任务提交到
共享 OCR worker、维护进行中任务的排他集合、转发 worker 进度并分派
完成/失败结果。CaptureService 保留同名信号作门面（UI 连接点不变）。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from src.business.recognition.ocr_worker import OcrTask, OcrWorker, OfficialImportTask


class OfficialImportGateway(QObject):
    """官方榜单导入网关：经共享 OCR worker 串行导入整批榜单图片。"""

    progress = Signal(str, int, int)
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending: set[str] = set()

    def submit(self, worker: OcrWorker, paths: dict[str, list[str]]) -> OfficialImportTask:
        """将整批官方榜单导入加入唯一 OCR worker 队列。"""
        selected_paths = {
            key: tuple(selected) for key, selected in paths.items() if selected
        }
        if not selected_paths:
            raise ValueError("未选择官方榜单图片")
        if self._pending:
            raise RuntimeError("已有官方榜单导入任务正在执行")
        task = OfficialImportTask(selected_paths)
        self._pending.add(task.task_id)
        self.progress.emit("正在等待 OCR 队列...", 0, 0)
        worker.submit(task)
        return task

    def on_worker_progress(
        self, task_id: str, status: str, current: int, total: int,
    ) -> None:
        """转发 worker 的导入进度，忽略已不在本批的任务。"""
        if task_id in self._pending:
            self.progress.emit(status, current, total)

    def on_task_completed(self, task: OcrTask | OfficialImportTask) -> None:
        """分派官方导入的完成/失败结果；非本网关的任务一律忽略。"""
        if not isinstance(task, OfficialImportTask) or task.task_id not in self._pending:
            return
        self._pending.remove(task.task_id)
        result = task.result or {"outcome": "official_import_failed"}
        if result.get("outcome") == "official_imported":
            self.completed.emit(result.get("summaries", []))
        else:
            self.failed.emit(result.get("detail", "未知错误"))
