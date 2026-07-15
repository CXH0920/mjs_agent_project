"""
名将杀 Agent - AI 批量生成编排函数

提供 ai_batch 调用的 4 个生成循环函数：
  - run_guide_generation()     全量/增量攻略生成
  - run_synergy_generation()   全量相性评分生成
  - run_synergy_pair_generation()  指定武将（2~8 个）两两配对
  - run_synergy_single_generation()  选定武将 x 全体

所有函数共享统一的 generator 接口（AIBatchGenerator / PlaywrightGenerator）
以及相同的 _save_json 保存机制。

合并自原有的 ai_guide.py / ai_synergy.py / ai_synergy_pair.py / ai_synergy_single.py
"""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path

from src.scraper.ai_utils import _save_json, GUIDE_BATCH_SAVE_INTERVAL, SYNERGY_BATCH_SAVE_INTERVAL

logger = logging.getLogger(__name__)


# ============================================================
# 攻略生成
# ============================================================

def run_guide_generation(
    heroes,
    generator,
    guide_path,
    existing_guides,
    api_config,
    update_mode=False,
):
    """执行攻略生成循环

    Args:
        heroes: 武将列表
        generator: AIBatchGenerator 实例
        guide_path: 攻略输出路径
        existing_guides: 已有攻略 {hero_id: guide_dict}
        api_config: API 配置（用于显示模型名）
        update_mode: True = 重新生成所有（删除旧数据再追加）；False = 跳过已存在的（断点续传）

    Returns:
        (prompt_tokens, completion_tokens): 本次生成的 token 统计
    """
    total_prompt_tokens = 0
    total_completion_tokens = 0

    def _accumulate_usage(usage):
        nonlocal total_prompt_tokens, total_completion_tokens
        if usage:
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

    sep_line = "=" * 55
    model_name = api_config["model"]
    print(f"\n{sep_line}")
    print(f"  生成攻略 -- {model_name} ({len(heroes)} 个武将)")
    print(f"{sep_line}")

    new_guides = []
    total_heroes = len(heroes)

    for i, hero in enumerate(heroes, 1):
        hero_id = hero.get("id", 0)
        if not update_mode and hero_id in existing_guides:
            logger.info("[%d/%d] 跳过 %s（已存在）", i, total_heroes, hero.get("name", ""))
            continue

        # 更新模式：先删除旧数据，避免最终合并时重复
        if update_mode and hero_id in existing_guides:
            del existing_guides[hero_id]

        hero_name = hero.get("name", "")
        print(f"  [{hero_name}] 开始...", flush=True)
        result, usage = generator.generate_guide(hero)
        _accumulate_usage(usage)

        if result:
            new_guides.append(result)
            print(f"  [{i}/{total_heroes}] {hero_name} OK", flush=True)
        else:
            print(f"  [{i}/{total_heroes}] {hero_name} FAIL", flush=True)
            print(f"RESULT: FAIL={hero_name}", flush=True)

        # 批量保存
        if new_guides and len(new_guides) % GUIDE_BATCH_SAVE_INTERVAL == 0:
            all_guides = list(existing_guides.values()) + new_guides
            _save_json(guide_path, all_guides)
            print(f"    [保存] 已保存", flush=True)

    # 最终保存
    if new_guides:
        all_guides = list(existing_guides.values()) + new_guides
        _save_json(guide_path, all_guides)
        logger.info("攻略已保存: %s (%d 条)", guide_path, len(all_guides))

    guide_count = len(existing_guides) + len(new_guides)
    print(f"\n  攻略完成: 新增 {len(new_guides)} 个，共 {guide_count} 个")
    if not new_guides and guide_count > 0:
        print("  已有全部攻略，无需生成")
    elif not new_guides:
        print("  未生成任何攻略，请检查 API Key 和网络连接")

    return total_prompt_tokens, total_completion_tokens


# ============================================================
# 全量相性生成
# ============================================================

def run_synergy_generation(
    heroes,
    generator,
    synergy_path,
    existing_synergy_dict,
    existing_synergy_keys,
    score_threshold,
    api_config,
):
    """执行相性评分生成循环（始终重新生成所有对）

    Args:
        heroes: 武将列表
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_dict: 已有相性 {(a_id, b_id): dict}
        existing_synergy_keys: 已有相性 key 集合
        score_threshold: 评分过滤下限
        api_config: API 配置（用于显示模型名）

    Returns:
        (prompt_tokens, completion_tokens): 本次生成的 token 统计
    """
    total_prompt_tokens = 0
    total_completion_tokens = 0

    def _accumulate_usage(usage):
        nonlocal total_prompt_tokens, total_completion_tokens
        if usage:
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

    total_pairs = len(heroes) * (len(heroes) - 1) // 2
    sep_line = "=" * 55
    model_name = api_config["model"]
    print(f"\n{sep_line}")
    print(f"  生成相性评分 -- {model_name} ({total_pairs:,} 对)")
    print(f"{sep_line}")

    # 清空旧数据，重新生成所有（不使用断点续传）
    existing_synergy_dict.clear()
    existing_synergy_keys.clear()

    new_count = 0
    processed = 0
    failed = 0

    for i in range(len(heroes)):
        for j in range(i + 1, len(heroes)):
            ha, hb = heroes[i], heroes[j]
            processed += 1
            key = tuple(sorted([ha["id"], hb["id"]]))

            print(f"  [{processed}/{total_pairs}] {ha['name']} <-> {hb['name']}...", flush=True)

            result, usage = generator.generate_synergy(ha, hb)
            _accumulate_usage(usage)

            if result:
                score = result.get("score", 0)
                if score >= score_threshold:
                    existing_synergy_dict[key] = result
                    existing_synergy_keys.add(key)
                    new_count += 1
            else:
                failed += 1

            # 批量保存
            if new_count > 0 and new_count % SYNERGY_BATCH_SAVE_INTERVAL == 0:
                _save_json(synergy_path, list(existing_synergy_dict.values()))

    # 最终保存
    if new_count > 0:
        _save_json(synergy_path, list(existing_synergy_dict.values()))
        logger.info("相性已保存: %s (%d 条)", synergy_path, len(existing_synergy_dict))

    print(f"\n  相性完成: 新增 {new_count} 对，共 {len(existing_synergy_dict)} 对")
    if failed > 0:
        print(f"  失败: {failed} 对")
    if new_count == 0:
        print("  未生成任何相性评分，请检查 API Key 和网络连接")

    return total_prompt_tokens, total_completion_tokens


# ============================================================
# 指定武将配对（2~8 个）
# ============================================================

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
            print(f"RESULT: FAIL={ha['name']}<->{hb['name']}", flush=True)

    return total_prompt_tokens, total_completion_tokens


# ============================================================
# 选定武将 x 全体
# ============================================================

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
            print(f"RESULT: FAIL={hb['name']}", flush=True)

    _save_json(synergy_path, list(existing_synergy_dict.values()))
    print(f"  相性完成: 新增 {new_count} 对，跳过 {skipped} 对，失败 {failed} 对, 共 {len(existing_synergy_dict)} 对", flush=True)
    return total_prompt_tokens, total_completion_tokens
