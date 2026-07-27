"""轮询协调器的行为测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.ui.poll_coordinator import PollCoordinator, PollOutcome, PollResult


class _Signal:
    def __init__(self) -> None:
        self._handlers = []

    def connect(self, handler) -> None:
        self._handlers.append(handler)

    def emit(self, *args) -> None:
        for handler in self._handlers:
            handler(*args)


class _OcrService:
    def __init__(self) -> None:
        self.poll_tick = _Signal()
        self.poll_state_changed = _Signal()
        self.config = {"mumu_ocr_poll_mode": True, "mumu_ocr_poll_interval": 3}
        self.poll_generation = 4
        self.started: list[int] = []
        self.completed: list[tuple] = []
        self.stop_count = 0

    def start_poll(self, interval: int) -> None:
        self.started.append(interval)

    def stop_poll(self) -> None:
        self.stop_count += 1

    def complete_poll(self, *args) -> None:
        self.completed.append(args)


class _CaptureService:
    def __init__(self, capture) -> None:
        self.capture = capture
        self.connection_failures: list[tuple[object, str]] = []

    def sync_poll_connection_state(self, capture, detail: str) -> None:
        self.connection_failures.append((capture, detail))


def test_sync_with_connection_only_starts_poll_for_connected_capture() -> None:
    capture = SimpleNamespace(connected=True)
    ocr_service = _OcrService()
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    coordinator.sync_with_connection()
    capture.connected = False
    coordinator.sync_with_connection()

    assert ocr_service.started == [3000]
    assert ocr_service.stop_count == 1


def test_consume_result_discards_stale_result_and_notifies_after_completion() -> None:
    capture = object()
    capture_service = _CaptureService(capture)
    ocr_service = _OcrService()
    coordinator = PollCoordinator(capture_service, ocr_service, lambda: [])
    received: list[PollResult] = []
    coordinator.poll_result_ready.connect(received.append)

    coordinator._consume_poll_result(PollResult(
        4,
        PollOutcome.RETRYABLE_CONNECTION,
        "设备离线",
        capture,
    ))
    coordinator._consume_poll_result(PollResult(
        3,
        PollOutcome.MATCHED,
        capture=capture,
    ))

    assert ocr_service.completed == [(4, "retryable_connection", "设备离线")]
    assert capture_service.connection_failures == [(capture, "设备离线")]
    assert received == [PollResult(4, PollOutcome.RETRYABLE_CONNECTION, "设备离线", capture)]
