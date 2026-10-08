"""
名将杀 Agent - AI 批量生成工具模块

提供 AI 批量生成中共享的工具函数和常量。
保留核心 IO 和数据校验函数，prompt 构建和 JSON 提取已拆分出去。
"""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlsplit

from src.data.hero_manager import HeroManager
from src.data.json_repository import atomic_write_json

logger = logging.getLogger(__name__)


def safe_url_origin(url: str) -> str:
    """返回不含认证、路径、查询参数和片段的 URL 来源。"""
    try:
        parsed = urlsplit(str(url))
        if not parsed.scheme or not parsed.hostname:
            return "<invalid-url>"
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme}://{host}{port}"
    except ValueError:
        return "<invalid-url>"

# ============================================================
# 常量
# ============================================================

# 批量保存间隔
GUIDE_BATCH_SAVE_INTERVAL = 10
SYNERGY_BATCH_SAVE_INTERVAL = 10


# ============================================================
# 数据加载与保存
# ============================================================


def load_heroes(filepath: str | Path = "") -> list[dict]:
    """加载并完整校验武将数据，存在错误时拒绝部分加载。"""
    path = Path(filepath) if filepath else Path()
    if not path.exists():
        logger.error("武将数据文件不存在: %s", path)
        return []

    manager = HeroManager(path)
    issues = manager.load()
    errors = [issue for issue in issues if issue.severity == "error"]
    if errors:
        logger.error("武将数据校验失败: %s (%d 项错误)", path, len(errors))
        return []

    heroes = [hero.model_dump(mode="json") for hero in manager.list_heroes()]
    logger.info("加载 %d 个武将", len(heroes))
    return heroes


def _save_json(filepath: str | Path, data: list) -> None:
    """原子写入 JSON 文件（统一委托 json_repository，跨进程写同一路径不再共用固定临时名）"""
    atomic_write_json(filepath, data)
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
                logger.warning(
                    "字段 %s 转 int 失败: %s（元素数 %d）",
                    field,
                    type(e).__name__,
                    len(original),
                )
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
        logger.error("HeroGuide Pydantic 校验失败: %s", type(e).__name__)
        return None


def validate_synergy(data: dict) -> dict | None:
    """通过 Pydantic SynergyScore 模型校验相性数据"""
    try:
        from src.data.models import SynergyScore
        validated = SynergyScore.model_validate(data)
        logger.debug("SynergyScore 校验通过")
        return validated.model_dump(mode="json")
    except Exception as e:
        logger.error("SynergyScore Pydantic 校验失败: %s", type(e).__name__)
        return None


# ============================================================
# 必填字段预检（调用方在 Pydantic 校验前快速失败）
# ID 由调用方注入，不在此检查；命中缺失可省去一次 Pydantic 异常开销
# ============================================================

# 模板占位符标记：模型偶发原样复制模板指令文本作 description 值，需拦截
_PLACEHOLDER_MARKERS = ("此处放入", "放入此字段", "保持原文不变")
# 攻略正文下限：模板要求 600-1000 字，低于此必为占位符或截断
_MIN_GUIDE_DESCRIPTION_LEN = 200
# 相性正文下限：模板要求 ≤1500 字，低于此必为占位符或截断
_MIN_SYNERGY_DESCRIPTION_LEN = 200


def has_required_guide_fields(raw: dict) -> bool:
    """攻略结果必填字段预检：key_points / description，并拦截模板占位符正文。"""
    if not all(f in raw for f in ("key_points", "description")):
        return False
    desc = raw.get("description", "")
    if not isinstance(desc, str) or len(desc) < _MIN_GUIDE_DESCRIPTION_LEN:
        return False
    return not any(marker in desc for marker in _PLACEHOLDER_MARKERS)


def has_required_synergy_fields(raw: dict) -> bool:
    """相性结果必填字段预检：score / description，并拦截模板占位符正文。"""
    if not all(f in raw for f in ("score", "description")):
        return False
    desc = raw.get("description", "")
    if not isinstance(desc, str) or len(desc) < _MIN_SYNERGY_DESCRIPTION_LEN:
        return False
    return not any(marker in desc for marker in _PLACEHOLDER_MARKERS)


# ============================================================
# 生成结果后处理（API/浏览器双生成器共用管线，P1-5 收口）
# ============================================================


def normalize_and_validate_guide(raw: dict, hero: dict, *, logger_prefix: str = "") -> dict | None:
    """攻略后处理：注入 hero_id、synergizes_with 转 int、必填预检与 Pydantic 校验。

    此前 api/browser 两生成器各抄一份逐字同构管线，新增字段需改约 6 处，
    收口为单一实现。logger_prefix 为浏览器版日志前缀（如 "[攻略] 武将名: "，
    其进度窗口解析日志行，前缀语义保留），API 版为空串。
    """
    raw["hero_id"] = hero.get("id", 0)
    convert_ids_to_int(raw, ["synergizes_with"])
    if not has_required_guide_fields(raw):
        logger.warning("%s攻略必填字段缺失: %s", logger_prefix, sorted(raw))
        return None
    result = validate_guide(raw)
    if result is None:
        logger.warning("%s攻略 Pydantic 校验失败", logger_prefix)
        logger.debug("%s攻略校验失败字段: %s", logger_prefix, sorted(raw))
        return None
    return result


def normalize_and_validate_synergy(raw: dict, hero_a: dict, hero_b: dict, *, logger_prefix: str = "") -> dict | None:
    """相性后处理：注入双武将 ID、combat_synergy 兼容 shim、必填预检与 Pydantic 校验。"""
    raw["hero_a_id"] = hero_a.get("id", 0)
    raw["hero_b_id"] = hero_b.get("id", 0)
    # 兼容旧 prompt 中的 combat_synergy 字段
    if "combat_synergy" in raw and "combo_ceiling" not in raw:
        logger.info("%s兼容字段 combat_synergy → combo_ceiling", logger_prefix)
        raw["combo_ceiling"] = raw.pop("combat_synergy")
    if not has_required_synergy_fields(raw):
        logger.warning("%s相性必填字段缺失: %s", logger_prefix, sorted(raw))
        return None
    result = validate_synergy(raw)
    if result is None:
        logger.warning("%s相性 Pydantic 校验失败", logger_prefix)
        logger.debug("%s相性校验失败字段: %s", logger_prefix, sorted(raw))
        return None
    return result
