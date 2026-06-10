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
    python -m src.scraper.ai_batch --guide --synergy
"""

from __future__ import annotations

import argparse
import json
import re
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from src.config.env import (
    get_api_config,
    get_runtime_params,
    DEFAULT_MODEL,
    PRICE_INPUT_PER_M,
    PRICE_OUTPUT_PER_M,
)

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"

# Prompt 文件路径
PROMPT_DIR = PROJECT_ROOT / "docs" / "prompts"
GUIDE_PROMPT_FILE = PROMPT_DIR / "hero_guide.md"
SYNERGY_PROMPT_FILE = PROMPT_DIR / "synergy_score.md"


# ============================================================
# 工具函数
# ============================================================

def load_prompt(filepath):
    """加载 prompt 模板文件"""
    path = Path(filepath)
    if not path.exists():
        logger.warning("Prompt 文件不存在: %s", path)
        return ""
    return path.read_text(encoding="utf-8")


def _estimate_cost(tokens_input, tokens_output):
    """根据 DeepSeek v4-pro 定价估算费用（RMB）"""
    cost = (
        tokens_input * PRICE_INPUT_PER_M / 1_000_000
        + tokens_output * PRICE_OUTPUT_PER_M / 1_000_000
    )
    return round(cost, 4)


# ============================================================
# 调用参数
# ============================================================

# 批量保存间隔
GUIDE_BATCH_SAVE_INTERVAL = 10
SYNERGY_BATCH_SAVE_INTERVAL = 20


# ============================================================
# AI 批量生成器
# ============================================================

class AIBatchGenerator:
    """AI 批量生成器

    封装 DeepSeek API 调用、重试、限速、JSON 提取和 Pydantic 校验。
    """

    def __init__(
        self,
        api_key: str,
        api_url: str | None = None,
        model: str | None = None,
        requests_per_minute: int = 30,
        max_retries: int = 3,
        http_timeout: int = 300,
    ):
        if not api_key:
            raise ValueError("api_key 不能为空")

        self.api_key = api_key
        self.api_url = api_url or "https://api.deepseek.com/v1/chat/completions"
        self.model = model or "deepseek-v4-pro"
        self.max_retries = max_retries
        self.http_timeout = http_timeout
        self._client = httpx.Client(timeout=http_timeout)

        # 限速控制
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self._last_request_time = 0.0

    def _call_api(self, messages: list[dict], temperature: float = 0.7) -> dict | None:
        """调用 DeepSeek API，带指数退避重试"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": 4096,
        }

        for attempt in range(1, self.max_retries + 1):
            # 限速等待
            elapsed = time.time() - self._last_request_time
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)

            try:
                resp = self._client.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                )
                self._last_request_time = time.time()
                resp.raise_for_status()
                data = resp.json()
                return data
            except httpx.HTTPStatusError as e:
                logger.warning("API 返回错误 [%d/%d]: HTTP %s",
                               attempt, self.max_retries, e.response.status_code)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)
            except Exception as e:
                logger.warning("API 请求异常 [%d/%d]: %s",
                               attempt, self.max_retries, e)
                if attempt < self.max_retries:
                    time.sleep(2 ** attempt)

        logger.error("API 请求超过最大重试次数 %d", self.max_retries)
        return None

    @staticmethod
    def _extract_json(text: str) -> dict:
        """从 API 返回文本中提取 JSON

        处理多种格式：
        - 纯 JSON
        - 被 ```json ... ``` 包裹
        - 被 ``` ... ``` 包裹
        - 用 --- 分隔符分隔出 JSON 部分
        """
        text = text.strip()

        # 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试从 ```json ... ``` 中提取
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 尝试从 --- 分隔符后提取第一个 JSON 代码块
        if "---" in text:
            parts = text.split("---")
            for part in reversed(parts):
                m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", part, re.DOTALL)
                if m:
                    try:
                        return json.loads(m.group(1).strip())
                    except json.JSONDecodeError:
                        continue
                try:
                    return json.loads(part.strip())
                except json.JSONDecodeError:
                    continue

        raise ValueError(f"无法从响应中提取 JSON:\n{text[:500]}")

    # ---------------------------------------------------------------
    # Prompt 构建
    # ---------------------------------------------------------------

    def _build_guide_prompt(self, hero: dict) -> str:
        """构建单个武将的攻略 prompt"""
        lines = [f"武将: {hero.get('name', '')}"]
        lines.append(f"势力: {hero.get('faction', '')}")
        lines.append(f"定位: {hero.get('position', '')}")
        lines.append(f"体力: {hero.get('max_hp', 4)}  手牌: {hero.get('max_hand', 4)}")
        lines.append(f"性别: {hero.get('gender', '男')}")
        lines.append(f"难度: {hero.get('difficulty', 2)}")
        if hero.get("skills"):
            lines.append("")
            lines.append("技能:")
            for sk in hero["skills"]:
                lines.append(f"  - {sk.get('name', '')}: {sk.get('description', '')}")
        return "\n".join(lines)

    def _build_synergy_prompt(self, hero_a: dict, hero_b: dict) -> str:
        """构建武将对的相性评分 prompt"""
        def hero_block(label: str, h: dict) -> list[str]:
            lines = [f"## {label}: {h.get('name', '')}"]
            lines.append(f"  势力: {h.get('faction', '')}")
            lines.append(f"  定位: {h.get('position', '')}")
            lines.append(f"  体力/手牌: {h.get('max_hp', 4)}/{h.get('max_hand', 4)}")
            if h.get("skills"):
                lines.append("  技能:")
                for sk in h["skills"]:
                    lines.append(f"    - {sk.get('name', '')}: {sk.get('description', '')}")
            return lines

        lines = []
        lines.extend(hero_block("武将 A", hero_a))
        lines.append("")
        lines.extend(hero_block("武将 B", hero_b))
        return "\n".join(lines)

    # ---------------------------------------------------------------
    # 生成攻略
    # ---------------------------------------------------------------

    def generate_guide(self, hero: dict) -> tuple[dict | None, dict | None]:
        """为单个武将生成攻略

        Returns:
            (guide_dict, usage_dict) 或 (None, usage_dict)
        """
        system_prompt = load_prompt(GUIDE_PROMPT_FILE)
        if not system_prompt:
            logger.error("攻略 prompt 模板未找到: %s", GUIDE_PROMPT_FILE)
            return None, None

        user_prompt = self._build_guide_prompt(hero)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._call_api(messages, temperature=0.7)
        if not response:
            return None, None

        usage = response.get("usage", {})
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not content:
            logger.warning("API 返回内容为空")
            return None, usage

        try:
            raw = self._extract_json(content)
        except ValueError as e:
            logger.warning("JSON 提取失败: %s", e)
            return None, usage

        # 补充 hero_id
        raw["hero_id"] = hero.get("id", 0)

        # Pydantic 校验
        try:
            from src.data.models import HeroGuide
            validated = HeroGuide.model_validate(raw)
            return validated.model_dump(mode="json"), usage
        except Exception as e:
            logger.warning("Pydantic 校验失败: %s", e)
            logger.debug("异常数据: %s", json.dumps(raw, ensure_ascii=False))
            return None, usage

    # ---------------------------------------------------------------
    # 生成相性评分
    # ---------------------------------------------------------------

    def generate_synergy(self, hero_a: dict, hero_b: dict) -> tuple[dict | None, dict | None]:
        """为武将对生成相性评分

        Returns:
            (synergy_dict, usage_dict) 或 (None, usage_dict)
        """
        system_prompt = load_prompt(SYNERGY_PROMPT_FILE)
        if not system_prompt:
            logger.error("相性 prompt 模板未找到: %s", SYNERGY_PROMPT_FILE)
            return None, None

        user_prompt = self._build_synergy_prompt(hero_a, hero_b)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = self._call_api(messages, temperature=0.7)
        if not response:
            return None, None

        usage = response.get("usage", {})
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")

        if not content:
            logger.warning("API 返回内容为空")
            return None, usage

        try:
            raw = self._extract_json(content)
        except ValueError as e:
            logger.warning("JSON 提取失败: %s", e)
            return None, usage

        # 补充 hero ids
        raw["hero_a_id"] = hero_a.get("id", 0)
        raw["hero_b_id"] = hero_b.get("id", 0)

        # Pydantic 校验
        try:
            from src.data.models import SynergyScore
            validated = SynergyScore.model_validate(raw)
            return validated.model_dump(mode="json"), usage
        except Exception as e:
            logger.warning("Pydantic 校验失败: %s", e)
            logger.debug("异常数据: %s", json.dumps(raw, ensure_ascii=False))
            return None, usage

    def close(self):
        """关闭 HTTP 客户端"""
        self._client.close()


# ============================================================
# 辅助函数
# ============================================================

def load_heroes(filepath=DEFAULT_HEROES_FILE):
    """从 JSON 文件加载武将数据"""
    path = Path(filepath)
    if not path.exists():
        logger.error("武将数据文件不存在: %s", path)
        return []
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            logger.error("武将数据文件损坏: %s", path)
            return []
    logger.info("加载 %d 个武将", len(data))
    return data


def estimate_cost(hero_count, mode, model=None):
    """估算批量生成成本

    Args:
        hero_count: 武将数量
        mode: "guide" 或 "synergy"
        model: 模型名称（仅用于显示）

    Returns:
        dict: 成本估算结果
    """
    if model is None:
        model = DEFAULT_MODEL

    if hero_count == 0:
        return {
            "mode": mode,
            "items": 0,
            "estimated_tokens": 0,
            "estimated_input_tokens": 0,
            "estimated_output_tokens": 0,
            "estimated_cost_cny": 0.0,
        }

    if mode == "guide":
        items = hero_count
        # 每个武将：2000 输入 + 500 输出 tokens
        input_tokens = items * 2000
        output_tokens = items * 500
    elif mode == "synergy":
        items = hero_count * (hero_count - 1) // 2
        input_tokens = items * 800
        output_tokens = items * 200
    else:
        raise ValueError(f"未知 mode: {mode}")

    total_tokens = input_tokens + output_tokens
    cost_cny = round(
        input_tokens * PRICE_INPUT_PER_M / 1_000_000
        + output_tokens * PRICE_OUTPUT_PER_M / 1_000_000,
        4,
    )

    return {
        "mode": mode,
        "items": items,
        "estimated_tokens": total_tokens,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_cny": cost_cny,
    }


def _save_json(filepath, data):
    """原子写入 JSON 文件"""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)
    logger.debug("已保存 %d 条到 %s", len(data), filepath)


# ============================================================
# CLI 入口
# ============================================================

def main():
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
    parser.add_argument("--verbose", "-v", action="store_true", help="详细日志")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")

    if not args.guide and not args.synergy:
        parser.error("请指定 --guide 和/或 --synergy")

    # ============================================================
    # 加载武将数据
    # ============================================================
    heroes = load_heroes(args.heroes_file)
    if not heroes:
        logger.error("没有加载到武将数据")
        sys.exit(1)
    logger.info("加载 %d 个武将", len(heroes))

    # ============================================================
    # 获取 API 配置（config.env > 默认值 > 环境变量）
    # ============================================================
    api_config = get_api_config()
    runtime_params = get_runtime_params()

    # ============================================================
    # Dry-run 模式
    # ============================================================
    if args.dry_run:
        print("=" * 55)
        print(f"  AI 批量生成 - 成本估算（{api_config['model']}）")
        print("=" * 55)
        if args.guide:
            est = estimate_cost(len(heroes), "guide", api_config["model"])
            print(f"  攻略生成: {est['items']} 个")
            token_msg = f"  预估 Token: {est['estimated_tokens']:,}"
            token_detail = f"(输入 {est['estimated_input_tokens']:,} + 输出 {est['estimated_output_tokens']:,})"
            print(f"{token_msg} {token_detail}")
            print(f"  预估费用: CNY{est['estimated_cost_cny']:.4f}")
            print()
        if args.synergy:
            est = estimate_cost(len(heroes), "synergy", api_config["model"])
            print(f"  相性评分: {est['items']:,} 对")
            token_msg = f"  预估 Token: {est['estimated_tokens']:,}"
            token_detail = f"(输入 {est['estimated_input_tokens']:,} + 输出 {est['estimated_output_tokens']:,})"
            print(f"{token_msg} {token_detail}")
            print(f"  预估费用: CNY{est['estimated_cost_cny']:.4f}")
            print()
        print(f"  （在 config.env 中配置 DEEPSEEK_API_KEY，然后去除 --dry-run 执行）")
        print(f"  定价参考（deepseek-v4-pro）：输入 CNY3/百万tokens，输出 CNY6/百万tokens")
        print("=" * 55)
        return

    # ============================================================
    # 检查 API Key
    # ============================================================
    logger.info("API URL: %s", api_config["api_url"])
    logger.info("模型: %s", api_config["model"])
    logger.info("速率限制: %d req/min, 最多重试 %d 次",
                runtime_params["requests_per_minute"], runtime_params["max_retries"])

    if not api_config["api_key"]:
        print("")
        print("  错误：未找到 API Key")
        print("  请通过以下任一方式提供：")
        print("    1. config.env 中的 DEEPSEEK_API_KEY 字段")
        print("    3. DEEPSEEK_API_KEY 或 OPENAI_API_KEY 环境变量")
        print("")
        sys.exit(1)

    # ============================================================
    # 初始化生成器
    # ============================================================
    generator = AIBatchGenerator(
        api_key=api_config["api_key"],
        api_url=api_config["api_url"],
        model=api_config["model"],
        requests_per_minute=runtime_params["requests_per_minute"],
        max_retries=runtime_params["max_retries"],
        http_timeout=runtime_params["http_timeout"],
    )

    # ============================================================
    # 加载已有数据（断点续传）
    # ============================================================
    existing_guides = {}
    existing_synergy_list = []
    existing_synergy_keys = set()

    guide_path = Path(args.guides_file)
    synergy_path = Path(args.synergies_file)

    if guide_path.exists():
        try:
            with open(guide_path, "r", encoding="utf-8") as f:
                for g in json.load(f):
                    existing_guides[g["hero_id"]] = g
            logger.info("已有 %d 份攻略", len(existing_guides))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("攻略文件损坏或为空 (%s)，将重新生成", guide_path.name)
            guide_path.unlink(missing_ok=True)

    if synergy_path.exists():
        try:
            with open(synergy_path, "r", encoding="utf-8") as f:
                existing_synergy_list = json.load(f)
            for s in existing_synergy_list:
                key = tuple(sorted([s["hero_a_id"], s["hero_b_id"]]))
                existing_synergy_keys.add(key)
            logger.info("已有 %d 对相性", len(existing_synergy_list))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("相性文件损坏或为空 (%s)，将重新生成", synergy_path.name)
            synergy_path.unlink(missing_ok=True)

    total_prompt_tokens = 0
    total_completion_tokens = 0

    # ============================================================
    # 生成攻略（委托给 ai_guide 模块）
    # ============================================================
    if args.guide:
        from src.scraper.ai_guide import run_guide_generation
        pt, ct = run_guide_generation(
            heroes=heroes,
            generator=generator,
            guide_path=guide_path,
            existing_guides=existing_guides,
            api_config=api_config,
        )
        total_prompt_tokens += pt
        total_completion_tokens += ct

    # ============================================================
    # 生成相性评分（委托给 ai_synergy 模块）
    # ============================================================
    if args.synergy:
        from src.scraper.ai_synergy import run_synergy_generation
        pt, ct = run_synergy_generation(
            heroes=heroes,
            generator=generator,
            synergy_path=synergy_path,
            existing_synergy_list=existing_synergy_list,
            existing_synergy_keys=existing_synergy_keys,
            score_threshold=args.score_threshold,
            api_config=api_config,
        )
        total_prompt_tokens += pt
        total_completion_tokens += ct

    # ============================================================
    # 最终统计
    # ============================================================
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

    generator.close()
    print(f"\n  全部完成！\n")


if __name__ == "__main__":
    main()
