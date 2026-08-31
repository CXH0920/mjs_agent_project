# -*- coding: utf-8 -*-
"""武将概要视图（供 RAG 维护页/武将分类 LLM 建议使用）。

技能文本格式（`name：description　结算：settlement`）是 RAG 语料域知识，
归位业务层，UI 不再自行拼接。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_hero_briefs(
    root: Path, fallback_names: set[str] | None = None,
) -> tuple[set[str], dict[str, str], dict[str, str]]:
    """从 data/heroes.json 读取武将名、定位与技能文本；文件缺失时使用传入集合。

    返回 (names, positions, skills)；skills 值为按行拼接的技能文本。
    """
    heroes_path = root / "data" / "heroes.json"
    try:
        heroes = json.loads(heroes_path.read_text(encoding="utf-8"))
        names = {str(h.get("name", "")) for h in heroes if h.get("name")}
        positions = {str(h.get("name", "")): str(h.get("position", "") or "")
                     for h in heroes if h.get("name")}
        skills: dict[str, str] = {}
        for h in heroes:
            name = str(h.get("name", ""))
            if not name:
                continue
            parts = []
            for s in h.get("skills", []):
                line = f"{s.get('name', '')}：{s.get('description', '')}"
                if s.get("settlement"):
                    line += f"　结算：{s['settlement']}"
                parts.append(line)
            skills[name] = "\n".join(parts)
        return names, positions, skills
    except (OSError, json.JSONDecodeError, ValueError) as error:
        logger.warning("heroes.json 读取失败，使用回退武将集合: %s", error)
        return set(fallback_names or ()), {}, {}
