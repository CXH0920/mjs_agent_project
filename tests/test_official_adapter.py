"""官网 JS 解析适配器测试。"""

from __future__ import annotations

import logging

import pytest
from src.scraper.official_source.adapter import extract_js_array


def test_extract_js_array_returns_balanced_array_text() -> None:
    """数组内的引号字符串里的方括号不参与深度计数。"""
    js = 'var a=1;const e=[{"name":"曹操","tags":["魏"]}];var b=2'

    assert extract_js_array(js) == '[{"name":"曹操","tags":["魏"]}]'


def test_extract_js_array_failure_carries_js_prefix(caplog) -> None:
    """起始标记未找到时异常与日志都带现场前缀，官网改版当天即可定位（批次2）。"""
    js = "var x=1; function boot(){} // 改版后数组变量名不再是 e"

    with caplog.at_level(logging.ERROR, logger="src.scraper.official_source.adapter"):
        with pytest.raises(RuntimeError, match="JS 开头"):
            extract_js_array(js)

    assert "const e=[" in caplog.text
    assert "var x=1" in caplog.text
