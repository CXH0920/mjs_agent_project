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

from src.config.env import PROJECT_ROOT, PROVIDER_PRESETS, resolve_api_config
from src.data.corpus_fields import CARD_FIELDS, HERO_FIELDS, fields_for
from src.data.json_repository import atomic_write_json
from src.scraper.ai.api_generator import AIBatchGenerator
from src.scraper.ai.json_extract import extract_json

logger = logging.getLogger(__name__)

DEFAULT_CORPUS_DIR = PROJECT_ROOT / "data" / "rag_corpus"
REFINABLE_FILES = ("卡牌RAG语料.json", "武将RAG语料.json")

# 待精化判定字段：与块类型可精化字段集一致（special_rules 已于批次 3 纳入判定）
PENDING_FIELDS: dict[str, tuple[str, ...]] = {
    "card": CARD_FIELDS,
    "skill": HERO_FIELDS,
}

REFINEMENT_SYSTEM_PROMPT = (
    "你是名将杀（三国杀类）游戏的规则索引解析器。根据给定的卡牌或武将技能原文，输出 JSON，字段为：\n"
    "- timing（时机锚点，字符串数组）：只写技能生效的时间点。优先从词表选：回合开始时/回合结束时/"
    "每轮开始时/每轮结束时/出牌阶段/出牌阶段开始时/出牌阶段结束时/摸牌阶段开始时/摸牌阶段结束时/"
    "弃牌阶段开始时/弃牌阶段结束时/受到伤害后/造成伤害后/获得牌后/打出牌时/使用牌时/成为目标时/"
    "阵亡时/登场时/常驻被动。词表外用原文短语，不得改变原意、不得省略主语。\n"
    "- trigger_condition（触发情形，字符串数组）：一行写一个完整触发情形——时机+前提+次数限制合成"
    "一句独立可读的话，多个前提用「且」，选择关系用「或」；技能有多个独立触发器时分多行；"
    "常驻技能写「常驻生效」，有生效门槛写「常驻生效：<门槛>」。效果属性（如「生效2次」）不要写在这里。\n"
    "- target（影响对象，字符串数组）：每行一个对象短语，如「一名其他角色」「所有角色」。\n"
    "- special_rules（结算边界，字符串数组）：每行一条，只允许压缩结算说明中已有的边界规则，"
    "禁止添加原文没有的解释。\n"
    "示例——原文「其他角色的回合结束时，若其在本回合没有发动过技能，且未拥有此技能，则你可以令其获得此技能」："
    "正确 trigger_condition=[\"其他角色的回合结束时，且该角色本回合没有发动过技能、且其未拥有此技能\"]；"
    "错误（禁止碎片化）=[\"其他角色的回合结束时\",\"若其在本回合没有发动过技能\",\"未拥有此技能\"]。\n"
    "没有对应内容时返回空数组。只输出 JSON，不要解释。"
)


@dataclass
class PendingBlock:
    """语料块视图：待精化 / 已精化（curated）/ 普通块共用。

    - pending：无 curated 且待精化判定字段（PENDING_FIELDS）任一为空；
    - curated：有 curated（method/updated_at 标记精化来源与时间）；
    - normal：无 curated 且判定字段全非空（构建规则抽取已填满）。
    fields 按块类型字段集（fields_for）构建，keywords/related 为自动只读字段不在其中。
    """

    corpus: str
    block_id: str
    name: str
    kind: str  # "card" | "skill"
    text: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    method: str = ""  # curated 块来源（"llm" | "manual"），其余为空
    updated_at: str = ""  # curated 块更新时间（ISO 日期）


@dataclass
class RefinementUpdate:
    """精化结果：逻辑层字段 + 来源标记（卡牌块 target/special_rules 恒为空）。"""

    timing: list[str] = field(default_factory=list)
    trigger_condition: list[str] = field(default_factory=list)
    target: list[str] = field(default_factory=list)
    special_rules: list[str] = field(default_factory=list)
    method: str = "manual"
    updated_at: str = ""


def scan_blocks(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> dict[str, list[PendingBlock]]:
    """一次扫描卡牌/武将语料，按精化状态三分类（pending/curated/normal）。

    - pending：无 curated 且待精化判定字段（PENDING_FIELDS）任一为空；
    - curated：有 curated（已处理，fields 以 curated 内容为权威）；
    - normal：无 curated 且判定字段全非空（规则抽取已填满）。
    武将语料只取技能块（跳过 overview 块）。
    """
    result: dict[str, list[PendingBlock]] = {"pending": [], "curated": [], "normal": []}
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
        kind = "card" if fname.startswith("卡牌") else "skill"
        fields = fields_for(kind)
        for block in data:
            if not isinstance(block, dict) or not block.get("block_id"):
                continue
            if kind == "skill" and not block.get("skill"):
                continue  # 跳过 overview 块
            curated = block.get("curated")
            if isinstance(curated, dict):
                # curated 已有键为权威（含显式空数组，尊重人工"无内容"决定）；
                # 缺键回退读顶层——存量 curated 无 target/special_rules 键，
                # 顶层 target 是构建抽取的真实值，不回退会在保存时被静默清空
                values = {f: list(curated[f]) if f in curated else list(block.get(f) or [])
                          for f in fields}
                result["curated"].append(_to_block(kind, block, values,
                                                   method=str(curated.get("method") or ""),
                                                   updated_at=str(curated.get("updated_at") or "")))
                continue
            values = {f: list(block.get(f) or []) for f in fields}
            missing = [f for f in PENDING_FIELDS[kind] if not values[f]]
            if missing:
                result["pending"].append(_to_block(kind, block, values))
            else:
                result["normal"].append(_to_block(kind, block, values))
    return result


def _to_block(kind: str, block: dict, fields: dict[str, list[str]],
              method: str = "", updated_at: str = "") -> PendingBlock:
    """从语料块构建 PendingBlock 视图（名称/原文/缺失字段统一推导）。"""
    name = str(block.get("skill") or block.get("name") or block.get("card")
               or block.get("hero") or "")
    if not name and kind == "card":
        # 卡牌块无名称字段：block_id 形如 card_{id}_{卡名}，取卡名段（与 indexer 派生一致）
        name = str(block["block_id"]).split("_", 2)[-1]
    if not name:
        name = str(block["block_id"])
    missing = [f for f in PENDING_FIELDS[kind] if not fields[f]]
    return PendingBlock(
        corpus="卡牌RAG语料.json" if kind == "card" else "武将RAG语料.json",
        block_id=str(block["block_id"]),
        name=name,
        kind=kind,
        text=_block_text(block),
        fields=fields,
        missing=missing,
        method=method,
        updated_at=updated_at,
    )


def list_pending(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> list[PendingBlock]:
    """返回无 curated 且待精化判定字段任一为空的块（武将语料只取技能块）。"""
    return scan_blocks(corpus_dir)["pending"]


def list_curated(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> list[PendingBlock]:
    """返回已有 curated 的块（已精化，fields 以 curated 内容为权威）。"""
    return scan_blocks(corpus_dir)["curated"]


def list_normal(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> list[PendingBlock]:
    """返回无 curated 且判定字段全非空的块（构建规则抽取已填满）。"""
    return scan_blocks(corpus_dir)["normal"]


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
    if block.kind == "card":
        kind_text = "卡牌（只需填写 timing 和 trigger_condition，target/special_rules 返回空数组）"
    else:
        kind_text = "武将技能"
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
        target=norm("target", 8),
        special_rules=norm("special_rules", 8),
        method=method,
        updated_at=date.today().isoformat(),
    )


def apply_curated(corpus_dir: Path, updates: dict[str, RefinementUpdate], fname: str) -> int:
    """写回精化结果：更新块顶层逻辑层字段并新增 curated 字段（原子保存）。

    curated 字典整体替换为块类型字段集 + 来源标记——keywords/related 已退出
    精化模型（自动只读字段），不再写入 curated。
    """
    path = corpus_dir / fname
    if not path.exists():
        raise FileNotFoundError(f"语料文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"语料格式异常（非数组）: {fname}")
    kind = "card" if fname.startswith("卡牌") else "skill"
    fields = fields_for(kind)
    by_id = {str(block.get("block_id")): block for block in data if isinstance(block, dict)}
    applied = 0
    for block_id, update in updates.items():
        block = by_id.get(block_id)
        if block is None:
            raise ValueError(f"block_id 不存在: {block_id}（{fname}）")
        for f in fields:
            block[f] = list(getattr(update, f))
        block["curated"] = {
            **{f: list(getattr(update, f)) for f in fields},
            "method": update.method,
            "updated_at": update.updated_at or date.today().isoformat(),
        }
        applied += 1
    _atomic_json_write(path, data)
    return applied


def clear_curated(corpus_dir: Path, block_id: str, fname: str) -> bool:
    """删除块的 curated 字段（取消精化），原子保存。

    返回 False 表示该块本就没有 curated（无需处理）；块不存在抛 ValueError。
    """
    path = corpus_dir / fname
    if not path.exists():
        raise FileNotFoundError(f"语料文件不存在: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"语料格式异常（非数组）: {fname}")
    by_id = {str(block.get("block_id")): block for block in data if isinstance(block, dict)}
    block = by_id.get(block_id)
    if block is None:
        raise ValueError(f"block_id 不存在: {block_id}（{fname}）")
    if "curated" not in block:
        return False
    del block["curated"]
    _atomic_json_write(path, data)
    return True


def _atomic_json_write(path: Path, data: object) -> None:
    """以 UTF-8、LF、indent=1（与 build 脚本一致）原子保存 JSON。"""
    atomic_write_json(path, data, indent=1)


def build_generator(profile_name: str | None = None) -> AIBatchGenerator | None:
    """按指定 API 档案（无则默认档案 → 旧链兜底）构造生成器；供应商语义缺 Key 时返回 None。"""
    config = resolve_api_config(profile_name)
    provider = config.get("provider", "deepseek")
    if PROVIDER_PRESETS.get(provider, {}).get("requires_key", True) and not config.get("api_key"):
        logger.warning("未配置 API Key，无法生成 LLM 建议")
        return None
    return AIBatchGenerator(
        api_key=config["api_key"],
        api_url=config.get("api_url"),
        model=config.get("model"),
        provider=provider,
    )