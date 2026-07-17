"""
名将杀 Agent - AI 批量生成工具模块

提供 AI 批量生成中共享的工具函数和常量。
保留核心 IO 和数据校验函数，prompt 构建和 JSON 提取已拆分出去。
"""

from __future__ import annotations

import json
import logging
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

# ============================================================
# 常量
# ============================================================

# 批量保存间隔
GUIDE_BATCH_SAVE_INTERVAL = 10
SYNERGY_BATCH_SAVE_INTERVAL = 20


# ============================================================
# 数据加载与保存
# ============================================================


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
