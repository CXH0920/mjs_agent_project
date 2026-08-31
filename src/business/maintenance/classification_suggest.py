# -*- coding: utf-8 -*-
"""武将机制分类的 LLM 建议（业务逻辑；UI 面板仅持线程壳调用本模块）。"""

from __future__ import annotations

import logging

from src.scraper.ai.json_extract import extract_json

logger = logging.getLogger(__name__)

_CLASSIFICATION_SYSTEM_PROMPT = (
    "你是名将杀（三国杀类）武将机制分类器。根据武将技能原文，从给定分类清单中"
    "选出该武将符合的机制分类（可多选）。只从清单中选，不确定的不选。"
    "输出 JSON：{\"categories\": [\"分类名\", ...]}，只输出 JSON，不要解释。"
)


def suggest_hero_categories(hero: str, skills_text: str, position: str,
                            categories, generator) -> list[str] | None:
    """调用 LLM 建议武将归入哪些已有分类。

    categories: list[ClassificationCategory]（用 name + core_features 构造 prompt）。
    返回 None=API 失败/解析失败；list=已过滤的分类名（只含清单内、去重保序，可能空）。
    """
    if not categories or not skills_text:
        return None
    cat_lines = "\n".join(f"- {c.name}：{c.core_features}" for c in categories)
    messages = [
        {"role": "system", "content": _CLASSIFICATION_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"武将：{hero}（定位：{position or '未知'}）\n"
            f"技能：\n{skills_text}\n"
            f"可选分类清单：\n{cat_lines}"
        )},
    ]
    try:
        response = generator.complete(messages, temperature=0.2)
    except Exception as error:
        logger.warning("武将分类建议请求异常 %s: %s", hero, error)
        return None
    if not response:
        return None
    content = response.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        data = extract_json(content)
    except (ValueError, TypeError):
        logger.warning("武将分类建议解析失败 %s", hero)
        return None
    if not isinstance(data, dict):
        return None
    raw = data.get("categories", [])
    if not isinstance(raw, list):
        return None
    valid_names = {c.name for c in categories}
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        name = str(item).strip()
        if name and name in valid_names and name not in seen:
            seen.add(name)
            result.append(name)
    return result
