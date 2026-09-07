"""OCR 轮询退避与暂停测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.business.recognition.ocr_service import OcrService


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_start_poll_runs_repeating_timer() -> None:
    _app()
    service = OcrService()

    service.start_poll(1_000)

    assert service._poll_timer.isActive()
    assert not service._poll_timer.isSingleShot()
    assert service._poll_timer.interval() == 1_000
    assert service.poll_state == "running"


def test_poll_failures_back_off_and_pause() -> None:
    _app()
    service = OcrService()
    states: list[str] = []
    service.poll_state_changed.connect(lambda state, detail: states.append(state))

    service.start_poll(1_000)
    generation = service.poll_generation
    intervals = []
    for _ in range(4):
        service.complete_poll(generation, "retryable_connection", "offline")
        intervals.append(service._poll_timer.interval())

    assert intervals == [2_000, 5_000, 15_000, 30_000]
    assert service._poll_timer.isActive()
    assert states[-1] == "backing_off"

    service.complete_poll(generation, "retryable_connection", "offline")

    assert not service._poll_timer.isActive()
    assert service.poll_state == "paused"
    assert states[-1] == "paused"


def test_healthy_poll_resets_failure_backoff() -> None:
    _app()
    service = OcrService()

    service.start_poll(1_000)
    generation = service.poll_generation

    service.complete_poll(generation, "retryable_capture", "timeout")
    assert service._poll_timer.interval() == 2_000
    assert service._poll_timer.isActive()

    service.complete_poll(generation, "healthy_no_match")
    assert service._poll_timer.interval() == 1_000  # 恢复基础间隔，定时器不重启停止

    service.complete_poll(generation, "retryable_capture", "timeout")
    assert service._poll_timer.interval() == 2_000


def test_resume_poll_starts_new_generation() -> None:
    _app()
    service = OcrService()

    service.start_poll(2_000)
    old_generation = service.poll_generation
    for _ in range(5):
        service.complete_poll(old_generation, "retryable_connection", "offline")
    assert not service._poll_timer.isActive()  # 连续失败后已暂停

    service.resume_poll()

    assert service.poll_generation > old_generation
    assert service._poll_timer.isActive()
    assert service._poll_timer.interval() == 2_000
    assert service.poll_state == "running"


def test_invalidate_inflight_poll_cancels_session_and_resets_inflight_flag() -> None:
    """作废在途轮询：代数递增、旧取消事件置位、在途标记复位，定时器不受影响。"""
    _app()
    service = OcrService()
    service.start_poll(1_000)
    old_generation = service.poll_generation
    old_event = service.poll_cancel_event
    service._poll_in_flight = True  # 模拟在途一轮尚未回写

    service.invalidate_inflight_poll()

    assert service.poll_generation == old_generation + 1
    assert old_event.is_set()
    assert not service.is_poll_cancelled(service.poll_generation)
    assert service._poll_in_flight is False
    assert service._poll_timer.isActive()


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
    monkeypatch.setattr("src.business.recognition.ocr_service.get_template_manager", lambda _name: manager)

    service = OcrService()
    service.select_template(str(source), "match_guide")

    assert manager.template_path.read_bytes() == b"external-template"
    assert not metadata_path.exists()
    assert manager.reloaded
