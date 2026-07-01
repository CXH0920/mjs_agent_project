"""名将杀 Agent - apply_incremental_update 集成测试"""

from pathlib import Path

import pytest

from src.data.manager import apply_incremental_update, HeroManager, SynergyManager, GuideManager
from src.data.models import Hero, SynergyScore, HeroGuide, IncrementalUpdate


class TestApplyIncrementalUpdate:
    """apply_incremental_update 集成测试"""

    @pytest.fixture
    def managers(self):
        """创建使用临时文件的三个 Manager"""
        import tempfile
        tmpdir = tempfile.mkdtemp()
        hero_mgr = HeroManager(heroes_file=Path(tmpdir) / "heroes.json")
        synergy_mgr = SynergyManager(synergies_file=Path(tmpdir) / "synergies.json")
        guide_mgr = GuideManager(guides_file=Path(tmpdir) / "guides.json")
        return hero_mgr, synergy_mgr, guide_mgr

    def test_add_heroes(self, managers):
        hero_mgr, synergy_mgr, guide_mgr = managers
        heroes = [Hero(id=1, name="曹操", faction="魏")]
        update = IncrementalUpdate(added_heroes=heroes)
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert stats["added_heroes"] == 1
        assert hero_mgr.get_hero(1) is not None

    def test_modified_heroes(self, managers):
        hero_mgr, synergy_mgr, guide_mgr = managers
        hero_mgr.add_hero(Hero(id=1, name="曹操", faction="魏"))
        update = IncrementalUpdate(modified_heroes=[Hero(id=1, name="曹孟德", faction="魏")])
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert stats["modified_heroes"] == 1
        assert hero_mgr.get_hero(1).name == "曹孟德"

    def test_removed_heroes_cleanup_relations(self, managers):
        """删除武将时同时清理关联的相性和攻略"""
        hero_mgr, synergy_mgr, guide_mgr = managers
        hero_mgr.add_hero(Hero(id=1, name="曹操", faction="魏"))
        hero_mgr.add_hero(Hero(id=2, name="刘备", faction="蜀"))
        synergy_mgr.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5))
        guide_mgr.add_guide(HeroGuide(hero_id=1))

        update = IncrementalUpdate(removed_hero_ids=[1])
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert stats["removed_heroes"] == 1
        assert hero_mgr.get_hero(1) is None
        # 关联的相性和攻略也应被清理
        assert len(synergy_mgr.list_synergies()) == 0
        assert guide_mgr.get_guide(1) is None

    def test_add_synergies(self, managers):
        hero_mgr, synergy_mgr, guide_mgr = managers
        synergies = [SynergyScore(hero_a_id=1, hero_b_id=2, score=8)]
        update = IncrementalUpdate(added_synergies=synergies)
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert stats["added_synergies"] == 1
        assert synergy_mgr.get_synergy(1, 2) is not None

    def test_add_duplicate_synergy_skipped(self, managers):
        """重复的相性应跳过不报错"""
        hero_mgr, synergy_mgr, guide_mgr = managers
        synergy_mgr.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5))
        update = IncrementalUpdate(added_synergies=[SynergyScore(hero_a_id=1, hero_b_id=2, score=8)])
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert stats["added_synergies"] == 0  # 跳过
        assert synergy_mgr.get_synergy(1, 2).score == 5  # 保持原值

    def test_add_guides(self, managers):
        hero_mgr, synergy_mgr, guide_mgr = managers
        guides = [HeroGuide(hero_id=1, description="攻略")]
        update = IncrementalUpdate(added_guides=guides)
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert stats["added_guides"] == 1
        assert guide_mgr.get_guide(1) is not None

    def test_empty_update(self, managers):
        """空更新应返回全 0 统计"""
        hero_mgr, synergy_mgr, guide_mgr = managers
        update = IncrementalUpdate()
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert all(v == 0 for v in stats.values())

    def test_full_update(self, managers):
        """混合多种更新操作"""
        hero_mgr, synergy_mgr, guide_mgr = managers
        hero_mgr.add_hero(Hero(id=1, name="曹操", faction="魏"))

        update = IncrementalUpdate(
            added_heroes=[Hero(id=2, name="刘备", faction="蜀")],
            modified_heroes=[Hero(id=1, name="曹孟德", faction="魏")],
            added_synergies=[SynergyScore(hero_a_id=1, hero_b_id=2, score=7)],
            added_guides=[HeroGuide(hero_id=1, description="曹操攻略")],
        )
        stats = apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
        assert stats["added_heroes"] == 1
        assert stats["modified_heroes"] == 1
        assert stats["added_synergies"] == 1
        assert stats["added_guides"] == 1
        assert stats["removed_heroes"] == 0
        assert stats["removed_synergies"] == 0
        assert stats["removed_guides"] == 0