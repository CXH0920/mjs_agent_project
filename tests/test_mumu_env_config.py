"""get_mumu_config 对 MUMU_SCREENSHOT_MODE 的透传测试。

该键此前存在文档与映射但 get_mumu_config 返回字典漏掉它，
capture_service 的 config.get 永远拿到 "auto"（死开关）。
"""

from __future__ import annotations

from src.config import env


def test_screenshot_mode_passthrough(monkeypatch) -> None:
    monkeypatch.setattr(
        env, "load_env_config", lambda: {"mumu_screenshot_mode": "raw"}
    )
    assert env.get_mumu_config()["mumu_screenshot_mode"] == "raw"


def test_screenshot_mode_defaults_to_auto(monkeypatch) -> None:
    monkeypatch.setattr(env, "load_env_config", lambda: {})
    assert env.get_mumu_config()["mumu_screenshot_mode"] == "auto"
