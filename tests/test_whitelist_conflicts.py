"""白名单×武将库冲突防护：合成词表正反例 + 真实词表全量重检。"""

from __future__ import annotations

import json

import pytest
from src.config.env import PROJECT_ROOT
from src.ocr.character_similarity import (
    CharacterSimilarityService,
    whitelist_conflicts_with_roster,
)

# ── 合成词表：谓词正反例 ───────────────────────────────────────────────


def test_conflict_when_wrong_char_appears_in_roster() -> None:
    # 错字出现在词表名中：替换会破坏该名的合法读数
    conflicts = whitelist_conflicts_with_roster({"王": "皇"}, ["王翦", "李广"])
    assert any("错字" in line for line in conflicts)


def test_conflict_when_right_char_hits_no_name() -> None:
    # 正字未命中任何名：该对当前词表下不可能生效，疑似配置错误
    conflicts = whitelist_conflicts_with_roster({"甲": "乙"}, ["王翦"])
    assert any("正字" in line for line in conflicts)


def test_clean_pair_passes() -> None:
    assert whitelist_conflicts_with_roster({"瓚": "瓒"}, ["公孙瓒", "王翦"]) == []


# ── 真实词表：基线 + 用户层全量重检（红线 6）───────────────────────────


def test_effective_whitelist_clean_on_game_roster() -> None:
    heroes_path = PROJECT_ROOT / "data" / "heroes.json"
    if not heroes_path.exists():
        pytest.skip("data/heroes.json 未入库（经私有数据仓同步），跳过真实词表重检")
    document = json.loads(heroes_path.read_text(encoding="utf-8"))
    if isinstance(document, dict):
        document = document.get("heroes", document)
    names = [h["name"] if isinstance(h, dict) else h for h in document]
    service = CharacterSimilarityService()
    conflicts = whitelist_conflicts_with_roster(service.effective_whitelist, names)
    assert conflicts == []
