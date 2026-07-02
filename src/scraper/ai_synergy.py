"""
名将杀 Agent - 相性评分生成流程

从 ai_batch 中抽取的相性评分生成循环逻辑。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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
    # 延迟导入，避免循环依赖
    from src.scraper.ai_utils import _save_json, SYNERGY_BATCH_SAVE_INTERVAL

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
