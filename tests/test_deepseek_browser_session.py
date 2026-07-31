"""DeepSeek 浏览器会话的无网络测试。"""

from __future__ import annotations

from src.scraper.ai.browser_session import DeepSeekBrowserSession


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
