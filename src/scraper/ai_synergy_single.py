"""
名将杀 Agent - 相性单武将配对生成流程

从 ai_batch 中抽取的选定武将（单武将 x 全体）相性评分生成逻辑。
支持断点续传：已有的相性对跳过不重复生成。
"""

from __future__ import annotations

import json
import logging

from src.scraper.ai_utils import _save_json

logger = logging.getLogger(__name__)


def run_synergy_single_generation(
    single_file: str,
    heroes: list,
    generator,
    synergy_path,
    existing_synergy_dict: dict,
    existing_synergy_keys: set,
):
    """执行相性单武将配对生成（选定武将 vs 所有其他武将）

    支持断点续传：只生成尚不存在的相性对，已有的直接跳过。
    生成完成后保存全部结果。

    Args:
        single_file: JSON 文件路径，包含 1 个武将
        heroes: 全武将列表（用于配对）
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_dict: 已有相性 {(a_id, b_id): dict}
        existing_synergy_keys: 已有相性 key 集合

    Returns:
        (prompt_tokens, completion_tokens)
    """
    total_prompt_tokens = 0
    total_completion_tokens = 0

    print(f"\n  相性配对生成 (选定武将 x 全体)...", flush=True)
    with open(single_file, "r", encoding="utf-8") as f:
        single_heroes = json.load(f)

    if len(single_heroes) != 1:
        logger.error("synergy-single 需要恰好 1 个武将，实际 %d 个", len(single_heroes))
        return 0, 0

    target = single_heroes[0]
    target_id = target["id"]

    pairs = [(target, h) for h in heroes if h["id"] != target_id]
    print(f"  {target['name']} <-> {len(pairs)} 个武将", flush=True)

    new_count = 0
    skipped = 0
    failed = 0

    for i, (ha, hb) in enumerate(pairs, 1):
        key = tuple(sorted([ha["id"], hb["id"]]))

        # 断点续传：已有则跳过
        if key in existing_synergy_keys:
            skipped += 1
            continue

        print(f"  [{i}/{len(pairs)}] {hb['name']}...", flush=True)
        result, usage = generator.generate_synergy(ha, hb)
        if usage:
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)
        if result:
            existing_synergy_dict[key] = result
            existing_synergy_keys.add(key)
            new_count += 1
            print(f"  [{i}/{len(pairs)}] {hb['name']} OK - 评分: {result.get('score', '?')}", flush=True)
        else:
            failed += 1
            print(f"  [{i}/{len(pairs)}] {hb['name']} FAIL", flush=True)

    _save_json(synergy_path, list(existing_synergy_dict.values()))
    print(f"  相性完成: 新增 {new_count} 对，跳过 {skipped} 对，失败 {failed} 对, 共 {len(existing_synergy_dict)} 对", flush=True)
    return total_prompt_tokens, total_completion_tokens
