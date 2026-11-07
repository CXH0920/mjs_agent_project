# -*- coding: utf-8 -*-
"""browser_session 同名测试：聊天配置合并与登录等待的单源超时（第 6 批审查 P3 补测）。"""

from __future__ import annotations

from src.scraper.ai.browser_session import (
    DEFAULT_CHAT_CONFIG,
    DeepSeekBrowserSession,
)


def test_chat_config_merge_always_carries_default_login_timeout() -> None:
    """空/自定义 chat_config 均经 DEFAULT 合并：login_timeout 恒有单源默认值。"""
    empty = DeepSeekBrowserSession(chat_config=None)
    custom = DeepSeekBrowserSession(chat_config={"url": "https://example.com/"})

    assert empty._chat_cfg["login_timeout"] == DEFAULT_CHAT_CONFIG["login_timeout"]
    assert custom._chat_cfg["login_timeout"] == DEFAULT_CHAT_CONFIG["login_timeout"]
    assert custom._chat_cfg["url"] == "https://example.com/"  # 自定义键仍然生效


def test_wait_for_login_uses_single_source_default_timeout() -> None:
    """登录等待超时取 DEFAULT_CHAT_CONFIG 单源（120000），不再有 15000 分歧回退。"""
    received: dict[str, object] = {}

    class _FakePage:
        def wait_for_selector(self, selector, timeout=None):
            received["selector"] = selector
            received["timeout"] = timeout
            return True

    session = object.__new__(DeepSeekBrowserSession)
    session._chat_cfg = dict(DEFAULT_CHAT_CONFIG)
    session._page = _FakePage()

    assert session._wait_for_login() is True
    assert received["timeout"] == DEFAULT_CHAT_CONFIG["login_timeout"] == 120000
