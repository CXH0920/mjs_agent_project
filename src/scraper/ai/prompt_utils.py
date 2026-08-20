"""
名将杀 Agent - Prompt 加载与构建工具

提供 AI 批量生成中共享的 prompt 加载、构建和成本估算函数。
拆分自 ai_utils.py，消除 ai_generator.py <-> ai_playwright.py 之间的代码重复。
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.config.env import (
    DEFAULT_MODEL,
    get_model_pricing,
)

from src.scraper.ai.rag_prompt import build_rag_context, build_synergy_rag_context, _rag_enabled
from src.scraper.ai.rule_summary import load_core_rules

logger = logging.getLogger(__name__)


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

def _estimate_cost(tokens_input: int, tokens_output: int, model: str | None = None) -> float | None:
    """根据指定模型的版本控制价格估算费用。"""
    model = model or DEFAULT_MODEL
    pricing = get_model_pricing(model)
    if pricing is None:
        return None
    cost = (
        tokens_input * pricing["input_per_million"] / 1_000_000
        + tokens_output * pricing["output_per_million"] / 1_000_000
    )
    return round(cost, 4)


def estimate_cost(hero_count: int, mode: str, model: str | None = None, use_rag: bool = True) -> dict:
    """估算批量生成成本

    Args:
        hero_count: 武将数量
        mode: "guide" 或 "synergy"
        model: 模型名称（用于查找价格）
        use_rag: True = RAG 语料注入版本，False = 经典模式（无 RAG）

    Returns:
        dict: 成本估算结果
    """
    if model is None:
        model = DEFAULT_MODEL

    if mode == "guide":
        items = hero_count
    elif mode == "synergy":
        items = hero_count * (hero_count - 1) // 2
    else:
        raise ValueError(f"未知 mode: {mode}")

    return estimate_item_cost(items, mode, model, use_rag=use_rag)


def estimate_item_cost(
    item_count: int,
    mode: str,
    model: str | None = None,
    use_rag: bool = True,
) -> dict:
    """按实际 API 请求项数估算生成成本。

    use_rag: True = RAG 语料注入版本（输入 token 更高），False = 经典模式（无 RAG）。
    """
    if model is None:
        model = DEFAULT_MODEL

    if mode == "guide":
        input_per_item = 2000 if use_rag else 800
        output_tokens = item_count * 500
    elif mode == "synergy":
        input_per_item = 3500 if use_rag else 800
        output_tokens = item_count * 200
    else:
        raise ValueError(f"未知 mode: {mode}")

    input_tokens = item_count * input_per_item
    total_tokens = input_tokens + output_tokens
    pricing = get_model_pricing(model)
    cost_cny = _estimate_cost(input_tokens, output_tokens, model)
    message = ""
    if pricing is None:
        message = f"模型 {model} 未配置价格，无法自动估算费用"

    return {
        "mode": mode,
        "items": item_count,
        "estimated_tokens": total_tokens,
        "estimated_input_tokens": input_tokens,
        "estimated_output_tokens": output_tokens,
        "estimated_cost_cny": cost_cny,
        "model": model,
        "pricing_available": pricing is not None,
        "message": message,
    }


# ============================================================
# Prompt 构建（共享函数，消除 AIBatchGenerator 和 PlaywrightGenerator 的重复）
# ============================================================


def build_guide_prompt(hero: dict, rag_max_chars: int | None = None) -> str:
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
            line = f"  - {sk.get('name', '')}: {sk.get('description', '')}"
            settlement = sk.get('settlement', '')
            if settlement:
                line += f" ｜结算：{settlement}"
            lines.append(line)
    rag = build_rag_context(hero, max_chars=rag_max_chars)
    if rag:
        lines.extend(["", rag])
    elif not _rag_enabled():
        rules = load_core_rules()
        if rules:
            lines.extend(["", rules])
    return "\n".join(lines)


def build_synergy_prompt(hero_a: dict, hero_b: dict, rag_max_chars: int | None = None) -> str:
    """构建武将对的相性评分 prompt（含武将 ID + 可选 RAG 语料，兼容 API 和 Browser 双模式）"""
    def hero_block(label: str, h: dict) -> list[str]:
        lines = [f"## {label}: {h.get('name', '')} (ID={h.get('id', 0)})"]
        lines.append(f"  势力: {h.get('faction', '')}")
        lines.append(f"  定位: {h.get('position', '')}")
        lines.append(f"  体力/手牌: {h.get('max_hp', 4)}/{h.get('max_hand', 4)}")
        if h.get("skills"):
            lines.append("  技能:")
            for sk in h["skills"]:
                line = f"    - {sk.get('name', '')}: {sk.get('description', '')}"
                settlement = sk.get('settlement', '')
                if settlement:
                    line += f" ｜结算：{settlement}"
                lines.append(line)
        return lines

    lines = []
    lines.extend(hero_block("武将 A", hero_a))
    lines.append("")
    lines.extend(hero_block("武将 B", hero_b))
    rag = build_synergy_rag_context(hero_a, hero_b, max_chars=rag_max_chars)
    if rag:
        lines.extend(["", rag])
    elif not _rag_enabled():
        rules = load_core_rules()
        if rules:
            lines.extend(["", rules])
    return "\n".join(lines)
