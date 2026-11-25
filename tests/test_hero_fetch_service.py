"""武将采集服务子进程进度解析测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.business.fetching import base_fetch_service as bfs
from src.business.fetching.hero_fetch_service import HeroFetchService


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_parse_progress_line() -> None:
    _app()
    service = HeroFetchService()
    events = []
    service.progress_updated.connect(
        lambda current, total, text: events.append((current, total, text))
    )
    service._on_stdout_line("[1/5] 定位数据源...")
    service._on_stdout_line("  [3/3] 数据清洗与写入.. ")
    assert events == [
        (1, 5, "定位数据源..."),
        (3, 3, "数据清洗与写入.."),
    ]


def test_ignore_non_progress_lines() -> None:
    _app()
    service = HeroFetchService()
    events = []
    service.progress_updated.connect(
        lambda current, total, text: events.append((current, total, text))
    )
    service._on_stdout_line("  完成! 共 171 个武将")
    service._on_stdout_line("  -> 官网原始数据: 172 条")
    assert events == []


def test_ignore_malformed_progress_lines() -> None:
    _app()
    service = HeroFetchService()
    events = []
    service.progress_updated.connect(
        lambda current, total, text: events.append((current, total, text))
    )
    service._on_stdout_line("[abc/5] 无效进度")
    service._on_stdout_line("5/3] 缺括号")
    assert events == []


def test_fetch_methods_return_false_when_busy() -> None:
    """忙碌时返回 False 且不启动子进程（H5：调用方据此识别失败而非静默）。"""
    _app()
    service = HeroFetchService()
    started: list[list[str]] = []
    service._is_busy = lambda: True
    service._start_process = lambda args: started.append(args)

    assert service.fetch_all() is False
    assert service.fetch_incremental() is False
    assert service.fetch_specific([1, 2]) is False
    assert started == []


def test_fetch_methods_start_process_when_idle() -> None:
    _app()
    service = HeroFetchService()
    started: list[list[str]] = []
    service._is_busy = lambda: False
    service._start_process = lambda args: started.append(args)

    assert service.fetch_all() is True
    assert service.fetch_specific([7, 9]) is True
    assert started == [
        ["-m", "src.scraper.official"],
        ["-m", "src.scraper.incremental", "--hero-id", "7,9"],
    ]


def test_fetch_specific_rejects_empty_ids() -> None:
    """回归（VUL-B4-002）：空 ID 列表不得启动子进程。

    子进程对空目标会打印「无目标武将」并以 0 退出，父进程据此上报成功，
    公告更新流程会把未采集的批次当成已完成并 mark_applied，待更新武将被
    静默丢弃。
    """
    _app()
    service = HeroFetchService()
    started: list[list[str]] = []
    service._is_busy = lambda: False
    service._start_process = lambda args: started.append(args)
    statuses: list[str] = []
    service.status_changed.connect(statuses.append)

    assert service.fetch_specific([]) is False
    assert started == []
    assert statuses == ["未选择任何武将，无需采集"]


# ---------------------------------------------------------------------------
# 落盘回报（指定采集子进程保存后输出的「[写入武将] 1,3」协议行）
# ---------------------------------------------------------------------------


class _FakeProcess:
    """QProcess 替身：按块吐出 stdout，够走真实的行分发与完成收尾。"""

    def __init__(self, stdout_chunks: list[bytes]) -> None:
        self._chunks = list(stdout_chunks)

    def readAllStandardOutput(self) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""

    def readAllStandardError(self) -> bytes:
        return b""

    def deleteLater(self) -> None:
        pass


def test_written_line_is_parsed_from_stdout_bytes() -> None:
    """落盘回报行经真实的字节分发路径解析为已写入武将 id 集合。

    协议见 src.scraper.official_source.incremental.run：子进程保存后输出
    「[写入武将] 1,3」。日志前缀行与内容非法的行都不是协议行：不匹配，也不覆盖。
    """
    _app()
    service = HeroFetchService()

    service._dispatch_stdout_line("  [写入武将] 1, 3\r\n".encode("utf-8"))

    assert service.written_hero_ids == {1, 3}
    service._dispatch_stdout_line("2026-11-25 [INFO] x.y: [写入武将] 9".encode("utf-8"))
    service._dispatch_stdout_line("[写入武将] 7,x".encode("utf-8"))
    assert service.written_hero_ids == {1, 3}


def test_written_report_is_ready_before_fetch_completed() -> None:
    """回报在 fetch_completed 之前解析完毕：完成回调里读到的必须是本轮结果。

    回报行按最不利的时序送达：收尾时才到、且是没有换行的末行（须经 flush 分发）。
    """
    _app()
    service = HeroFetchService()
    service._process = _FakeProcess([b"[1/3] step\n", "[写入武将] 7,9".encode("utf-8")])
    seen: list[set[int] | None] = []
    service.fetch_completed.connect(lambda _ok: seen.append(service.written_hero_ids))

    service._on_stdout_ready()  # 运行期间到达的进度行
    assert seen == [] and service.written_hero_ids is None

    service._on_finished(0)

    assert seen == [{7, 9}]


def test_each_run_starts_with_clean_written_report(monkeypatch) -> None:
    """每轮启动清空上一轮的回报与请求集：残留会让无关采集的完成事件算出假的「未更新」。"""
    _app()
    service = HeroFetchService()
    monkeypatch.setattr(service, "_start_watchdog", lambda: None)
    monkeypatch.setattr(service, "_release_process", lambda: None)
    monkeypatch.setattr(bfs.QProcess, "start", lambda *a, **kw: None)
    service.written_hero_ids = {1}
    service.requested_hero_ids = {1}

    assert service.fetch_specific([5, 6]) is True
    assert service.written_hero_ids is None
    assert service.requested_hero_ids == {5, 6}

    assert service.fetch_all() is True
    assert service.requested_hero_ids == set()
    assert service.unwritten_hero_ids == set()


def test_unwritten_hero_ids_reports_requested_but_not_written() -> None:
    """指定采集请求了但未落盘的武将 id；子进程未回报时无从判定，视为无。"""
    _app()
    service = HeroFetchService()
    service.requested_hero_ids = {1, 2, 3}

    service.written_hero_ids = {1, 3}
    assert service.unwritten_hero_ids == {2}
    service.written_hero_ids = None
    assert service.unwritten_hero_ids == set()