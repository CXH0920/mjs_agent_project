"""官网手牌库（卡牌百科）抓取清洗与逐卡 diff 基元。

- 数据源：/shoupaiku/ 页面引用的 ``spk.<hash>.js``（内嵌 49 张基础牌），
  发现机制与武将 mjbk chunk 同构但页面不同；
- 哈希口径：name/card_type/card_desc/card_detail 四件套。card_amount 官网没有，
  img_url/story_source/design_idea/display_priority/status 本地不存，均不入哈希；
- 基线快照与变更记录的持久化见 src/data/card_sync_store.py。
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import logging
import re
import unicodedata
from typing import Any

from src.scraper.official_source.crawler import clean_html, fetch_all_cards_raw

logger = logging.getLogger(__name__)

# 哈希覆盖的官网字段（与 card_field_diff_summary 共用口径）
CARD_HASH_FIELDS = ("name", "card_type", "card_desc", "card_detail")
CARD_FIELD_LABELS = {"name": "名称", "card_type": "类型", "card_desc": "描述", "card_detail": "结算详解"}
SUMMARY_LINE_LIMIT = 120


def normalize_text(value: Any) -> str:
    """规范化官网文本：去标签/HTML 解码/去空白/全半角统一（与武将侧同口径）。"""
    return unicodedata.normalize("NFKC", clean_html(value)).strip()


def clean_card_detail(html_text: str | None) -> str:
    """card_detail 去 HTML：块级标签转换行保留分段结构（存盘格式）。

    哈希比对不受影响：normalize_text 会把换行归一成空格，存盘文本与官网原文
    归一后一致。
    """
    if not html_text:
        return ""
    text = str(html_text)
    text = re.sub(r"<(br\s*/?|/p|/li|/div|/ul|/ol)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_module.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def fetch_official_cards() -> list[dict] | None:
    """获取官网手牌库全部卡牌原始记录；失败返回 None（不中断检查）。"""
    try:
        raw_list = fetch_all_cards_raw()
    except Exception:
        logger.exception("官网卡牌数据获取失败")
        return None
    logger.info("官网卡牌原始数据: %d 条", len(raw_list))
    return raw_list


def card_content_hash(card: dict) -> str:
    """基于官网字段计算卡牌内容哈希（不含本地扩展字段）。"""
    payload = {key: normalize_text(card.get(key, "")) for key in CARD_HASH_FIELDS}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def build_card_snapshot(cards: list[dict]) -> dict[str, dict]:
    """将卡牌列表转为 {id: {name, hash}} 快照结构（id 统一字符串，与本地对齐）。"""
    snapshot = {}
    for card in cards:
        card_id = card.get("id")
        if card_id is None:
            continue
        snapshot[str(card_id)] = {"name": str(card.get("name", "")), "hash": card_content_hash(card)}
    return snapshot


def _id_sort_key(card_id: str):
    """快照键为数字字符串，按数值排序展示；非数字兜底排尾部按字典序。"""
    try:
        return (0, int(card_id), "")
    except ValueError:
        return (1, 0, card_id)


def diff_cards(current: dict[str, dict], baseline: dict[str, dict]) -> dict[str, list[dict]]:
    """对比当前与基线快照，返回 {added, modified, removed} 卡牌清单（id 为字符串）。"""
    current_ids = set(current)
    baseline_ids = set(baseline)

    def entry(card_id: str, source: dict[str, dict]) -> dict:
        return {"id": card_id, "name": source[card_id]["name"]}

    added = sorted(current_ids - baseline_ids, key=_id_sort_key)
    removed = sorted(baseline_ids - current_ids, key=_id_sort_key)
    modified = sorted(
        (
            card_id
            for card_id in current_ids & baseline_ids
            if current[card_id]["hash"] != baseline[card_id]["hash"]
        ),
        key=_id_sort_key,
    )
    return {
        "added": [entry(card_id, current) for card_id in added],
        "modified": [entry(card_id, current) for card_id in modified],
        "removed": [entry(card_id, baseline) for card_id in removed],
    }


def _truncate(value, limit: int = SUMMARY_LINE_LIMIT) -> str:
    text = str(value or "").strip()
    return text[:limit] + "…" if len(text) > limit else text


def card_field_diff_summary(local: dict, official: dict) -> list[str]:
    """对比本地与官网卡牌的四件套字段，返回中文差异摘要；无差异返回空列表。"""
    lines: list[str] = []
    for key in CARD_HASH_FIELDS:
        local_text = normalize_text(local.get(key, ""))
        official_text = normalize_text(official.get(key, ""))
        if local_text != official_text:
            lines.append(
                f"{CARD_FIELD_LABELS[key]}：本地「{_truncate(local_text)}」→ 官网「{_truncate(official_text)}」"
            )
    return lines


def format_card_full_text(card: dict) -> str:
    """将卡牌字段格式化为只读全文（用于确认对话框的本地/官网对比）。"""
    if not card:
        return ""
    lines = [
        f"名称：{normalize_text(card.get('name', ''))}",
        f"类型：{normalize_text(card.get('card_type', ''))}",
        f"描述：{normalize_text(card.get('card_desc', ''))}",
        "",
        "结算详解：",
        normalize_text(card.get("card_detail", "")) or "（无）",
    ]
    return "\n".join(lines)
