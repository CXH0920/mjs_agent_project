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
import json
import logging
import sys
from pathlib import Path

from src.config.env import (
    get_api_config,
    get_runtime_params,
)

from src.scraper.prompt_utils import _estimate_cost, estimate_cost
from src.scraper.ai_utils import (
    load_heroes,
    _save_json,
)

from src.scraper.ai_generator import AIBatchGenerator

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"


def _load_existing_synergies(synergy_path: Path) -> tuple[dict, set]:
    """加载已有相性数据用于断点续传

    Returns:
        (synergy_dict, existing_keys)
        synergy_dict: {(a_id, b_id): synergy_dict} 以排序 tuple 为 key
        existing_keys: set[(a_id, b_id)] 用于快速查找
    """
    existing_dict = {}
    existing_keys = set()
    if synergy_path.exists():
        try:
            with open(synergy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for s in data:
                key = tuple(sorted([s["hero_a_id"], s["hero_b_id"]]))
                existing_dict[key] = s
                existing_keys.add(key)
            logger.info("已有 %d 对相性", len(existing_dict))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("相性文件损坏或为空 (%s)，将重新生成", synergy_path.name)
            synergy_path.unlink(missing_ok=True)
    return existing_dict, existing_keys


def _load_existing_guides(guide_path: Path) -> dict:
    """加载已有攻略数据用于断点续传"""
    existing = {}
    if guide_path.exists():
        try:
            with open(guide_path, "r", encoding="utf-8") as f:
                for g in json.load(f):
                    existing[g["hero_id"]] = g
            logger.info("已有 %d 份攻略", len(existing))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("攻略文件损坏或为空 (%s)，将重新生成", guide_path.name)
            guide_path.unlink(missing_ok=True)
    return existing


def _show_cost_estimate(heroes: list, api_config: dict, args) -> None:
    """显示 dry-run 成本估算"""
    print("=" * 55)
    print(f"  AI 批量生成 - 成本估算（{api_config['model']}）")
    print("=" * 55)
    if args.guide:
        est = estimate_cost(len(heroes), "guide", api_config["model"])
        print(f"  攻略生成: {est['items']} 个")
        print(f"  预估 Token: {est['estimated_tokens']:,} (输入 {est['estimated_input_tokens']:,} + 输出 {est['estimated_output_tokens']:,})")
        print(f"  预估费用: CNY{est['estimated_cost_cny']:.4f}\n")
    if args.synergy:
        est = estimate_cost(len(heroes), "synergy", api_config["model"])
        print(f"  相性评分: {est['items']:,} 对")
        print(f"  预估 Token: {est['estimated_tokens']:,} (输入 {est['estimated_input_tokens']:,} + 输出 {est['estimated_output_tokens']:,})")
        print(f"  预估费用: CNY{est['estimated_cost_cny']:.4f}\n")
    print(f"  （在 config.env 中配置 DEEPSEEK_API_KEY，然后去除 --dry-run 执行）")
    print(f"  定价参考（deepseek-v4-pro）：输入 CNY3/百万tokens，输出 CNY6/百万tokens")
    print("=" * 55)


def _check_api_key(api_config: dict) -> None:
    """检查 API Key 是否配置"""
    if not api_config["api_key"]:
        print("\n  错误：未找到 API Key")
        print("  请通过以下任一方式提供：")
        print("    1. config.env 中的 DEEPSEEK_API_KEY 字段")
        print("    2. DEEPSEEK_API_KEY 或 OPENAI_API_KEY 环境变量\n")
        sys.exit(1)


def _print_token_summary(total_prompt_tokens: int, total_completion_tokens: int) -> None:
    """打印最终 token 统计"""
    if total_prompt_tokens > 0 or total_completion_tokens > 0:
        total_cost = _estimate_cost(total_prompt_tokens, total_completion_tokens)
        sep_line = "=" * 55
        print(f"\n{sep_line}")
        print(f"  Token 使用统计")
        print(f"{sep_line}")
        print(f"  输入 tokens:  {total_prompt_tokens:,}")
        print(f"  输出 tokens:  {total_completion_tokens:,}")
        print(f"  合计 tokens:  {total_prompt_tokens + total_completion_tokens:,}")
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
    args = parser.parse_args()

    runtime_params = get_runtime_params()
    log_level = "DEBUG" if args.verbose else runtime_params["log_level"]
    from src.config.logging_config import setup_logging
    setup_logging(log_level=log_level, log_to_file=runtime_params["log_to_file"])

    has_synergy_mode = args.synergy or args.synergy_pair or args.synergy_single
    if not args.guide and not has_synergy_mode:
        parser.error("请指定 --guide 和/或 --synergy / --synergy-pair / --synergy-single")

    # 加载武将数据和 API 配置
    heroes = load_heroes(args.heroes_file)
    if not heroes:
        logger.error("没有加载到武将数据")
        sys.exit(1)

    api_config = get_api_config()

    if args.dry_run:
        _show_cost_estimate(heroes, api_config, args)
        return

    _check_api_key(api_config)
    logger.info("API URL: %s", api_config["api_url"])
    logger.info("模型: %s", api_config["model"])
    logger.info("速率限制: %d req/min, 最多重试 %d 次",
                runtime_params["requests_per_minute"], runtime_params["max_retries"])

    if args.browser:
        from src.scraper.ai_playwright import PlaywrightGenerator
        generator = PlaywrightGenerator()
        logger.info("使用浏览器模式")
    else:
        generator = AIBatchGenerator(
            api_key=api_config["api_key"],
            api_url=api_config["api_url"],
            model=api_config["model"],
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

    total_prompt_tokens = 0
    total_completion_tokens = 0
    had_failure = False

    if args.guide:
        from src.scraper.ai_generation import run_guide_generation
        pt, ct = run_guide_generation(
            heroes=heroes, generator=generator, guide_path=guide_path,
            existing_guides=existing_guides, api_config=api_config,
            update_mode=args.update,
        )
        total_prompt_tokens += pt
        total_completion_tokens += ct

    if args.synergy:
        from src.scraper.ai_generation import run_synergy_generation
        pt, ct = run_synergy_generation(
            heroes=heroes, generator=generator, synergy_path=synergy_path,
            existing_synergy_dict=existing_synergy_dict,
            existing_synergy_keys=existing_synergy_keys,
            score_threshold=args.score_threshold, api_config=api_config,
        )
        total_prompt_tokens += pt
        total_completion_tokens += ct

    if args.synergy_pair:
        from src.scraper.ai_generation import run_synergy_pair_generation
        pt, ct = run_synergy_pair_generation(
            pair_file=args.synergy_pair, heroes=heroes, generator=generator,
            synergy_path=synergy_path,
            existing_synergy_dict=existing_synergy_dict,
            existing_synergy_keys=existing_synergy_keys,
        )
        total_prompt_tokens += pt
        total_completion_tokens += ct

    if args.synergy_single:
        from src.scraper.ai_generation import run_synergy_single_generation
        pt, ct = run_synergy_single_generation(
            single_file=args.synergy_single, heroes=heroes, generator=generator,
            synergy_path=synergy_path,
            existing_synergy_dict=existing_synergy_dict,
            existing_synergy_keys=existing_synergy_keys,
        )
        total_prompt_tokens += pt
        total_completion_tokens += ct

    _print_token_summary(total_prompt_tokens, total_completion_tokens)
    generator.close()

    # 浏览器模式不产生 usage 统计，不以 token 为成败依据
    if not args.browser and total_prompt_tokens == 0 and total_completion_tokens == 0:
        had_failure = True

    if had_failure:
        print(f"\n  [警告] 部分或全部操作失败，请检查 API Key 和网络连接\n")
        sys.exit(1)

    print(f"\n  全部完成！\n")


if __name__ == "__main__":
    main()
