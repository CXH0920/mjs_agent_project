# -*- coding: utf-8 -*-
"""武将分类 LLM 建议归类测试：prompt 构造、LLM 返回解析与过滤。

覆盖模块级函数 suggest_hero_categories（不实例化 widget，无需 QApplication）。
"""

from __future__ import annotations

import json

from src.data.hero_classification_repository import ClassificationCategory
from src.business.maintenance.classification_suggest import suggest_hero_categories


class FakeGenerator:
    """模拟 AIBatchGenerator：按 payload 返回固定 JSON，或按 content 返回原始文本，或 None。"""

    def __init__(self, payload: dict | None, *, content: str | None = None):
        self._payload = payload
        self._content = content
        self.calls: list[list[dict]] = []

    def complete(self, messages, temperature=0.7):
        self.calls.append(messages)
        if self._content is not None:
            return {"content": self._content, "finish_reason": "stop", "usage": {}}
        if self._payload is None:
            return None
        return {"content": json.dumps(self._payload, ensure_ascii=False),
                "finish_reason": "stop", "usage": {}}


def _categories():
    return [
        ClassificationCategory(name="高爆发型", core_features="高输出快攻"),
        ClassificationCategory(name="卖血型", core_features="被动收益"),
        ClassificationCategory(name="辅助型", core_features="提供手牌与增益"),
    ]


def _skills():
    return "技能一：造成伤害　结算：受到伤害后摸牌\n技能二：令一名角色摸牌"


def test_suggest_returns_filtered_categories():
    """LLM 返回含未知分类名时，只保留清单内的、去重保序。"""
    fake = FakeGenerator({"categories": ["高爆发型", "不存在的", "辅助型"]})
    result = suggest_hero_categories("测试武将", _skills(), "主公", _categories(), fake)
    assert result == ["高爆发型", "辅助型"]
    assert len(fake.calls) == 1


def test_suggest_dedup_preserves_order():
    fake = FakeGenerator({"categories": ["辅助型", "高爆发型", "辅助型", "高爆发型"]})
    result = suggest_hero_categories("测试武将", _skills(), "主公", _categories(), fake)
    assert result == ["辅助型", "高爆发型"]


def test_suggest_empty_when_all_unknown():
    fake = FakeGenerator({"categories": ["不存在的甲", "不存在的乙"]})
    result = suggest_hero_categories("测试武将", _skills(), "主公", _categories(), fake)
    assert result == []


def test_suggest_none_on_api_failure():
    fake = FakeGenerator(None)
    assert suggest_hero_categories("测试武将", _skills(), "主公", _categories(), fake) is None


def test_suggest_none_on_bad_json():
    fake = FakeGenerator(None, content="这不是JSON")
    assert suggest_hero_categories("测试武将", _skills(), "主公", _categories(), fake) is None


def test_suggest_none_on_empty_content():
    fake = FakeGenerator(None, content="")
    assert suggest_hero_categories("测试武将", _skills(), "主公", _categories(), fake) is None


def test_suggest_none_when_no_skills():
    """无技能文本时不调 LLM，直接返回 None。"""
    fake = FakeGenerator({"categories": ["高爆发型"]})
    assert suggest_hero_categories("测试武将", "", "主公", _categories(), fake) is None
    assert len(fake.calls) == 0


def test_suggest_none_when_no_categories():
    """无分类清单时不调 LLM，直接返回 None。"""
    fake = FakeGenerator({"categories": ["高爆发型"]})
    assert suggest_hero_categories("测试武将", _skills(), "主公", [], fake) is None
    assert len(fake.calls) == 0


def test_suggest_none_when_categories_not_list():
    """LLM 返回的 categories 不是数组时返回 None。"""
    fake = FakeGenerator(None, content='{"categories": "高爆发型"}')
    assert suggest_hero_categories("测试武将", _skills(), "主公", _categories(), fake) is None


def test_suggest_prompt_contains_skills_and_categories():
    """prompt 含武将名、定位、技能原文、分类名与核心特征。"""
    fake = FakeGenerator({"categories": ["高爆发型"]})
    suggest_hero_categories("张三", "奇策：造成伤害", "主公", _categories(), fake)
    messages = fake.calls[0]
    assert messages[0]["role"] == "system"
    user_content = messages[1]["content"]
    assert "张三" in user_content
    assert "主公" in user_content
    assert "奇策：造成伤害" in user_content
    assert "高爆发型" in user_content
    assert "高输出快攻" in user_content  # core_features 入 prompt
    assert "提供手牌与增益" in user_content
