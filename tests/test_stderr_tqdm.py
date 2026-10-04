"""stderr 进度条降噪回归：tqdm 行降为 DEBUG，不再贡献 WARNING 噪声。

Paddle/transformers 加载进度条走子进程 stderr，此前一律记 WARNING，
曾贡献 ai_generation.log 89% 的 WARNING（824/925 条）。
"""

from __future__ import annotations

import logging

import pytest
from src.business.fetching.base_fetch_service import BaseFetchService


@pytest.fixture
def service(qapp):
    return BaseFetchService()


def test_tqdm_stderr_downgraded_to_debug(service, caplog, monkeypatch):
    class _FakeProcess:
        @staticmethod
        def readAllStandardError():
            return " 50%|█████   | 5/10 [00:01<00:01]\n".encode("utf-8")

    monkeypatch.setattr(service, "_process", _FakeProcess())

    with caplog.at_level(logging.DEBUG, logger="subprocess.unclassified.stderr"):
        service._read_stderr()

    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG and "%|" in r.getMessage()]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert debugs and not warnings


def test_real_error_stderr_stays_warning(service, caplog, monkeypatch):
    class _FakeProcess:
        @staticmethod
        def readAllStandardError():
            return "Traceback (most recent call last)\nValueError: boom\n".encode("utf-8")

    monkeypatch.setattr(service, "_process", _FakeProcess())

    with caplog.at_level(logging.DEBUG, logger="subprocess.unclassified.stderr"):
        service._read_stderr()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ValueError" in warnings[0].getMessage()
