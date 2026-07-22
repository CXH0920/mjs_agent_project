"""名将杀官网 Nuxt 页面与 JS chunk 的解析适配器。"""

from __future__ import annotations

import json
import re

BASE_URL = "https://mjs.ztgame.com"
CHUNK_URL_PATTERN = re.compile(r"/_nuxt/mjbk\.[a-f0-9]+\.js")


def find_chunk_url(html: str) -> str:
    """从官网首页找到武将数据 JS chunk 的完整 URL。"""
    match = CHUNK_URL_PATTERN.search(html)
    if not match:
        raise RuntimeError("JS chunk 未找到")
    return BASE_URL + match.group()


def extract_js_array(js_text: str) -> str:
    """提取 ``const e=[...]`` 中的数组，并忽略字符串内的方括号。"""
    start_marker = "const e=["
    start = js_text.find(start_marker)
    if start < 0:
        raise RuntimeError("const e=[ 未找到")

    start += len(start_marker) - 1
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(start, len(js_text)):
        char = js_text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("\"", "'", "`"):
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return js_text[start : index + 1]
    raise RuntimeError("JS 数组未闭合")


def js_to_json(text: str) -> list[dict]:
    """将官网使用的 JavaScript 对象数组转为 Python 数据。"""
    text = re.sub(r'(?<=[{,])\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'"\1":', text)
    text = re.sub(r":\s*undefined(?=[,}\]])", ":null", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return json.loads(text)


def parse_heroes_chunk(js_text: str) -> list[dict]:
    """解析官网武将 JS chunk，返回原始武将记录。"""
    return js_to_json(extract_js_array(js_text))
