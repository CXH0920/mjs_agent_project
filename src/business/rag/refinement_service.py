# -*- coding: utf-8 -*-
"""RAG 语料索引字段精化服务。

维护对象：卡牌RAG语料.json / 武将RAG语料.json 中「无 curated 且索引字段为空」的块；
流程：待精化清单 -> LLM 生成建议 -> 人工确认 -> apply_curated 写回（curated 分层保留，
重跑 build 脚本不覆盖精化成果）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from src.config.env import PROJECT_ROOT, get_api_config
from src.data.json_repository import atomic_write_json
from src.scraper.ai.api_generator import AIBatchGenerator
from src.scraper.ai.json_extract import extract_json

logger = logging.getLogger(__name__)

INDEX_FIELDS = ("timing", "trigger_condition", "keywords", "related")
DEFAULT_CORPUS_DIR = PROJECT_ROOT / "data" / "rag_corpus"
REFINABLE_FILES = ("卡牌RAG语料.json", "武将RAG语料.json")

REFINEMENT_SYSTEM_PROMPT = (
    "你是名将杀（三国杀类）游戏的规则索引解析器。根据给定的卡牌或武将技能原文，"
    "输出 JSON，字段为 timing（时机，字符串数组）、trigger_condition（触发条件，字符串数组）、"
    "keywords（检索关键词，字符串数组）、related（关联引用，如「卡牌:诸葛连弩」「规则:时机-回合开始」，字符串数组）。"
    "没有对应内容时返回空数组。只输出 JSON，不要解释。"
)


@dataclass
class PendingBlock:
    """待精化块：语料文件名 + 块信息 + 当前索引字段。"""

    corpus: str
    block_id: str
    name: str
    kind: str  # "card" | "skill"
    text: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


@dataclass
class RefinementUpdate:
    """精化结果：4 个索引字段 + 来源标记。"""

    timing: list[str] = field(default_factory=list)
    trigger_condition: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    method: str = "manual"
    updated_at: str = ""


def list_pending(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> list[PendingBlock]:
    """返回无 curated 且任一索引字段为空的块（武将语料只取技能块）。"""
    pending: list[PendingBlock] = []
    for fname in REFINABLE_FILES:
        path = corpus_dir / fname
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.error("精化清单读取失败 %s: %s", fname, error)
            continue
        if not isinstance(data, list):
            continue
        for block in data:
            if not isinstance(block, dict) or not block.get("block_id"):
                continue
            if fname.startswith("武将") and not block.get("skill"):
                continue  # 跳过 overview 块
            if block.get("curated"):
                continue
            values = {f: list(block.get(f) or []) for f in INDEX_FIELDS}
            missing = [f for f in INDEX_FIELDS if not values[f]]
            if not missing:
                continue
            name = str(block.get("skill") or block.get("name") or block.get("card") or block.get("hero") or block["block_id"])
            pending.append(PendingBlock(
                corpus=fname,
                block_id=str(block["block_id"]),
                name=name,
                kind="card" if fname.startswith("卡牌") else "skill",
                text=_block_text(block),
                fields=values,
                missing=missing,
            ))
    return pending


def _block_text(block: dict) -> str:
    """拼接块的原文（卡牌效果+说明 / 技能描述+结算），供 LLM 与界面展示。"""
    if block.get("effect") or block.get("effect_detail"):
        parts = [str(block.get("effect") or "")]
        if block.get("effect_detail"):
            parts.append(str(block["effect_detail"]))
        return "\n".join(parts)
    if block.get("skill"):
        parts = [f"技能：{block['skill']}"]
        if block.get("description"):
            parts.append("描述：" + str(block["description"]))
        if block.get("settlement"):
            parts.append("结算：" + str(block["settlement"]))
        return "\n".join(parts)
    return json.dumps(block, ensure_ascii=False)[:2000]


def generate_suggestions(pending: list[PendingBlock], generator) -> dict[str, RefinementUpdate]:
    """逐块调用 LLM 生成建议；单块失败跳过，返回 {block_id: RefinementUpdate}。"""
    updates: dict[str, RefinementUpdate] = {}
    for block in pending:
        suggestion = suggest_one(block, generator)
        if suggestion is not None:
            updates[block.block_id] = suggestion
    return updates


def suggest_one(block: PendingBlock, generator) -> RefinementUpdate | None:
    """单块 LLM 建议（公开接口）；API 失败/解析失败返回 None。"""
    kind_text = "卡牌" if block.kind == "card" else "武将技能"
    messages = [
        {"role": "system", "content": REFINEMENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"语料类型：{kind_text}\n名称：{block.name}\n原文：\n{block.text}"},
    ]
    try:
        response = generator.complete(messages, temperature=0.2)
    except Exception as error:
        logger.warning("精化建议请求异常 %s: %s", block.block_id, error)
        return None
    if not response:
        return None
    content = response.get("content", "")
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        data = extract_json(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return _to_update(data, method="llm")


def _to_update(data: dict, method: str) -> RefinementUpdate:
    def norm(key: str, limit: int) -> list[str]:
        value = data.get(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()][:limit]

    return RefinementUpdate(
        timing=norm("timing", 8),
        trigger_condition=norm("trigger_condition", 8),
        keywords=norm("keywords", 12),
        related=norm("related", 12),
        method=method,
        updated_at=date.today().isoformat(),
    )


def apply_curated(corpus_dir: Path, updates: dict[str, RefinementUpdate], fname: str) -> int:
    """写回精化结果：更新块顶层索引字段并新增 curated 字段（原子保存）。"""
    path = corpus_dir / fname
    if not path.exists():
        raise FileNotFoundError(f"语料文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"语料格式异常（非数组）: {fname}")
    by_id = {str(block.get("block_id")): block for block in data if isinstance(block, dict)}
    applied = 0
    for block_id, update in updates.items():
        block = by_id.get(block_id)
        if block is None:
            raise ValueError(f"block_id 不存在: {block_id}（{fname}）")
        for f in INDEX_FIELDS:
            block[f] = list(getattr(update, f))
        block["curated"] = {
            "timing": list(update.timing),
            "trigger_condition": list(update.trigger_condition),
            "keywords": list(update.keywords),
            "related": list(update.related),
            "method": update.method,
            "updated_at": update.updated_at or date.today().isoformat(),
        }
        applied += 1
    _atomic_json_write(path, data)
    return applied


def _atomic_json_write(path: Path, data: object) -> None:
    """以 UTF-8、LF、indent=1（与 build 脚本一致）原子保存 JSON。"""
    atomic_write_json(path, data, indent=1)


def build_generator() -> AIBatchGenerator | None:
    """按 config.env 构造 DeepSeek 生成器；无 API Key 时返回 None。"""
    config = get_api_config()
    if not config.get("api_key"):
        logger.warning("未配置 DEEPSEEK_API_KEY，无法生成 LLM 建议")
        return None
    return AIBatchGenerator(
        api_key=config["api_key"],
        api_url=config.get("api_url"),
        model=config.get("model"),
    )