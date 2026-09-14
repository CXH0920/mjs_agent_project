"""名将杀官网 Nuxt 页面与 JS chunk 的解析适配器。"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

BASE_URL = "https://mjs.ztgame.com"
CHUNK_URL_PATTERN = re.compile(r"/_nuxt/mjbk\.[a-f0-9]+\.js")
# 卡牌数据 chunk：与武将同机制但页面不同（/shoupaiku/），spk 后要求紧跟点号，
# 天然排除同页引用的 spk-legacy.* 预取脚本
CARD_CHUNK_URL_PATTERN = re.compile(r"/_nuxt/spk\.[a-f0-9]+\.js")
# 官网改版诊断用：页面里引用的全部 _nuxt 脚本（不限文件名格式）
_NUXT_SCRIPT_PATTERN = re.compile(r"/_nuxt/[A-Za-z0-9._-]+\.js")
# 对象键位置：{ 或 , 之后的 标识符 + 可选空白 + 冒号
_KEY_POSITION_RE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")
# :undefined 值（冒号后允许空白，后随 , 或 } 即值结束）
_UNDEFINED_VALUE_RE = re.compile(r"\s*undefined\s*(?=[,}\]])")
# 尾逗号：后随 } 或 ]
_TRAILING_COMMA_RE = re.compile(r"\s*[}\]]")


def find_chunk_url(html: str) -> str:
    """从官网首页找到武将数据 JS chunk 的完整 URL。"""
    match = CHUNK_URL_PATTERN.search(html)
    if match:
        return BASE_URL + match.group()
    # 官网重新构建会更换 chunk 哈希文件名：把现场信息带进异常与日志，改版当天即可定位
    hints = sorted({m.group() for m in _NUXT_SCRIPT_PATTERN.finditer(html)})
    preview = html[:300].replace("\n", " ")
    logger.error("官网数据 chunk 未找到，可能已改版。发现的 _nuxt 脚本: %s；页面开头: %s",
                 hints or "无", preview)
    raise RuntimeError(
        "官网页面中未找到武将数据 JS chunk（可能已改版）。"
        f"发现的 _nuxt 脚本: {hints or '无'}；页面开头: {preview}"
    )


def find_card_chunk_url(html: str) -> str:
    """从手牌库页面找到卡牌数据 JS chunk 的完整 URL。"""
    match = CARD_CHUNK_URL_PATTERN.search(html)
    if match:
        return BASE_URL + match.group()
    # 与 find_chunk_url 一致：改版当天即可凭现场信息定位
    hints = sorted({m.group() for m in _NUXT_SCRIPT_PATTERN.finditer(html)})
    preview = html[:300].replace("\n", " ")
    logger.error("官网卡牌数据 chunk 未找到，可能已改版。发现的 _nuxt 脚本: %s；页面开头: %s",
                 hints or "无", preview)
    raise RuntimeError(
        "官网页面中未找到卡牌数据 JS chunk（可能已改版）。"
        f"发现的 _nuxt 脚本: {hints or '无'}；页面开头: {preview}"
    )


def extract_js_array(js_text: str, start_marker: str = "const e=[") -> str:
    """提取起始标记后的平衡数组，并忽略字符串内的方括号。"""
    start = js_text.find(start_marker)
    if start < 0:
        # 官网改版会更换变量名/打包形态：与 find_chunk_url 一致，把现场信息
        # 带进日志与异常，改版当天即可定位
        prefix = js_text[:300].replace("\n", " ")
        logger.error("官网 JS 中未找到 %r 起始标记，可能已改版。JS 开头: %s", start_marker, prefix)
        raise RuntimeError(
            f"官网 JS 中未找到 {start_marker!r} 起始标记（可能已改版）。JS 开头: {prefix}"
        )
    return _extract_balanced_array(js_text, start + len(start_marker) - 1)


def _extract_balanced_array(js_text: str, start: int) -> str:
    """从 start（``[`` 的位置）提取括号平衡的数组文本；字符串字面量内的括号不计数。"""
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
    """将官网使用的 JavaScript 对象数组转为 Python 数据。

    JS→JSON 的三类差异（标识符键补引号、:undefined 改 null、尾逗号删除）由
    字符级状态机完成，且只在字符串字面量之外执行——旧正则版会把技能描述里
    的 "效果{x:1}"、",变化:无" 等内容误改写，导致整批解析失败。
    """
    return json.loads(_to_json_text(text))


def _to_json_text(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch == '"' or ch == "'":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == ":":
            out.append(ch)
            i += 1
            m = _UNDEFINED_VALUE_RE.match(text, i)
            if m:
                out.append("null")
                i = m.end()
            continue
        if ch == ",":
            if _TRAILING_COMMA_RE.match(text, i + 1):
                i += 1  # 尾逗号：} / ] 前直接丢弃
                continue
            out.append(ch)
            i += 1
        else:
            out.append(ch)
            i += 1
        # { 与 , 之后处于对象键位置：标识符键补双引号（含键前后的空白与冒号）
        if ch in (",", "{"):
            m = _KEY_POSITION_RE.match(text, i)
            if m:
                out.append(text[i:m.start(1)])
                out.append('"')
                out.append(m.group(1))
                out.append('"')
                out.append(text[m.end(1):m.end()])
                i = m.end()
                m2 = _UNDEFINED_VALUE_RE.match(text, i)
                if m2:
                    out.append("null")
                    i = m2.end()
    return "".join(out)


def parse_heroes_chunk(js_text: str) -> list[dict]:
    """解析官网武将 JS chunk，返回原始武将记录。"""
    return js_to_json(extract_js_array(js_text))


# 卡牌数组入口：手牌库 modern chunk 无 const e= 标记，数据数组以 ``=[{`` 形式内嵌
CARD_ARRAY_START_RE = re.compile(r"=\s*\[\{")


def parse_cards_chunk(js_text: str) -> list[dict]:
    """解析官网卡牌 JS chunk，返回原始卡牌记录。

    逐个 ``=[{`` 候选位置提取平衡数组，以「对象列表且含 card_type 键」自证，
    避免误抓页面其他数据数组；全部候选失败时按官网改版处理。
    """
    for match in CARD_ARRAY_START_RE.finditer(js_text):
        try:
            text = _extract_balanced_array(js_text, match.end() - 2)
        except RuntimeError:
            continue
        try:
            records = js_to_json(text)
        except ValueError:
            continue
        if (
            records
            and all(isinstance(record, dict) for record in records)
            and any("card_type" in record for record in records)
        ):
            return records
    raise RuntimeError("官网 JS 中未找到卡牌数据数组（可能已改版）。")
