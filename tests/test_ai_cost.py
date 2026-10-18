"""ai_cost 成本估算门面的口径特征测试（VUL-B4-003 / TD-A7-001 修复护栏）。

估算口径（prompt_utils.estimate_item_cost）：guide 输入 RAG 2000/经典 800、
输出 500/条；synergy 输入 RAG 3500/经典 800、输出 200/条。本文件锁定门面对
use_rag 的透传与默认口径（不传 = RAG，与后端选择对话框默认勾选一致）。
"""

from __future__ import annotations

import pytest
from src.business.ai_cost import estimate_generation_cost

MODEL = "cost-test-model"


@pytest.fixture
def fixed_pricing(monkeypatch) -> None:
    """固定模型单价（输入 10 元/百万、输出 30 元/百万），隔离 api_profiles 配置。"""
    monkeypatch.setattr(
        "src.scraper.ai.prompt_utils.get_model_pricing",
        lambda model: {"input_per_million": 10.0, "output_per_million": 30.0},
    )


def test_guide_defaults_to_rag_budget(fixed_pricing) -> None:
    """guide 不传 use_rag 按 RAG 口径（后端选择对话框默认即 RAG，二者须一致）。"""
    result = estimate_generation_cost(5, "guide", model=MODEL)
    assert result["estimated_input_tokens"] == 5 * 2000
    assert result["estimated_output_tokens"] == 5 * 500


def test_guide_passes_use_rag_false_through(fixed_pricing) -> None:
    """guide 分支持 use_rag=False 须按经典口径（VUL-B4-003：此前被静默丢弃）。"""
    result = estimate_generation_cost(5, "guide", model=MODEL, use_rag=False)
    assert result["estimated_input_tokens"] == 5 * 800
    assert result["estimated_output_tokens"] == 5 * 500


def test_synergy_defaults_to_rag_budget(fixed_pricing) -> None:
    """synergy 不传 use_rag 按 RAG 口径（实施中新发现：None 透传曾 falsy 回落经典 800）。"""
    result = estimate_generation_cost(10, "synergy", model=MODEL)
    assert result["estimated_input_tokens"] == 10 * 3500
    assert result["estimated_output_tokens"] == 10 * 200


def test_synergy_classic_budget(fixed_pricing) -> None:
    result = estimate_generation_cost(10, "synergy", model=MODEL, use_rag=False)
    assert result["estimated_input_tokens"] == 10 * 800
    assert result["estimated_output_tokens"] == 10 * 200
    assert result["estimated_cost_cny"] == pytest.approx(
        (10 * 800) * 10 / 1_000_000 + (10 * 200) * 30 / 1_000_000
    )


def test_unknown_kind_raises(fixed_pricing) -> None:
    """未知 kind 显式报错，不静默降级为 synergy 口径（TD-A7-001）。"""
    with pytest.raises(ValueError, match="未知生成类型"):
        estimate_generation_cost(3, "bogus", model=MODEL)


def test_model_defaults_to_active_api_profile(monkeypatch, fixed_pricing) -> None:
    monkeypatch.setattr("src.business.ai_cost.get_api_config", lambda: {"model": "profile-model"})
    result = estimate_generation_cost(3, "synergy")
    assert result["model"] == "profile-model"
