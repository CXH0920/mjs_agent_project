# -*- coding: utf-8 -*-
"""character_similarity 同名测试：字形相似度评分、白名单与安全替换（D5 锚点迁移，自 test_ocr_components 归位）。"""

from __future__ import annotations

import pytest
from src.ocr.character_feature_repository import CharacterFeatureRepository
from src.ocr.character_similarity import CharacterSimilarityService


def test_character_similarity_service_corrects_with_injected_repository(tmp_path) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load().update({
        "曹": {"four_corner": "5500", "cangjie": "HV", "radical": "曰", "pinyin": "cao", "total_strokes": "11"},
        "不": {"four_corner": "1090", "cangjie": "MF", "radical": "一", "pinyin": "bu", "total_strokes": "4"},
        "丕": {"four_corner": "1090", "cangjie": "MF", "radical": "一", "pinyin": "pi", "total_strokes": "5"},
        "仁": {"four_corner": "2121", "cangjie": "OMM", "radical": "人", "pinyin": "ren", "total_strokes": "4"},
    })
    service = CharacterSimilarityService(repository)

    assert service.correct_hero_name("曹不", ["曹仁", "曹丕"]) == "曹丕"


def test_character_similarity_requires_safe_glyph_for_single_substitution() -> None:
    service = CharacterSimilarityService()

    assert service.is_safe_single_substitution("赢政", "嬴政")
    assert not service.is_safe_single_substitution("正瑜", "周瑜")


def test_character_similarity_does_not_score_missing_four_corner_codes(tmp_path) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load().update({
        "甲": {"four_corner": "", "cangjie": "", "radical": "", "pinyin": "", "total_strokes": ""},
        "乙": {"four_corner": "", "cangjie": "", "radical": "", "pinyin": "", "total_strokes": ""},
    })
    service = CharacterSimilarityService(repository)

    assert service.single_substitution_similarity("甲", "乙") == 0.0


def test_character_similarity_scores_only_four_valid_corner_positions(tmp_path) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load().update({
        "甲": {"four_corner": "1234.", "cangjie": "", "radical": "", "total_strokes": ""},
        "乙": {"four_corner": "5678.", "cangjie": "", "radical": "", "total_strokes": ""},
        "丙": {"four_corner": "1567", "cangjie": "", "radical": "", "total_strokes": ""},
        "丁": {"four_corner": "1538", "cangjie": "", "radical": "", "total_strokes": ""},
        "戊": {"four_corner": "1238", "cangjie": "", "radical": "", "total_strokes": ""},
        "己": {"four_corner": "1234.9", "cangjie": "", "radical": "", "total_strokes": ""},
        "庚": {"four_corner": "123", "cangjie": "", "radical": "", "total_strokes": ""},
    })
    service = CharacterSimilarityService(repository)

    assert service._four_corner_score("甲", "乙") == 0.0
    assert service._four_corner_score("甲", "丙") == 0.25
    assert service._four_corner_score("甲", "丁") == 0.5
    assert service._four_corner_score("甲", "戊") == 0.75
    assert service._four_corner_score("甲", "己") == 1.0
    assert service._four_corner_score("甲", "庚") == 0.0


def test_character_similarity_scores_cangjie_by_normalized_edit_distance(tmp_path) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load().update({
        "甲": {"cangjie": " tbnm "},
        "乙": {"cangjie": "TBMO"},
        "丙": {"cangjie": "TBNH"},
        "丁": {"cangjie": "TBNM"},
        "戊": {"cangjie": ""},
    })
    service = CharacterSimilarityService(repository)

    assert service._cangjie_score("甲", "乙") == 0.5
    assert service._cangjie_score("甲", "丙") == 0.75
    assert service._cangjie_score("甲", "丁") == 1.0
    assert service._cangjie_score("甲", "戊") == 0.0


def test_character_similarity_scores_wubi_by_normalized_edit_distance(tmp_path) -> None:
    repository = CharacterFeatureRepository(tmp_path / "char_info_cache.json")
    repository.load().update({
        "甲": {"wubi": "AQJF"},
        "乙": {"wubi": "AQKF"},
        "丙": {"wubi": "AQJF"},
        "丁": {"wubi": ""},
    })
    service = CharacterSimilarityService(repository)

    assert service._wubi_score("甲", "乙") == pytest.approx(0.75)
    assert service._wubi_score("甲", "丙") == 1.0
    assert service._wubi_score("甲", "丁") == 0.0


def test_character_similarity_uses_revised_scores_for_wang_jian_candidates() -> None:
    service = CharacterSimilarityService()

    # 权重 0.3/0.3/0.4：剪→翦 五笔 UEJV/UEJN 仅末码差 → 0.75
    assert service.single_substitution_similarity("王剪", "王翦") == pytest.approx(0.75)
    assert service.single_substitution_similarity("王剪", "王异") == pytest.approx(0.1)
    # 邻→郃 命中确定性白名单，直接视为安全
    assert service.single_substitution_similarity("张邻", "张郃") == 1.0


def test_character_similarity_whitelists_recurring_name_misreads() -> None:
    service = CharacterSimilarityService()

    # 2026-10 B1 语料实测的 v6 反复误读对（樊哙/赵婕妤/张郃）与双引擎仍活跃的
    # 旧对（芈八子），白名单后恢复自动纠正
    assert service.single_substitution_similarity("樊哈", "樊哙") == 1.0
    assert service.single_substitution_similarity("赵婕好", "赵婕妤") == 1.0
    assert service.single_substitution_similarity("半八子", "芈八子") == 1.0
    assert service.is_safe_single_substitution("樊哈", "樊哙") is True
    # 字形相近的简繁对不入白名单：视觉评分（0.94）已过 0.55 安全线，
    # 视觉路径自动矫正，入表冗余（剥对后 901 槽零退化实测，2026-10-06）
    assert service.single_substitution_similarity("公孙瓚", "公孙瓒") == pytest.approx(0.94)
    assert service.is_safe_single_substitution("公孙瓚", "公孙瓒") is True
