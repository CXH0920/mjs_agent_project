"""
名将杀 Agent - AI 批量生成编排函数

提供 ai_batch 调用的 4 个生成循环函数：
  - run_guide_generation()     全量/增量攻略生成
  - run_synergy_generation()   全量相性评分生成
  - run_synergy_pair_generation()  指定武将（2~8 个）两两配对
  - run_synergy_single_generation()  选定武将 x 全体

所有函数共享统一的 generator 接口（AIBatchGenerator / PlaywrightGenerator）
以及相同的分批原子提交机制。

合并自原有的 ai_guide.py / ai_synergy.py / ai_synergy_pair.py / ai_synergy_single.py
"""

from __future__ import annotations

import itertools
import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.scraper.ai.utils import _save_json, GUIDE_BATCH_SAVE_INTERVAL, SYNERGY_BATCH_SAVE_INTERVAL
from src.scraper.ai import rag_prompt

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

    @property
    def succeeded(self) -> bool:
        """所有请求均成功时为真；失败项不影响已提交的成功结果。"""
        return not self.failed_items

    def add_usage(self, usage: dict | None) -> None:
        if usage:
            self.prompt_tokens += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)


def _commit_generation_batch(
    result: GenerationResult,
    output_path: str | Path,
    data: list[dict],
) -> None:
    """将已校验成功的工作副本原子写入正式文件。"""
    _save_json(output_path, data)
    result.committed = True
    logger.info("生成结果已分批提交: %s (%d 条)", output_path, len(data))


def _with_synergy_updated_date(generated: dict) -> dict:
    """为校验成功的相性结果写入本次生成日期。"""
    return {**generated, "last_updated": date.today().isoformat()}


def _pair_label(ha: dict, hb: dict) -> str:
    """配对在协议行中的展示名。"""
    return f"{ha['name']} <-> {hb['name']}"


def _pair_fail_label(ha: dict, hb: dict) -> str:
    """配对失败时 failed_items 中的条目格式（无空格，与展示名不同）。"""
    return f"{ha['name']}<->{hb['name']}"


def _report_rag_degradation() -> None:
    """RAG 被选择但运行时不可用时，向 stdout 输出一次降级提示（进度窗口可见）。"""
    reason = rag_prompt.take_degraded_reason()
    if reason:
        print(f"  [RAG] 语料不可用，本次已降级为经典模式（{reason}）", flush=True)


def _run_synergy_pairs(
    pairs,
    generator,
    synergy_path,
    existing_synergy_dict,
    existing_synergy_keys,
    *,
    skip_existing,
    score_threshold,
    label_of,
    fail_label_of,
    missing_score_text,
    result_summary=None,
):
    """四个相性生成循环的共享核心：跳过→生成→记录→分批提交→回写 existing。

    协议行、failed_items 格式与提交时机由 tests/test_generation_loops.py
    特征化测试锁定，改动任何输出前先更新对应测试。

    Args:
        skip_existing: 已有相性是否跳过（全量模式为 False，永不跳过）
        score_threshold: 评分下限；None 不设限，设定时低于下限移除旧记录（仅全量模式）
        label_of: (ha, hb) → 协议行展示名（单武将模式用对方单名，其余 a <-> b）
        fail_label_of: (ha, hb) → failed_items 条目格式
        missing_score_text: OK 行评分缺失时的占位（全量 "0"，其余 "?"）
        result_summary: 预置汇总（实战配队清单把循环前解析出的无效配对先记入失败项）
    """
    result_summary = result_summary or GenerationResult()
    total = len(pairs)
    # 从旧数据开始工作，失败配对始终保留原有记录。
    working_synergies = dict(existing_synergy_dict)
    committed_pairs = 0

    for idx, (ha, hb) in enumerate(pairs, start=1):
        pair_key = tuple(sorted([ha["id"], hb["id"]]))

        if skip_existing and pair_key in existing_synergy_keys:
            result_summary.skipped += 1
            print(f"  [{idx}/{total}] {label_of(ha, hb)} SKIP（已有相性）", flush=True)
            continue

        print(f"  [{idx}/{total}] {label_of(ha, hb)} START", flush=True)
        generated, usage = generator.generate_synergy(ha, hb)
        result_summary.add_usage(usage)
        _report_rag_degradation()

        if generated:
            result_summary.completed += 1
            if score_threshold is None:
                working_synergies[pair_key] = _with_synergy_updated_date(generated)
            else:
                score = generated.get("score", 0)
                if score >= score_threshold:
                    working_synergies[pair_key] = _with_synergy_updated_date(generated)
                else:
                    # 本次结果校验成功但未达到用户设置的下限，移除旧记录。
                    working_synergies.pop(pair_key, None)
            print(
                f"  [{idx}/{total}] {label_of(ha, hb)} OK - 评分: {generated.get('score', missing_score_text)}",
                flush=True,
            )
        else:
            result_summary.failed_items.append(fail_label_of(ha, hb))
            print(f"  [{idx}/{total}] {label_of(ha, hb)} FAIL", flush=True)

        # 每批仅提交已校验成功的结果；失败配对保留旧数据。
        if result_summary.completed - committed_pairs >= SYNERGY_BATCH_SAVE_INTERVAL:
            _commit_generation_batch(result_summary, synergy_path, list(working_synergies.values()))
            committed_pairs = result_summary.completed

    if result_summary.completed > committed_pairs:
        _commit_generation_batch(result_summary, synergy_path, list(working_synergies.values()))
    if result_summary.committed:
        existing_synergy_dict.clear()
        existing_synergy_dict.update(working_synergies)
        existing_synergy_keys.clear()
        existing_synergy_keys.update(working_synergies)
    return result_summary


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
    committed_guides = 0
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
        _report_rag_degradation()

        if generated:
            working_guides[hero_id] = generated
            new_guides.append(generated)
            result_summary.completed += 1
            print(f"  [{i}/{total_heroes}] {hero_name} OK", flush=True)
        else:
            result_summary.failed_items.append(hero_name or str(hero_id))
            print(f"  [{i}/{total_heroes}] {hero_name} FAIL", flush=True)

        # 每批仅提交已通过校验的攻略；失败武将继续保留原有记录。
        if len(new_guides) - committed_guides >= GUIDE_BATCH_SAVE_INTERVAL:
            _commit_generation_batch(result_summary, guide_path, list(working_guides.values()))
            committed_guides = len(new_guides)
            print("    [提交] 已保存", flush=True)

    if len(new_guides) > committed_guides:
        _commit_generation_batch(result_summary, guide_path, list(working_guides.values()))
    if result_summary.committed:
        existing_guides.clear()
        existing_guides.update(working_guides)

    guide_count = len(working_guides)
    print(f"\n  攻略完成: 新增 {len(new_guides)} 个，共 {guide_count} 个")
    if result_summary.failed_items:
        print(f"  失败: {len(result_summary.failed_items)} 个；成功项已提交，失败项保留旧数据", flush=True)
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
    total_pairs = len(heroes) * (len(heroes) - 1) // 2
    sep_line = "=" * 55
    model_name = api_config["model"]
    print(f"\n{sep_line}")
    print(f"  生成相性评分 -- {model_name} ({total_pairs:,} 对)")
    print(f"{sep_line}")

    result_summary = _run_synergy_pairs(
        list(itertools.combinations(heroes, 2)),
        generator,
        synergy_path,
        existing_synergy_dict,
        existing_synergy_keys,
        skip_existing=False,
        score_threshold=score_threshold,
        label_of=_pair_label,
        fail_label_of=_pair_fail_label,
        missing_score_text="0",
    )

    print(f"\n  相性完成: 成功 {result_summary.completed} 对，共 {len(existing_synergy_dict)} 对")
    if result_summary.failed_items:
        print(f"  失败: {len(result_summary.failed_items)} 对；成功项已提交，失败项保留旧数据", flush=True)
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
    update_mode: bool = False,
):
    """执行相性配对生成（指定 2~8 个武将，两两配对）

    对所选武将做排列组合（C(N,2)），逐个调用 AI 生成相性评分。
    每批仅提交校验成功的结果，失败配对保留已有记录。

    Args:
        pair_file: JSON 文件路径，包含 2~8 个武将
        heroes: 全武将列表
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_dict: 已有相性 {(a_id, b_id): dict}
        existing_synergy_keys: 已有相性 key 集合
        update_mode: True 时重新生成已有相性；False 时跳过已有相性。

    Returns:
        GenerationResult: 本次生成的结构化结果。
    """
    result_summary = GenerationResult()

    print("\n  相性配对生成 (指定武将)...", flush=True)
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

    result_summary = _run_synergy_pairs(
        list(itertools.combinations(pair_heroes, 2)),
        generator,
        synergy_path,
        existing_synergy_dict,
        existing_synergy_keys,
        skip_existing=not update_mode,
        score_threshold=None,
        label_of=_pair_label,
        fail_label_of=_pair_fail_label,
        missing_score_text="?",
    )
    print(
        f"  相性完成: 新增 {result_summary.completed} 对，跳过 {result_summary.skipped} 对，"
        f"共 {len(existing_synergy_dict)} 对",
        flush=True,
    )
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
    每批仅提交校验成功的结果，失败配对保留已有记录。

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

    print("\n  相性配对生成 (选定武将 x 全体)...", flush=True)
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

    result_summary = _run_synergy_pairs(
        pairs,
        generator,
        synergy_path,
        existing_synergy_dict,
        existing_synergy_keys,
        skip_existing=True,
        score_threshold=None,
        label_of=lambda _ha, hb: hb["name"],
        fail_label_of=lambda _ha, hb: hb["name"],
        missing_score_text="?",
    )
    print(
        f"  相性完成: 新增 {result_summary.completed} 对，跳过 {result_summary.skipped} 对，"
        f"失败 {len(result_summary.failed_items)} 对, 共 {len(existing_synergy_dict)} 对",
        flush=True,
    )
    return result_summary


# ============================================================
# 实战配队清单（显式 id 配对列表）
# ============================================================

def run_synergy_list_generation(
    pairs_file: str,
    heroes: list,
    generator,
    synergy_path,
    existing_synergy_dict: dict,
    existing_synergy_keys: set,
    update_mode: bool = False,
):
    """执行实战配队清单相性生成（显式配对列表）

    配对清单 JSON 格式：[{"hero_a_id": int, "hero_b_id": int}, ...]
    武将字典由全量武将表按 id 解析，解析失败的配对记为失败项。
    支持断点续传与分批原子提交，协议与两两配对模式一致。

    Args:
        pairs_file: JSON 文件路径，包含显式配对清单
        heroes: 全武将列表（用于按 id 解析武将字典）
        generator: AIBatchGenerator 实例
        synergy_path: 相性输出路径
        existing_synergy_dict: 已有相性 {(a_id, b_id): dict}
        existing_synergy_keys: 已有相性 key 集合
        update_mode: True 时重新生成已有相性；False 时跳过已有相性。

    Returns:
        GenerationResult: 本次生成的结构化结果。
    """
    result_summary = GenerationResult()

    print("\n  相性配对生成 (实战配队清单)...", flush=True)
    with open(pairs_file, "r", encoding="utf-8") as f:
        pairs_raw = json.load(f)

    hero_by_id = {h["id"]: h for h in heroes}
    pairs = []
    for raw in pairs_raw:
        ha = hero_by_id.get(int(raw["hero_a_id"]))
        hb = hero_by_id.get(int(raw["hero_b_id"]))
        if ha and hb and ha["id"] != hb["id"]:
            pairs.append((ha, hb))
        else:
            result_summary.failed_items.append(
                f"#{raw.get('hero_a_id')}<->#{raw.get('hero_b_id')}（配对无效）"
            )
    if not pairs:
        result_summary.failed_items.append("配对清单为空或全部无效")
        return result_summary

    total = len(pairs)
    print(f"  配对清单: {total} 对", flush=True)

    result_summary = _run_synergy_pairs(
        pairs,
        generator,
        synergy_path,
        existing_synergy_dict,
        existing_synergy_keys,
        skip_existing=not update_mode,
        score_threshold=None,
        label_of=_pair_label,
        fail_label_of=_pair_fail_label,
        missing_score_text="?",
        result_summary=result_summary,
    )
    print(
        f"  相性完成: 新增 {result_summary.completed} 对，跳过 {result_summary.skipped} 对，"
        f"失败 {len(result_summary.failed_items)} 项, 共 {len(existing_synergy_dict)} 对",
        flush=True,
    )
    return result_summary
