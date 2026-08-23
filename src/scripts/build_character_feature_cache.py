"""生成 OCR 名称纠错所需的静态汉字特征缓存。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from src.config.env import PROJECT_ROOT

from src.ocr.character_feature_repository import CharacterFeatureRepository


HEROES_PATH = PROJECT_ROOT / "data" / "heroes.json"
COMMON_OCR_CONFUSION_CHARACTERS = "不剪赢缘还翡或媛邰答谢半昧翊隐助珍怀候部会正工頂"


def required_characters(heroes_path: Path = HEROES_PATH) -> set[str]:
    """返回全量武将名和常见误识字涉及的字符。"""
    with heroes_path.open("r", encoding="utf-8") as file:
        heroes = json.load(file)
    return {
        char
        for hero in heroes
        for char in str(hero.get("name", ""))
    } | set(COMMON_OCR_CONFUSION_CHARACTERS)


def main() -> None:
    repository = CharacterFeatureRepository(user_cache_path=None)
    repository.warmup()
    characters = required_characters()
    missing = repository.warmup_characters(characters)
    entries = repository.load()
    incomplete = [
        char for char in entries
        if (
            not entries[char].get("cangjie") and not entries[char].get("four_corner")
        ) or not entries[char].get("wubi")
    ]
    for char in incomplete:
        entries[char] = repository._build_feature(char)
    repository.save()
    print(
        f"字符特征缓存已更新：新增 {missing} 字，重建 {len(incomplete)} 字，"
        f"合计 {len(repository.load())} 字"
    )


if __name__ == "__main__":
    main()
