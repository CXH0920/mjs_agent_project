# -*- coding: utf-8 -*-
"""api_generator 同名测试：限流退避 _retry_wait 合同与 429 重试循环接线。

补第 6 批审查 P2 盲区——429 限流处理恰是最易在生产踩、最难本地复现的
路径（需真实被限流才触发），此前全目录零覆盖；本文件以纯函数用例锁合同、
以 MockTransport 集成用例锁 _call_api 的调用点接线。
"""

from __future__ import annotations

import httpx
from src.scraper.ai import api_generator
from src.scraper.ai.api_generator import AIBatchGenerator

# 限速间隔置 0（构造后直写）：elapsed < 0 恒假，time.sleep 只会来自重试等待本身


# ---------------------------------------------------------------
# _retry_wait 纯函数合同
# ---------------------------------------------------------------

def test_retry_wait_non_429_uses_exponential_backoff() -> None:
    """非 429（瞬时 5xx/408）按 2^attempt 指数退避。"""
    assert AIBatchGenerator._retry_wait(500, 0, {}) == 1
    assert AIBatchGenerator._retry_wait(503, 2, {}) == 4


def test_retry_wait_429_without_header_has_floor_and_scales() -> None:
    """429 无 Retry-After：5×attempt 递增，attempt=0 触发 3 秒下限。"""
    assert AIBatchGenerator._retry_wait(429, 0, {}) == 3
    assert AIBatchGenerator._retry_wait(429, 1, {}) == 5
    assert AIBatchGenerator._retry_wait(429, 3, {}) == 15


def test_retry_wait_429_retry_after_clamped_to_3_30() -> None:
    """429 带 Retry-After：采用服务器指示值，钳制在 [3, 30] 秒。"""
    assert AIBatchGenerator._retry_wait(429, 0, {"retry-after": "10"}) == 10
    assert AIBatchGenerator._retry_wait(429, 0, {"retry-after": "2"}) == 3
    assert AIBatchGenerator._retry_wait(429, 0, {"retry-after": "60"}) == 30


def test_retry_wait_429_http_date_falls_back_to_scaling() -> None:
    """Retry-After 为 HTTP-date（非纯数字）时按无头回退处理。"""
    assert AIBatchGenerator._retry_wait(
        429, 2, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}) == 10


# ---------------------------------------------------------------
# _call_api 重试循环接线（MockTransport 构造 429→200，免真实网络）
# ---------------------------------------------------------------

def _ok_response() -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "正文内容"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5},
    })


def test_call_api_retries_on_429_using_retry_after(monkeypatch, capsys) -> None:
    """429 → 按 Retry-After 等待后重试成功；等待值与 [重试] 提示行同步。"""
    generator = AIBatchGenerator(api_key="test-key")
    generator._min_interval = 0.0
    sleeps: list[float] = []
    monkeypatch.setattr(api_generator.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return _ok_response()

    generator._client = httpx.Client(transport=httpx.MockTransport(handler))

    data = generator._call_api([{"role": "user", "content": "hi"}])

    assert data is not None
    assert data["content"] == "正文内容"
    assert calls["n"] == 2
    assert sleeps == [7]  # 429 分支等待来自 Retry-After（钳制区间内原样采用）
    assert "[重试] HTTP 429" in capsys.readouterr().out


def test_call_api_gives_up_after_max_retries_without_header(monkeypatch) -> None:
    """持续 429 且无 Retry-After：按 5×attempt 等待，末次尝试后不再等待并返回 None。"""
    generator = AIBatchGenerator(api_key="test-key", max_retries=2)
    generator._min_interval = 0.0
    sleeps: list[float] = []
    monkeypatch.setattr(api_generator.time, "sleep", lambda s: sleeps.append(s))
    generator._client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(429)))

    data = generator._call_api([{"role": "user", "content": "hi"}])

    assert data is None
    assert sleeps == [5]  # 仅 attempt=1 等待（5×1）；attempt=2 为末次，直接判失败
