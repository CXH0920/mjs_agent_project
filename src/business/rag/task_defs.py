# -*- coding: utf-8 -*-
"""RAG 语料任务定义（单一事实源）。

「知识库维护 → 语料状态」工作台（src/ui/maintenance/rag_maintenance_panel.py）
与 scripts/maintain_rag.py 调度脚本共用；新增/修改语料任务只需改这里。

字段：
- name: 任务名（UI 展示与 --only 过滤共用）；
- script: 生成语料的 build 脚本（scripts/ 下）；
- sources: 依赖的 T0 源文件（相对项目根，含跨语料联动文件）；
- outputs: 生成的语料 json 文件名（data/rag_corpus/ 下）；
- expected: 期望块数——int=精确匹配；"snapshot"=以快照基线只增不删；
  None=动态数量，只报不校验。
"""

from __future__ import annotations

TASKS: list[dict] = [
    {
        "name": "武将语料",
        "script": "build_rag_corpus.py",
        "sources": ["data/heroes.json", "data/cards.json", "data/mjs_adjustments.json"],
        "outputs": ["武将RAG语料.json"],
        "expected": 615,
    },
    {
        "name": "卡牌语料",
        "script": "build_card_corpus.py",
        "sources": ["data/cards.json"],
        "outputs": ["卡牌RAG语料.json"],
        "expected": 49,
    },
    {
        "name": "点数花色语料",
        "script": "build_cardpts.py",
        "sources": ["data/card_points.json"],
        "outputs": ["卡牌点数花色语料.json"],
        "expected": 49,
    },
    {
        "name": "装备属性语料",
        "script": "build_equip_attr.py",
        "sources": [
            "data/cards.json",
            "data/equip_attrs.json",
            "data/rag_corpus/卡牌RAG语料.json",  # build_equip_attr 会注入卡牌语料装备字段
        ],
        "outputs": ["装备属性语料.json"],
        "expected": 27,
    },
    {
        "name": "加强削弱语料",
        "script": "build_modify_corpus.py",
        "sources": ["data/cards.json", "data/card_annotations.json"],
        "outputs": ["加强削弱语料.json"],
        "expected": 49,
    },
    {
        "name": "元规则/术语/FAQ",
        "script": "build_rule_corpus.py",
        "sources": ["docs/元规则整理-完整版.md"],
        "outputs": ["元规则RAG语料-章节块.json", "术语表.json", "FAQ裁定块.json"],
        "expected": "snapshot",
    },
    {
        "name": "特殊机制语料",
        "script": "build_special_corpus.py",
        "sources": ["data/special_cards.json"],
        "outputs": ["特殊机制语料.json"],
        "expected": 83,
    },
    {
        "name": "武将分类语料",
        "script": "build_classification_corpus.py",
        "sources": ["data/hero_classification.json", "data/heroes.json"],
        "outputs": ["武将分类语料.json"],
        "expected": None,
    },
    {
        "name": "组合语料",
        "script": "build_combo_corpus.py",
        "sources": [
            "data/raw_guides/jinxia/combos/bilibili_videos_weijiang.csv",
            "data/raw_guides/jinxia/combos/强力组合.md",
            "data/raw_guides/jinxia/combos/巴清搭配.md",
            "data/raw_guides/jinxia/combos/平阳公主强势组合盘点.md",
            "data/raw_guides/jinxia/combos/孟尝君 + 黄月英.md",
            "data/heroes.json",
        ],
        "outputs": ["组合RAG语料.json"],
        "expected": None,
    },
    {
        "name": "武将攻略语料",
        "script": "build_guide_corpus.py",
        "sources": ["data/raw_guides/jinxia/guides/", "data/heroes.json", "data/mjs_adjustments.json"],
        "outputs": ["武将攻略RAG语料.json"],
        "expected": None,
    },
]
