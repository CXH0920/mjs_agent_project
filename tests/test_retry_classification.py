# -*- coding: utf-8 -*-
"""D4 重试分类回归：不可重试状态码立即失败，不再消耗退避轮次。"""
from __future__ import annotations

import urllib.error

import httpx
import pytest
from src.scraper.ai.api_generator import AIBatchGenerator
from src.scraper.official_source import crawler


def _make_generator(status: int, calls: list[int]) -> AIBatchGenerator:
    """构造 client 被 MockTransport 替换的生成器，记录请求次数。"""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(status, json={"error": "boom"})

    generator = AIBatchGenerator(api_key="sk-test", max_retries=3, requests_per_minute=1000)
    generator._client = httpx.Client(transport=httpx.MockTransport(handler))
    return generator


def test_non_retryable_status_fails_immediately() -> None:
    """401 属 Key 配错：只请求一次即抛，不消耗 3 轮退避。"""
    calls: list[int] = []
    generator = _make_generator(401, calls)

    with pytest.raises(httpx.HTTPStatusError):
        generator.complete([{"role": "user", "content": "hi"}])

    assert len(calls) == 1


def test_server_error_still_retries_full_round() -> None:
    """5xx 属瞬时故障：维持原有完整退避重试轮次（耗尽后按现有契约返回 None）。"""
    calls: list[int] = []
    generator = _make_generator(500, calls)
    generator._min_interval = 0.0  # 测试不等限速

    result = generator.complete([{"role": "user", "content": "hi"}])

    assert result is None
    assert len(calls) == 3


def test_crawler_404_not_retried(monkeypatch) -> None:
    """404 重试只会重复打同一请求：立即抛出，仅请求一次。"""
    calls: list[int] = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.HTTPError):
        crawler.fetch("https://example.com/missing.json")

    assert len(calls) == 1


def test_crawler_transient_error_still_retries(monkeypatch) -> None:
    """连接类瞬时异常维持原有重试轮次。"""
    calls: list[int] = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        raise OSError("connection reset")

    monkeypatch.setattr(crawler.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(crawler.time, "sleep", lambda _s: None)

    with pytest.raises(OSError):
        crawler.fetch("https://example.com/data.json")

    assert len(calls) == crawler.MAX_RETRIES
