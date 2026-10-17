"""相性获取业务服务测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.business.fetching.synergy_fetch_service import SynergyFetchService


def _service_with_captured_args(monkeypatch) -> tuple[SynergyFetchService, dict[str, list[str]]]:
    service = SynergyFetchService(SimpleNamespace(file_path="synergies.json"))
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(service, "_start_process", lambda args: captured.setdefault("args", args))
    return service, captured


def test_fetch_pair_appends_no_rag_when_classic(monkeypatch) -> None:
    """经典模式（use_rag=False）时相性配对子进程参数应包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pair(
        [{"id": 1, "name": "曹操"}, {"id": 2, "name": "刘备"}],
        backend="api",
        overwrite=False,
        use_rag=False,
    )
    assert "--no-rag" in captured["args"]


def test_fetch_pair_omits_no_rag_when_rag_enabled(monkeypatch) -> None:
    """RAG 增强（use_rag=True）时相性配对子进程参数不应包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pair(
        [{"id": 1, "name": "曹操"}, {"id": 2, "name": "刘备"}],
        backend="api",
        overwrite=False,
        use_rag=True,
    )
    assert "--no-rag" not in captured["args"]


def test_fetch_single_appends_no_rag_when_classic(monkeypatch) -> None:
    """经典模式（use_rag=False）时选定武将相性子进程参数应包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_single(
        {"id": 1, "name": "曹操"},
        [{"id": 1, "name": "曹操"}, {"id": 2, "name": "刘备"}],
        backend="api",
        use_rag=False,
    )
    assert "--no-rag" in captured["args"]


def test_fetch_pairs_list_builds_synergy_list_args(monkeypatch) -> None:
    """实战配队清单：经典模式参数包含 --synergy-list 与 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pairs_list(
        [{"hero_a_id": 1, "hero_b_id": 2}],
        backend="api",
        overwrite=False,
        use_rag=False,
    )
    args = captured["args"]
    assert "--synergy-list" in args
    assert "--no-rag" in args
    assert "--update" not in args


def test_fetch_pairs_list_overwrite_appends_update(monkeypatch) -> None:
    """实战配队清单：覆盖模式参数包含 --update 且不包含 --no-rag。"""
    service, captured = _service_with_captured_args(monkeypatch)
    service.fetch_pairs_list(
        [{"hero_a_id": 1, "hero_b_id": 2}],
        backend="api",
        overwrite=True,
        use_rag=True,
    )
    args = captured["args"]
    assert "--synergy-list" in args
    assert "--update" in args
    assert "--no-rag" not in args


class _CallbackSignal:
    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in self._callbacks:
            # 目标可能是真 Qt Signal（服务的中继信号），按 Qt 语义转 emit
            if hasattr(callback, "emit"):
                callback.emit(*args)
            else:
                callback(*args)


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


def test_reload_from_disk_writes_manager_and_emits_finished(monkeypatch, tmp_path) -> None:
    """重载完成把结果原子写回共享 manager，并广播完成/失败信号（取消生成后保住分批数据）。"""
    manager = SimpleNamespace(file_path=tmp_path / "synergies.json")
    writes: list = []
    manager.replace_loaded_data = lambda synergies, issues: writes.append((synergies, issues))
    service = SynergyFetchService(manager)
    finished: list[bool] = []
    failures: list[str] = []
    service.reload_finished.connect(lambda: finished.append(True))
    service.reload_failed.connect(failures.append)

    monkeypatch.setattr(
        "src.business.fetching.synergy_fetch_service.SynergyReloadWorker", _ReloadWorker
    )
    assert service.reload_from_disk() is True
    assert _ReloadWorker.instance.started

    synergy = {"pair": (1, 2)}
    _ReloadWorker.instance.loaded.emit([synergy], [])
    assert writes == [([synergy], [])]
    assert finished == [True]

    _ReloadWorker.instance.failed.emit("boom")
    assert failures == ["boom"]


def test_reload_from_disk_skips_when_already_running(monkeypatch) -> None:
    """已有重载进行中时不重复启动，也不产生第二个 worker。"""
    service = SynergyFetchService(SimpleNamespace(file_path="synergies.json"))
    monkeypatch.setattr(
        "src.business.fetching.synergy_fetch_service.SynergyReloadWorker", _ReloadWorker
    )
    assert service.reload_from_disk() is True
    assert service.reload_from_disk() is False
    assert _ReloadWorker.instance is not None


class _DanglingReloadWorker:
    """失败路径替身：模拟 PySide6 在 deleteLater 销毁 C++ 对象后的行为。

    failed → finished → deleteLater 之后 Python 引用仍在，但任何方法调用
    抛 RuntimeError（Internal C++ object already deleted）——正是
    reload_from_disk 残留悬挂引用后重试的触发条件。
    """

    instance = None

    def __init__(self, _file_path, _parent=None) -> None:
        self.loaded = _CallbackSignal()
        self.failed = _CallbackSignal()
        self.finished = _CallbackSignal()
        self._cpp_alive = True
        _DanglingReloadWorker.instance = self

    def isRunning(self) -> bool:
        if not self._cpp_alive:
            raise RuntimeError("Internal C++ object (SynergyReloadWorker) already deleted")
        return False

    def start(self) -> None:
        pass

    def deleteLater(self) -> None:
        self._cpp_alive = False


def test_reload_failure_then_retry_starts_new_worker(monkeypatch) -> None:
    """重载失败释放 worker 引用后，重试可正常启动新 worker 而不抛 RuntimeError。

    回归（TD-A8-002）：failed 只转发不清引用时，worker 经 deleteLater 销毁
    C++ 对象后，重试的 isRunning() 必抛 RuntimeError。
    """
    service = SynergyFetchService(SimpleNamespace(file_path="synergies.json"))
    monkeypatch.setattr(
        "src.business.fetching.synergy_fetch_service.SynergyReloadWorker", _DanglingReloadWorker
    )
    failures: list[str] = []
    service.reload_failed.connect(failures.append)

    assert service.reload_from_disk() is True
    first_worker = _DanglingReloadWorker.instance
    first_worker.failed.emit("文件损坏")
    first_worker.finished.emit()  # → deleteLater（C++ 对象销毁）

    assert failures == ["文件损坏"]
    assert service._reload_worker is None
    assert service.reload_from_disk() is True  # 不抛 RuntimeError，启动新 worker
    assert _DanglingReloadWorker.instance is not first_worker


def test_reload_from_disk_rejects_while_fetch_running(monkeypatch) -> None:
    """抓取子进程运行中拒绝重载：避免旧文件快照覆盖内存态（VUL-B3-001）。"""
    from PySide6.QtCore import QProcess

    service = SynergyFetchService(SimpleNamespace(file_path="synergies.json"))
    monkeypatch.setattr(
        "src.business.fetching.synergy_fetch_service.SynergyReloadWorker", _DanglingReloadWorker
    )
    _DanglingReloadWorker.instance = None  # 类变量跨测试残留，先复位
    service._process = SimpleNamespace(state=lambda: QProcess.ProcessState.Running)

    assert service.reload_from_disk() is False
    assert _DanglingReloadWorker.instance is None  # 未创建 worker，未覆盖内存数据
