"""势力配色读取、校验和展示缓存。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)

FACTION_COLORS_FILE = PROJECT_ROOT / "config" / "faction_colors.json"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_FACTION_COLORS: dict[str, str] = {
    "秦": "#8B4513", "汉": "#B22222", "楚": "#2F4F4F", "赵": "#556B2F",
    "魏": "#800020", "燕": "#6A0DAD", "齐": "#1B7A3D", "韩": "#CD853F",
    "孙吴": "#4169E1", "蜀": "#228B22", "曹魏": "#800020", "群雄": "#8B0000",
    "晋": "#4A6741", "新朝": "#B8860B",
}

_faction_colors_cache: dict[str, str] | None = None


def load_faction_colors(path: Path = FACTION_COLORS_FILE) -> dict[str, str]:
    """读取并规范化已保存的势力颜色，读取失败时返回空字典。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("读取势力配色失败: %s", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("势力配色格式无效：根节点不是对象")
        return {}
    return {
        str(name): value.upper()
        for name, value in data.items()
        if isinstance(name, str) and isinstance(value, str) and HEX_COLOR_RE.fullmatch(value)
    }


def get_faction_colors() -> dict[str, str]:
    """获取用于界面展示的势力颜色，失败时使用内建兜底色。"""
    global _faction_colors_cache
    if _faction_colors_cache is None:
        _faction_colors_cache = load_faction_colors() or dict(DEFAULT_FACTION_COLORS)
    return _faction_colors_cache


def reload_faction_colors() -> dict[str, str]:
    """清除展示缓存并重新读取势力颜色。"""
    global _faction_colors_cache
    _faction_colors_cache = None
    return get_faction_colors()
