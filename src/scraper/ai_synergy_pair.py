"""
名将杀 Agent - 相性配对生成流程

从 ai_batch 中抽取的指定武将（多武将两两配对）相性评分生成逻辑。
支持选择 2~8 个武将，自动排列组合所有配对。
"""

from __future__ import annotations

import itertools
import json
import logging

from src.scraper.ai_utils import _save_json

logger = logging.getLogger(__name__)


def run_synergy_pair_generation(
    pair_file: str,
    heroes: list,
    generator,
    synergy_path,
    existing_synergy_dict: dict,
    existing_synergy_keys: set,
):
    """执行相性配对生成（指定 2~8 个武将，两两配对）

    对所选武将做排列组合（C(N,2)），逐个调用 AI 生成相性评分。
    先删除已有旧数据再写入新结果。

    Args:
        pair_file: JSON 文件路径，包含 2~8 个武将
        heroes: 全武将列表
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_dict: 已有相性 {(a_id, b_id): dict}
        existing_synergy_keys: 已有相性 key 集合

    Returns:
        (prompt_tokens, completion_tokens)
    """
    total_prompt_tokens = 0
    total_completion_tokens = 0

    print(f"\n  相性配对生成 (指定武将)...", flush=True)
    with open(pair_file, "r", encoding="utf-8") as f:
        pair_heroes = json.load(f)

    count = len(pair_heroes)
    total_pairs = count * (count - 1) // 2

    if count < 2:
        logger.error("synergy-pair 至少需要 2 个武将，实际 %d 个", count)
        return 0, 0
    if count > 8:
        logger.error("synergy-pair 最多支持 8 个武将，实际 %d 个", count)
        return 0, 0

    print(f"  所选武将: {count} 个, 共 {total_pairs} 对", flush=True)

    for idx, (ha, hb) in enumerate(itertools.combinations(pair_heroes, 2), start=1):
        pair_key = tuple(sorted([ha["id"], hb["id"]]))
        print(f"  [{idx}/{total_pairs}] {ha['name']} <-> {hb['name']}...", flush=True)

        result, usage = generator.generate_synergy(ha, hb)
        if usage:
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)
        if result:
            if pair_key in existing_synergy_dict:
                del existing_synergy_dict[pair_key]
                existing_synergy_keys.discard(pair_key)
            existing_synergy_dict[pair_key] = result
            existing_synergy_keys.add(pair_key)
            _save_json(synergy_path, list(existing_synergy_dict.values()))
            print(f"  [{idx}/{total_pairs}] {ha['name']} <-> {hb['name']} OK - 评分: {result.get('score', '?')}", flush=True)
        else:
            print(f"  [{idx}/{total_pairs}] {ha['name']} <-> {hb['name']} FAIL", flush=True)

    return total_prompt_tokens, total_completion_tokens
