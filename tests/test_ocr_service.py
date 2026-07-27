"""OCR 轮询退避与暂停测试。"""

from __future__ import annotations

import os
from pathlib import Path

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


def test_stop_poll_cancels_active_session() -> None:
    _app()
    service = OcrService()
    service.start_poll(1_000)
    generation = service.poll_generation
    cancel_event = service.poll_cancel_event

    service.stop_poll()

    assert cancel_event.is_set()
    assert service.is_poll_cancelled(generation)


def test_poll_tasks_have_independent_activation_and_cooldowns() -> None:
    _app()
    service = OcrService()
    service.start_poll(1_000)

    assert service.due_poll_tasks() == ["hero_selection"]

    service.activate_task("match_guide")
    service.set_task_cooldown("hero_selection", 60)
    assert service.due_poll_tasks() == ["match_guide"]

    service.set_task_cooldown("match_guide", 60)
    service.clear_task_cooldown("hero_selection")
    assert service.due_poll_tasks() == ["hero_selection"]


def test_match_guide_task_stays_disabled_until_hero_selection_reactivates() -> None:
    _app()
    service = OcrService()
    service.start_poll(1_000)
    service.set_task_cooldown("hero_selection", 60)
    service.activate_task("match_guide")

    assert service.due_poll_tasks() == ["match_guide"]

    service.deactivate_task("match_guide")
    assert service.due_poll_tasks() == []

    service.clear_task_cooldown("match_guide")
    service.activate_task("match_guide")
    assert service.due_poll_tasks() == ["match_guide"]


def test_select_template_clears_stale_reference_metadata(tmp_path: Path, monkeypatch) -> None:
    class _TemplateManager:
        def __init__(self) -> None:
            self.template_path = tmp_path / "managed.png"
            self.is_loaded = True
            self.reloaded = False

        def reload(self) -> None:
            self.reloaded = True

    manager = _TemplateManager()
    source = tmp_path / "external.png"
    source.write_bytes(b"external-template")
    manager.template_path.write_bytes(b"old-template")
    metadata_path = manager.template_path.with_suffix(".json")
    metadata_path.write_text('{"reference_width": 2560}', encoding="utf-8")
    monkeypatch.setattr("src.business.ocr_service.get_template_manager", lambda _name: manager)

    service = OcrService()
    service.select_template(str(source), "match_guide")

    assert manager.template_path.read_bytes() == b"external-template"
    assert not metadata_path.exists()
    assert manager.reloaded
