"""OCR 轮询流程协调器。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Signal


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

    @classmethod
    def from_raw(cls, value: object) -> "PollTaskResult":
        if isinstance(value, cls):
            return value
        raw = value if isinstance(value, dict) else {}
        try:
            outcome = PollOutcome(raw.get("outcome", PollOutcome.RETRYABLE_OCR.value))
        except ValueError:
            outcome = PollOutcome.RETRYABLE_OCR
        return cls(outcome, str(raw.get("detail", "")), list(raw.get("ocr_results") or []))


@dataclass(frozen=True)
class PollResult:
    """一次轮询采集的强类型结果；``from_raw`` 兼容旧调用方。"""

    generation: int
    outcome: PollOutcome
    detail: str = ""
    capture: object | None = None
    task_results: dict[str, PollTaskResult] = field(default_factory=dict)
    ocr_results: list[dict] = field(default_factory=list)

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
        )


class PollCoordinator(QObject):
    """协调轮询定时、后台采集和 OCR 任务，不直接更新界面。"""

    poll_result_ready = Signal(object)
    poll_state_changed = Signal(str, str)
    _poll_result_received = Signal(object)

    POLL_OCR_WAIT_TIMEOUT_SECONDS = 10
    MATCH_GUIDE_MIN_RECOGNIZED_NAMES = 3

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

        self._ocr_service.poll_tick.connect(self._on_poll_tick)
        self._ocr_service.poll_state_changed.connect(self.poll_state_changed.emit)
        self._poll_result_received.connect(self._consume_poll_result)

    def sync_with_connection(self) -> None:
        """根据配置和 ADB 连接状态启动或停止轮询。"""
        capture = self._capture_service.capture
        poll_enabled = self._ocr_service.config.get("mumu_ocr_poll_mode", False)
        if not poll_enabled or not capture or not capture.connected:
            self._ocr_service.stop_poll()
            return

        interval = self._ocr_service.config.get("mumu_ocr_poll_interval", 2) * 1000
        self._ocr_service.start_poll(interval)

    def shutdown(self) -> None:
        """停止轮询，并取消正在运行的后台工作。"""
        self._ocr_service.stop_poll()

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
    def _validate_match_guide_result(cls, result: PollTaskResult) -> PollTaskResult:
        """仅在识别到足够的候选角色后触发对局攻略自动跳转。"""
        if result.outcome is not PollOutcome.MATCHED:
            return result
        recognized_count = sum(
            bool(str(item.get("name", "")).strip())
            for item in result.ocr_results
        )
        if recognized_count >= cls.MATCH_GUIDE_MIN_RECOGNIZED_NAMES:
            return result
        return PollTaskResult(
            PollOutcome.HEALTHY_NO_MATCH,
            f"对局攻略候选角色不足: {recognized_count}/{cls.MATCH_GUIDE_MIN_RECOGNIZED_NAMES}",
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
        self.poll_result_ready.emit(poll_result)
