"""
名将杀 Agent - JSON 提取工具

从 AI 回复文本中宽容提取 JSON，支持 4 种回退策略。
拆分自 ai_utils.py。
"""

from __future__ import annotations

import json
import re


def _repair_strings(s: str) -> str:
    """修复 JSON 字符串值内的字面换行和未转义引号"""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and in_string:
            result.append(c)
            if i + 1 < len(s):
                result.append(s[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c == '"':
            in_string = not in_string
            result.append(c)
            i += 1
            continue
        if in_string and c in '\r\n':
            result.append('\\n')
            i += 1
            continue
        result.append(c)
        i += 1
    return ''.join(result)


def _raw_parse(s: str) -> dict | None:
    """用 raw_decode 解析 JSON 字符串，容忍尾部多余字符"""
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _try_extract(candidates: list[str]) -> dict | None:
    """遍历候选字符串，依次尝试直接解析和修复后解析"""
    for c in candidates:
        result = _raw_parse(c)
        if result:
            return result
        repaired = _repair_strings(c)
        if repaired != c:
            result = _raw_parse(repaired)
            if result:
                return result
    return None


def extract_json(text: str) -> dict:
    """从 AI 回复文本中提取 JSON（4 种回退策略）

    1. 直接全文解析（raw_decode 容忍尾部多余字符）
    2. 从 ```json 或 ``` 代码块提取
    3. 通过 --- 分隔线提取最后一段
    4. 找到第一个 { 到最后一个 }

    Raises:
        ValueError: 无法从文本中提取有效 JSON
    """
    text = text.strip()

    # 1. 直接全文
    result = _try_extract([text])
    if result:
        return result

    # 2. 从 ```json 或 ``` 代码块提取
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        result = _try_extract([m.group(1).strip()])
        if result:
            return result

    # 3. 通过 --- 分隔线提取最后一段（切片长度与实际匹配的分隔符等长，避免吞掉首字符）
    last_sep = text.rfind("\n---\n")
    sep_len = 5
    if last_sep < 0:
        last_sep = text.rfind("\n---")
        sep_len = 4
    if last_sep >= 0:
        result = _try_extract([text[last_sep + sep_len:].strip()])
        if result:
            return result

    # 4. 找到第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        result = _try_extract([text[start:end + 1]])
        if result:
            return result

    raise ValueError(f"无法从响应中提取 JSON（响应长度: {len(text)}）")
