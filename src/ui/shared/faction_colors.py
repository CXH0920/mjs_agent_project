"""势力配色与筛选展示顺序读取、校验和展示缓存。

配置文件为数组结构（faction + color），数组位置即筛选界面的势力展示顺序。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path

from src.config.env import BUNDLE_ROOT

logger = logging.getLogger(__name__)

FACTION_COLORS_FILE = BUNDLE_ROOT / "config" / "faction_colors.json"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_FACTION_COLORS: dict[str, str] = {
    "秦": "#8B4513", "汉": "#B22222", "楚": "#2F4F4F", "赵": "#556B2F",
    "魏": "#800020", "燕": "#6A0DAD", "齐": "#1B7A3D", "韩": "#CD853F",
    "孙吴": "#4169E1", "蜀": "#228B22", "曹魏": "#800020", "群雄": "#8B0000",
    "晋": "#4A6741", "新朝": "#B8860B",
}

_faction_colors_cache: dict[str, str] | None = None


def load_faction_colors(path: Path = FACTION_COLORS_FILE) -> dict[str, str]:
    """读取并规范化已保存的势力颜色，字典顺序即配置的展示顺序，失败时返回空字典。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("读取势力配色失败: %s", exc)
        return {}
    if not isinstance(data, list):
        logger.warning("势力配色格式无效：根节点不是数组")
        return {}
    colors: dict[str, str] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get("faction")
        value = entry.get("color")
        if (
            isinstance(name, str) and name
            and isinstance(value, str) and HEX_COLOR_RE.fullmatch(value)
        ):
            colors[name] = value.upper()
    return colors


def get_faction_colors() -> dict[str, str]:
    """获取用于界面展示的势力颜色，失败时使用内建兜底色。"""
    global _faction_colors_cache
    if _faction_colors_cache is None:
        _faction_colors_cache = load_faction_colors() or dict(DEFAULT_FACTION_COLORS)
    return _faction_colors_cache


def sort_factions_by_config(factions: Iterable[str]) -> list[str]:
    """按配置文件中的势力顺序排序；配置外的势力按码点序追加尾部。"""
    ordered = get_faction_colors()
    pending = set(factions)
    known = [name for name in ordered if name in pending]
    unknown = sorted(name for name in pending if name not in ordered)
    return known + unknown


def reload_faction_colors() -> dict[str, str]:
    """清除展示缓存并重新读取势力颜色。"""
    global _faction_colors_cache
    _faction_colors_cache = None
    return get_faction_colors()
