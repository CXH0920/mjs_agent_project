# -*- coding: utf-8 -*-
"""browser_session 同名测试：聊天配置合并与登录超时单源（第 6 批审查 P3 补测）
与无网络的会话发送/等待/页面失效行为（原 test_deepseek_browser_session.py 并入
——同模块单测试文件，锚点约定优先于限定前缀登记，18 批 D 级注记已同步）。"""

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


class _FakeKeyboard:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def press(self, key: str) -> None:
        self.keys.append(key)


class _FakeMessage:
    def text_content(self) -> str:
        return "最终回复"


class _FakeMessages:
    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def count(self) -> int:
        self._page.count_calls += 1
        return 1 if self._page.count_calls == 1 else 2

    def nth(self, _index: int) -> _FakeMessage:
        return _FakeMessage()


class _FakePage:
    def __init__(self) -> None:
        self.keyboard = _FakeKeyboard()
        self.filled: list[tuple[str, str]] = []
        self.waits: list[int] = []
        self.count_calls = 0

    def fill(self, selector: str, prompt: str) -> None:
        self.filled.append((selector, prompt))

    def locator(self, _selector: str) -> _FakeMessages:
        return _FakeMessages(self)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.waits.append(milliseconds)


def test_session_sends_prompt_and_extracts_stable_reply_without_browser() -> None:
    session = DeepSeekBrowserSession(chat_config={
        "input_selector": "#chat-input",
        "assistant_selector": ".assistant",
        "response_timeout": 1000,
    })
    page = _FakePage()
    session._page = page
    session._started = True

    reply = session.send_and_wait("测试 prompt")

    assert reply == "最终回复"
    assert page.filled == [("#chat-input", "测试 prompt")]
    assert page.keyboard.keys == ["Enter"]
    assert page.waits[-1] == 1000


# ---------------------------------------------------------------
# VUL-B2-001：超时后页面必须标记失效，避免后续请求级联超时
# ---------------------------------------------------------------


class _NeverAppearPage(_FakePage):
    """消息数始终不增长：模拟 AI 无响应 / 页面停在验证码。"""

    def __init__(self) -> None:
        super().__init__()
        self.count_calls = 0

    def locator(self, _selector: str) -> "_NeverAppearMessages":
        return _NeverAppearMessages(self)

    def evaluate(self, _script: str) -> str:
        return ""


class _NeverAppearMessages:
    def __init__(self, page: _NeverAppearPage) -> None:
        self._page = page

    def count(self) -> int:
        self._page.count_calls += 1
        return 1  # 恒为 1，永远不大于 before_count

    def nth(self, _index: int) -> _FakeMessage:
        return _FakeMessage()


def test_timeout_marks_page_for_rebuild() -> None:
    """首段等不到新消息时，必须置 _started=False 让下次调用重建上下文。"""
    session = DeepSeekBrowserSession(chat_config={
        "input_selector": "#chat-input",
        "assistant_selector": ".assistant",
        "response_timeout": 300,
    })
    session._page = _NeverAppearPage()
    session._started = True

    reply = session.send_and_wait("测试 prompt")

    assert reply is None
    assert session._started is False


def test_fill_failure_marks_page_for_rebuild() -> None:
    """填入 prompt 失败（页面可能已跳转/失效）同样需要重建。"""

    class _FillFailsPage(_FakePage):
        def fill(self, _selector: str, _prompt: str) -> None:
            raise RuntimeError("fill failed")

    session = DeepSeekBrowserSession(chat_config={
        "input_selector": "#chat-input",
        "assistant_selector": ".assistant",
    })
    session._page = _FillFailsPage()
    session._started = True

    assert session.send_and_wait("测试 prompt") is None
    assert session._started is False


def test_successful_reply_keeps_page_reusable() -> None:
    """成功路径不得误置失效标志，否则每个武将都要重启一次浏览器。"""
    session = DeepSeekBrowserSession(chat_config={
        "input_selector": "#chat-input",
        "assistant_selector": ".assistant",
        "response_timeout": 1000,
    })
    session._page = _FakePage()
    session._started = True

    assert session.send_and_wait("测试 prompt") == "最终回复"
    assert session._started is True
