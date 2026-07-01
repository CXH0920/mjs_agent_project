"""
名将杀 Agent - AI 批量生成工具模块

提供 AI 批量生成中共享的工具函数和常量。
消除 ai_batch.py <-> ai_guide.py / ai_synergy.py 之间的循环导入，
以及 ai_generator.py <-> ai_playwright.py 之间的代码重复。
"""

from __future__ import annotations

import json
import logging
import re
import traceback
from pathlib import Path

from src.config.env import (
    DEFAULT_MODEL,
    PRICE_INPUT_PER_M,
    PRICE_OUTPUT_PER_M,
)

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

# 批量保存间隔
GUIDE_BATCH_SAVE_INTERVAL = 10
SYNERGY_BATCH_SAVE_INTERVAL = 20


# ============================================================
# Prompt 加载
# ============================================================

def load_prompt(filepath: str | Path) -> str:
    """加载 prompt 模板文件"""
    path = Path(filepath)
    if not path.exists():
        logger.warning("Prompt 文件不存在: %s", path)
        return ""
    return path.read_text(encoding="utf-8")


# ============================================================
# 成本估算
# ============================================================

def _estimate_cost(tokens_input: int, tokens_output: int) -> float:
    """根据 DeepSeek v4-pro 定价估算费用（RMB）"""
    cost = (
        tokens_input * PRICE_INPUT_PER_M / 1_000_000
        + tokens_output * PRICE_OUTPUT_PER_M / 1_000_000
    )
    return round(cost, 4)


def estimate_cost(hero_count: int, mode: str, model: str | None = None) -> dict:
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


# ============================================================
# Prompt 构建（共享函数，消除 AIBatchGenerator 和 PlaywrightGenerator 的重复）
# ============================================================


def build_guide_prompt(hero: dict) -> str:
    """构建单个武将的攻略 prompt（含武将 ID，兼容 API 和 Browser 双模式）"""
    lines = [f"武将ID: {hero.get('id', 0)}"]
    lines.append(f"武将: {hero.get('name', '')}")
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


def build_synergy_prompt(hero_a: dict, hero_b: dict) -> str:
    """构建武将对的相性评分 prompt（含武将 ID，兼容 API 和 Browser 双模式）"""
    def hero_block(label: str, h: dict) -> list[str]:
        lines = [f"## {label}: {h.get('name', '')} (ID={h.get('id', 0)})"]
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

def load_heroes(filepath: str | Path = "") -> list[dict]:
    """从 JSON 文件加载武将数据"""
    path = Path(filepath) if filepath else Path()
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


def _save_json(filepath: str | Path, data: list) -> None:
    """原子写入 JSON 文件"""
    path = Path(filepath)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)
    logger.debug("已保存 %d 条到 %s", len(data), filepath)


# ============================================================
# JSON 提取（从 AI 回复文本中提取 JSON）
# ============================================================


def _repair_strings(s: str) -> str:
    """修复 JSON 字符串值内的字面换行和未转义引号"""
    result = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == '\\' and in_string:
            result.append(c)
            if i + 1 < len(s):
                result.append(s[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c == '"':
            in_string = not in_string
            result.append(c)
            i += 1
            continue
        if in_string and c in '\r\n':
            result.append('\\n')
            i += 1
            continue
        result.append(c)
        i += 1
    return ''.join(result)


def _raw_parse(s: str) -> dict | None:
    """用 raw_decode 解析 JSON 字符串，容忍尾部多余字符"""
    try:
        decoder = json.JSONDecoder()
        obj, _ = decoder.raw_decode(s)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _try_extract(candidates: list[str]) -> dict | None:
    """遍历候选字符串，依次尝试直接解析和修复后解析"""
    for c in candidates:
        result = _raw_parse(c)
        if result:
            return result
        repaired = _repair_strings(c)
        if repaired != c:
            result = _raw_parse(repaired)
            if result:
                return result
    return None


def extract_json(text: str) -> dict:
    """从 AI 回复文本中提取 JSON（4 种回退策略）

    1. 直接全文解析（raw_decode 容忍尾部多余字符）
    2. 从 ```json 或 ``` 代码块提取
    3. 通过 --- 分隔线提取最后一段
    4. 找到第一个 { 到最后一个 }

    Raises:
        ValueError: 无法从文本中提取有效 JSON
    """
    text = text.strip()

    # 1. 直接全文
    result = _try_extract([text])
    if result:
        return result

    # 2. 从 ```json 或 ``` 代码块提取
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        result = _try_extract([m.group(1).strip()])
        if result:
            return result

    # 3. 通过 --- 分隔线提取最后一段
    last_sep = text.rfind("\n---\n")
    if last_sep < 0:
        last_sep = text.rfind("\n---")
    if last_sep >= 0:
        result = _try_extract([text[last_sep + 5:].strip()])
        if result:
            return result

    # 4. 找到第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        result = _try_extract([text[start:end + 1]])
        if result:
            return result

    raise ValueError(f"无法从响应中提取 JSON:\n{text[:500]}")


# ============================================================
# 数据类型转换
# ============================================================


def convert_ids_to_int(data: dict, fields: list[str]) -> dict:
    """将指定字段中的 ID 元素统一转为 int"""
    for field in fields:
        if field in data and isinstance(data[field], list):
            original = data[field]
            try:
                data[field] = [int(v) for v in data[field]]
            except (ValueError, TypeError) as e:
                logger.warning("字段 %s 转 int 失败: %s, 原始值: %s", field, e, original)
    return data


# ============================================================
# Pydantic 校验
# ============================================================


def validate_guide(data: dict) -> dict | None:
    """通过 Pydantic HeroGuide 模型校验攻略数据"""
    try:
        from src.data.models import HeroGuide
        validated = HeroGuide.model_validate(data)
        logger.debug("HeroGuide 校验通过")
        return validated.model_dump(mode="json")
    except Exception as e:
        logger.error("HeroGuide Pydantic 校验失败: %s", e)
        logger.debug("异常 traceback:\n%s", traceback.format_exc())
        return None


def validate_synergy(data: dict) -> dict | None:
    """通过 Pydantic SynergyScore 模型校验相性数据"""
    try:
        from src.data.models import SynergyScore
        validated = SynergyScore.model_validate(data)
        logger.debug("SynergyScore 校验通过")
        return validated.model_dump(mode="json")
    except Exception as e:
        logger.error("SynergyScore Pydantic 校验失败: %s", e)
        logger.debug("异常 traceback:\n%s", traceback.format_exc())
        return None
