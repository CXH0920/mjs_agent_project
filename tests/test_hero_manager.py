"""名将杀 Agent - HeroManager 单元测试"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest

from data.hero_manager import HeroManager
from data.models import Hero, Gender, Difficulty


class TestHeroManager:
    """HeroManager 单元测试"""

    def _make_hero(self, hid: int, name: str = "曹操", faction: str = "魏") -> Hero:
        return Hero(id=hid, name=name, faction=faction)

    # ---------------------------------------------------------------
    # CRUD 基础操作
    # ---------------------------------------------------------------

    def test_add_and_get_hero(self):
        mgr = HeroManager()
        hero = self._make_hero(1)
        mgr.add_hero(hero)
        assert mgr.get_hero(1) == hero
        assert mgr.get_hero(999) is None

    def test_add_duplicate_raises(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1))
        with pytest.raises(ValueError, match="武将已存在"):
            mgr.add_hero(self._make_hero(1))

    def test_update_hero(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1, "曹操"))
        mgr.update_hero(self._make_hero(1, "曹孟德"))
        assert mgr.get_hero(1).name == "曹孟德"

    def test_delete_hero(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1))
        mgr.delete_hero(1)
        assert mgr.get_hero(1) is None

    def test_delete_nonexistent(self):
        mgr = HeroManager()
        mgr.delete_hero(999)

    # ---------------------------------------------------------------
    # 查询方法
    # ---------------------------------------------------------------

    def test_list_heroes(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1, "曹操", "魏"))
        mgr.add_hero(self._make_hero(2, "刘备", "蜀"))
        assert len(mgr.list_heroes()) == 2

    def test_list_factions(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1, "曹操", "魏"))
        mgr.add_hero(self._make_hero(2, "刘备", "蜀"))
        mgr.add_hero(self._make_hero(3, "孙权", "吴"))
        factions = mgr.list_factions()
        assert len(factions) == 3
        assert "魏" in factions
        assert "蜀" in factions
        assert "吴" in factions

    def test_list_heroes_by_faction(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1, "曹操", "魏"))
        mgr.add_hero(self._make_hero(2, "刘备", "蜀"))
        mgr.add_hero(self._make_hero(3, "夏侯惇", "魏"))
        wei = mgr.list_heroes_by_faction("魏")
        assert len(wei) == 2
        assert all(h.faction == "魏" for h in wei)

    def test_get_hero_by_name(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1, "曹操", "魏"))
        mgr.add_hero(self._make_hero(2, "刘备", "蜀"))
        assert mgr.get_hero_by_name("曹操") is not None
        assert mgr.get_hero_by_name("不存在") is None

    def test_search_heroes(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1, "曹操", "魏"))
        mgr.add_hero(self._make_hero(2, "曹植", "魏"))
        mgr.add_hero(self._make_hero(3, "刘备", "蜀"))
        results = mgr.search_heroes("曹")
        assert len(results) == 2

    # ---------------------------------------------------------------
    # 持久化
    # ---------------------------------------------------------------

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "heroes.json"
            mgr = HeroManager(heroes_file=filepath)
            mgr.add_hero(self._make_hero(1, "曹操", "魏"))
            mgr.add_hero(self._make_hero(2, "刘备", "蜀"))
            mgr.save()

            mgr2 = HeroManager(heroes_file=filepath)
            mgr2.load()
            assert len(mgr2.list_heroes()) == 2
            assert mgr2.get_hero(1).name == "曹操"

    def test_load_nonexistent_file(self):
        mgr = HeroManager(heroes_file=Path("/nonexistent/heroes.json"))
        mgr.load()
        assert mgr.list_heroes() == []

    def test_load_corrupted_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "heroes.json"
            filepath.write_text("{invalid json", encoding="utf-8")
            mgr = HeroManager(heroes_file=filepath)
            mgr.load()
            assert mgr.list_heroes() == []