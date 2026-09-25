"""OCR 轮询流程协调器。"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Signal
from src.ui.app.frame_fingerprint import compute_fingerprint, frames_match

logger = logging.getLogger(__name__)


class PollOutcome(str, Enum):
    """轮询流程允许的结果类型。"""

    MATCHED = "matched"
    HEALTHY_NO_MATCH = "healthy_no_match"
    TEMPLATE_MISSING = "template_missing"
    RETRYABLE_CONNECTION = "retryable_connection"
    RETRYABLE_CAPTURE = "retryable_capture"
    RETRYABLE_OCR = "retryable_ocr"
    PREREQUISITE_UNCONFIGURED = "prerequisite_unconfigured"


@dataclass(frozen=True)
class PollTaskResult:
    """单个模板检测任务的强类型结果。"""

    outcome: PollOutcome
    detail: str = ""
    ocr_results: list[dict] = field(default_factory=list)
    # 模板未命中但走兜底 OCR 时为 False：此类结果的读数来自错位 ROI，
    # 不得作为 match_guide 命中触发自动导入/跳转
    template_matched: bool = True

    @classmethod
    def from_raw(cls, value: object) -> "PollTaskResult":
        if isinstance(value, cls):
            return value
        raw = value if isinstance(value, dict) else {}
        try:
            outcome = PollOutcome(raw.get("outcome", PollOutcome.RETRYABLE_OCR.value))
        except ValueError:
            outcome = PollOutcome.RETRYABLE_OCR
        return cls(
            outcome,
            str(raw.get("detail", "")),
            list(raw.get("ocr_results") or []),
            bool(raw.get("template_matched", True)),
        )


@dataclass(frozen=True)
class PollResult:
    """一次轮询采集的强类型结果；``from_raw`` 兼容旧调用方。"""

    generation: int
    outcome: PollOutcome
    detail: str = ""
    capture: object | None = None
    task_results: dict[str, PollTaskResult] = field(default_factory=dict)
    ocr_results: list[dict] = field(default_factory=list)
    frame_unchanged: bool = False

    @classmethod
    def from_raw(cls, value: object) -> "PollResult":
        if isinstance(value, cls):
            return value
        raw = value if isinstance(value, dict) else {}
        try:
            outcome = PollOutcome(raw.get("outcome", PollOutcome.RETRYABLE_OCR.value))
        except ValueError:
            outcome = PollOutcome.RETRYABLE_OCR
        task_results = {
            name: PollTaskResult.from_raw(result)
            for name, result in (raw.get("task_results") or {}).items()
        }
        return cls(
            int(raw.get("generation", -1)), outcome, str(raw.get("detail", "")),
            raw.get("capture"), task_results, list(raw.get("ocr_results") or []),
            bool(raw.get("frame_unchanged", False)),
        )


class PollCoordinator(QObject):
    """协调轮询定时、后台采集、OCR 任务与结果路由；界面更新只经信号通知。

    结果路由（选将/对局任务的激活、冷却、超时失活与页面跳转请求）
    自 MainWindow 迁入：任务状态的写者收敛一处，界面出口只有三个信号。
    """

    poll_result_ready = Signal(object)
    poll_state_changed = Signal(str, str)
    _poll_result_received = Signal(object)
    # ── 路由的界面出口 ──
    hero_selection_matched = Signal(list)  # 选将命中：携带 ocr_results 灌入选将推荐页
    match_guide_matched = Signal(object)   # 对局命中：携带 PollTaskResult 灌入对局攻略页
    page_switch_requested = Signal(str)    # 自动切页请求："recommendation" / "match_guide"

    POLL_OCR_WAIT_TIMEOUT_SECONDS = 10
    MATCH_GUIDE_MIN_CONFIRMED_NAMES = 3
    # match_guide 激活后的空闲上限：覆盖进局加载动画，超时仍无命中视为没进对局，
    # 避免在非对局页面（如巅峰赛后回大厅）无限期 fallback 空转 OCR
    MATCH_GUIDE_IDLE_TIMEOUT_SECONDS = 90
    # 兜底 OCR（模板未命中）的读数质量门槛：ROI 与页面错位时（如巅峰牌面）
    # 读数靠拼图/逐槽回退拼凑，完整直读的槽位很少且置信度低；对齐的对局页
    # 则为 batch_plain 完整直读。按 2026-09-22/24 日志实测标定：对齐页
    # confidence ≥0.997，错位页 ≤0.90，取 0.95 分界
    FALLBACK_MIN_CLEAN_CONFIDENCE = 0.95
    IDLE_PAUSE_MINUTES = 5  # 连续无画面变化达到该时长即暂停轮询（对局长考仅数十秒，留 3~5 倍余量）

    def __init__(
        self,
        capture_service,
        ocr_service,
        hero_names_provider: Callable[[], list[str]],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._hero_names_provider = hero_names_provider
        self._poll_thread_lock = threading.Lock()
        self._last_fingerprint: bytes | None = None
        self._idle_unchanged_count = 0
        # ── 路由状态（选将/对局页的激活与跳转去重）──
        self._selection_page_active = False
        self._match_guide_page_active = False
        self._match_guide_activated_at: float | None = None
        # 巅峰赛识别面板晚于本协调器创建（组合根先建、_setup_ui 后建），
        # 由主窗口在面板创建后注入查询；未注入时按"未在识别"处理
        self._peak_recognizing_provider: Callable[[], bool] = lambda: False

        self._ocr_service.poll_tick.connect(self._on_poll_tick)
        self._ocr_service.poll_state_changed.connect(self.poll_state_changed.emit)
        self._poll_result_received.connect(self._consume_poll_result)

    def set_peak_recognizing_provider(self, provider: Callable[[], bool]) -> None:
        """注入巅峰赛识别会话查询；面板创建后由主窗口回填。"""
        self._peak_recognizing_provider = provider

    def sync_with_connection(self) -> None:
        """根据配置和 ADB 连接状态启动或停止轮询。"""
        self._reset_idle_watch()
        capture = self._capture_service.capture
        poll_enabled = self._ocr_service.config.get("mumu_ocr_poll_mode", False)
        if not poll_enabled or not capture or not capture.connected:
            self._ocr_service.stop_poll()
            return

        interval = self._ocr_service.config.get("mumu_ocr_poll_interval", 2) * 1000
        self._ocr_service.start_poll(interval)

    def shutdown(self) -> None:
        """停止轮询，并取消正在运行的后台工作。"""
        self._reset_idle_watch()
        self._ocr_service.stop_poll()

    def resume_from_idle_pause(self) -> None:
        """闲置暂停后由用户交互恢复；仅闲置暂停态生效，其他状态一律忽略。"""
        if not self._ocr_service.is_poll_idle_paused():
            return
        logger.info("闲置暂停的轮询已由用户交互恢复")
        self.sync_with_connection()

    def _on_poll_tick(self) -> None:
        """在后台执行一次采集，再将结构化结果送回 GUI 线程。"""
        self._capture_service.start_ocr_worker()
        generation = self._ocr_service.begin_poll()
        capture = self._capture_service.capture
        if generation is None:
            return
        cancel_event = self._ocr_service.poll_cancel_event
        task_names = self._ocr_service.due_poll_tasks()
        if not task_names:
            # 无帧可比对，按约定清零闲置计数（宁漏暂停不误暂停）
            self._reset_idle_watch()
            self._ocr_service.complete_poll(
                generation,
                PollOutcome.HEALTHY_NO_MATCH.value,
                "当前没有到期的轮询任务",
            )
            return
        if not capture:
            self._poll_result_received.emit(PollResult(
                generation,
                PollOutcome.PREREQUISITE_UNCONFIGURED,
                "ADB 未配置",
            ))
            return
        if not self._poll_thread_lock.acquire(blocking=False):
            self._ocr_service.complete_poll(
                generation,
                PollOutcome.RETRYABLE_CAPTURE.value,
                "上一轮轮询仍在执行",
            )
            return

        hero_names = self._hero_names_provider()
        idle_watch_enabled = bool(
            self._ocr_service.config.get("mumu_ocr_poll_idle_pause", True)
        )

        def do_poll_work() -> None:
            try:
                if cancel_event.is_set():
                    return
                ok, result, failure_kind = self._capture_service.capture_for_poll(capture)
                if cancel_event.is_set():
                    return
                if not ok:
                    outcome = (
                        PollOutcome.RETRYABLE_CONNECTION
                        if failure_kind == "connection"
                        else PollOutcome.RETRYABLE_CAPTURE
                    )
                    self._poll_result_received.emit(PollResult(
                        generation, outcome, str(result), capture,
                    ))
                    return

                image = result
                # 指纹状态仅由持锁的 poll 线程串行读写；GUI 线程 _reset_idle_watch
                # 的并发清空只是原子赋值，最坏情况多比一对陈旧帧，无碍正确性
                fingerprint = compute_fingerprint(image) if idle_watch_enabled else None
                frame_unchanged = frames_match(fingerprint, self._last_fingerprint)
                self._last_fingerprint = fingerprint
                task_results: dict[str, PollTaskResult] = {}
                has_match = False
                has_retryable_error = False
                for task_name in task_names:
                    if cancel_event.is_set():
                        return
                    ocr_task = self._capture_service.submit_ocr_task(
                        image,
                        hero_names=hero_names,
                        template_name=task_name,
                        recognize=True,
                        fallback_on_template_miss=task_name == "match_guide",
                        allow_result_reuse=True,
                    )
                    task_result = self.wait_for_ocr_task(ocr_task, cancel_event)
                    if task_result is None:
                        return
                    if task_name == "match_guide":
                        task_result = self._validate_match_guide_result(task_result)
                    if task_result.outcome is PollOutcome.MATCHED:
                        has_match = True
                    elif task_result.outcome is PollOutcome.RETRYABLE_OCR:
                        has_retryable_error = True
                    task_results[task_name] = task_result

                outcome = (
                    PollOutcome.RETRYABLE_OCR if has_retryable_error
                    else PollOutcome.MATCHED if has_match
                    else PollOutcome.HEALTHY_NO_MATCH
                )
                self._poll_result_received.emit(PollResult(
                    generation, outcome, capture=capture, task_results=task_results,
                    frame_unchanged=frame_unchanged,
                ))
            finally:
                self._poll_thread_lock.release()

        threading.Thread(target=do_poll_work, daemon=True, name="ocr-poll").start()

    @classmethod
    def wait_for_ocr_task(
        cls,
        ocr_task,
        cancel_event: threading.Event,
    ) -> PollTaskResult | None:
        """有限等待 OCR 任务；会话取消后不再回写轮询结果。"""
        if cancel_event.is_set():
            return None
        if not ocr_task.completed.wait(cls.POLL_OCR_WAIT_TIMEOUT_SECONDS):
            return PollTaskResult(
                PollOutcome.RETRYABLE_OCR,
                f"OCR 任务超时（{cls.POLL_OCR_WAIT_TIMEOUT_SECONDS} 秒）",
            )
        if cancel_event.is_set():
            return None
        return PollTaskResult.from_raw(ocr_task.result)

    @classmethod
    def _is_clean_fallback_read(cls, item: dict) -> bool:
        """完整精确的高置信读数：ROI 与页面对齐时 batch_plain 直读的形态。

        模板未命中的兜底 OCR 无法靠模板区分"真对局页"与"错位页面"（两者
        置信度同为 0.3 档），但读数质量可以：错位页的读数靠拼图碎片/逐槽
        回退拼凑，length_mode 降级或置信度不足；对齐页完整直读。
        """
        try:
            confidence = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            return False
        return (
            bool(str(item.get("name", "")).strip())
            and item.get("resolution") not in {"unresolved", "unknown", "conflict"}
            and item.get("length_mode") == "complete"
            and confidence >= cls.FALLBACK_MIN_CLEAN_CONFIDENCE
        )

    @classmethod
    def _validate_match_guide_result(cls, result: PollTaskResult) -> PollTaskResult:
        """仅在确认足够的角色名称后触发对局攻略自动跳转。

        模板未命中的兜底结果额外按读取质量把关：错位 ROI 也能读出真实
        武将名（确认数达标），但完整直读的槽位少，不足以证明页面正确。
        """
        if result.outcome is not PollOutcome.MATCHED:
            return result
        if not result.template_matched:
            clean_count = sum(
                1 for item in result.ocr_results if cls._is_clean_fallback_read(item)
            )
            if clean_count >= cls.MATCH_GUIDE_MIN_CONFIRMED_NAMES:
                return result
            return PollTaskResult(
                PollOutcome.HEALTHY_NO_MATCH,
                f"对局攻略模板未命中，兜底读数质量不足: {clean_count}/{cls.MATCH_GUIDE_MIN_CONFIRMED_NAMES}",
                result.ocr_results,
            )
        confirmed_count = sum(
            bool(str(item.get("name", "")).strip())
            and item.get("resolution") not in {"unresolved", "unknown", "conflict"}
            for item in result.ocr_results
        )
        if confirmed_count >= cls.MATCH_GUIDE_MIN_CONFIRMED_NAMES:
            return result
        return PollTaskResult(
            PollOutcome.HEALTHY_NO_MATCH,
            f"对局攻略已确认角色不足: {confirmed_count}/{cls.MATCH_GUIDE_MIN_CONFIRMED_NAMES}",
            result.ocr_results,
        )

    def _consume_poll_result(self, result: PollResult | dict) -> None:
        """丢弃过期结果，完成轮询状态迁移后再通知界面。"""
        poll_result = PollResult.from_raw(result)
        if poll_result.generation != self._ocr_service.poll_generation:
            return

        capture = poll_result.capture
        if capture is not None and capture is not self._capture_service.capture:
            return
        if (
            poll_result.outcome
            in {PollOutcome.RETRYABLE_CONNECTION, PollOutcome.RETRYABLE_CAPTURE}
            and capture is not None
        ):
            self._capture_service.sync_poll_connection_state(capture, poll_result.detail)

        self._ocr_service.complete_poll(
            poll_result.generation,
            poll_result.outcome.value,
            poll_result.detail,
        )
        self._track_idle_watch(poll_result)
        self.poll_result_ready.emit(poll_result)
        self._route_result(poll_result)

    # ── 结果路由（自 MainWindow 迁入）────────────────────────────────

    def handle_peak_exit(self) -> None:
        """巅峰赛牌面自动退出：衔接对局攻略轮询，等待用户进入对局页。"""
        self._match_guide_page_active = False
        if self._ocr_service.is_polling:
            self._ocr_service.activate_task("match_guide")
            self._match_guide_activated_at = time.monotonic()

    def _route_result(self, result: PollResult | dict) -> None:
        """按任务结果迁移识别任务状态，界面效果经信号通知。

        只有阶段令牌式的任务级结果才走任务路由；无 task_results 的旧载荷
        走单任务兼容分支，避免外部调用方行为改变。
        """
        poll_result = PollResult.from_raw(result)
        task_results = poll_result.task_results
        if not task_results:
            self._route_legacy_result(poll_result.outcome, poll_result.ocr_results)
            return

        hero_result = task_results.get("hero_selection")
        if hero_result and hero_result.outcome is PollOutcome.TEMPLATE_MISSING:
            self._ocr_service.deactivate_task("hero_selection")
        elif hero_result and hero_result.outcome is PollOutcome.HEALTHY_NO_MATCH:
            self._selection_page_active = False
        elif hero_result and hero_result.outcome is PollOutcome.MATCHED:
            if self._peak_recognizing_provider():
                # 持有锁之外的残余泄漏（如在途竞态）：会话中选将轮询结果一律
                # 不可信，冷却/激活/跳转/面板刷新全部跳过，guide 分支不受影响
                logger.debug("巅峰赛识别运行中，丢弃泄漏的选将轮询结果")
            else:
                self._ocr_service.set_task_cooldown(
                    "hero_selection",
                    self._ocr_service.config["mumu_hero_selection_cooldown"],
                )
                self._ocr_service.clear_task_cooldown("match_guide")
                self._ocr_service.activate_task("match_guide")
                self._match_guide_activated_at = time.monotonic()
                # 每次新选将命中都开启一轮新的对局攻略自动跳转。
                self._match_guide_page_active = False
                if not self._selection_page_active:
                    self._selection_page_active = True
                    if self._ocr_service.config.get("mumu_ocr_auto_switch_tab", False):
                        self.page_switch_requested.emit("recommendation")
                ocr_results = hero_result.ocr_results
                if ocr_results:
                    self.hero_selection_matched.emit(ocr_results)
                    recognized = len([item for item in ocr_results if item.get("name")])
                    logger.debug("轮询: OCR 识别到 %d 个武将", recognized)

        guide_result = task_results.get("match_guide")
        if guide_result and guide_result.outcome is PollOutcome.TEMPLATE_MISSING:
            self._ocr_service.deactivate_task("match_guide")
            self._match_guide_activated_at = None
        elif guide_result and guide_result.outcome is PollOutcome.MATCHED:
            self._ocr_service.deactivate_task("match_guide")
            self._match_guide_activated_at = None
            if not self._match_guide_page_active:
                self._match_guide_page_active = True
                if self._ocr_service.config.get("mumu_ocr_auto_switch_tab", False):
                    self.page_switch_requested.emit("match_guide")
            self.match_guide_matched.emit(guide_result)
        elif guide_result and guide_result.outcome is PollOutcome.HEALTHY_NO_MATCH:
            self._deactivate_match_guide_if_idle()

    def _deactivate_match_guide_if_idle(self) -> None:
        """激活后超过空闲阈值仍无命中即失活，停止非对局页面上的空转轮询。"""
        if self._match_guide_activated_at is None:
            return
        if time.monotonic() - self._match_guide_activated_at <= self.MATCH_GUIDE_IDLE_TIMEOUT_SECONDS:
            return
        self._ocr_service.deactivate_task("match_guide")
        self._match_guide_activated_at = None

    def _route_legacy_result(self, outcome: PollOutcome, ocr_results: list[dict]) -> None:
        """兼容旧版单任务轮询结果（载荷无 task_results），避免行为改变。"""
        if outcome is PollOutcome.HEALTHY_NO_MATCH:
            self._selection_page_active = False
            return
        if outcome is not PollOutcome.MATCHED:
            return
        if self._peak_recognizing_provider():
            logger.debug("巅峰赛识别运行中，丢弃泄漏的选将轮询结果")
            return
        if not self._selection_page_active:
            self._selection_page_active = True
            if self._ocr_service.config.get("mumu_ocr_auto_switch_tab", False):
                self.page_switch_requested.emit("recommendation")
        if ocr_results:
            self.hero_selection_matched.emit(ocr_results)

    # ── 闲置自动暂停 ──────────────────────────────────────────────────

    def _track_idle_watch(self, result: PollResult) -> None:
        """统计连续无变化拍数；只有健康无命中且画面未变的拍才累计，其余一律清零。

        注意只清计数、不清 _last_fingerprint：指纹是相邻比较的基线，由
        do_poll_work 随每次采集更新，在此清除会让下一拍失去前帧可比。
        """
        if not self._ocr_service.config.get("mumu_ocr_poll_idle_pause", True):
            self._idle_unchanged_count = 0
            return
        if result.outcome is not PollOutcome.HEALTHY_NO_MATCH or not result.frame_unchanged:
            self._idle_unchanged_count = 0
            return

        self._idle_unchanged_count += 1
        interval = max(self._ocr_service.config.get("mumu_ocr_poll_interval", 2), 1)
        threshold = math.ceil(self.IDLE_PAUSE_MINUTES * 60 / interval)
        if self._idle_unchanged_count < threshold:
            return

        logger.info(
            "轮询连续 %d 拍无画面变化，闲置暂停（阈值 %d 分钟）",
            self._idle_unchanged_count,
            self.IDLE_PAUSE_MINUTES,
        )
        self._reset_idle_watch()
        self._ocr_service.pause_for_idle(self.IDLE_PAUSE_MINUTES)

    def _reset_idle_watch(self) -> None:
        """清空闲置计数与指纹缓存；恢复、停启和任何异常结果后都必须回到干净状态。"""
        self._idle_unchanged_count = 0
        self._last_fingerprint = None
