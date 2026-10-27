"""BaseFetchService 错误通道收尾语义回归。

Qt 在子进程 native 崩溃时先后发 errorOccurred(Crashed) 与 finished 两个
信号：错误通道只对 FailedToStart（Qt 不为它发 finished）做完整收尾，
Crashed 等其余错误只记日志并把原因暂存，广播与台账统一交给 finished
通道——否则同一事件双通道重复，UI 连弹两个错误框、台账双记账
（与 ScriptRunner 的收尾契约对齐）。
"""

from __future__ import annotations

import logging

import src.business.fetching.base_fetch_service as bfs
from PySide6.QtCore import QProcess
from src.business.fetching.base_fetch_service import BaseFetchService


class _FakeProc:
    """_on_error / _on_finished 路径所需的 QProcess 替身。"""

    def state(self):
        return QProcess.ProcessState.NotRunning

    def kill(self):
        pass

    def deleteLater(self):
        pass

    def readAllStandardOutput(self):
        return b""

    def readAllStandardError(self):
        return b""

    def errorString(self) -> str:
        return "模拟错误描述"


def _make_service(qapp, monkeypatch) -> tuple[BaseFetchService, dict]:
    """构造接好信号收集与台账替身的服务实例。"""
    ledger: list[dict] = []
    monkeypatch.setattr(bfs, "record_task", lambda name, **kw: ledger.append({"name": name, **kw}))
    service = BaseFetchService()
    service._process = _FakeProc()
    return service, ledger


def test_crashed_defers_to_finished_channel(qapp, monkeypatch) -> None:
    """Crashed 只记日志并暂存原因；广播与台账等 finished 到达后各做一次。"""
    service, ledger = _make_service(qapp, monkeypatch)
    errors: list[str] = []
    service.error_occurred.connect(errors.append)

    service._on_error(QProcess.ProcessError.Crashed)

    assert errors == []          # 错误通道不广播
    assert ledger == []          # 也不记台账
    assert service._pending_error_msg is not None

    service._on_finished(1)      # Qt 在 Crashed 后必发 finished

    assert len(errors) == 1      # 全链路仅广播一次
    assert "子进程崩溃" in errors[0]
    assert len(ledger) == 1      # 台账仅记一次
    assert "子进程崩溃" in ledger[0]["reason"]  # 崩溃原因优先于裸退出码
    assert ledger[0]["ok"] is False
    assert service._pending_error_msg is None


def test_write_error_also_defers_to_finished_channel(qapp, monkeypatch) -> None:
    """ReadError / WriteError / Timedout 与 Crashed 同语义：不提前收尾。"""
    service, ledger = _make_service(qapp, monkeypatch)
    errors: list[str] = []
    service.error_occurred.connect(errors.append)

    service._on_error(QProcess.ProcessError.WriteError)

    assert errors == []
    assert ledger == []
    assert service._pending_error_msg is not None


def test_crashed_during_cancel_stays_silent(qapp, monkeypatch) -> None:
    """取消链路的树杀以 Crashed 到达：静默让位给取消收尾。"""
    service, ledger = _make_service(qapp, monkeypatch)
    errors: list[str] = []
    service.error_occurred.connect(errors.append)
    service._cancel_requested = True

    service._on_error(QProcess.ProcessError.Crashed)

    assert errors == []
    assert ledger == []
    assert service._pending_error_msg is None  # 不得污染后续 finished 的 msg


def test_failed_to_start_finalizes_without_finished(qapp, monkeypatch, caplog) -> None:
    """FailedToStart 无 finished 跟随：错误通道即唯一收尾通道。"""
    service, ledger = _make_service(qapp, monkeypatch)
    errors: list[str] = []
    statuses: list[str] = []
    service.error_occurred.connect(errors.append)
    service.status_changed.connect(statuses.append)

    with caplog.at_level(logging.ERROR):
        service._on_error(QProcess.ProcessError.FailedToStart)

    assert len(errors) == 1
    assert "子进程启动失败" in errors[0]
    assert statuses[-1] == "子进程出错"
    assert len(ledger) == 1
    assert service._process is None  # 已释放，_is_busy 不再永久 True


def test_failed_to_start_during_cancel_finishes_cancellation(qapp, monkeypatch) -> None:
    """取消期间 Starting 失败：释放进程并走取消收尾，广播 cancelled（TD-A5-001）。"""
    service, ledger = _make_service(qapp, monkeypatch)
    errors: list[str] = []
    cancelled: list[bool] = []
    service.error_occurred.connect(errors.append)
    service.cancelled.connect(lambda: cancelled.append(True))
    service._cancel_requested = True

    service._on_error(QProcess.ProcessError.FailedToStart)

    assert errors == []          # 取消路径不弹错误框
    assert cancelled == [True]
    assert service._process is None
    assert service._cancel_requested is False  # 取消标记复位，服务可继续接新任务
    assert len(ledger) == 1      # 失败仍留台账


def test_pending_error_msg_reset_on_next_task(qapp, monkeypatch) -> None:
    """新一轮任务的启动必须清掉上一轮暂存的错误原因，不跨任务残留。"""
    service, _ledger = _make_service(qapp, monkeypatch)
    service._pending_error_msg = "上一轮的崩溃原因"

    monkeypatch.setattr(service, "_start_watchdog", lambda: None)
    monkeypatch.setattr(service, "_release_process", lambda: None)
    monkeypatch.setattr(bfs.QProcess, "start", lambda *a, **kw: None)
    service._start_process(["-m", "src.scraper.ai_batch", "--guide"])

    assert service._pending_error_msg is None
