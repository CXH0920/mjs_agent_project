"""名将杀 Agent - 数据模型单元测试"""

import os
import sys

# 将 src 目录加入模块搜索路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from datetime import date

import pytest
from pydantic import ValidationError

from data.models import (
    Card,
    CardType,
    Difficulty,
    Gender,
    Hero,
    HeroGuide,
    IncrementalUpdate,
    Skill,
    SynergyScore,
    ViabilityTier,
)


class TestSkill:
    def test_basic_skill(self) -> None:
        s = Skill(name="奸雄", description="出牌阶段限一次")
        assert s.name == "奸雄"

    def test_empty_name_should_raise(self) -> None:
        with pytest.raises(ValidationError):
            Skill(name="  ")

    def test_skill_with_settlement(self) -> None:
        s = Skill(name="奸雄", description="出牌阶段限一次", settlement="结算细则1...")
        assert s.settlement == "结算细则1..."


class TestHero:
    def test_basic_hero(self) -> None:
        h = Hero(
            id=1, name="曹操", faction="魏", max_hp=5, max_hand=4,
            gender=Gender.MALE, position="输出",
        )
        assert h.id == 1
        assert h.name == "曹操"
        assert h.max_hp == 5
        assert h.difficulty == Difficulty.MEDIUM
        assert h.last_updated == date.today().isoformat()

    def test_hero_with_skills(self) -> None:
        h = Hero(
            id=2, name="诸葛亮", faction="蜀",
            skills=[Skill(name="观星", description="判定阶段观看牌堆顶部")],
        )
        assert len(h.skills) == 1
        assert h.skills[0].name == "观星"

    def test_invalid_id_should_raise(self) -> None:
        with pytest.raises(ValidationError):
            Hero(id=0, name="test", faction="魏")

    def test_empty_name_should_raise(self) -> None:
        with pytest.raises(ValidationError):
            Hero(id=1, name="", faction="魏")

    def test_hero_with_validation_alias(self) -> None:
        """模拟官网数据(中文字段名)解析"""
        h = Hero.model_validate({
            "角色ID": 3,
            "名称": "孙权",
            "势力": "吴",
            "定位": "辅助",
            "体力上限": 4,
            "手牌上限": 4,
            "性别": "男",
            "技能": [{"name": "制衡", "description": "出牌阶段可弃置任意张牌并摸等量牌"}],
        })
        assert h.id == 3
        assert h.name == "孙权"
        assert h.gender == Gender.MALE

    def test_hero_default_gender(self) -> None:
        h = Hero(id=4, name="test", faction="魏")
        assert h.gender == Gender.MALE

    def test_hero_default_values(self) -> None:
        h = Hero(id=5, name="test", faction="魏")
        assert h.max_hp == 4
        assert h.max_hand == 4
        assert h.position == ""
        assert h.title == ""
        assert h.mode_viability == {}


class TestSynergyScore:
    def test_basic_synergy(self) -> None:
        s = SynergyScore(hero_a_id=1, hero_b_id=2, score=8)
        assert s.score == 8
        assert s.synergy_rating == "C"

    def test_synergy_with_rating(self) -> None:
        s = SynergyScore(hero_a_id=1, hero_b_id=2, score=8, synergy_rating="S")
        assert s.synergy_rating == "S"

    def test_invalid_synergy_rating_should_raise(self) -> None:
        with pytest.raises(ValidationError):
            SynergyScore(hero_a_id=1, hero_b_id=2, score=5, synergy_rating="E")

    def test_synergy_rating_case_insensitive(self) -> None:
        s = SynergyScore(hero_a_id=1, hero_b_id=2, score=5, synergy_rating="s")
        assert s.synergy_rating == "S"

    def test_invalid_hero_id_should_raise(self) -> None:
        with pytest.raises(ValidationError):
            SynergyScore(hero_a_id=0, hero_b_id=1, score=5)

    def test_score_range(self) -> None:
        with pytest.raises(ValidationError):
            SynergyScore(hero_a_id=1, hero_b_id=2, score=15)

    def test_default_values(self) -> None:
        s = SynergyScore(hero_a_id=1, hero_b_id=2, score=0)
        assert s.combo_ceiling == 5
        assert s.combo_stability == 5
        assert s.adaptability == 5


class TestHeroGuide:
    def test_basic_guide(self) -> None:
        g = HeroGuide(hero_id=1)
        assert g.hero_id == 1
        assert g.key_points == []

    def test_guide_with_int_refs(self) -> None:
        g = HeroGuide(
            hero_id=1,
            key_points=["先手优势"],
            counters=[2, 3],
            synergizes_with=[4, 5],
        )
        assert g.counters == [2, 3]
        assert g.synergizes_with == [4, 5]

    def test_empty_counter_and_synergy(self) -> None:
        g = HeroGuide(hero_id=1, counters=[], synergizes_with=[])
        assert g.counters == []
        assert g.synergizes_with == []


class TestCard:
    def test_basic_card(self) -> None:
        c = Card(id="sha", name="杀", card_type=CardType.BASIC, card_amount=44)
        assert c.name == "杀"
        assert c.card_amount == 44

    def test_card_with_alias(self) -> None:
        c = Card.model_validate({
            "id": "shan",
            "name": "闪",
            "card_type": "基本牌",
            "card_amount": 30,
        })
        assert c.name == "闪"
        assert c.card_type == CardType.BASIC

    def test_card_empty_name_should_raise(self) -> None:
        with pytest.raises(ValidationError):
            Card(id="test", name="", card_type=CardType.BASIC)


class TestIncrementalUpdate:
    def test_empty_update(self) -> None:
        u = IncrementalUpdate()
        assert u.version == "1.0"
        assert u.added_heroes == []

    def test_update_with_int_ids(self) -> None:
        h = Hero(id=1, name="曹操", faction="魏")
        u = IncrementalUpdate(
            version="1.1",
            added_heroes=[h],
            removed_hero_ids=[2, 3],
            removed_synergy_ids=[(1, 2), (3, 4)],
            removed_guide_ids=[5],
        )
        assert len(u.added_heroes) == 1
        assert u.removed_hero_ids == [2, 3]
        assert u.removed_synergy_ids == [(1, 2), (3, 4)]
        assert u.removed_guide_ids == [5]
