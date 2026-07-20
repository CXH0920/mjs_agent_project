"""
名将杀 Agent - AI 批量生成编排函数

提供 ai_batch 调用的 4 个生成循环函数：
  - run_guide_generation()     全量/增量攻略生成
  - run_synergy_generation()   全量相性评分生成
  - run_synergy_pair_generation()  指定武将（2~8 个）两两配对
  - run_synergy_single_generation()  选定武将 x 全体

所有函数共享统一的 generator 接口（AIBatchGenerator / PlaywrightGenerator）
以及相同的 staging 安全提交机制。

合并自原有的 ai_guide.py / ai_synergy.py / ai_synergy_pair.py / ai_synergy_single.py
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.scraper.ai_utils import _save_json, GUIDE_BATCH_SAVE_INTERVAL, SYNERGY_BATCH_SAVE_INTERVAL

logger = logging.getLogger(__name__)


@dataclass
class GenerationResult:
    """一次 AI 生成任务的结构化执行结果。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    completed: int = 0
    skipped: int = 0
    failed_items: list[str] = field(default_factory=list)
    committed: bool = False
    staging_path: Path | None = None

    @property
    def succeeded(self) -> bool:
        """只有所有请求成功时，结果才允许提交到正式数据文件。"""
        return not self.failed_items

    def add_usage(self, usage: dict | None) -> None:
        if usage:
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)


def _staging_path(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.name}.staging")


def _save_staging(staging_path: Path, data: list[dict]) -> None:
    """持久化可恢复的暂存结果，绝不修改正式数据。"""
    _save_json(staging_path, data)


def _finalize_generation(
    result: GenerationResult,
    output_path: str | Path,
    data: list[dict],
    *,
    should_commit: bool,
) -> None:
    """失败保留正式文件，成功时将 staging 原子替换为正式文件。"""
    staging_path = _staging_path(output_path)
    if not result.succeeded:
        _save_staging(staging_path, data)
        result.staging_path = staging_path
        logger.warning("生成存在失败项，正式数据未变更，暂存结果保留在: %s", staging_path)
        return

    if not should_commit:
        staging_path.unlink(missing_ok=True)
        return

    _save_staging(staging_path, data)
    staging_path.replace(output_path)
    result.committed = True
    logger.info("生成结果已原子提交: %s (%d 条)", output_path, len(data))


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
        update_mode: True = 重新生成已有数据；False = 跳过已存在的

    Returns:
        GenerationResult: 本次生成的结构化结果。
    """
    result_summary = GenerationResult()

    sep_line = "=" * 55
    model_name = api_config["model"]
    print(f"\n{sep_line}")
    print(f"  生成攻略 -- {model_name} ({len(heroes)} 个武将)")
    print(f"{sep_line}")

    working_guides = dict(existing_guides)
    new_guides = []
    total_heroes = len(heroes)

    for i, hero in enumerate(heroes, 1):
        hero_id = hero.get("id", 0)
        if not update_mode and hero_id in existing_guides:
            logger.info("[%d/%d] 跳过 %s（已存在）", i, total_heroes, hero.get("name", ""))
            result_summary.skipped += 1
            continue

        hero_name = hero.get("name", "")
        print(f"  [{hero_name}] 开始...", flush=True)
        generated, usage = generator.generate_guide(hero)
        result_summary.add_usage(usage)

        if generated:
            working_guides[hero_id] = generated
            new_guides.append(generated)
            result_summary.completed += 1
            print(f"  [{i}/{total_heroes}] {hero_name} OK", flush=True)
        else:
            result_summary.failed_items.append(hero_name or str(hero_id))
            print(f"  [{i}/{total_heroes}] {hero_name} FAIL", flush=True)

        # 批量保存到暂存文件，正式文件只在全部成功后替换。
        if new_guides and len(new_guides) % GUIDE_BATCH_SAVE_INTERVAL == 0:
            _save_staging(_staging_path(guide_path), list(working_guides.values()))
            print("    [暂存] 已保存", flush=True)

    _finalize_generation(
        result_summary,
        guide_path,
        list(working_guides.values()),
        should_commit=bool(new_guides),
    )
    if result_summary.committed:
        existing_guides.clear()
        existing_guides.update(working_guides)

    guide_count = len(working_guides)
    print(f"\n  攻略完成: 新增 {len(new_guides)} 个，共 {guide_count} 个")
    if result_summary.failed_items:
        print(f"  失败: {len(result_summary.failed_items)} 个；正式数据未变更", flush=True)
    elif not new_guides and guide_count > 0:
        print("  已有全部攻略，无需生成")
    elif not new_guides:
        print("  未生成任何攻略，请检查 API Key 和网络连接")

    return result_summary


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
        GenerationResult: 本次生成的结构化结果。
    """
    result_summary = GenerationResult()

    total_pairs = len(heroes) * (len(heroes) - 1) // 2
    sep_line = "=" * 55
    model_name = api_config["model"]
    print(f"\n{sep_line}")
    print(f"  生成相性评分 -- {model_name} ({total_pairs:,} 对)")
    print(f"{sep_line}")

    # 全量生成在独立工作副本中进行，旧正式数据直到任务完整成功前都不变。
    working_synergies: dict[tuple[int, int], dict] = {}

    processed = 0

    for i in range(len(heroes)):
        for j in range(i + 1, len(heroes)):
            ha, hb = heroes[i], heroes[j]
            processed += 1
            key = tuple(sorted([ha["id"], hb["id"]]))

            print(f"  [{processed}/{total_pairs}] {ha['name']} <-> {hb['name']}...", flush=True)

            generated, usage = generator.generate_synergy(ha, hb)
            result_summary.add_usage(usage)

            if generated:
                result_summary.completed += 1
                score = generated.get("score", 0)
                if score >= score_threshold:
                    working_synergies[key] = generated
            else:
                result_summary.failed_items.append(f"{ha['name']}<->{hb['name']}")

            # 批量保存到暂存文件，保证中途失败不覆盖旧数据。
            if result_summary.completed and result_summary.completed % SYNERGY_BATCH_SAVE_INTERVAL == 0:
                _save_staging(_staging_path(synergy_path), list(working_synergies.values()))

    _finalize_generation(
        result_summary,
        synergy_path,
        list(working_synergies.values()),
        should_commit=True,
    )
    if result_summary.committed:
        existing_synergy_dict.clear()
        existing_synergy_dict.update(working_synergies)
        existing_synergy_keys.clear()
        existing_synergy_keys.update(working_synergies)

    print(f"\n  相性完成: 成功 {result_summary.completed} 对，共 {len(working_synergies)} 对")
    if result_summary.failed_items:
        print(f"  失败: {len(result_summary.failed_items)} 对；正式数据未变更", flush=True)
    if result_summary.completed == 0 and not result_summary.failed_items:
        print("  未生成任何相性评分，请检查 API Key 和网络连接")

    return result_summary


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
    全部配对成功后才用新结果替换已有记录。

    Args:
        pair_file: JSON 文件路径，包含 2~8 个武将
        heroes: 全武将列表
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_dict: 已有相性 {(a_id, b_id): dict}
        existing_synergy_keys: 已有相性 key 集合

    Returns:
        GenerationResult: 本次生成的结构化结果。
    """
    result_summary = GenerationResult()

    print(f"\n  相性配对生成 (指定武将)...", flush=True)
    with open(pair_file, "r", encoding="utf-8") as f:
        pair_heroes = json.load(f)

    count = len(pair_heroes)
    total_pairs = count * (count - 1) // 2

    if count < 2:
        logger.error("synergy-pair 至少需要 2 个武将，实际 %d 个", count)
        result_summary.failed_items.append("指定武将数量不足")
        return result_summary
    if count > 8:
        logger.error("synergy-pair 最多支持 8 个武将，实际 %d 个", count)
        result_summary.failed_items.append("指定武将数量超出上限")
        return result_summary

    print(f"  所选武将: {count} 个, 共 {total_pairs} 对", flush=True)
    working_synergies = dict(existing_synergy_dict)

    for idx, (ha, hb) in enumerate(itertools.combinations(pair_heroes, 2), start=1):
        pair_key = tuple(sorted([ha["id"], hb["id"]]))
        print(f"  [{idx}/{total_pairs}] {ha['name']} <-> {hb['name']}...", flush=True)

        generated, usage = generator.generate_synergy(ha, hb)
        result_summary.add_usage(usage)
        if generated:
            result_summary.completed += 1
            working_synergies[pair_key] = generated
            _save_staging(_staging_path(synergy_path), list(working_synergies.values()))
            print(f"  [{idx}/{total_pairs}] {ha['name']} <-> {hb['name']} OK - 评分: {generated.get('score', '?')}", flush=True)
        else:
            result_summary.failed_items.append(f"{ha['name']}<->{hb['name']}")
            print(f"  [{idx}/{total_pairs}] {ha['name']} <-> {hb['name']} FAIL", flush=True)

    _finalize_generation(
        result_summary,
        synergy_path,
        list(working_synergies.values()),
        should_commit=result_summary.completed > 0,
    )
    if result_summary.committed:
        existing_synergy_dict.clear()
        existing_synergy_dict.update(working_synergies)
        existing_synergy_keys.clear()
        existing_synergy_keys.update(working_synergies)
    return result_summary


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
    全部请求成功后才提交全部结果。

    Args:
        single_file: JSON 文件路径，包含 1 个武将
        heroes: 全武将列表（用于配对）
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_dict: 已有相性 {(a_id, b_id): dict}
        existing_synergy_keys: 已有相性 key 集合

    Returns:
        GenerationResult: 本次生成的结构化结果。
    """
    result_summary = GenerationResult()

    print(f"\n  相性配对生成 (选定武将 x 全体)...", flush=True)
    with open(single_file, "r", encoding="utf-8") as f:
        single_heroes = json.load(f)

    if len(single_heroes) != 1:
        logger.error("synergy-single 需要恰好 1 个武将，实际 %d 个", len(single_heroes))
        result_summary.failed_items.append("指定武将数量无效")
        return result_summary

    target = single_heroes[0]
    target_id = target["id"]

    pairs = [(target, h) for h in heroes if h["id"] != target_id]
    print(f"  {target['name']} <-> {len(pairs)} 个武将", flush=True)

    working_synergies = dict(existing_synergy_dict)

    for i, (ha, hb) in enumerate(pairs, 1):
        key = tuple(sorted([ha["id"], hb["id"]]))

        # 断点续传：已有则跳过
        if key in existing_synergy_keys:
            result_summary.skipped += 1
            continue

        print(f"  [{i}/{len(pairs)}] {hb['name']}...", flush=True)
        generated, usage = generator.generate_synergy(ha, hb)
        result_summary.add_usage(usage)
        if generated:
            working_synergies[key] = generated
            result_summary.completed += 1
            if result_summary.completed % SYNERGY_BATCH_SAVE_INTERVAL == 0:
                _save_staging(_staging_path(synergy_path), list(working_synergies.values()))
            print(f"  [{i}/{len(pairs)}] {hb['name']} OK - 评分: {generated.get('score', '?')}", flush=True)
        else:
            result_summary.failed_items.append(hb["name"])
            print(f"  [{i}/{len(pairs)}] {hb['name']} FAIL", flush=True)

    _finalize_generation(
        result_summary,
        synergy_path,
        list(working_synergies.values()),
        should_commit=result_summary.completed > 0,
    )
    if result_summary.committed:
        existing_synergy_dict.clear()
        existing_synergy_dict.update(working_synergies)
        existing_synergy_keys.clear()
        existing_synergy_keys.update(working_synergies)
    print(
        f"  相性完成: 新增 {result_summary.completed} 对，跳过 {result_summary.skipped} 对，"
        f"失败 {len(result_summary.failed_items)} 对, 共 {len(working_synergies)} 对",
        flush=True,
    )
    return result_summary
