"""名将杀 Agent - SynergyManager 单元测试"""

import json
import tempfile
from pathlib import Path

import pytest

from src.data.synergy_manager import SynergyManager
from src.data.models import SynergyScore


class TestSynergyManager:
    """SynergyManager 单元测试"""

    def _make_synergy(self, a_id: int = 1, b_id: int = 2, score: int = 8) -> SynergyScore:
        return SynergyScore(hero_a_id=a_id, hero_b_id=b_id, score=score)

    # ---------------------------------------------------------------
    # CRUD 基础操作
    # ---------------------------------------------------------------

    def test_add_and_get_synergy(self):
        mgr = SynergyManager()
        s = self._make_synergy(1, 2, 8)
        mgr.add_synergy(s)
        assert mgr.get_synergy(1, 2) == s
        assert mgr.get_synergy(2, 1) == s  # 顺序无关
        assert mgr.get_synergy(1, 3) is None

    def test_add_duplicate_raises(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2))
        with pytest.raises(ValueError, match="已存在"):
            mgr.add_synergy(self._make_synergy(1, 2))

    def test_add_duplicate_reverse_raises(self):
        """(1,2) 和 (2,1) 应视为重复"""
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2))
        with pytest.raises(ValueError):
            mgr.add_synergy(self._make_synergy(2, 1))

    def test_update_synergy(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2, 5))
        mgr.update_synergy(self._make_synergy(1, 2, 9))
        assert mgr.get_synergy(1, 2).score == 9
        assert mgr.get_synergy(1, 2).synergy_rating == "S"

    def test_update_synergy_preserves_all_editable_fields(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2, 5))
        updated = SynergyScore(
            hero_a_id=1,
            hero_b_id=2,
            score=-3,
            combo_ceiling=9,
            combo_stability=8,
            adaptability=7,
            description="手工调整后的相性说明",
        )
        mgr.update_synergy(updated)
        result = mgr.get_synergy(1, 2)
        assert result.score == -3
        assert result.synergy_rating == "D"
        assert result.combo_ceiling == 9
        assert result.combo_stability == 8
        assert result.adaptability == 7
        assert result.description == "手工调整后的相性说明"

    def test_update_synergy_with_reversed_ids_updates_same_record(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2, 5))
        mgr.update_synergy(self._make_synergy(2, 1, 9))
        assert len(mgr.list_synergies()) == 1
        assert mgr.get_synergy(1, 2).score == 9
        assert mgr.get_synergy(2, 1).score == 9

    def test_delete_synergy(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2))
        mgr.delete_synergy(1, 2)
        assert mgr.get_synergy(1, 2) is None

    def test_delete_nonexistent(self):
        mgr = SynergyManager()
        mgr.delete_synergy(1, 2)  # 不应抛出异常

    # ---------------------------------------------------------------
    # 查询方法
    # ---------------------------------------------------------------

    def test_list_synergies(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2))
        mgr.add_synergy(self._make_synergy(1, 3))
        assert len(mgr.list_synergies()) == 2

    def test_list_synergies_for_hero(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2))
        mgr.add_synergy(self._make_synergy(1, 3))
        mgr.add_synergy(self._make_synergy(4, 5))
        results = mgr.list_synergies_for_hero(1)
        assert len(results) == 2
        results = mgr.list_synergies_for_hero(4)
        assert len(results) == 1
        results = mgr.list_synergies_for_hero(999)
        assert len(results) == 0

    # ---------------------------------------------------------------
    # 批量删除
    # ---------------------------------------------------------------

    def test_delete_synergies_for_hero(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2))
        mgr.add_synergy(self._make_synergy(1, 3))
        mgr.add_synergy(self._make_synergy(4, 5))
        count = mgr.delete_synergies_for_hero(1)
        assert count == 2
        assert len(mgr.list_synergies()) == 1

    def test_delete_synergies_for_nonexistent_hero(self):
        mgr = SynergyManager()
        mgr.add_synergy(self._make_synergy(1, 2))
        count = mgr.delete_synergies_for_hero(999)
        assert count == 0

    # ---------------------------------------------------------------
    # 持久化
    # ---------------------------------------------------------------

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "synergies.json"
            mgr = SynergyManager(synergies_file=filepath)
            mgr.add_synergy(self._make_synergy(1, 2, 8))
            mgr.add_synergy(self._make_synergy(1, 3, 5))
            mgr.update_synergy(SynergyScore(
                hero_a_id=1,
                hero_b_id=2,
                score=-3,
                combo_ceiling=8,
                combo_stability=7,
                adaptability=6,
                description="完整字段持久化",
            ))
            mgr.save()

            mgr2 = SynergyManager(synergies_file=filepath)
            mgr2.load()
            assert len(mgr2.list_synergies()) == 2
            updated = mgr2.get_synergy(1, 2)
            assert updated.score == -3
            assert updated.synergy_rating == "D"
            assert updated.combo_ceiling == 8
            assert updated.combo_stability == 7
            assert updated.adaptability == 6
            assert updated.description == "完整字段持久化"

    def test_load_nonexistent_file(self):
        mgr = SynergyManager(synergies_file=Path("/nonexistent/synergies.json"))
        mgr.load()
        assert mgr.list_synergies() == []

    def test_load_corrupted_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "synergies.json"
            filepath.write_text("{invalid", encoding="utf-8")
            mgr = SynergyManager(synergies_file=filepath)
            mgr.load()
            assert mgr.list_synergies() == []

    # ---------------------------------------------------------------
    # _synergy_key 工具方法
    # ---------------------------------------------------------------

    def test_synergy_key_order_independent(self):
        assert SynergyManager._synergy_key(1, 2) == SynergyManager._synergy_key(2, 1)
        assert SynergyManager._synergy_key(5, 3) == (3, 5)