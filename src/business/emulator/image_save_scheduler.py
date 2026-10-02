"""截图 PNG 的后台保存调度。

从 CaptureService 出仓的图像保存职责：单线程执行器串行落盘，
完成后经信号回到 GUI 线程广播 image_saved。
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from src.capture.image_utils import save_image

logger = logging.getLogger(__name__)


class ImageSaveScheduler(QObject):
    """PNG 截图后台保存：单线程执行器串行写盘。"""

    image_saved = Signal(dict)
    _save_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-save")
        self._save_ready.connect(self._on_save_ready)

    def schedule(self, image, save_path: Path) -> Future:
        future = self._executor.submit(save_image, image, save_path)
        future.add_done_callback(
            lambda completed, source=image, path=save_path: self._save_ready.emit(
                (source, path, completed),
            )
        )
        return future

    def _on_save_ready(self, payload: object) -> None:
        image, save_path, future = payload
        try:
            saved, detail = future.result()
        except Exception as exc:
            saved, detail = False, str(exc)
        if saved:
            logger.info("截图已保存: %s", save_path)
        else:
            logger.warning("截图保存失败: %s", detail)
        self.image_saved.emit({
            "image": image,
            "save_path": save_path if saved else None,
            "detail": detail,
        })

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
