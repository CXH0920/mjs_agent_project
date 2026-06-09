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
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ENV_FILE = PROJECT_ROOT / "config.env"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"

# Prompt 文件路径
PROMPT_DIR = PROJECT_ROOT / "docs" / "prompts"
GUIDE_PROMPT_FILE = PROMPT_DIR / "hero_guide.md"
SYNERGY_PROMPT_FILE = PROMPT_DIR / "synergy_score.md"

# ============================================================
# DeepSeek API 默认值
# ============================================================

DEFAULT_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-v4-pro"

# deepseek-v4-pro 定价（RMB / 百万 tokens）
PRICE_INPUT_PER_M = 3.0     # CNY3 / 百万输入 tokens（缓存未命中）
PRICE_OUTPUT_PER_M = 6.0    # CNY6 / 百万输出 tokens

# ============================================================
# 配置文件加载
# ============================================================

def parse_env_file(env_path=None):
    """解析标准 .env 格式文件

    支持 KEY=VALUE 格式，忽略空行和 # 注释行，自动去除值两侧的引号。

    Args:
        env_path: .env 文件路径，默认为项目根目录下的 config.env

    Returns:
        dict[str, str]: 解析出的键值对
    """
    if env_path is None:
        env_path = DEFAULT_ENV_FILE
    path = Path(env_path)
    if not path.exists():
        logger.debug(".env 文件不存在: %s，使用默认值", path)
        return {}

    result = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            if key:
                result[key] = value
        logger.debug("已加载 .env 配置: %s (%d 项)", path, len(result))
        return result
    except Exception as e:
        logger.warning(".env 文件解析失败 %s: %s", path, e)
        return {}


def load_env_config(env_path=None):
    """从 .env 文件加载配置（统一小写键名，便于使用）

    将 config.env 中的大写 KEY 映射为小写键名供程序内部使用。
    若文件不存在或解析失败则返回空 dict。

    Args:
        env_path: .env 文件路径

    Returns:
        dict: 小写键名的配置 dict，如 {"api_key": "...", "api_url": "..."}
    """
    raw = parse_env_file(env_path)
    key_mapping = {
        "DEEPSEEK_API_KEY": "api_key",
        "DEEPSEEK_API_URL": "api_url",
        "DEEPSEEK_MODEL": "model",
        "REQUESTS_PER_MINUTE": "requests_per_minute",
        "HTTP_TIMEOUT": "http_timeout",
        "MAX_RETRIES": "max_retries",
    }
    config = {}
    for env_key, cfg_key in key_mapping.items():
        if env_key in raw:
            value = raw[env_key]
            if cfg_key in ("requests_per_minute", "max_retries", "http_timeout"):
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    logger.warning("配置 %s 值不是有效整数: %s，使用默认值", env_key, value)
                    continue
            config[cfg_key] = value
    return config

def get_api_config():
    """获取 API 配置（合并 config.env、环境变量、默认值）

    优先级：config.env > 环境变量 > 默认值

    Returns:
        {"api_key": str, "api_url": str, "model": str}
    """
    config = load_env_config()

    api_key = (
        config.get("api_key", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
        or os.getenv("OPENAI_API_KEY", "")
    )
    api_url = (
        config.get("api_url", "")
        or DEFAULT_API_URL
    )
    model = (
        config.get("model", "")
        or DEFAULT_MODEL
    )

    return {"api_key": api_key, "api_url": api_url, "model": model}


def get_runtime_params():
    """从 config.env 获取运行时参数（带默认值）

    Returns:
        {"requests_per_minute": int, "max_retries": int, "http_timeout": int}
    """
    config = load_env_config()
    return {
        "requests_per_minute": config.get("requests_per_minute", 30),
        "max_retries": config.get("max_retries", 3),
        "http_timeout": config.get("http_timeout", 300),
    }

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
        api_key,
        api_url=DEFAULT_API_URL,
        model=DEFAULT_MODEL,
        requests_per_minute=30,
        max_retries=3,
        http_timeout=300,
    ):
        self.api_key = api_key
        self.api_url = api_url
        self.model = model
        self.requests_per_minute = requests_per_minute
        self.max_retries = max_retries
        self.http_timeout = http_timeout
        self._min_interval = 60.0 / max(requests_per_minute, 1)
        self.client = httpx.Client(timeout=http_timeout)
        self._last_request_time = 0.0

    # ----------------------------------------------------------
    # 底层 API 调用
    # ----------------------------------------------------------

    def _rate_limit(self):
        """简单的速率限制，确保不超过 requests_per_minute"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def _call_llm(self, system_prompt, user_prompt):
        """调用 DeepSeek Chat Completions API

        返回 (content, usage) 二元组。
        - 成功时 content 为响应文本，usage 为 {"prompt_tokens": N, "completion_tokens": N}
        - 失败时 content 为 None，usage 为 None
        """
        last_error = None
        base_delay = 1.0

        for attempt in range(1, self.max_retries + 1):
            try:
                self._rate_limit()
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }
                payload = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 16384,
                }
                resp = self.client.post(self.api_url, headers=headers, json=payload)

                if resp.status_code == 401:
                    logger.error("API 鉴权失败（401）：请检查 API Key 是否正确")
                    print("  [错误] API 鉴权失败（401）：请检查 API Key 是否正确", flush=True)
                    return None, None
                if resp.status_code == 402:
                    logger.error("API 余额不足（402）：请前往 https://platform.deepseek.com 充值")
                    print("  [错误] API 余额不足（402）", flush=True)
                    return None, None
                if resp.status_code == 429:
                    wait = base_delay * (2 ** (attempt - 1))
                    logger.warning("触发限流（429），%.1fs 后重试 [%d/%d]", wait, attempt, self.max_retries)
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    wait = base_delay * (2 ** (attempt - 1))
                    logger.warning("服务端错误（%d），%.1fs 后重试 [%d/%d]", resp.status_code, wait, attempt, self.max_retries)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                usage = data.get("usage", None)

                if usage:
                    logger.debug(
                        "API 调用成功: %d 输入 + %d 输出 tokens",
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                    )
                return content, usage

            except httpx.TimeoutException:
                wait = base_delay * (2 ** (attempt - 1))
                logger.warning("请求超时，%.1fs 后重试 [%d/%d]", wait, attempt, self.max_retries)
                time.sleep(wait)
                last_error = None
                continue
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = base_delay * (2 ** (attempt - 1))
                    logger.warning("请求异常: %s，%.1fs 后重试 [%d/%d]", e, wait, attempt, self.max_retries)
                    time.sleep(wait)
                else:
                    logger.error("请求最终失败: %s", e)
                    return None, None

        logger.error("重试 %d 次后仍然失败", self.max_retries)
        return None, None

    # ----------------------------------------------------------
    # 响应解析
    # ----------------------------------------------------------

    @staticmethod
    @staticmethod
    def _extract_json(text):
        """从 LLM 响应中提取 JSON

        策略：先在全文找最外层的 { ... } 尝试解析，
              失败后回退到代码块提取。
        """
        text = text.strip()
        # 1. 尝试直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 2. 在全文找到最外层的 { ... } 提取
        brace_start = text.find("{")
        if brace_start >= 0:
            depth = 0
            for i in range(brace_start, len(text)):
                ch = text[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[brace_start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            pass
                        break
        # 3. 尝试从 markdown 代码块提取（回退）
        for marker in ("```json", "```"):
            start = text.find(marker)
            if start >= 0:
                start = text.index("\n", start) + 1
                end = text.find("```", start)
                if end > start:
                    return json.loads(text[start:end].strip())
        raise json.JSONDecodeError("无法从响应中提取 JSON", text, 0)

    @staticmethod
    def _convert_ids_to_int(data, fields):
        """将指定字段中的元素转为 int

        处理 LLM 可能输出字符串 ID（如 ["114", "115"]）的情况。
        """
        for field in fields:
            values = data.get(field, [])
            if values and isinstance(values[0], str):
                data[field] = [int(v) for v in values]
        return data

    # ----------------------------------------------------------
    # Pydantic 校验
    # ----------------------------------------------------------

    def _validate_guide(self, data):
        """通过 Pydantic HeroGuide 校验攻略数据"""
        from src.data.models import HeroGuide

        try:
            obj = HeroGuide.model_validate(data)
            return obj.model_dump(mode="json")
        except Exception as e:
            logger.error("攻略 Pydantic 校验失败 hero_id=%s: %s", data.get("hero_id"), e)
            return None

    def _validate_synergy(self, data):
        """通过 Pydantic SynergyScore 校验相性数据"""
        from src.data.models import SynergyScore

        try:
            obj = SynergyScore.model_validate(data)
            return obj.model_dump(mode="json")
        except Exception as e:
            logger.error("相性 Pydantic 校验失败 %s <-> %s: %s",
                         data.get("hero_a_id"), data.get("hero_b_id"), e)
            return None

    # ----------------------------------------------------------
    # Prompt 构建
    # ----------------------------------------------------------

    def _build_guide_prompt(self, hero):
        """构建武将攻略的 user prompt（对齐 hero_guide.md）"""
        skills_formatted = "\n".join(
            f"  - {s['name']}: {s['description']}"
            for s in hero.get("skills", [])
        )
        return (
            f"武将 ID: {hero['id']}\n"
            f"名称: {hero['name']}\n"
            f"称号: {hero.get('title', '')}\n"
            f"势力: {hero.get('faction', '')}\n"
            f"定位: {hero.get('position', '')}\n"
            f"体力上限: {hero.get('max_hp', 4)}\n"
            f"手牌上限: {hero.get('max_hand', 4)}\n"
            f"性别: {hero.get('gender', '男')}\n"
            f"难度: {hero.get('difficulty', 3)}/5\n"
            f"技能:\n{skills_formatted}"
        )

    def _build_synergy_prompt(self, hero_a, hero_b):
        """构建相性评分的 user prompt

        格式对齐 docs/prompts/synergy_score.md 的 Input Specification：
            英雄ID | 英雄名 | 体力上限 | 定位 | 技能列表（含触发时机、效果描述、限制条件）
        技能列表包含 name、description 和 settlement（结算详情）。
        """
        def _fmt_skills(h):
            skills = h.get("skills", [])
            if not skills:
                return "无技能"
            parts = []
            for s in skills:
                desc = f"{s['name']}: {s['description']}"
                settlement = s.get("settlement", "")
                if settlement:
                    desc += f"（结算：{settlement}）"
                parts.append(desc)
            return "；".join(parts)

        def _fmt(h):
            return (
                f"{h['id']} | {h['name']} | {h.get('max_hp', 4)}"
                f" | {h.get('position', '未知')} | {_fmt_skills(h)}"
            )

        return (
            f"=== 武将 A ===\n{_fmt(hero_a)}\n\n"
            f"=== 武将 B ===\n{_fmt(hero_b)}"
        )

    # ----------------------------------------------------------
    # 业务生成方法
    # ----------------------------------------------------------

    def generate_guide(self, hero):
        """为单个武将生成攻略

        Returns:
            (validated_data, token_usage) 二元组
            - validated_data: 经过 Pydantic 校验的攻略 dict，失败为 None
            - token_usage: {"prompt_tokens": N, "completion_tokens": N} 或 None
        """
        system_prompt = load_prompt(GUIDE_PROMPT_FILE)
        if not system_prompt:
            logger.error("攻略 prompt 文件为空")
            return None, None

        user_prompt = self._build_guide_prompt(hero)
        content, usage = self._call_llm(system_prompt, user_prompt)
        if not content:
            if content is None:
                logger.error("攻略 API 调用失败: %s", hero["name"])
            else:
                logger.error("攻略 API 返回空内容: %s", hero["name"])
            return None, usage

        try:
            data = self._extract_json(content)
        except json.JSONDecodeError as e:
            logger.error('攻略 JSON 解析失败 %s (完整响应):\n%s\n', hero["name"], content)
            return None, usage

        data["hero_id"] = hero["id"]
        data = self._convert_ids_to_int(data, ["counters", "synergizes_with"])
        data.setdefault("key_points", [])
        data.setdefault("counters", [])
        data.setdefault("synergizes_with", [])
        data.setdefault("description", "")
        data.setdefault("tips_for_beginners", "")
        data["last_updated"] = datetime.now().strftime("%Y-%m-%d")

        validated = self._validate_guide(data)
        if validated:
            logger.info("攻略生成成功: %s", hero["name"])
            return validated, usage
        else:
            logger.warning("攻略校验失败: %s", hero["name"])
            return None, usage

    def generate_synergy(self, hero_a, hero_b):
        """为两个武将生成相性评分

        Returns:
            (validated_data, token_usage) 二元组
        """
        system_prompt = load_prompt(SYNERGY_PROMPT_FILE)
        if not system_prompt:
            logger.error("相性 prompt 文件为空")
            return None, None

        user_prompt = self._build_synergy_prompt(hero_a, hero_b)
        content, usage = self._call_llm(system_prompt, user_prompt)
        if not content:
            if content is None:
                logger.error("相性 API 调用失败: %s <-> %s", hero_a["name"], hero_b["name"])
            else:
                logger.error("相性 API 返回空内容: %s <-> %s", hero_a["name"], hero_b["name"])
            return None, usage

        try:
            data = self._extract_json(content)
        except json.JSONDecodeError as e:
            logger.error('相性 JSON 解析失败 %s <-> %s (完整响应):\n%s\n', hero_a["name"], hero_b["name"], content)
            return None, usage

        data["hero_a_id"] = hero_a["id"]
        data["hero_b_id"] = hero_b["id"]
        # 兼容旧 prompt 中的 combat_synergy 字段
        if "combat_synergy" in data and "combo_ceiling" not in data:
            data["combo_ceiling"] = data.pop("combat_synergy")
        data.setdefault("synergy_rating", "C")
        data.setdefault("combo_ceiling", 5)
        data.setdefault("combo_stability", 5)
        data.setdefault("adaptability", 5)
        data.setdefault("description", "")

        validated = self._validate_synergy(data)
        if validated:
            logger.info("相性生成成功: %s <-> %s", hero_a["name"], hero_b["name"])
            return validated, usage
        else:
            logger.warning("相性校验失败 %s <-> %s", hero_a["name"], hero_b["name"])
            return None, usage


# ============================================================
# 辅助函数
# ============================================================

def load_heroes(filepath=DEFAULT_HEROES_FILE):
    """加载武将数据"""
    path = Path(filepath)
    if not path.exists():
        logger.error("武将数据文件不存在: %s", path)
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def estimate_cost(hero_count, mode, model=DEFAULT_MODEL):
    """估算 token 消耗和费用（RMB）

    Args:
        hero_count: 武将数量
        mode: "guide" 或 "synergy"
        model: 模型名称，仅用于显示

    Returns:
        包含 items, estimated_tokens, estimated_cost_cny 等字段的 dict
    """
    if mode == "guide":
        est_input_per = 800     # 每个攻略约 800 输入 tokens（prompt + 武将信息）
        est_output_per = 700    # 每个攻略约 700 输出 tokens
        total = hero_count
    else:
        est_input_per = 600     # 每对相性约 600 输入 tokens
        est_output_per = 200    # 每对相性约 200 输出 tokens
        total = hero_count * (hero_count - 1) // 2

    total_input = total * est_input_per
    total_output = total * est_output_per
    estimated_tokens = total_input + total_output
    cost = _estimate_cost(total_input, total_output)

    return {
        "mode": mode,
        "model": model,
        "items": total,
        "estimated_tokens": estimated_tokens,
        "estimated_input_tokens": total_input,
        "estimated_output_tokens": total_output,
        "estimated_cost_cny": cost,
    }


def _save_json(filepath, data):
    """安全写入 JSON 文件（原子写入：先写 .tmp 再 rename）"""
    filepath = Path(filepath)
    filepath.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = filepath.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(filepath)
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

    guide_path = Path(args.guides_file)
    synergy_path = Path(args.synergies_file)

    # ============================================================
    # 加载已有数据（断点续传）
    # ============================================================
    existing_guides = {}
    existing_synergy_list = []
    existing_synergy_keys = set()

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

    # Token 累计统计
    total_prompt_tokens = 0
    total_completion_tokens = 0

    def _accumulate_usage(usage):
        nonlocal total_prompt_tokens, total_completion_tokens
        if usage:
            total_prompt_tokens += usage.get("prompt_tokens", 0)
            total_completion_tokens += usage.get("completion_tokens", 0)

    # ============================================================
    # 生成攻略
    # ============================================================
    if args.guide:
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
            print(f"  [{i}/{total_heroes}] {hero_name}...", flush=True)
            result, usage = generator.generate_guide(hero)
            _accumulate_usage(usage)

            if result:
                new_guides.append(result)
                print("    OK", flush=True)
            else:
                print("    FAIL", flush=True)

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

    # ============================================================
    # 生成相性评分
    # ============================================================
    if args.synergy:
        total_pairs = len(heroes) * (len(heroes) - 1) // 2
        sep_line = "=" * 55
        model_name = api_config["model"]
        print(f"\n{sep_line}")
        print(f"  生成相性评分 -- {model_name} ({total_pairs:,} 对)")
        print(f"{sep_line}")

        new_synergies = []
        processed = 0
        skipped = 0
        failed = 0

        for i in range(len(heroes)):
            for j in range(i + 1, len(heroes)):
                ha, hb = heroes[i], heroes[j]
                processed += 1
                key = tuple(sorted([ha["id"], hb["id"]]))

                if key in existing_synergy_keys:
                    skipped += 1
                    continue

                print(f"  进度: {processed}/{total_pairs}  ", end="\r", flush=True)

                result, usage = generator.generate_synergy(ha, hb)
                _accumulate_usage(usage)

                if result:
                    score = result.get("score", 0)
                    if score >= args.score_threshold:
                        new_synergies.append(result)
                        existing_synergy_keys.add(key)
                else:
                    failed += 1

                # 批量保存
                if new_synergies and len(new_synergies) % SYNERGY_BATCH_SAVE_INTERVAL == 0:
                    all_synergies = existing_synergy_list + new_synergies
                    _save_json(synergy_path, all_synergies)

        # 最终保存
        if new_synergies:
            all_synergies = existing_synergy_list + new_synergies
            _save_json(synergy_path, all_synergies)
            logger.info("相性已保存: %s (%d 条)", synergy_path, len(all_synergies))

        synergy_count = len(existing_synergy_list) + len(new_synergies)
        print(f"\n  相性完成: 新增 {len(new_synergies)} 对，skip {skipped} 对，共 {synergy_count} 对")
        if failed > 0:
            print(f"  失败: {failed} 对")
        if not new_synergies and synergy_count == 0:
            print("  未生成任何相性评分，请检查 API Key 和网络连接")

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

    print(f"\n  全部完成！\n")


if __name__ == "__main__":
    main()