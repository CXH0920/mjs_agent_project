"""RAG 攻略语料注入集成测试。

模型/向量检索通过 FakeRetriever 隔离，避免测试加载 bge 模型；
语料加载（load_all_blocks）走真实 data/rag_corpus 数据。
"""
import os

import pytest

from src.scraper.ai import rag_prompt
from src.scraper.ai.prompt_utils import build_guide_prompt
from src.rag import config as rag_config


class FakeRetriever:
    def __init__(self):
        self.hero_blocks_calls = []
        self.search_calls = []

    def hero_blocks(self, name):
        self.hero_blocks_calls.append(name)
        return [{"block_id": "hero_1_skill_1", "text": "技能描述内容", "metadata": {"kind": "hero"}}]

    def search(self, query, heroes=None, top_k=None):
        self.search_calls.append(query)
        return [{"block_id": "faq_1", "text": "FAQ 裁定内容", "metadata": {"kind": "faq"}}]


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
    assert "[hero_1_skill_1]" in ctx
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
    assert "[hero_1_skill_1]" in prompt


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