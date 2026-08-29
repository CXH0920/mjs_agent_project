"""名将杀 Agent - ComboManager 单元测试"""

import json
import tempfile
from pathlib import Path

from src.data.combo_manager import ComboManager
from src.data.models import Combo


def _make_combo(a_id: int = 1, b_id: int = 2, rating: int = 9) -> Combo:
    return Combo(
        hero1_name="甲",
        hero2_name="乙",
        hero1_id=a_id,
        hero2_id=b_id,
        rating=rating,
        position="both",
        note="测试组合",
        hero1_seats=[1],
        hero2_seats=[4],
    )


class TestComboManager:
    """ComboManager 单元测试"""

    def test_add_and_get_combo(self):
        mgr = ComboManager()
        combo = _make_combo(1, 2)
        mgr.update(combo, mgr._combo_key(1, 2))
        assert mgr.get_combo(1, 2) == combo
        assert mgr.get_combo(2, 1) == combo  # 顺序无关
        assert mgr.get_combo(1, 3) is None

    def test_list_combos(self):
        mgr = ComboManager()
        mgr.update(_make_combo(1, 2), (1, 2))
        mgr.update(_make_combo(1, 3), (1, 3))
        mgr.update(_make_combo(4, 5), (4, 5))
        assert len(mgr.list_combos()) == 3

    def test_list_combos_for_hero(self):
        mgr = ComboManager()
        mgr.update(_make_combo(1, 2), (1, 2))
        mgr.update(_make_combo(3, 1), (1, 3))
        mgr.update(_make_combo(4, 5), (4, 5))
        results = mgr.list_combos_for_hero(1)
        assert len(results) == 2
        assert mgr.list_combos_for_hero(999) == []

    def test_combo_key_order_independent(self):
        assert ComboManager._combo_key(1, 2) == ComboManager._combo_key(2, 1)
        assert ComboManager._combo_key(5, 3) == (3, 5)

    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "combos.json"
            mgr = ComboManager(filepath)
            combo = Combo(
                hero1_name="刘备",
                hero2_name="孙权",
                hero1_id=10,
                hero2_id=20,
                rating=9,
                position="14",
                note="孙权4+刘备1：刘备留一张牌发动孙权技能",
                hero1_seats=[1],
                hero2_seats=[4],
            )
            mgr.update(combo, mgr._combo_key(10, 20))
            mgr.save()

            mgr2 = ComboManager(filepath)
            mgr2.load()
            loaded = mgr2.get_combo(20, 10)
            assert loaded == combo
            assert loaded.hero1_seats == [1]
            assert loaded.rating == 9

    def test_load_nonexistent_file(self):
        mgr = ComboManager(Path("/nonexistent/combos.json"))
        mgr.load()
        assert mgr.list_combos() == []

    def test_load_skips_duplicate_reversed_pair(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "combos.json"
            first = _make_combo(1, 2, 8).model_dump(mode="json")
            duplicate = _make_combo(2, 1, 5).model_dump(mode="json")
            filepath.write_text(json.dumps([first, duplicate], ensure_ascii=False), encoding="utf-8")

            mgr = ComboManager(filepath)
            issues = mgr.load()

            assert mgr.get_combo(1, 2).rating == 8
            assert [issue.kind for issue in issues] == ["duplicate_key"]


class TestComboModel:
    """Combo 模型校验"""

    def test_seats_sorted_and_validated(self):
        combo = Combo(
            hero1_name="甲", hero2_name="乙", hero1_id=1, hero2_id=2,
            rating=5, hero1_seats=[3, 1], hero2_seats=[],
        )
        assert combo.hero1_seats == [1, 3]

    def test_invalid_seat_rejected(self):
        import pytest
        with pytest.raises(ValueError, match="座次号位"):
            Combo(
                hero1_name="甲", hero2_name="乙", hero1_id=1, hero2_id=2,
                rating=5, hero1_seats=[5], hero2_seats=[],
            )

    def test_same_hero_rejected(self):
        import pytest
        with pytest.raises(ValueError, match="同一武将"):
            Combo(hero1_name="甲", hero2_name="甲", hero1_id=1, hero2_id=1, rating=5)

    def test_rating_range_validated(self):
        import pytest
        with pytest.raises(ValueError):
            Combo(hero1_name="甲", hero2_name="乙", hero1_id=1, hero2_id=2, rating=11)
