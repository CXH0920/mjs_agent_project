"""
名将杀 Agent - 相性配对生成流程

从 ai_batch 中抽取的指定武将（两武将配对）相性评分生成逻辑。
"""

from __future__ import annotations

import json
import logging

from src.scraper.ai_utils import _save_json

logger = logging.getLogger(__name__)


def run_synergy_pair_generation(
    pair_file: str,
    heroes: list,
    generator,
    synergy_path,
    existing_synergy_list: list,
    existing_synergy_keys: set,
):
    """执行相性配对生成（指定 2 个武将）

    Args:
        pair_file: JSON 文件路径，包含 2 个武将
        heroes: 全武将列表
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_list: 已有相性列表
        existing_synergy_keys: 已有相性 key 集合

    Returns:
        (prompt_tokens, completion_tokens)
    """
    total_prompt_tokens = 0
    total_completion_tokens = 0

    print(f"\n  相性配对生成 (指定武将)...", flush=True)
    with open(pair_file, "r", encoding="utf-8") as f:
        pair_heroes = json.load(f)

    if len(pair_heroes) != 2:
        logger.error("synergy-pair 需要恰好 2 个武将，实际 %d 个", len(pair_heroes))
        return 0, 0

    ha, hb = pair_heroes[0], pair_heroes[1]
    print(f"  {ha['name']} <-> {hb['name']}...", flush=True)
    result, usage = generator.generate_synergy(ha, hb)
    if usage:
        total_prompt_tokens += usage.get("prompt_tokens", 0)
        total_completion_tokens += usage.get("completion_tokens", 0)
    if result:
        key = tuple(sorted([result["hero_a_id"], result["hero_b_id"]]))
        if key not in existing_synergy_keys:
            existing_synergy_list.append(result)
            existing_synergy_keys.add(key)
        _save_json(synergy_path, existing_synergy_list)
        print(f"    OK - 评分: {result.get('score', '?')}", flush=True)
    else:
        print(f"    FAIL", flush=True)

    return total_prompt_tokens, total_completion_tokens
