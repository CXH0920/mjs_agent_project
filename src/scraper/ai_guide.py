"""
名将杀 Agent - 攻略生成流程

从 ai_batch 中抽取的攻略生成循环逻辑。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def run_guide_generation(
    heroes,
    generator,
    guide_path,
    existing_guides,
    api_config,
):
    """执行攻略生成循环

    Args:
        heroes: 武将列表
        generator: AIBatchGenerator 实例
        guide_path: 攻略输出路径
        existing_guides: 已有攻略 {hero_id: guide_dict}
        api_config: API 配置（用于显示模型名）

    Returns:
        (prompt_tokens, completion_tokens): 本次生成的 token 统计
    """
    # 延迟导入，避免循环依赖
    from src.scraper.ai_utils import _save_json, GUIDE_BATCH_SAVE_INTERVAL

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
        if hero_id in existing_guides:
            logger.info("[%d/%d] 跳过 %s（已存在）", i, total_heroes, hero.get("name", ""))
            continue

        hero_name = hero.get("name", "")
        print(f"  [{hero_name}] 开始...", flush=True)
        result, usage = generator.generate_guide(hero)
        _accumulate_usage(usage)

        if result:
            new_guides.append(result)
            print(f"  [{i}/{total_heroes}] {hero_name} OK", flush=True)
        else:
            print(f"  [{i}/{total_heroes}] {hero_name} FAIL", flush=True)

        # 批量保存
        if new_guides and len(new_guides) % GUIDE_BATCH_SAVE_INTERVAL == 0:
            all_guides = list(existing_guides.values()) + new_guides
            _save_json(guide_path, all_guides)
            print(f"    [保存] 已保存 {len(all_guides)} 条", flush=True)

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
