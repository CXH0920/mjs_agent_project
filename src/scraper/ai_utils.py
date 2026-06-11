"""
名将杀 Agent - AI 批量生成工具模块

提供 AI 批量生成中共享的工具函数和常量。
消除 ai_batch.py <-> ai_guide.py / ai_synergy.py 之间的循环导入。
"""

from __future__ import annotations

import json
import logging
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
# 工具函数
# ============================================================

def load_prompt(filepath: str | Path) -> str:
    """加载 prompt 模板文件"""
    path = Path(filepath)
    if not path.exists():
        logger.warning("Prompt 文件不存在: %s", path)
        return ""
    return path.read_text(encoding="utf-8")


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
