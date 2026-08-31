"""AI 生成 UI 工作流测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, SynergyScore
from src.data.synergy_manager import SynergyManager
from src.ui.generation import ai_generation_workflow as workflow_module
from src.ui.generation.ai_generation_workflow import AiGenerationWorkflow
from src.ui.generation.guide_progress_dialog import GuideProgressDialog
from src.ui.app.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _emit_completed_later(signal, success: bool, message: str) -> None:
    """真实服务经 QProcess 异步发完成信号（子进程结束后才到达）；工作流依赖
    "fetch 返回时信号尚未到达"的时序，假对象用零延迟定时器对齐该契约。"""
    QTimer.singleShot(0, lambda: signal.emit(success, message))


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
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def cancel(self) -> None:
        self.cancel_calls += 1

    def fetch_all(self, heroes: list[dict], backend: str, use_rag: bool = True) -> bool:
        self.calls.append(("all", heroes, backend, use_rag))
        _emit_completed_later(self.fetch_completed, True, "完成")
        return True

    def fetch_incremental(self, heroes: list[dict], backend: str, use_rag: bool = True) -> bool:
        self.calls.append(("incremental", heroes, backend, use_rag))
        _emit_completed_later(self.fetch_completed, True, "完成")
        return True

    def fetch_specific(self, heroes: list[dict], backend: str, use_rag: bool = True) -> bool:
        self.calls.append(("specific", heroes, backend, use_rag))
        _emit_completed_later(self.fetch_completed, True, "完成")
        return True


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
        self.pair_calls: list[tuple[list[dict], str, bool]] = []
        self.cancel_calls = 0
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy

    def cancel(self) -> None:
        self.cancel_calls += 1

    def fetch_pair(self, heroes: list[dict], backend: str, overwrite: bool = False, use_rag: bool = True) -> bool:
        self.pair_calls.append((heroes, backend, overwrite, use_rag))
        _emit_completed_later(self.fetch_completed, True, "完成")
        return True

    def fetch_single(self, hero: dict, all_heroes: list[dict], backend: str, use_rag: bool = True) -> bool:
        self.calls.append((hero, all_heroes, backend, use_rag))
        _emit_completed_later(self.fetch_completed, True, "完成")
        return True


class _BackendDialog:
    instances: list["_BackendDialog"] = []

    def __init__(self, **kwargs) -> None:
        self.estimation = kwargs.get("estimation")
        _BackendDialog.instances.append(self)

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted

    def get_selected_backend(self) -> str:
        return "browser"

    def get_selected_rag(self) -> bool:
        return True


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
        QApplication.processEvents()  # 送达 fetch 期间排队的零延迟完成信号（模拟真实模态事件循环）
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

    def emit(self, *args) -> None:
        for callback in self._callbacks:
            callback(*args)


class _SingleHeroDialog:
    def __init__(self, _hero_manager, parent=None) -> None:
        self.selected_hero = {"id": 1, "name": "曹操"}

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted


class _PairHeroDialog:
    def __init__(self, _hero_manager, _synergy_manager, parent=None) -> None:
        self.selected_heroes = [
            {"id": 1, "name": "曹操"},
            {"id": 2, "name": "刘备"},
        ]
        self.overwrite_existing = True

    def exec(self) -> QDialog.DialogCode:
        return QDialog.DialogCode.Accepted


class _GuideHeroDialog:
    guide_manager: GuideManager | None = None

    def __init__(self, _hero_manager, guide_manager, parent=None) -> None:
        _GuideHeroDialog.guide_manager = guide_manager
        self.selected_heroes = [{"id": 1, "name": "曹操"}]

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
    monkeypatch.setattr(
        workflow_module, "estimate_generation_cost",
        lambda count, kind, model=None, use_rag=None: {"items": count, "estimated_cost_cny": 0.0},
    )
    workflow.guides_changed.connect(lambda: changed.append(True))

    workflow.request_guide_incremental()

    assert guide_service.calls[0][0] == "incremental"
    assert [hero["id"] for hero in guide_service.calls[0][1]] == [2]
    assert guide_service.calls[0][2] == "browser"
    assert guide_service.calls[0][3] is True
    assert _ProgressDialog.instances[-1].item_count == 1
    assert _ProgressDialog.instances[-1].finished == [(True, "完成")]
    assert reloads == [True]
    assert changed == [True]


def test_specific_guide_workflow_passes_guide_manager(tmp_path: Path, monkeypatch) -> None:
    workflow, guide_service, _ = _workflow(tmp_path)
    monkeypatch.setattr(workflow_module, "GuideFetchDialog", _GuideHeroDialog)
    monkeypatch.setattr(workflow_module, "BackendChooseDialog", _BackendDialog)
    monkeypatch.setattr(workflow_module, "GuideProgressDialog", _ProgressDialog)
    monkeypatch.setattr(
        workflow_module, "estimate_generation_cost",
        lambda count, kind, model=None, use_rag=None: {"items": count, "estimated_cost_cny": 0.0},
    )

    workflow.request_guide_specific()

    assert _GuideHeroDialog.guide_manager is workflow._guide_manager
    assert guide_service.calls[0] == ("specific", [{"id": 1, "name": "曹操"}], "browser", True)


def test_single_synergy_workflow_refreshes_after_completion(tmp_path: Path, monkeypatch) -> None:
    workflow, _, synergy_service = _workflow(tmp_path)
    reloads: list[bool] = []
    changed: list[bool] = []
    monkeypatch.setattr(workflow._synergy_manager, "load", lambda: reloads.append(True))
    monkeypatch.setattr(workflow_module, "BackendChooseDialog", _BackendDialog)
    monkeypatch.setattr(workflow_module, "GuideProgressDialog", _ProgressDialog)
    monkeypatch.setattr(workflow_module, "SynergySingleDialog", _SingleHeroDialog)
    monkeypatch.setattr(
        workflow_module, "estimate_generation_cost",
        lambda count, kind, model=None, use_rag=None: {"items": count, "model": "test-model"},
    )
    workflow.synergies_changed.connect(lambda: changed.append(True))

    workflow.request_synergy_single()

    assert synergy_service.calls[0][0] == {"id": 1, "name": "曹操"}
    assert [hero["id"] for hero in synergy_service.calls[0][1]] == [1, 2]
    assert synergy_service.calls[0][2] == "browser"
    assert synergy_service.calls[0][3] is True
    assert _ProgressDialog.instances[-1].item_count == 1
    assert _ProgressDialog.instances[-1].item_label == "相性评分"
    assert _BackendDialog.instances[-1].estimation == {
        "items": 1, "model": "test-model", "estimate_kind": "synergy",
    }
    assert reloads == [True]
    assert changed == [True]


def test_pair_synergy_workflow_passes_overwrite_choice(tmp_path: Path, monkeypatch) -> None:
    workflow, _, synergy_service = _workflow(tmp_path)
    monkeypatch.setattr(workflow_module, "BackendChooseDialog", _BackendDialog)
    monkeypatch.setattr(workflow_module, "GuideProgressDialog", _ProgressDialog)
    monkeypatch.setattr(workflow_module, "SynergyPairDialog", _PairHeroDialog)
    monkeypatch.setattr(
        workflow_module, "estimate_generation_cost",
        lambda count, kind, model=None, use_rag=None: {"items": count, "model": "test-model"},
    )

    workflow.request_synergy_pair()

    assert synergy_service.pair_calls == [
        ([{"id": 1, "name": "曹操"}, {"id": 2, "name": "刘备"}], "browser", True, True),
    ]
    assert _ProgressDialog.instances[-1].item_count == 1
    assert _BackendDialog.instances[-1].estimation == {
        "items": 1, "model": "test-model", "estimate_kind": "synergy",
    }


def test_guide_workflow_skips_modal_when_service_busy(tmp_path: Path, monkeypatch) -> None:
    """回归：服务忙碌时不得进入模态 exec——busy 静默返回没有完成信号，进度框会永久卡死。"""
    workflow, guide_service, _ = _workflow(tmp_path)
    guide_service._busy = True
    warnings: list[str] = []
    instances_before = len(_ProgressDialog.instances)
    monkeypatch.setattr(
        workflow_module.QMessageBox, "warning",
        lambda _parent, title, _text: warnings.append(title),
    )

    workflow.request_guide_all()

    assert warnings == ["生成进行中"]
    assert guide_service.calls == []  # 未发起生成
    assert len(_ProgressDialog.instances) == instances_before  # 未弹出生成进度框


def test_progress_dialog_cancel_requests_guide_service(tmp_path: Path, monkeypatch) -> None:
    workflow, guide_service, _ = _workflow(tmp_path)
    monkeypatch.setattr(workflow_module, "BackendChooseDialog", _BackendDialog)
    monkeypatch.setattr(workflow_module, "GuideProgressDialog", _ProgressDialog)
    monkeypatch.setattr(
        workflow_module, "estimate_generation_cost",
        lambda count, kind, model=None, use_rag=None: {"items": count, "estimated_cost_cny": 0.0},
    )

    workflow.request_guide_all()
    _ProgressDialog.instances[-1].cancel_requested.emit()

    assert guide_service.cancel_calls == 1


def test_synergy_cancel_reloads_data_in_background(tmp_path: Path, monkeypatch) -> None:
    workflow, _, _ = _workflow(tmp_path)
    changed: list[bool] = []
    load_calls: list[bool] = []

    class _ReloadWorker:
        instance = None

        def __init__(self, _file_path, _parent=None) -> None:
            self.loaded = _CallbackSignal()
            self.failed = _CallbackSignal()
            self.finished = _CallbackSignal()
            self.started = False
            _ReloadWorker.instance = self

        def isRunning(self) -> bool:
            return self.started

        def start(self) -> None:
            self.started = True

        def deleteLater(self) -> None:
            pass

    monkeypatch.setattr(workflow._synergy_manager, "load", lambda: load_calls.append(True))
    monkeypatch.setattr(workflow_module, "SynergyReloadWorker", _ReloadWorker)
    workflow.synergies_changed.connect(lambda: changed.append(True))

    workflow._on_synergy_cancelled()

    assert load_calls == []
    assert _ReloadWorker.instance.started
    assert changed == []

    _ReloadWorker.instance.loaded.emit([SynergyScore(hero_a_id=1, hero_b_id=2, score=6)], [])

    assert workflow._synergy_manager.get_synergy(1, 2).score == 6
    assert changed == [True]


def test_synergy_progress_stays_at_zero_until_first_result() -> None:
    _app()
    dialog = GuideProgressDialog(3, item_label="相性评分")

    dialog.update_status("[1/3] 甲 <-> 乙 START")

    assert dialog._progress_bar.value() == 0
    assert dialog._progress_bar.format() == "0 / 3"
    assert dialog._status_label.text() == "正在生成 甲 <-> 乙 的相性评分..."
    dialog.update_status("[1/3] 甲 <-> 乙 OK - 评分: 8")
    dialog.update_status("2026-07-27 [INFO] src.scraper.ai.browser_generator: [休息] 随机休息 126 秒...")
    assert dialog._progress_bar.value() == 1
    assert dialog._status_label.text() == "冷却中（约 126 秒），已完成 1 / 3"
    dialog.on_process_finished(True)
    dialog.close()


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
