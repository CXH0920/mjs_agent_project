"""OCR 轮询退避与暂停测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.business.ocr_service import OcrService


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_poll_failures_back_off_and_pause(monkeypatch) -> None:
    _app()
    service = OcrService()
    scheduled: list[tuple[int, str]] = []
    states: list[str] = []
    monkeypatch.setattr(
        service,
        "_schedule_poll",
        lambda delay, state, detail: scheduled.append((delay, state)),
    )
    service.poll_state_changed.connect(lambda state, detail: states.append(state))

    service.start_poll(1_000)
    generation = service.poll_generation
    for _ in range(4):
        service.complete_poll(generation, "retryable_connection", "offline")

    assert scheduled[-4:] == [
        (2_000, "backing_off"),
        (5_000, "backing_off"),
        (15_000, "backing_off"),
        (30_000, "backing_off"),
    ]

    service.complete_poll(generation, "retryable_connection", "offline")

    assert service.poll_state == "paused"
    assert states[-1] == "paused"


def test_healthy_poll_resets_failure_backoff(monkeypatch) -> None:
    _app()
    service = OcrService()
    scheduled: list[tuple[int, str]] = []
    monkeypatch.setattr(
        service,
        "_schedule_poll",
        lambda delay, state, detail: scheduled.append((delay, state)),
    )

    service.start_poll(3_000)
    generation = service.poll_generation
    service.complete_poll(generation, "retryable_capture", "timeout")
    service.complete_poll(generation, "healthy_no_match")
    service.complete_poll(generation, "retryable_capture", "timeout")

    assert scheduled[-3:] == [
        (3_000, "backing_off"),
        (3_000, "running"),
        (3_000, "backing_off"),
    ]


def test_resume_poll_starts_new_generation(monkeypatch) -> None:
    _app()
    service = OcrService()
    scheduled: list[tuple[int, str]] = []
    monkeypatch.setattr(
        service,
        "_schedule_poll",
        lambda delay, state, detail: scheduled.append((delay, state)),
    )

    service.start_poll(2_000)
    old_generation = service.poll_generation
    for _ in range(5):
        service.complete_poll(old_generation, "retryable_connection", "offline")
    service.resume_poll()

    assert service.poll_generation > old_generation
    assert scheduled[-1] == (2_000, "running")
