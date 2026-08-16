# -*- coding: utf-8 -*-
"""知识库维护审计服务（audit_service.py）

为「知识库维护 → 语料状态」提供人工维护提示的结构化审计：
- audit_summary(): 汇总审计条目（AuditIssue 列表，供 UI 渲染与跳转）；
- collect_*(): 共享校验收集函数，scripts/rag_audit.py 与 audit_summary 共用，
  避免脚本侧与 UI 侧各维护一份校验逻辑。

校验规则（花色/点数/总张数/装备件数/细分类型/距离修正）以 src/data 各
repository 常量为单一事实源，不再在 UI/脚本层重复定义。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from src.business.rag.refinement_service import list_pending
from src.data.card_points_repository import (
    EXPECTED_TOTAL_CARDS,
    VALID_POINTS,
    VALID_SUITS,
)
from src.data.equip_attrs_repository import (
    EXPECTED_EQUIP_COUNT,
    VALID_DISTANCE_MODS,
    VALID_SUBTYPES,
)

CORPUS_DIR = "data/rag_corpus"


@dataclass(frozen=True)
class AuditIssue:
    """人工维护提示条目（结构化，供 UI 渲染跳转按钮）。

    - kind: 问题类型标识（unclassified_hero / unknown_hero / missing_settlement
      / bad_card_suit / bad_card_point / bad_equip_attrs 等）；
    - target_tab: 跳转目标一级页签名（空表示无跳转）；
    - target: 定位数据，按 kind 解释（未归类武将名列表 / 专属牌 (category, name)
      / 牌名列表）。
    """

    kind: str
    message: str
    severity: str = "warning"
    target_tab: str = ""
    target: object = None


def format_audit_issues(issues: list[AuditIssue]) -> list[str]:
    """结构化审计条目 → 纯文本列表（兼容旧消费方/测试）。"""
    return [issue.message for issue in issues]


# ---------------------------------------------------------------------------
# 共享校验收集（audit_summary 与 scripts/rag_audit.py 共用）
# ---------------------------------------------------------------------------


def collect_card_points(payload: object) -> list[dict]:
    """卡牌点数源校验，返回 [{kind, message}]。

    kind: structure=缺 cards 数组 / total=总张数不符 / bad_suit=异常花色
    / bad_point=异常点数。
    """
    cards = payload.get("cards") if isinstance(payload, dict) else None
    if not isinstance(cards, list):
        return [{"kind": "structure", "message": "data/card_points.json 结构异常（缺少 cards 数组）"}]
    issues = []
    bad_suits = sorted({c.get("name", "?") for c in cards if c.get("suit") not in VALID_SUITS})
    bad_points = sorted({c.get("name", "?") for c in cards if c.get("point") not in VALID_POINTS})
    total = sum(int(c.get("count", 1) or 1) for c in cards)
    if total != EXPECTED_TOTAL_CARDS:
        issues.append({
            "kind": "total",
            "message": f"卡牌点数张数 {total} != 期望 {EXPECTED_TOTAL_CARDS}",
        })
    if bad_suits:
        issues.append({
            "kind": "bad_suit",
            "message": f"卡牌点数异常花色 {len(bad_suits)} 张：{'、'.join(bad_suits[:6])}",
        })
    if bad_points:
        issues.append({
            "kind": "bad_point",
            "message": f"卡牌点数异常点数 {len(bad_points)} 张：{'、'.join(bad_points[:6])}",
        })
    return issues


def collect_equip_attrs(equips: object) -> list[dict]:
    """装备属性源校验，返回 [{kind, message}]。

    kind: structure=非数组 / count=件数不符 / bad_subtype=细分类型异常
    / bad_distance=距离修正异常。
    """
    if not isinstance(equips, list):
        return [{"kind": "structure", "message": "data/equip_attrs.json 结构异常（应为数组）"}]
    issues = []
    if len(equips) != EXPECTED_EQUIP_COUNT:
        issues.append({
            "kind": "count",
            "message": f"装备属性件数 {len(equips)} != 期望 {EXPECTED_EQUIP_COUNT}",
        })
    for item in equips:
        if item.get("subtype") not in VALID_SUBTYPES:
            issues.append({
                "kind": "bad_subtype",
                "message": f"装备 {item.get('name', '?')} 细分类型异常：{item.get('subtype')!r}",
            })
        if item.get("distance_mod") not in VALID_DISTANCE_MODS:
            issues.append({
                "kind": "bad_distance",
                "message": f"装备 {item.get('name', '?')} 距离修正异常：{item.get('distance_mod')!r}",
            })
    return issues


def collect_missing_settlements(specials: list) -> list[dict]:
    """专属牌/战法牌缺结算详情的条目（死士为非实体牌标记，豁免）。"""
    return [
        item for item in specials
        if item.get("category") in ("专属牌", "专属战法牌")
        and not item.get("settlement") and item.get("name") not in ("死士",)
    ]


def collect_unclassified(hero_names: set, classification: object) -> list[str]:
    """heroes.json 中未在 hero_categories 归类的武将名（升序）。"""
    classified = set(classification.get("hero_categories", {})) if isinstance(classification, dict) else set()
    return sorted(hero_names - classified)


def collect_unknown_heroes(specials: list, hero_names: set) -> list[str]:
    """专属牌 hero 字段拆分出的未知武将名（泛指/括号注释跳过），升序返回。"""
    unknown = set()
    for item in specials:
        hero = item.get("hero", "")
        if not hero:
            continue
        for _name in re.split(r"[\u3001,?]", hero):
            _name = re.split(r"[(\uff08]", _name, 1)[0].strip()
            if not _name or _name in ("通用", "—", "众多武将") or _name.endswith("等"):
                continue
            if _name not in hero_names:
                unknown.add(_name)
    return sorted(unknown)


# ---------------------------------------------------------------------------
# 汇总审计（UI 用）
# ---------------------------------------------------------------------------


def audit_summary(root: Path, pending_refinement: list | None = None) -> list[AuditIssue]:
    """返回人工维护提示清单（结构化条目；空列表表示无问题）。

    pending_refinement: 调用方已计算好的待精化清单；为 None 时内部读取语料
    （UI 工作台同一轮刷新已算过，传入可避免重复读文件）。
    """
    issues: list[AuditIssue] = []
    heroes_path = root / "data" / "heroes.json"
    classification_path = root / "data" / "hero_classification.json"
    special_path = root / "data" / "special_cards.json"
    try:
        heroes = json.loads(heroes_path.read_text(encoding="utf-8"))
        hero_names = {item.get("name") for item in heroes}
    except (OSError, json.JSONDecodeError):
        hero_names = set()
    try:
        classification = json.loads(classification_path.read_text(encoding="utf-8"))
        unclassified = collect_unclassified(hero_names, classification)
        if unclassified:
            issues.append(AuditIssue(
                kind="unclassified_hero",
                message=f"未归类武将 {len(unclassified)} 人（请补充 data/hero_classification.json）",
                target_tab="武将分类维护",
                target=unclassified,
            ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="classification_unreadable",
            message="data/hero_classification.json 缺失或无法解析",
            target_tab="武将分类维护",
        ))
    try:
        specials = json.loads(special_path.read_text(encoding="utf-8"))
        unknown = collect_unknown_heroes(specials, hero_names)
        if unknown:
            target = next(
                ((str(it.get("category", "")), str(it.get("name", ""))) for it in specials
                 if it.get("hero") and any(n in it["hero"] for n in unknown)),
                None,
            )
            issues.append(AuditIssue(
                kind="unknown_hero",
                message=f"专属牌引用未知武将 {len(unknown)} 人：{'、'.join(unknown[:8])}",
                target_tab="专属牌维护",
                target=target,
            ))
        missing_items = collect_missing_settlements(specials)
        if missing_items:
            names = [str(it.get("name", "")) for it in missing_items]
            first = missing_items[0]
            issues.append(AuditIssue(
                kind="missing_settlement",
                message=f"专属牌/战法牌缺结算详情 {len(missing_items)} 个：{'、'.join(names[:8])}",
                target_tab="专属牌维护",
                target=(str(first.get("category", "")), str(first.get("name", ""))),
            ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="specials_unreadable",
            message="data/special_cards.json 缺失或无法解析",
            target_tab="专属牌维护",
        ))
    # 卡牌点数源校验（data/card_points.json，原 xlsx sheet1 + 判定规则）
    points_path = root / "data" / "card_points.json"
    try:
        payload = json.loads(points_path.read_text(encoding="utf-8"))
        for item in collect_card_points(payload):
            if item["kind"] == "structure":
                issues.append(AuditIssue(
                    kind="card_points_structure", message=item["message"],
                    target_tab="卡牌点数维护",
                ))
            elif item["kind"] == "total":
                issues.append(AuditIssue(
                    kind="card_points_total", message=item["message"],
                    target_tab="卡牌点数维护",
                ))
            elif item["kind"] == "bad_suit":
                issues.append(AuditIssue(
                    kind="bad_card_suit", message=item["message"],
                    target_tab="卡牌点数维护",
                ))
            elif item["kind"] == "bad_point":
                issues.append(AuditIssue(
                    kind="bad_card_point", message=item["message"],
                    target_tab="卡牌点数维护",
                ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="card_points_unreadable",
            message="data/card_points.json 缺失或无法解析",
            target_tab="卡牌点数维护",
        ))
    # 装备属性源校验（data/equip_attrs.json，原 xlsx sheet2）
    equips_path = root / "data" / "equip_attrs.json"
    try:
        equips = json.loads(equips_path.read_text(encoding="utf-8"))
        for item in collect_equip_attrs(equips):
            if item["kind"] == "structure":
                issues.append(AuditIssue(
                    kind="equip_attrs_structure", message=item["message"],
                    target_tab="装备属性维护",
                ))
            elif item["kind"] == "count":
                issues.append(AuditIssue(
                    kind="equip_attrs_count", message=item["message"],
                    target_tab="装备属性维护",
                ))
            else:
                issues.append(AuditIssue(
                    kind="bad_equip_attrs", message=item["message"],
                    target_tab="装备属性维护",
                ))
    except (OSError, json.JSONDecodeError):
        issues.append(AuditIssue(
            kind="equip_attrs_unreadable",
            message="data/equip_attrs.json 缺失或无法解析",
            target_tab="装备属性维护",
        ))
    # 索引精化待办：无 curated 且索引字段为空的语料块（语料未构建时清单为空，静默跳过）
    if pending_refinement is None:
        pending_refinement = list_pending(root / CORPUS_DIR)
    if pending_refinement:
        issues.insert(0, AuditIssue(
            kind="pending_refinement",
            message=f"索引字段待精化 {len(pending_refinement)} 块（卡牌/武将语料）",
            severity="warning",
        ))
    return issues
