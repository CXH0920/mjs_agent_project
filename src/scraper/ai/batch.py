"""
名将杀 Agent - AI 批量生成工具

利用 DeepSeek API 批量生成武将攻略和相性评分。
支持断点续传（加载已有数据，跳过已生成的项）。
输出经过 Pydantic 模型校验后再写入。

API 配置优先级（从高到低）：
  1. config.env 配置文件（项目根目录，KEY=VALUE 格式）
  2. DEEPSEEK_API_KEY / OPENAI_API_KEY 环境变量
  3. 内置默认值

使用方法:
    python -m src.scraper.ai_batch --dry-run
    python -m src.scraper.ai_batch --guide
    python -m src.scraper.ai_batch --synergy
    python -m src.scraper.ai_batch --synergy-pair heroes.json
    python -m src.scraper.ai_batch --synergy-single heroes.json
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from src.config.env import (
    BUNDLE_ROOT,
    PROVIDER_PRESETS,
    get_runtime_params,
    resolve_api_config,
)
from src.data.guide_manager import GuideManager
from src.data.synergy_manager import SynergyManager

from src.scraper.ai.prompt_utils import _estimate_cost, estimate_cost
from src.scraper.ai.utils import (
    load_heroes,
    safe_url_origin,
)

from src.scraper.ai.api_generator import AIBatchGenerator

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================

DEFAULT_DATA_DIR = BUNDLE_ROOT / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"


def _preserve_invalid_data_file(path: Path, manager) -> Path:
    """备份无效原文件，并将已通过校验的记录写回原路径。"""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = path.with_name(f"{path.stem}.corrupt-{timestamp}{path.suffix}")
    counter = 1
    while backup_path.exists():
        backup_path = path.with_name(f"{path.stem}.corrupt-{timestamp}-{counter}{path.suffix}")
        counter += 1

    try:
        path.replace(backup_path)
    except OSError as exc:
        logger.error("无法备份损坏文件 %s: %s", path, exc)
        raise RuntimeError(f"无法备份损坏文件: {path}") from exc

    manager.save()
    logger.warning(
        "数据文件包含无效内容，原文件已备份为 %s，当前文件保留 %d 条有效记录",
        backup_path,
        len(manager.list_all()),
    )
    return backup_path


def _load_existing_synergies(synergy_path: Path) -> tuple[dict, set]:
    """加载已有相性数据用于断点续传

    Returns:
        (synergy_dict, existing_keys)
        synergy_dict: {(a_id, b_id): synergy_dict} 以排序 tuple 为 key
        existing_keys: set[(a_id, b_id)] 用于快速查找
    """
    if not synergy_path.exists():
        return {}, set()

    manager = SynergyManager(synergy_path)
    issues = manager.load()
    if any(issue.severity == "error" for issue in issues):
        _preserve_invalid_data_file(synergy_path, manager)

    existing_dict = {
        tuple(sorted((score.hero_a_id, score.hero_b_id))): score.model_dump(mode="json")
        for score in manager.list_synergies()
    }
    existing_keys = set(existing_dict)
    logger.info("已有 %d 对相性", len(existing_dict))
    return existing_dict, existing_keys


def _load_existing_guides(guide_path: Path) -> dict:
    """加载已有攻略数据用于断点续传"""
    if not guide_path.exists():
        return {}

    manager = GuideManager(guide_path)
    issues = manager.load()
    if any(issue.severity == "error" for issue in issues):
        _preserve_invalid_data_file(guide_path, manager)

    existing = {
        guide.hero_id: guide.model_dump(mode="json")
        for guide in manager.list_guides()
    }
    logger.info("已有 %d 份攻略", len(existing))
    return existing


def _show_cost_estimate(heroes: list, api_config: dict, args) -> None:
    print("=" * 55)
    print(f"  AI 批量生成 - 成本估算（{api_config['model']}）")
    print("=" * 55)
    if args.guide:
        _print_mode_estimates(len(heroes), "guide", "攻略生成", api_config["model"])
    if args.synergy:
        _print_mode_estimates(len(heroes), "synergy", "相性评分", api_config["model"])
    print("  （在 config.env 中配置 DEEPSEEK_API_KEY，然后去除 --dry-run 执行）")
    print("=" * 55)


def _print_mode_estimates(count: int, mode: str, label: str, model: str) -> None:
    """分别输出 RAG 增强与经典模式的成本估算（dry-run 预览）。"""
    unit = "个" if mode == "guide" else "对"
    for version, use_rag in (("RAG 增强", True), ("经典模式", False)):
        est = estimate_cost(count, mode, model, use_rag=use_rag)
        print(f"  {label}（{version}）: {est['items']:,} {unit}")
        print(f"  预估 Token: {est['estimated_tokens']:,} (输入 {est['estimated_input_tokens']:,} + 输出 {est['estimated_output_tokens']:,})")
        print(_format_cost_estimate(est))
    print()


def _check_api_key(api_config: dict) -> None:
    """检查 API Key 是否配置（供应商语义：requires_key=False 如 ollama 允许空）"""
    provider = api_config.get("provider", "deepseek")
    requires_key = PROVIDER_PRESETS.get(provider, {}).get("requires_key", True)
    if requires_key and not api_config["api_key"]:
        print(f"\n  错误：{provider} 档案未配置 API Key")
        print("  请在「配置 → API 配置」中为该档案填写 Key")
        print("  或设置环境变量 DEEPSEEK_API_KEY / OPENAI_API_KEY（脚本/CI 兜底）\n")
        sys.exit(1)


def _format_cost_estimate(estimation: dict) -> str:
    cost = estimation.get("estimated_cost_cny")
    if cost is None:
        return f"  预估费用: 无法自动估算（{estimation.get('message', '未配置模型价格')}）"
    return f"  预估费用: CNY{cost:.4f}"


def _print_token_summary(total_prompt_tokens: int, total_completion_tokens: int, model: str) -> None:
    """打印最终 token 统计"""
    if total_prompt_tokens > 0 or total_completion_tokens > 0:
        total_cost = _estimate_cost(total_prompt_tokens, total_completion_tokens, model)
        sep_line = "=" * 55
        print(f"\n{sep_line}")
        print("  Token 使用统计")
        print(f"{sep_line}")
        print(f"  输入 tokens:  {total_prompt_tokens:,}")
        print(f"  输出 tokens:  {total_completion_tokens:,}")
        print(f"  合计 tokens:  {total_prompt_tokens + total_completion_tokens:,}")
        if total_cost is None:
            print(f"  预估费用:     无法自动估算（模型 {model} 未配置价格）")
        else:
            print(f"  预估费用:     CNY{total_cost:.4f}")
        print(f"{sep_line}")


# ============================================================
# CLI 入口
# ============================================================

def main():
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    print("启动中...", flush=True)

    parser = argparse.ArgumentParser(description="名将杀 Agent - AI 批量生成工具")
    parser.add_argument("--guide", action="store_true", help="生成攻略")
    parser.add_argument("--synergy", action="store_true", help="生成相性评分")
    parser.add_argument("--heroes-file", type=str, default=str(DEFAULT_HEROES_FILE),
                         help="武将数据文件路径")
    parser.add_argument("--guides-file", type=str, default=str(DEFAULT_GUIDES_FILE),
                         help="攻略输出路径")
    parser.add_argument("--synergies-file", type=str, default=str(DEFAULT_SYNERGIES_FILE),
                         help="相性输出路径")
    parser.add_argument("--dry-run", action="store_true",
                         help="预览模式：仅估算 Token 和费用，不调用 API")
    parser.add_argument("--score-threshold", type=int, default=0,
                         help="相性评分过滤下限（仅保存 >= 此值的相性）")
    parser.add_argument("--synergy-pair", type=str, default=None,
                         help="生成指定两武将的相性评分，参数为包含两个武将的 JSON 文件路径")
    parser.add_argument("--synergy-single", type=str, default=None,
                         help="生成指定武将与其他所有武将的相性评分，参数为包含一个武将的 JSON 文件路径")
    parser.add_argument("--browser", action="store_true",
                         help="使用 Playwright + Edge 浏览器方式（替代 API 直连）")
    parser.add_argument("--update", action="store_true",
                         help="更新模式：重新生成已存在的数据（默认跳过已存在的）")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    parser.add_argument("--no-rag", action="store_true",
                         help="禁用 RAG 语料增强（默认启用）")
    parser.add_argument("--rebuild-rag-index", action="store_true",
                         help="重建 RAG 向量索引后退出")
    args = parser.parse_args()

    runtime_params = get_runtime_params()
    log_level = "DEBUG" if args.verbose else runtime_params["log_level"]
    from src.config.logging_config import setup_logging
    setup_logging(log_level=log_level, log_to_file=runtime_params["log_to_file"])
    if args.rebuild_rag_index:
        from src.rag.indexer import build_index
        n, _ = build_index(rebuild=True)
        sys.exit(0 if n else 1)

    if args.no_rag:
        os.environ["RAG_ENABLED"] = "false"
        logger.info("RAG enhanced context disabled via --no-rag")
    else:
        from src.scraper.ai.rag_prompt import _rag_enabled
        if not _rag_enabled():
            print("  [RAG] 已选择 RAG 语料增强，但当前 RAG_ENABLED=false，本次将以经典模式生成", flush=True)

    has_synergy_mode = args.synergy or args.synergy_pair or args.synergy_single
    if not args.guide and not has_synergy_mode:
        parser.error("请指定 --guide 和/或 --synergy / --synergy-pair / --synergy-single")

    # 加载武将数据和 API 配置
    heroes = load_heroes(args.heroes_file)
    if not heroes:
        logger.error("没有加载到武将数据")
        sys.exit(1)

    api_config = resolve_api_config(None)

    if args.dry_run:
        _show_cost_estimate(heroes, api_config, args)
        return

    if args.browser:
        from src.scraper.ai.browser_generator import PlaywrightGenerator
        generator = PlaywrightGenerator()
        logger.info("使用浏览器模式")
    else:
        _check_api_key(api_config)
        logger.info("API URL: %s", safe_url_origin(api_config["api_url"]))
        logger.info("模型: %s", api_config["model"])
        logger.info("速率限制: %d req/min, 最多重试 %d 次",
                    runtime_params["requests_per_minute"], runtime_params["max_retries"])
        generator = AIBatchGenerator(
            api_key=api_config["api_key"],
            api_url=api_config["api_url"],
            model=api_config["model"],
            provider=api_config.get("provider", "deepseek"),
            requests_per_minute=runtime_params["requests_per_minute"],
            max_retries=runtime_params["max_retries"],
            http_timeout=runtime_params["http_timeout"],
        )

    guide_path = Path(args.guides_file)
    synergy_path = Path(args.synergies_file)
    existing_guides = _load_existing_guides(guide_path) if args.guide else {}
    existing_synergy_dict, existing_synergy_keys = (
        _load_existing_synergies(synergy_path) if (args.synergy or args.synergy_pair or args.synergy_single)
        else ({}, set())
    )

    task_results = []
    try:
        if args.guide:
            from src.scraper.ai.generation import run_guide_generation
            task_results.append(run_guide_generation(
                heroes=heroes, generator=generator, guide_path=guide_path,
                existing_guides=existing_guides, api_config=api_config,
                update_mode=args.update,
            ))

        if args.synergy:
            from src.scraper.ai.generation import run_synergy_generation
            task_results.append(run_synergy_generation(
                heroes=heroes, generator=generator, synergy_path=synergy_path,
                existing_synergy_dict=existing_synergy_dict,
                existing_synergy_keys=existing_synergy_keys,
                score_threshold=args.score_threshold, api_config=api_config,
            ))

        if args.synergy_pair:
            from src.scraper.ai.generation import run_synergy_pair_generation
            task_results.append(run_synergy_pair_generation(
                pair_file=args.synergy_pair, heroes=heroes, generator=generator,
                synergy_path=synergy_path,
                existing_synergy_dict=existing_synergy_dict,
                existing_synergy_keys=existing_synergy_keys,
                update_mode=args.update,
            ))

        if args.synergy_single:
            from src.scraper.ai.generation import run_synergy_single_generation
            task_results.append(run_synergy_single_generation(
                single_file=args.synergy_single, heroes=heroes, generator=generator,
                synergy_path=synergy_path,
                existing_synergy_dict=existing_synergy_dict,
                existing_synergy_keys=existing_synergy_keys,
            ))
    finally:
        generator.close()

    total_prompt_tokens = sum(result.prompt_tokens for result in task_results)
    total_completion_tokens = sum(result.completion_tokens for result in task_results)
    _print_token_summary(total_prompt_tokens, total_completion_tokens, api_config["model"])

    failed_results = [result for result in task_results if not result.succeeded]
    if failed_results:
        failed_items = [item for result in failed_results for item in result.failed_items]
        print(f"\n  [错误] 生成失败：{len(failed_items)} 项；成功项已提交，失败项保留旧数据", flush=True)
        sys.exit(1)

    print("\n  全部完成！\n")


if __name__ == "__main__":
    main()
