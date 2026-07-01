"""
OCR 控制服务

管理模板生命周期、PaddleOCR 调配和持续轮询。
不持有 UI 引用，通过 Qt 信号与主窗口通信。
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from src.ocr.ocr_loader import get_template_manager, get_recognizer

logger = logging.getLogger(__name__)

PROJECT_ROOT = __file__  # placeholder


class OcrService(QObject):
    """OCR 控制服务"""

    status_changed = Signal(str)
    template_changed = Signal(bool)      # 模板加载/已删除
    ocr_completed = Signal(list)         # 识别结果
    poll_tick = Signal()                 # 轮询触发（由主窗口连接截图流程）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = {}
        self._hero_names: list[str] = []
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self.poll_tick.emit)
        self._poll_cooldown_until: datetime | None = None

    # ── 配置 ──────────────────────────────────────────────────────────

    def update_config(self, config: dict) -> None:
        """更新配置。"""
        self._config = config

    def set_hero_names(self, names: list[str]) -> None:
        """设置用于编辑距离矫正的武将名列表。"""
        self._hero_names = names

    # ── 模板管理 ──────────────────────────────────────────────────────

    def create_template(self, image, roi: tuple[int, int, int, int]) -> None:
        """制作模板。

        Args:
            image: 全屏截图。
            roi: (x, y, w, h) 框选区域。

        Raises:
            ValueError: ROI 参数无效。
        """
        try:
            tm = get_template_manager()
            tm.set_template(image, roi)
            logger.info("模板已制作: %s", tm.template_path)
            self.template_changed.emit(True)
            self.status_changed.emit("模板已制作")
        except ValueError:
            raise
        except Exception as e:
            logger.error("模板制作失败: %s", e)
            logger.debug(traceback.format_exc())
            raise

    def select_template(self, file_path: str) -> None:
        """从文件选择模板并加载。"""
        import shutil
        try:
            tm = get_template_manager()
            file_path_obj = type(tm.template_path)(file_path)

            tm.template_path.parent.mkdir(parents=True, exist_ok=True)
            if file_path_obj.resolve() != tm.template_path.resolve():
                shutil.copy2(str(file_path_obj), str(tm.template_path))

            tm.reload()
            logger.info("模板已选择: %s", file_path)
            self.template_changed.emit(tm.is_loaded)
            self.status_changed.emit(f"模板已加载: {tm.template_path.name}")
        except Exception as e:
            logger.error("模板选择失败: %s", e)
            logger.debug(traceback.format_exc())
            self.template_changed.emit(False)

    def is_template_loaded(self) -> bool:
        """检查模板是否已加载。"""
        return get_template_manager().is_loaded

    def delete_template(self) -> None:
        """删除模板。"""
        try:
            tm = get_template_manager()
            tm.delete_template()
            self.template_changed.emit(False)
            self.status_changed.emit("模板已删除")
        except Exception as e:
            logger.error("模板删除失败: %s", e)
            logger.debug(traceback.format_exc())

    # ── 轮询管理 ──────────────────────────────────────────────────────

    @property
    def is_polling(self) -> bool:
        return self._poll_timer.isActive()

    def start_poll(self, interval_ms: int) -> None:
        """启动轮询。"""
        self._poll_timer.start(interval_ms)

    def stop_poll(self) -> None:
        """停止轮询并清除冷却。"""
        self._poll_timer.stop()
        self._poll_cooldown_until = None

    def set_cooldown(self, seconds: int) -> None:
        """设置冷却时间（匹配成功后调用）。"""
        if seconds > 0:
            self._poll_cooldown_until = datetime.now() + timedelta(seconds=seconds)

    def clear_cooldown(self) -> None:
        self._poll_cooldown_until = None

    @property
    def is_on_cooldown(self) -> bool:
        """检查是否在轮询冷却期内。"""
        return self._poll_cooldown_until is not None and datetime.now() < self._poll_cooldown_until

    # ── OCR ───────────────────────────────────────────────────────────

    def run_ocr(self, image, rois=None) -> list[dict] | None:
        """对单张图片执行 OCR 识别。

        Args:
            image: PIL Image 或 numpy array。
            rois: ROI 坐标列表（可选，默认使用配置值）。

        Returns:
            识别结果列表，失败则返回 None。
        """
        try:
            rois = rois or self._config.get("ocr_generals_roi", None)
            recognizer = get_recognizer(rois, hero_names=self._hero_names)
            results = recognizer.recognize(image)
            logger.info("OCR 完成: %s", results)
            return results
        except Exception as e:
            logger.error("OCR 识别异常: %s", e)
            logger.debug(traceback.format_exc())
            return None
