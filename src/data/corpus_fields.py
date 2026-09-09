# -*- coding: utf-8 -*-
"""语料索引字段契约：精化服务（业务层）与构建合并（脚本层）共用的单一来源。

字段三层体系（2026-09 重构）：
- 逻辑层 timing/trigger_condition/target/special_rules：进 embedding，参与 LLM/人工精化；
- 辅助层 keywords/related：规则自动生成的只读字段，不属于精化范围；
- 原文层 description/settlement：一字不改，作精化回指锚点。
此前 refinement_service 与 rag_curated 各自定义 INDEX_FIELDS（4 字段 vs 5 字段）已漂移，
本模块为唯一权威定义。
"""

# 卡牌块可精化字段
CARD_FIELDS = ("timing", "trigger_condition")
# 武将技能块可精化字段（target 为构建既有抽取字段，special_rules 为新增结算边界字段）
HERO_FIELDS = ("timing", "trigger_condition", "target", "special_rules")
# curated 字典持有的字段并集（merge 回填时缺键 no-op）
CURATED_FIELDS = HERO_FIELDS


def fields_for(kind: str) -> tuple[str, ...]:
    """按块类型返回可精化字段集（kind: "card" | "skill"）。"""
    return CARD_FIELDS if kind == "card" else HERO_FIELDS
