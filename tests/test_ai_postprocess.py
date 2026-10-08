"""双生成器共享后处理管线直测（P1-5 收口）。

不经过传输层，直接验证 normalize_and_validate_guide / synergy：
ID 注入、synergizes_with 转 int、combat_synergy 兼容 shim、
必填预检（缺失/过短/占位符）与 Pydantic 校验失败路径——全部走真实模型校验。
"""

from __future__ import annotations

from src.scraper.ai.utils import (
    normalize_and_validate_guide,
    normalize_and_validate_synergy,
)

# 必填预检的正文下限是 200 字（has_required_*_fields）
_LONG_DESCRIPTION = "测试描述" * 100


def test_guide_injects_hero_id_and_converts_synergizes_with() -> None:
    raw = {"key_points": [], "description": _LONG_DESCRIPTION, "synergizes_with": ["1", "2"]}
    result = normalize_and_validate_guide(raw, {"id": 7, "name": "甲"})
    assert result is not None
    assert result["hero_id"] == 7
    assert result["synergizes_with"] == [1, 2]


def test_guide_defaults_hero_id_to_zero_when_missing() -> None:
    raw = {"key_points": [], "description": _LONG_DESCRIPTION}
    result = normalize_and_validate_guide(raw, {})
    assert result is not None
    assert result["hero_id"] == 0


def test_guide_missing_required_field_returns_none() -> None:
    assert normalize_and_validate_guide(
        {"description": _LONG_DESCRIPTION}, {"id": 7},
    ) is None


def test_guide_short_description_returns_none() -> None:
    assert normalize_and_validate_guide(
        {"key_points": [], "description": "过短"}, {"id": 7},
    ) is None


def test_guide_placeholder_description_returns_none() -> None:
    raw = {"key_points": [], "description": "此处放入" + _LONG_DESCRIPTION}
    assert normalize_and_validate_guide(raw, {"id": 7}) is None


def test_guide_pydantic_failure_returns_none() -> None:
    # synergizes_with 含不可转 int 的元素：转换失败保留原值，Pydantic list[int] 拒绝
    raw = {"key_points": [], "description": _LONG_DESCRIPTION, "synergizes_with": ["abc"]}
    assert normalize_and_validate_guide(raw, {"id": 7}) is None


def test_synergy_injects_ids_and_shims_legacy_combat_synergy() -> None:
    raw = {"score": 5, "description": _LONG_DESCRIPTION, "combat_synergy": 8}
    result = normalize_and_validate_synergy(raw, {"id": 1}, {"id": 2})
    assert result is not None
    assert result["hero_a_id"] == 1
    assert result["hero_b_id"] == 2
    assert result["combo_ceiling"] == 8
    assert "combat_synergy" not in result


def test_synergy_keeps_combo_ceiling_when_both_present() -> None:
    raw = {"score": 5, "description": _LONG_DESCRIPTION,
           "combat_synergy": 8, "combo_ceiling": 6}
    result = normalize_and_validate_synergy(raw, {"id": 1}, {"id": 2})
    assert result is not None
    assert result["combo_ceiling"] == 6


def test_synergy_missing_score_returns_none() -> None:
    assert normalize_and_validate_synergy(
        {"description": _LONG_DESCRIPTION}, {"id": 1}, {"id": 2},
    ) is None


def test_synergy_non_positive_id_fails_pydantic() -> None:
    # SynergyScore.id_positive 校验：ID 为 0 时必填预检通过但 Pydantic 拒绝
    raw = {"score": 5, "description": _LONG_DESCRIPTION}
    assert normalize_and_validate_synergy(raw, {"id": 0}, {"id": 2}) is None
