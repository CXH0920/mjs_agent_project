"""AI 生成 UI 工作流测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QDialog

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide
from src.data.synergy_manager import SynergyManager
from src.ui import ai_generation_workflow as workflow_module
from src.ui.ai_generation_workflow import AiGenerationWorkflow
from src.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _GuideService(QObject):
    status_changed = Signal(str)
    fetch_completed = Signal(bool, str)
    error_occurred = Signal(str)
    progress_output = Signal(str)
    progress_value = Signal(int, int)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, list[dict], str]] = []
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1

    def fetch_all(self, heroes: list[dict], backend: str) -> None:
        self.calls.append(("all", heroes, backend))
        self.fetch_completed.emit(True, "完成")

    def fetch_incremental(self, heroes: list[dict], backend: str) -> None:
        self.calls.append(("incremental", heroes, backend))
        self.fetch_completed.emit(True, "完成")

    def fetch_specific(self, heroes: list[dict], backend: str) -> None:
        self.calls.append(("specific", heroes, backend))
        self.fetch_completed.emit(True, "完成")


class _SynergyService(QObject):
    status_changed = Signal(str)
    fetch_completed = Signal(bool, str)
    error_occurred = Signal(str)
    progress_output = Signal(str)
    progress_value = Signal(int, int)
    cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[dict, list[dict], str]] = []
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1

    def fetch_pair(self, heroes: list[dict], backend: str) -> None:
        raise AssertionError("此测试不应执行指定配对")

    def fetch_single(self, hero: dict, all_heroes: list[dict], backend: str) -> None:
        self.calls.append((hero, all_heroes, backend))
        self.fetch_completed.emit(True, "完成")


class _BackendDialog:
    def __init__(self, **_kwargs) -> None:
        pass

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def get_selected_backend(self) -> str:
        return "browser"


class _ProgressDialog:
    instances: list["_ProgressDialog"] = []

    def __init__(self, item_count: int, title: str = "攻略生成进度", item_label: str = "攻略", parent=None) -> None:
        self.item_count = item_count
        self.title = title
        self.item_label = item_label
        self.finished: list[tuple[bool, str]] = []
        self.cancel_requested = _CallbackSignal()
        _ProgressDialog.instances.append(self)

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def on_process_finished(self, success: bool, message: str = "") -> None:
        self.finished.append((success, message))

    def update_status(self, _text: str) -> None:
        pass

    def update_progress(self, _current: int, _total: int) -> None:
        pass

    def on_process_cancelled(self) -> None:
        pass


class _CallbackSignal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self) -> None:
        for callback in self._callbacks:
            callback()


class _SingleHeroDialog:
    def __init__(self, _hero_manager, parent=None) -> None:
        self.selected_hero = {"id": 1, "name": "曹操"}

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted


def _workflow(tmp_path: Path) -> tuple[AiGenerationWorkflow, _GuideService, _SynergyService]:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    hero_manager.add_hero(Hero(id=2, name="刘备"))
    guide_manager.add_guide(HeroGuide(hero_id=1))
    guide_service = _GuideService()
    synergy_service = _SynergyService()
    workflow = AiGenerationWorkflow(
        hero_manager,
        guide_manager,
        synergy_manager,
        guide_service,
        synergy_service,
    )
    return workflow, guide_service, synergy_service


def test_incremental_guide_workflow_uses_only_missing_heroes(tmp_path: Path, monkeypatch) -> None:
    workflow, guide_service, _ = _workflow(tmp_path)
    reloads: list[bool] = []
    changed: list[bool] = []
    monkeypatch.setattr(workflow._guide_manager, "load", lambda: reloads.append(True))
    monkeypatch.setattr(workflow_module, "BackendChooseDialog", _BackendDialog)
    monkeypatch.setattr(workflow_module, "GuideProgressDialog", _ProgressDialog)
    monkeypatch.setattr("src.config.env.get_api_config", lambda: {"model": "test-model"})
    monkeypatch.setattr(
        "src.scraper.prompt_utils.estimate_cost",
        lambda count, *_args: {"items": count, "estimated_cost_cny": 0.0},
    )
    workflow.guides_changed.connect(lambda: changed.append(True))

    workflow.request_guide_incremental()

    assert guide_service.calls[0][0] == "incremental"
    assert [hero["id"] for hero in guide_service.calls[0][1]] == [2]
    assert guide_service.calls[0][2] == "browser"
    assert _ProgressDialog.instances[-1].item_count == 1
    assert _ProgressDialog.instances[-1].finished == [(True, "完成")]
    assert reloads == [True]
    assert changed == [True]


def test_single_synergy_workflow_refreshes_after_completion(tmp_path: Path, monkeypatch) -> None:
    workflow, _, synergy_service = _workflow(tmp_path)
    reloads: list[bool] = []
    changed: list[bool] = []
    monkeypatch.setattr(workflow._synergy_manager, "load", lambda: reloads.append(True))
    monkeypatch.setattr(workflow_module, "BackendChooseDialog", _BackendDialog)
    monkeypatch.setattr(workflow_module, "GuideProgressDialog", _ProgressDialog)
    monkeypatch.setattr(workflow_module, "SynergySingleDialog", _SingleHeroDialog)
    workflow.synergies_changed.connect(lambda: changed.append(True))

    workflow.request_synergy_single()

    assert synergy_service.calls[0][0] == {"id": 1, "name": "曹操"}
    assert [hero["id"] for hero in synergy_service.calls[0][1]] == [1, 2]
    assert synergy_service.calls[0][2] == "browser"
    assert _ProgressDialog.instances[-1].item_count == 1
    assert _ProgressDialog.instances[-1].item_label == "相性评分"
    assert reloads == [True]
    assert changed == [True]


def test_progress_dialog_cancel_requests_guide_service(tmp_path: Path, monkeypatch) -> None:
    workflow, guide_service, _ = _workflow(tmp_path)
    monkeypatch.setattr(workflow_module, "BackendChooseDialog", _BackendDialog)
    monkeypatch.setattr(workflow_module, "GuideProgressDialog", _ProgressDialog)
    monkeypatch.setattr("src.config.env.get_api_config", lambda: {"model": "test-model"})
    monkeypatch.setattr(
        "src.scraper.prompt_utils.estimate_cost",
        lambda count, *_args: {"items": count, "estimated_cost_cny": 0.0},
    )

    workflow.request_guide_all()
    _ProgressDialog.instances[-1].cancel_requested.emit()

    assert guide_service.cancel_calls == 1


def test_main_window_generation_entries_delegate_to_workflow() -> None:
    class _WorkflowRecorder:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def request_guide_all(self) -> None:
            self.calls.append("guide_all")

        def request_guide_incremental(self) -> None:
            self.calls.append("guide_incremental")

        def request_guide_specific(self) -> None:
            self.calls.append("guide_specific")

        def request_synergy_pair(self) -> None:
            self.calls.append("synergy_pair")

        def request_synergy_single(self) -> None:
            self.calls.append("synergy_single")

    window = MainWindow.__new__(MainWindow)
    window._ai_workflow = _WorkflowRecorder()

    window._request_guide_all()
    window._request_guide_incremental()
    window._request_guide_specific()
    window._request_synergy_pair()
    window._request_synergy_single()

    assert window._ai_workflow.calls == [
        "guide_all",
        "guide_incremental",
        "guide_specific",
        "synergy_pair",
        "synergy_single",
    ]
