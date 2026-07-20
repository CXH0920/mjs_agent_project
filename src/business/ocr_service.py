"""
OCR 控制服务

管理模板生命周期、PaddleOCR 调配和持续轮询。
不持有 UI 引用，通过 Qt 信号与主窗口通信。
"""

from __future__ import annotations

import logging
import traceback
from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import QObject, QTimer, Signal

from src.ocr.ocr_loader import get_template_manager

logger = logging.getLogger(__name__)

PROJECT_ROOT = __file__  # placeholder


@dataclass
class PollTaskState:
    """单个轮询任务的独立运行状态。"""

    active: bool = False
    cooldown_until: datetime | None = None
    last_match_time: datetime | None = None
    consecutive_failures: int = 0


class OcrService(QObject):
    """OCR 控制服务"""

    status_changed = Signal(str)
    template_changed = Signal(bool)      # 模板加载/已删除
    ocr_completed = Signal(list)         # 识别结果
    poll_tick = Signal()                 # 轮询触发（由主窗口连接截图流程）
    poll_state_changed = Signal(str, str)  # (状态, 详情)

    POLL_MAX_FAILURES = 5
    POLL_BACKOFF_DELAYS_MS = (2_000, 5_000, 15_000, 30_000)
    POLL_MATCH_COOLDOWN_MS = 180_000
    MATCH_GUIDE_COOLDOWN_MS = 5_000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._config = {}
        self._hero_names: list[str] = []
        self._poll_timer = QTimer(self)
        self._poll_timer.setSingleShot(True)
        self._poll_timer.timeout.connect(self._emit_poll_tick)
        self._poll_tasks = {
            "hero_selection": PollTaskState(),
            "match_guide": PollTaskState(),
        }
        self._poll_interval_ms = 0
        self._consecutive_poll_failures = 0
        self._poll_state = "stopped"
        self._poll_generation = 0
        self._poll_in_flight = False
        self._ocr_task_submitter = None

    # ── 配置 ──────────────────────────────────────────────────────────

    def update_config(self, config: dict) -> None:
        """更新配置。"""
        target_changed = (
            config.get("mumu_adb_path") != self._config.get("mumu_adb_path")
            or config.get("mumu_adb_port") != self._config.get("mumu_adb_port")
        )
        self._config = config
        if target_changed and self._poll_state != "stopped":
            self.stop_poll()

    @property
    def config(self) -> dict:
        """返回 OCR 配置副本。"""
        return dict(self._config)

    def set_hero_names(self, names: list[str]) -> None:
        """设置用于编辑距离矫正的武将名列表。"""
        self._hero_names = names

    def set_ocr_task_submitter(self, submitter) -> None:
        """注入 CaptureService 的串行 OCR 任务入口。"""
        self._ocr_task_submitter = submitter

    # ── 模板管理 ──────────────────────────────────────────────────────

    def create_template(
        self,
        image,
        roi: tuple[int, int, int, int],
        template_name: str = "hero_selection",
    ) -> None:
        """制作模板。

        Args:
            image: 全屏截图。
            roi: (x, y, w, h) 框选区域。

        Raises:
            ValueError: ROI 参数无效。
        """
        try:
            tm = get_template_manager(template_name)
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

    def select_template(self, file_path: str, template_name: str = "hero_selection") -> None:
        """从文件选择指定类型的模板并加载。"""
        import shutil
        try:
            tm = get_template_manager(template_name)
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

    def is_template_loaded(self, template_name: str = "hero_selection") -> bool:
        """检查指定模板是否已加载。"""
        return get_template_manager(template_name).is_loaded

    def delete_template(self, template_name: str = "hero_selection") -> None:
        """删除指定模板。"""
        try:
            tm = get_template_manager(template_name)
            tm.delete_template()
            self.template_changed.emit(False)
            self.status_changed.emit("模板已删除")
        except Exception as e:
            logger.error("模板删除失败: %s", e)
            logger.debug(traceback.format_exc())

    # ── 轮询管理 ──────────────────────────────────────────────────────

    @property
    def is_polling(self) -> bool:
        return self._poll_state != "stopped"

    @property
    def poll_state(self) -> str:
        """返回当前轮询状态。"""
        return self._poll_state

    @property
    def poll_generation(self) -> int:
        """返回当前轮询会话代数。"""
        return self._poll_generation

    def start_poll(self, interval_ms: int) -> None:
        """启动或重新启动轮询。"""
        self._poll_interval_ms = max(interval_ms, 1_000)
        self._consecutive_poll_failures = 0
        self._poll_in_flight = False
        self._poll_generation += 1
        self._poll_tasks["hero_selection"] = PollTaskState(active=True)
        self._poll_tasks["match_guide"] = PollTaskState(active=False)
        self._schedule_poll(self._poll_interval_ms, "running", "轮询运行中")

    def stop_poll(self) -> None:
        """停止轮询并清除当前会话状态。"""
        self._poll_timer.stop()
        for task in self._poll_tasks.values():
            task.active = False
            task.cooldown_until = None
        self._consecutive_poll_failures = 0
        self._poll_in_flight = False
        self._poll_generation += 1
        self._set_poll_state("stopped", "轮询未启用")

    def resume_poll(self) -> None:
        """用户主动恢复已暂停的轮询。"""
        if self._poll_interval_ms <= 0:
            self._poll_interval_ms = max(self._config.get("mumu_ocr_poll_interval", 2) * 1000, 1_000)
        self.start_poll(self._poll_interval_ms)

    def begin_poll(self) -> int | None:
        """标记一轮轮询开始，返回本轮会话代数。"""
        if self._poll_state not in {"running", "backing_off", "cooldown"} or self._poll_in_flight:
            return None
        self._poll_in_flight = True
        return self._poll_generation

    def due_poll_tasks(self) -> list[str]:
        """返回本轮可执行的任务名称，任务冷却彼此独立。"""
        now = datetime.now()
        return [
            name for name, task in self._poll_tasks.items()
            if task.active and (task.cooldown_until is None or now >= task.cooldown_until)
        ]

    def activate_task(self, task_name: str) -> None:
        """激活指定轮询任务。"""
        self._get_task(task_name).active = True
        logger.info("轮询任务已激活: %s", task_name)

    def deactivate_task(self, task_name: str) -> None:
        """停用指定轮询任务，不影响其他任务。"""
        self._get_task(task_name).active = False

    def set_task_cooldown(self, task_name: str, seconds: int | None = None) -> None:
        """设置指定任务冷却，不修改其他任务。"""
        task = self._get_task(task_name)
        if seconds is None:
            default_ms = (
                self.POLL_MATCH_COOLDOWN_MS
                if task_name == "hero_selection"
                else self.MATCH_GUIDE_COOLDOWN_MS
            )
            seconds = default_ms / 1000
        if seconds > 0:
            now = datetime.now()
            task.cooldown_until = now + timedelta(seconds=seconds)
            task.last_match_time = now
            logger.info("轮询任务进入冷却: %s, %.1f 秒", task_name, seconds)

    def clear_task_cooldown(self, task_name: str) -> None:
        self._get_task(task_name).cooldown_until = None

    def get_task_state(self, task_name: str) -> PollTaskState:
        """返回任务状态对象，供调度和测试读取。"""
        return self._get_task(task_name)

    @staticmethod
    def _validate_task_name(task_name: str) -> None:
        if task_name not in {"hero_selection", "match_guide"}:
            raise ValueError(f"不支持的轮询任务: {task_name}")

    def _get_task(self, task_name: str) -> PollTaskState:
        self._validate_task_name(task_name)
        return self._poll_tasks[task_name]

    def complete_poll(self, generation: int, outcome: str, detail: str = "") -> None:
        """由主线程记录一轮轮询结果并安排下一次执行。"""
        if generation != self._poll_generation:
            return
        self._poll_in_flight = False
        if outcome in {"healthy_no_match", "matched"}:
            self._consecutive_poll_failures = 0
            self._schedule_poll(self._poll_interval_ms, "running", "轮询运行中")
            return

        if outcome in {"prerequisite_unconfigured", "prerequisite_template_missing"}:
            self._poll_timer.stop()
            reason = detail or ("未配置 ADB" if outcome == "prerequisite_unconfigured" else "未加载识别模板")
            self._set_poll_state("paused", f"轮询已暂停：{reason}")
            return

        self._consecutive_poll_failures += 1
        if self._consecutive_poll_failures >= self.POLL_MAX_FAILURES:
            self._poll_timer.stop()
            self._set_poll_state("paused", f"轮询已暂停：连续 {self.POLL_MAX_FAILURES} 次失败")
            return

        delay = max(
            self._poll_interval_ms,
            self.POLL_BACKOFF_DELAYS_MS[self._consecutive_poll_failures - 1],
        )
        self._schedule_poll(
            delay,
            "backing_off",
            f"{delay // 1000} 秒后重试（第 {self._consecutive_poll_failures}/{self.POLL_MAX_FAILURES} 次失败）",
        )

    def set_cooldown(self, seconds: int) -> None:
        """兼容旧调用：仅设置武将选择任务冷却。"""
        self.set_task_cooldown("hero_selection", seconds)

    def clear_cooldown(self) -> None:
        self.clear_task_cooldown("hero_selection")

    @property
    def is_on_cooldown(self) -> bool:
        """兼容旧调用：检查武将选择任务冷却。"""
        task = self._get_task("hero_selection")
        return task.cooldown_until is not None and datetime.now() < task.cooldown_until

    def _set_poll_state(self, state: str, detail: str) -> None:
        if (state, detail) == (self._poll_state, getattr(self, "_poll_detail", "")):
            return
        self._poll_state = state
        self._poll_detail = detail
        self.poll_state_changed.emit(state, detail)

    def _schedule_poll(self, delay_ms: int, state: str, detail: str) -> None:
        self._set_poll_state(state, detail)
        self._poll_timer.start(delay_ms)

    def _emit_poll_tick(self) -> None:
        if self._poll_state in {"stopped", "paused"}:
            return
        self._set_poll_state("running", "正在执行轮询")
        self.poll_tick.emit()

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
            if self._ocr_task_submitter is None:
                raise RuntimeError("OCR worker 未初始化")
            task = self._ocr_task_submitter(
                image,
                hero_names=self._hero_names,
                template_name="hero_selection",
                rois=rois,
                match_template=False,
            )
            task.completed.wait()
            result = task.result or {}
            results = result.get("ocr_results")
            logger.info("OCR 完成: %s", results)
            return results
        except Exception as e:
            logger.error("OCR 识别异常: %s", e)
            logger.debug(traceback.format_exc())
            return None
