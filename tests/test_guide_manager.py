"""名将杀 Agent - GuideManager 单元测试"""

import tempfile
from pathlib import Path

import pytest

from src.data.guide_manager import GuideManager
from src.data.models import HeroGuide


class TestGuideManager:
    """GuideManager 单元测试"""

    def _make_guide(self, hero_id: int = 1) -> HeroGuide:
        return HeroGuide(
            hero_id=hero_id,
            key_points=["先手优势"],
            weak_against_type=["高爆发型"],
            strong_against_type=["慢速防御型"],
            synergizes_with=[4, 5],
            counter_strategy="保留闪避",
            description="攻略正文",
            tips_for_beginners="新手建议",
        )

    # ---------------------------------------------------------------
    # CRUD 基础操作
    # ---------------------------------------------------------------

    def test_add_and_get_guide(self):
        mgr = GuideManager()
        g = self._make_guide(1)
        mgr.add_guide(g)
        assert mgr.get_guide(1) == g
        assert mgr.get_guide(999) is None

    def test_add_duplicate_raises(self):
        mgr = GuideManager()
        mgr.add_guide(self._make_guide(1))
        with pytest.raises(ValueError, match="已存在"):
            mgr.add_guide(self._make_guide(1))

    def test_update_guide(self):
        mgr = GuideManager()
        mgr.add_guide(self._make_guide(1))
        updated = HeroGuide(hero_id=1, description="新的攻略")
        mgr.update_guide(updated)
        assert mgr.get_guide(1).description == "新的攻略"
        # update 时未提供的字段使用默认值
        assert mgr.get_guide(1).key_points == []

    def test_delete_guide(self):
        mgr = GuideManager()
        mgr.add_guide(self._make_guide(1))
        mgr.delete_guide(1)
        assert mgr.get_guide(1) is None

    def test_delete_nonexistent(self):
        mgr = GuideManager()
        mgr.delete_guide(999)  # 不应抛出异常

    def test_clear_all(self):
        mgr = GuideManager()
        mgr.add_guide(self._make_guide(1))
        mgr.add_guide(self._make_guide(2))
        assert mgr.clear_all() == 2
        assert mgr.list_guides() == []

    # ---------------------------------------------------------------
    # 查询方法
    # ---------------------------------------------------------------

    def test_list_guides(self):
        mgr = GuideManager()
        mgr.add_guide(self._make_guide(1))
        mgr.add_guide(self._make_guide(2))
        assert len(mgr.list_guides()) == 2

    def test_list_guides_empty(self):
        mgr = GuideManager()
        assert mgr.list_guides() == []

    # ---------------------------------------------------------------
    # 持久化
    # ---------------------------------------------------------------

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "guides.json"
            mgr = GuideManager(guides_file=filepath)
            mgr.add_guide(self._make_guide(1))
            mgr.add_guide(self._make_guide(2))
            mgr.save()

            mgr2 = GuideManager(guides_file=filepath)
            mgr2.load()
            assert len(mgr2.list_guides()) == 2
            assert mgr2.get_guide(1).description == "攻略正文"

    def test_load_nonexistent_file(self):
        mgr = GuideManager(guides_file=Path("/nonexistent/guides.json"))
        mgr.load()
        assert mgr.list_guides() == []

    def test_load_corrupted_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "guides.json"
            filepath.write_text("{invalid", encoding="utf-8")
            mgr = GuideManager(guides_file=filepath)
            mgr.load()
            assert mgr.list_guides() == []
