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
    # 正常写回路径的前提是文件在盘上（mtime 前后一致）；文件不存在属
    # "放弃写回"场景，由 test_reload_aborts_when_file_deleted_during_reload 锁定
    manager.file_path.write_text("[]", encoding="utf-8")
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


def test_reload_aborts_when_file_deleted_during_reload(monkeypatch, tmp_path) -> None:
    """重载期间相性文件被删除时放弃写回，旧快照不得复活成新数据。

    回归（第一轮审查 P3-1）：_reload_stale 曾把"文件已消失"（getmtime 抛
    OSError）当作"未变化"返回 False，启动时读到的旧快照会被
    replace_loaded_data 写回内存，后续 UI 保存会把已删除的文件复活。
    """
    manager = SimpleNamespace(file_path=tmp_path / "synergies.json")
    writes: list = []
    manager.replace_loaded_data = lambda synergies, issues: writes.append((synergies, issues))
    service = SynergyFetchService(manager)
    finished: list[bool] = []
    service.reload_finished.connect(lambda: finished.append(True))
    monkeypatch.setattr(
        "src.business.fetching.synergy_fetch_service.SynergyReloadWorker", _ReloadWorker
    )
    # 第 1 次 getmtime（reload_from_disk 启动时）：文件还在；第 2 次
    # （_reload_stale 复核时）：文件已被外部删除
    outcomes: list = [1730000000.0, FileNotFoundError("文件已删除")]
    monkeypatch.setattr(
        "src.business.fetching.synergy_fetch_service.os.path.getmtime",
        lambda _path: (_raise_if_exc(outcomes.pop(0))),
    )

    assert service.reload_from_disk() is True
    _ReloadWorker.instance.loaded.emit([{"pair": (1, 2)}], [])

    assert writes == []  # 放弃写回：不得把旧快照写回内存
    assert finished == [True]  # 完成信号照常广播，UI 状态正常复位


def _raise_if_exc(outcome):
    if isinstance(outcome, Exception):
        raise outcome
    return outcome


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


def test_submit_rejected_while_reload_running(monkeypatch) -> None:
    """回归（VUL-B3-002）：重载进行中不得启动生成子进程。

    只拦「抓取中重载」是单向的：重载线程读的是启动前快照，期间若启动生成
    并分批提交，重载完成会用旧快照覆盖新数据。
    """
    service = SynergyFetchService(SimpleNamespace(file_path="synergies.json"))
    monkeypatch.setattr(
        "src.business.fetching.synergy_fetch_service.SynergyReloadWorker", _ReloadWorker
    )
    started: list[list[str]] = []
    monkeypatch.setattr(service, "_start_process", lambda args: started.append(args))

    assert service.reload_from_disk() is True
    assert service.is_busy is True
    assert service.fetch_pairs_list([{"hero_a_id": 1, "hero_b_id": 2}]) is False
    assert started == []


def test_submit_dump_failure_emits_error_and_removes_tmp(monkeypatch, tmp_path) -> None:
    """临时配对清单写失败：句柄关闭、半截文件删除、发错误信号、返回未启动。"""
    import tempfile
    from pathlib import Path

    from src.business.fetching import synergy_fetch_service as synergy_module

    service = SynergyFetchService(SimpleNamespace(file_path=str(tmp_path / "synergies.json")))
    errors: list[str] = []
    service.error_occurred.connect(errors.append)
    monkeypatch.setattr(service, "_start_process", lambda args: None)
    created: list[str] = []
    real_ntf = tempfile.NamedTemporaryFile

    def _spy(*args, **kwargs):
        handle = real_ntf(*args, **kwargs)
        created.append(handle.name)
        return handle

    monkeypatch.setattr(synergy_module.tempfile, "NamedTemporaryFile", _spy)

    started = service.fetch_pair(
        [{"id": 1, "name": "甲", "bad": {1}}, {"id": 2, "name": "乙"}],
        backend="api", overwrite=False, use_rag=True,
    )

    assert started is False
    assert errors and "失败" in errors[0]
    assert created and not Path(created[0]).exists()
