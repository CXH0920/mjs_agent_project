"""轮询闲置自动暂停的行为测试。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PIL import Image

from src.ui.app.frame_fingerprint import FINGERPRINT_SIZE, compute_fingerprint, frames_match
from src.ui.app.poll_coordinator import PollCoordinator, PollOutcome, PollResult


class _Signal:
    def __init__(self) -> None:
        self._handlers = []

    def connect(self, handler) -> None:
        self._handlers.append(handler)

    def emit(self, *args) -> None:
        for handler in self._handlers:
            handler(*args)


class _OcrService:
    def __init__(self, *, idle_paused: bool = False) -> None:
        self.poll_tick = _Signal()
        self.poll_state_changed = _Signal()
        self.config = {
            "mumu_ocr_poll_mode": True,
            "mumu_ocr_poll_interval": 3,
            "mumu_ocr_poll_idle_pause": True,
        }
        self.poll_generation = 4
        self.idle_paused = idle_paused
        self._cancel_event = threading.Event()
        self.due_tasks = ["hero_selection"]
        self.started: list[int] = []
        self.completed: list[tuple] = []
        self.pause_calls: list[int] = []
        self.stop_count = 0

    def start_poll(self, interval: int) -> None:
        self.started.append(interval)

    def stop_poll(self) -> None:
        self.stop_count += 1

    def complete_poll(self, *args) -> None:
        self.completed.append(args)

    def pause_for_idle(self, minutes: int) -> None:
        self.pause_calls.append(minutes)

    def is_poll_idle_paused(self) -> bool:
        return self.idle_paused

    def begin_poll(self) -> int | None:
        if self.idle_paused:
            return None
        return self.poll_generation

    @property
    def poll_cancel_event(self) -> threading.Event:
        return self._cancel_event

    def due_poll_tasks(self) -> list[str]:
        return list(self.due_tasks)


class _CaptureService:
    def __init__(self, capture) -> None:
        self.capture = capture
        self.connection_failures: list[tuple[object, str]] = []
        self.start_ocr_worker_count = 0

    def start_ocr_worker(self) -> None:
        self.start_ocr_worker_count += 1

    def sync_poll_connection_state(self, capture, detail: str) -> None:
        self.connection_failures.append((capture, detail))


class _OcrTask:
    """即时完成的 OCR 任务桩。"""

    def __init__(self) -> None:
        self.completed = threading.Event()
        self.completed.set()
        self.result = {"outcome": "healthy_no_match"}


class _PipelineCaptureService(_CaptureService):
    """按序返回预置图像的采集桩，用于验证 do_poll_work 的指纹接入。"""

    def __init__(self, capture, images) -> None:
        super().__init__(capture)
        self._images = list(images)

    def capture_for_poll(self, capture) -> tuple[bool, object, str]:
        return True, self._images.pop(0), ""

    def submit_ocr_task(self, image, **kwargs) -> _OcrTask:
        return _OcrTask()


def _solid_image(value: int, size: tuple[int, int] = (320, 180)) -> Image.Image:
    return Image.new("L", size, value)


def _unchanged_result(capture, generation: int = 4) -> PollResult:
    return PollResult(generation, PollOutcome.HEALTHY_NO_MATCH, capture=capture, frame_unchanged=True)


def _drain(coordinator: PollCoordinator, qapp, timeout_seconds: float = 5.0) -> None:
    """等待在途 poll 线程结束并把队列化的结果派发回 GUI 线程。"""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if not coordinator._poll_thread_lock.locked():
            qapp.processEvents()
            return
        time.sleep(0.01)
    raise AssertionError("poll 工作线程未在时限内完成")


# ── 帧指纹 ────────────────────────────────────────────────────────────


def test_fingerprint_is_fixed_length_grayscale_bytes() -> None:
    fingerprint = compute_fingerprint(_solid_image(128))

    assert fingerprint is not None
    assert len(fingerprint) == FINGERPRINT_SIZE[0] * FINGERPRINT_SIZE[1]


def test_frames_match_tolerates_tiny_noise_but_rejects_real_change() -> None:
    base = compute_fingerprint(_solid_image(100))
    noisy = compute_fingerprint(_solid_image(101))    # 每格 +1，MAD=1
    changed = compute_fingerprint(_solid_image(120))  # 每格 +20，MAD=20

    assert frames_match(base, base)
    assert frames_match(base, noisy)
    assert not frames_match(base, changed)
    # 缺失指纹一律判"有变化"（宁漏暂停不误暂停）
    assert not frames_match(None, base)
    assert not frames_match(base, None)


# ── 闲置计数与暂停 ────────────────────────────────────────────────────


def test_repeated_unchanged_frames_trigger_idle_pause(monkeypatch) -> None:
    monkeypatch.setattr(PollCoordinator, "IDLE_PAUSE_MINUTES", 0.05)  # 阈值 = ceil(3/3) = 1 拍
    capture = object()
    ocr_service = _OcrService()
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    coordinator._consume_poll_result(_unchanged_result(capture))

    assert ocr_service.pause_calls == [0.05]
    assert ocr_service.stop_count == 0  # 闲置暂停不占用故障语义的 stop_poll
    assert coordinator._idle_unchanged_count == 0  # 暂停后回到干净状态


def test_any_non_healthy_result_resets_idle_count(monkeypatch) -> None:
    monkeypatch.setattr(PollCoordinator, "IDLE_PAUSE_MINUTES", 0.1)  # 阈值 2 拍
    capture = object()
    ocr_service = _OcrService()
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    coordinator._consume_poll_result(_unchanged_result(capture))
    assert coordinator._idle_unchanged_count == 1

    # MATCHED 即使帧未变也清零；失败类结果同样清零
    coordinator._consume_poll_result(PollResult(
        4, PollOutcome.MATCHED, capture=capture, frame_unchanged=True,
    ))
    coordinator._consume_poll_result(PollResult(
        4, PollOutcome.RETRYABLE_CAPTURE, "截图失败", capture=capture,
    ))

    assert coordinator._idle_unchanged_count == 0
    assert ocr_service.pause_calls == []


def test_disabled_config_never_pauses(monkeypatch) -> None:
    monkeypatch.setattr(PollCoordinator, "IDLE_PAUSE_MINUTES", 0.05)
    capture = object()
    ocr_service = _OcrService()
    ocr_service.config["mumu_ocr_poll_idle_pause"] = False
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    for _ in range(5):
        coordinator._consume_poll_result(_unchanged_result(capture))

    assert ocr_service.pause_calls == []
    assert coordinator._idle_unchanged_count == 0


def test_resume_from_idle_pause_only_actuates_in_idle_state() -> None:
    capture = SimpleNamespace(connected=True)
    ocr_service = _OcrService(idle_paused=False)
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    coordinator.resume_from_idle_pause()
    assert ocr_service.started == []

    ocr_service.idle_paused = True
    coordinator.resume_from_idle_pause()
    assert ocr_service.started == [3000]  # sync_with_connection 按配置间隔重启


def test_sync_with_connection_clears_idle_watch_state() -> None:
    capture = SimpleNamespace(connected=True)
    ocr_service = _OcrService()
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    coordinator._consume_poll_result(_unchanged_result(capture))
    assert coordinator._idle_unchanged_count == 1

    coordinator.sync_with_connection()

    assert coordinator._idle_unchanged_count == 0
    assert coordinator._last_fingerprint is None
    assert ocr_service.started == [3000]


def test_tick_without_due_tasks_clears_idle_count() -> None:
    capture = object()
    ocr_service = _OcrService()
    ocr_service.due_tasks = []
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    coordinator._consume_poll_result(_unchanged_result(capture))
    assert coordinator._idle_unchanged_count == 1

    coordinator._on_poll_tick()

    assert ocr_service.completed[-1] == (4, "healthy_no_match", "当前没有到期的轮询任务")
    assert coordinator._idle_unchanged_count == 0


# ── 采集管线集成：指纹在 do_poll_work 内计算并随结果回传 ──────────────


def test_poll_pipeline_pauses_after_repeated_unchanged_frames(qapp, monkeypatch) -> None:
    monkeypatch.setattr(PollCoordinator, "IDLE_PAUSE_MINUTES", 0.1)  # 阈值 2 拍
    capture = object()
    ocr_service = _OcrService()
    image = _solid_image(90)
    capture_service = _PipelineCaptureService(capture, [image, image.copy(), image.copy()])
    coordinator = PollCoordinator(capture_service, ocr_service, lambda: [])

    for _ in range(3):
        coordinator._on_poll_tick()
        _drain(coordinator, qapp)

    # 首拍无前帧可比较（未累计），随后两拍判定同一画面并触发暂停
    assert ocr_service.pause_calls == [0.1]
    assert coordinator._idle_unchanged_count == 0


def test_poll_pipeline_resets_count_when_frame_changes(qapp) -> None:
    capture = object()
    ocr_service = _OcrService()
    images = [_solid_image(90), _solid_image(90), _solid_image(150), _solid_image(90)]
    capture_service = _PipelineCaptureService(capture, images)
    coordinator = PollCoordinator(capture_service, ocr_service, lambda: [])

    for _ in range(len(images)):
        coordinator._on_poll_tick()
        _drain(coordinator, qapp)

    assert ocr_service.pause_calls == []
    assert coordinator._idle_unchanged_count == 0  # 第 3 帧画面变化清零，末帧相对前帧仍是变化


# ── 兼容性 ────────────────────────────────────────────────────────────


def test_from_raw_defaults_frame_unchanged_for_legacy_payloads() -> None:
    result = PollResult.from_raw({"generation": 4, "outcome": "matched"})

    assert result.frame_unchanged is False
    assert PollResult.from_raw(result).frame_unchanged is False
