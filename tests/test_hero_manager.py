"""名将杀 Agent - HeroManager 单元测试"""

import json
import tempfile
from pathlib import Path

import pytest
from src.data.hero_manager import HeroManager
from src.data.models import Hero


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
        with pytest.raises(ValueError, match="已存在"):
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

    def test_get_hero_by_name(self):
        mgr = HeroManager()
        mgr.add_hero(self._make_hero(1, "曹操", "魏"))
        mgr.add_hero(self._make_hero(2, "刘备", "蜀"))
        assert mgr.get_hero_by_name("曹操") is not None
        assert mgr.get_hero_by_name("不存在") is None

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

    def test_load_skips_invalid_record_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "heroes.json"
            data = [self._make_hero(1).model_dump(mode="json"), {"id": "bad"}]
            source = json.dumps(data, ensure_ascii=False)
            filepath.write_text(source, encoding="utf-8")

            mgr = HeroManager(heroes_file=filepath)
            issues = mgr.load()

            assert [hero.id for hero in mgr.list_heroes()] == [1]
            assert [issue.kind for issue in issues] == ["invalid_record"]
            assert filepath.read_text(encoding="utf-8") == source

    def test_load_invalid_record_preserves_source_to_backups(self):
        """回归（VUL-B4-001）：逐条坏记录加载时按原字节留底——否则此后任意
        一次 save()（其调用方虽各有备份，留底是数据层自己的最后防线）会把
        坏记录永久覆盖掉。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "heroes.json"
            data = [self._make_hero(1).model_dump(mode="json"), {"id": "bad"}]
            source = json.dumps(data, ensure_ascii=False)
            filepath.write_text(source, encoding="utf-8")

            mgr = HeroManager(heroes_file=filepath)
            mgr.load()

            backups = sorted((Path(tmpdir) / "backups").glob("heroes-*.json"))
            assert len(backups) == 1
            assert backups[0].read_text(encoding="utf-8") == source

    def test_load_skips_duplicate_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "heroes.json"
            first = self._make_hero(1, "曹操").model_dump(mode="json")
            duplicate = self._make_hero(1, "曹孟德").model_dump(mode="json")
            filepath.write_text(json.dumps([first, duplicate], ensure_ascii=False), encoding="utf-8")

            mgr = HeroManager(heroes_file=filepath)
            issues = mgr.load()

            assert mgr.get_hero(1).name == "曹操"
            assert [issue.kind for issue in issues] == ["duplicate_key"]

    def test_save_rejected_when_disk_errors(self):
        """守卫：加载存在磁盘来源 error（坏记录）时拒绝覆盖保存，原文件字节不动。

        坏记录已在加载时按原字节留底 backups/（VUL-B4-001）；此前任意一次
        save() 会静默把坏记录覆盖掉，现在改为显式拒绝并指引备份恢复。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "heroes.json"
            data = [self._make_hero(1).model_dump(mode="json"), {"id": "bad"}]
            source = json.dumps(data, ensure_ascii=False)
            filepath.write_text(source, encoding="utf-8")

            mgr = HeroManager(heroes_file=filepath)
            mgr.load()
            with pytest.raises(RuntimeError, match="禁止覆盖保存"):
                mgr.save()
            assert filepath.read_text(encoding="utf-8") == source

    def test_save_allows_rebuild_after_whole_file_corrupt(self):
        """守卫豁免：整体不可用（invalid_json，内存为空）不拦——此时保存必是
        有意重建（公告损坏按首跑重建同口径），原文件已整份留底。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "heroes.json"
            filepath.write_text("{broken", encoding="utf-8")

            mgr = HeroManager(heroes_file=filepath)
            mgr.load()
            assert mgr.list_heroes() == []
            mgr.add_hero(self._make_hero(1))
            mgr.save()  # 不抛：空库重建放行

            reloaded = HeroManager(heroes_file=filepath)
            reloaded.load()
            assert reloaded.get_hero(1) is not None
