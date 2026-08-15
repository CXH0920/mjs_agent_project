"""RAG 攻略语料注入集成测试。

模型/向量检索通过 FakeRetriever 隔离，避免测试加载 bge 模型；
语料加载（load_all_blocks）走真实 data/rag_corpus 数据。
"""
import os

import pytest

from src.scraper.ai import rag_prompt
from src.scraper.ai.prompt_utils import build_guide_prompt, build_synergy_prompt
from src.rag import config as rag_config


class FakeRetriever:
    def __init__(self):
        self.hero_blocks_calls = []
        self.search_calls = []

    def hero_blocks(self, name):
        self.hero_blocks_calls.append(name)
        return [{"block_id": f"hero_{name}_skill_1", "text": f"{name} 技能描述内容", "metadata": {"kind": "hero"}}]

    def search(self, query, heroes=None, top_k=None):
        self.search_calls.append((query, heroes))
        return [
            {"block_id": "faq_1", "text": "FAQ 裁定内容", "metadata": {"kind": "faq"}},
            {"block_id": "rule_1", "text": "规则内容", "metadata": {"kind": "rule"}},
        ]


@pytest.fixture
def fake_retriever(monkeypatch):
    fake = FakeRetriever()
    monkeypatch.setattr(rag_prompt, "_get_retriever", lambda: fake)
    monkeypatch.setattr(rag_config, "TOP_K", 12)
    monkeypatch.setattr(rag_config, "RAG_PROMPT_CHARS", 6000)
    return fake


def test_rag_disabled_via_env(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")
    assert rag_prompt._rag_enabled() is False
    hero = {"id": 1, "name": "测试英雄", "skills": []}
    prompt = build_guide_prompt(hero)
    assert "RAG 官方规则语料" not in prompt


def test_rag_context_injected(fake_retriever):
    hero = {"id": 1, "name": "测试英雄", "faction": "测试", "skills": []}
    ctx = rag_prompt.build_rag_context(hero)
    assert "RAG 官方规则语料" in ctx
    assert "[hero_测试英雄_skill_1]" in ctx
    assert "[faq_1]" in ctx
    assert "技能描述内容" in ctx


def test_rag_context_budget_truncation(fake_retriever, monkeypatch):
    monkeypatch.setattr(rag_config, "RAG_PROMPT_CHARS", 30)
    hero = {"id": 1, "name": "测试英雄", "skills": []}
    ctx = rag_prompt.build_rag_context(hero)
    assert len(ctx) <= 80  # 标题 + 预算截断后的正文（允许块前缀开销）


def test_build_guide_prompt_contains_rag(fake_retriever):
    hero = {"id": 1, "name": "测试英雄", "faction": "测试", "position": "攻击",
            "max_hp": 4, "max_hand": 4, "skills": [{"name": "技能", "description": "描述"}]}
    prompt = build_guide_prompt(hero)
    assert "测试英雄" in prompt
    assert "RAG 官方规则语料" in prompt
    assert "[hero_测试英雄_skill_1]" in prompt


def test_rag_context_fallback_on_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("index missing")

    monkeypatch.setattr(rag_prompt, "_get_retriever", boom)
    hero = {"id": 1, "name": "测试英雄", "skills": []}
    ctx = rag_prompt.build_rag_context(hero)
    assert ctx == ""


def test_rag_corpus_loaded():
    """真实语料可加载（无需模型）。"""
    from src.rag.indexer import load_all_blocks
    blocks = load_all_blocks()
    assert len(blocks) > 1000
    kinds = {meta["kind"] for _, _, meta in blocks}
    assert "classification" in kinds
    assert "modify" in kinds


def test_rag_config_defaults():
    assert rag_config.RAG_ENABLED is True
    assert rag_config.CORPUS_DIR.name == "rag_corpus"
    assert rag_config.CHROMA_DIR.name == "chroma"
    assert rag_config.RAG_SYNERGY_PROMPT_CHARS == 6000


def test_synergy_rag_context_injected(fake_retriever):
    ha = {"id": 1, "name": "甲", "skills": [{"name": "技能A"}]}
    hb = {"id": 2, "name": "乙", "skills": [{"name": "技能B"}]}
    ctx = rag_prompt.build_synergy_rag_context(ha, hb)
    assert "RAG 官方规则语料" in ctx
    assert "[hero_甲_skill_1]" in ctx
    assert "[hero_乙_skill_1]" in ctx
    assert "[faq_1]" in ctx
    assert "[rule_1]" in ctx


def test_synergy_rag_disabled_via_env(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "false")
    ha = {"id": 1, "name": "甲", "skills": []}
    hb = {"id": 2, "name": "乙", "skills": []}
    assert rag_prompt.build_synergy_rag_context(ha, hb) == ""
    prompt = build_synergy_prompt(ha, hb)
    assert "RAG 官方规则语料" not in prompt


def test_synergy_rag_budget_truncation(fake_retriever, monkeypatch):
    monkeypatch.setattr(rag_config, "RAG_SYNERGY_PROMPT_CHARS", 30)
    ha = {"id": 1, "name": "甲", "skills": []}
    hb = {"id": 2, "name": "乙", "skills": []}
    ctx = rag_prompt.build_synergy_rag_context(ha, hb)
    assert len(ctx) <= 80  # 标题 + 预算截断后的正文（允许块前缀开销）


def test_synergy_rag_fallback_on_exception(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("index missing")

    rag_prompt.degraded_reason = None
    monkeypatch.setattr(rag_prompt, "_get_retriever", boom)
    ha = {"id": 1, "name": "甲", "skills": []}
    hb = {"id": 2, "name": "乙", "skills": []}
    ctx = rag_prompt.build_synergy_rag_context(ha, hb)
    assert ctx == ""
    assert rag_prompt.take_degraded_reason() == "RuntimeError"
    assert rag_prompt.take_degraded_reason() is None


def test_build_synergy_prompt_contains_rag(fake_retriever):
    ha = {"id": 1, "name": "甲", "faction": "魏", "position": "攻击",
          "max_hp": 4, "max_hand": 4, "skills": [{"name": "技能A", "description": "描述A"}]}
    hb = {"id": 2, "name": "乙", "faction": "蜀", "position": "防御",
          "max_hp": 5, "max_hand": 4, "skills": [{"name": "技能B", "description": "描述B"}]}
    prompt = build_synergy_prompt(ha, hb)
    assert "甲" in prompt and "乙" in prompt
    assert "RAG 官方规则语料" in prompt
    assert "[hero_甲_skill_1]" in prompt
    assert "[hero_乙_skill_1]" in prompt

def test_synergy_rag_excludes_unrelated_hero_blocks(monkeypatch):
    """跨类检索结果中的其他武将块（hero/分类）不得注入相性 prompt。"""

    class _PollutedRetriever:
        def hero_blocks(self, name):
            return [{"block_id": f"hero_{name}_skill_1", "text": f"{name} 技能描述内容", "metadata": {"kind": "hero"}}]

        def search(self, query, heroes=None, top_k=None):
            return [
                {"block_id": "faq_1", "text": "FAQ 裁定内容", "metadata": {"kind": "faq"}},
                {"block_id": "rule_1", "text": "规则内容", "metadata": {"kind": "rule"}},
                {"block_id": "hero_丙_skill_1", "text": "丙技能描述", "metadata": {"kind": "hero", "hero": "丙"}},
                {"block_id": "classification_丙", "text": "丙分类", "metadata": {"kind": "classification", "hero": "丙"}},
            ]

    monkeypatch.setattr(rag_prompt, "_get_retriever", lambda: _PollutedRetriever())
    ha = {"id": 1, "name": "甲", "skills": [{"name": "技能A"}]}
    hb = {"id": 2, "name": "乙", "skills": [{"name": "技能B"}]}
    ctx = rag_prompt.build_synergy_rag_context(ha, hb)
    assert "[faq_1]" in ctx
    assert "[rule_1]" in ctx
    assert "[hero_丙_skill_1]" not in ctx
    assert "[classification_丙]" not in ctx


def test_synergy_rag_query_uses_mechanism_keywords(fake_retriever):
    """跨类查询应包含双方武将名/技能名与技能描述中的机制词，且不含泛分析词。"""
    ha = {"id": 1, "name": "甲", "skills": [{"name": "技能A", "description": "出牌阶段限1次，弃置1张牌然后摸1张牌"}]}
    hb = {"id": 2, "name": "乙", "skills": [{"name": "技能B", "description": "当你获得牌时，你可以打出1张杀"}]}
    rag_prompt.build_synergy_rag_context(ha, hb)
    query = fake_retriever.search_calls[-1][0]
    assert "甲" in query and "乙" in query
    assert "技能A" in query and "技能B" in query
    assert "弃置" in query and "获得" in query and "打出" in query
    assert "联动" not in query
