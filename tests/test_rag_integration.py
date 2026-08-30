"""RAG 攻略语料注入集成测试。

模型/向量检索通过 FakeRetriever 隔离，避免测试加载 bge 模型；
语料加载（load_all_blocks）走真实 data/rag_corpus 数据。
"""

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
    if not any(rag_config.CORPUS_DIR.glob("*.json")):
        pytest.skip("rag_corpus 语料未入库（CI），跳过真实语料加载回归")
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
    """双 query 融合：query1 含武将名，query2 含技能名+机制词；不含泛分析词。"""
    ha = {"id": 1, "name": "甲", "skills": [{"name": "技能A", "description": "出牌阶段限1次，弃置1张牌然后摸1张牌"}]}
    hb = {"id": 2, "name": "乙", "skills": [{"name": "技能B", "description": "当你获得牌时，你可以打出1张杀"}]}
    rag_prompt.build_synergy_rag_context(ha, hb)
    assert len(fake_retriever.search_calls) == 2            # 确认是双 query
    all_calls = " ".join(q for q, _ in fake_retriever.search_calls)
    assert "甲" in all_calls and "乙" in all_calls         # query1 武将名
    assert "技能A" in all_calls and "技能B" in all_calls   # query2 技能名
    assert "弃置" in all_calls and "获得" in all_calls      # query2 机制词
    assert "联动" not in all_calls


def test_build_guide_prompt_includes_settlement(monkeypatch):
    """技能含 settlement 时应注入到攻略 prompt（结算规则是关键细节分析的唯一直接来源）。"""
    monkeypatch.setenv("RAG_ENABLED", "false")  # 隔离 RAG，只测 settlement 直接注入
    hero = {"id": 1, "name": "甲",
            "skills": [{"name": "观星", "description": "控制牌堆",
                        "settlement": "结算时机：摸牌阶段开始时"}]}
    prompt = build_guide_prompt(hero)
    assert "结算时机：摸牌阶段开始时" in prompt


def test_build_guide_prompt_omits_empty_settlement(monkeypatch):
    """技能无 settlement 时不输出结算占位，避免噪声。"""
    monkeypatch.setenv("RAG_ENABLED", "false")  # 隔离 RAG，避免语料块含"结算："干扰
    hero = {"id": 1, "name": "甲",
            "skills": [{"name": "观星", "description": "控制牌堆"}]}
    prompt = build_guide_prompt(hero)
    assert "结算：" not in prompt


def test_guide_rag_search_uses_unfiltered_rich_query(fake_retriever):
    """攻略跨类检索不带 heroes 过滤，query 含武将名+技能名+机制词。"""
    hero = {"id": 1, "name": "甲",
            "skills": [{"name": "技能A", "description": "出牌阶段限1次，弃置1张牌然后摸1张牌"}]}
    rag_prompt.build_rag_context(hero)
    query, heroes = fake_retriever.search_calls[-1]
    assert heroes is None                 # 不带 hero 过滤 → 允许跨类召回
    assert "甲" in query and "技能A" in query
    assert "弃置" in query               # 机制词来自技能描述


def test_guide_rag_excludes_unrelated_hero_blocks(monkeypatch):
    """攻略跨类检索结果中的其他武将块（hero/分类）不得注入 prompt。"""
    class _PollutedRetriever:
        def hero_blocks(self, name):
            return [{"block_id": f"hero_{name}_skill_1", "text": f"{name} 技能",
                     "metadata": {"kind": "hero", "hero": name}}]

        def search(self, query, heroes=None, top_k=None):
            return [
                {"block_id": "faq_1", "text": "FAQ", "metadata": {"kind": "faq"}},
                {"block_id": "card_藤甲", "text": "藤甲效果", "metadata": {"kind": "card"}},
                {"block_id": "hero_丙_skill_1", "text": "丙技能",
                 "metadata": {"kind": "hero", "hero": "丙"}},
                {"block_id": "classification_丙", "text": "丙分类",
                 "metadata": {"kind": "classification", "hero": "丙"}},
            ]

    monkeypatch.setattr(rag_prompt, "_get_retriever", lambda: _PollutedRetriever())
    hero = {"id": 1, "name": "甲", "skills": []}
    ctx = rag_prompt.build_rag_context(hero)
    assert "[faq_1]" in ctx
    assert "[card_藤甲]" in ctx          # 跨类卡牌块保留
    assert "[hero_丙_skill_1]" not in ctx
    assert "[classification_丙]" not in ctx


def test_format_rag_chunks_core_before_extra():
    """两段式预算：核心块优先于补充块，预算充足时两者都进。"""
    core = [{"block_id": "c1", "text": "核心块内容"}]
    extra = [{"block_id": "e1", "text": "补充块内容"}]
    ctx = rag_prompt._format_rag_chunks(core, extra, 10000)
    assert "[c1]" in ctx and "[e1]" in ctx
    assert ctx.index("[c1]") < ctx.index("[e1]")  # core 排在 extra 前


def test_guide_prompt_injects_core_rules_when_rag_disabled(monkeypatch):
    """无 RAG 时攻略 prompt 应注入基础规则参考区块。"""
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setattr("src.scraper.ai.prompt_utils.load_core_rules",
                        lambda: "## 基础规则参考\n测试规则内容")
    hero = {"id": 1, "name": "甲", "skills": []}
    prompt = build_guide_prompt(hero)
    assert "基础规则参考" in prompt
    assert "测试规则内容" in prompt


def test_synergy_prompt_injects_core_rules_when_rag_disabled(monkeypatch):
    """无 RAG 时相性 prompt 应注入基础规则参考区块。"""
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setattr("src.scraper.ai.prompt_utils.load_core_rules",
                        lambda: "## 基础规则参考\n测试规则内容")
    ha = {"id": 1, "name": "甲", "skills": []}
    hb = {"id": 2, "name": "乙", "skills": []}
    prompt = build_synergy_prompt(ha, hb)
    assert "基础规则参考" in prompt
    assert "测试规则内容" in prompt


def test_has_required_guide_fields():
    """攻略必填字段预检：key_points / description 缺一即失败，描述须达到最小长度。"""
    from src.scraper.ai.utils import has_required_guide_fields
    assert has_required_guide_fields({"key_points": [], "description": "测试描述" * 100}) is True
    assert has_required_guide_fields({"description": "测试描述" * 100}) is False
    assert has_required_guide_fields({"key_points": []}) is False
    assert has_required_guide_fields({"key_points": [], "description": "太短"}) is False


def test_has_required_synergy_fields():
    """相性必填字段预检：score / description 缺一即失败，描述须达到最小长度。"""
    from src.scraper.ai.utils import has_required_synergy_fields
    assert has_required_synergy_fields({"score": 5, "description": "测试描述" * 100}) is True
    assert has_required_synergy_fields({"description": "测试描述" * 100}) is False
    assert has_required_synergy_fields({"score": 5}) is False
    assert has_required_synergy_fields({"score": 5, "description": "太短"}) is False
