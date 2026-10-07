"""实战配队批量生成对话框测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtWidgets import QApplication
from src.data.combo_manager import ComboManager
from src.data.hero_manager import HeroManager
from src.data.models import Combo, Hero, SynergyScore
from src.data.synergy_manager import SynergyManager
from src.ui.generation.synergy_combos_dialog import SynergyCombosDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


COMBOS = [
    {"hero1_name": "曹操", "hero2_name": "刘备", "hero1_id": 1, "hero2_id": 2,
     "rating": 9, "position": "14", "note": "曹操1+刘备4"},
    {"hero1_name": "刘备", "hero2_name": "孙权", "hero1_id": 2, "hero2_id": 3,
     "rating": 8, "position": "both", "note": ""},
    {"hero1_name": "孙权", "hero2_name": "曹操", "hero1_id": 3, "hero2_id": 1,
     "rating": 5, "position": "23", "note": ""},
]


def _dialog(tmp_path: Path, with_synergy: bool = False) -> SynergyCombosDialog:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    for hero_id, name in [(1, "曹操"), (2, "刘备"), (3, "孙权"), (4, "吕布")]:
        hero_manager.add_hero(Hero(id=hero_id, name=name))
    combo_manager = ComboManager(tmp_path / "combos.json")
    for item in COMBOS:
        combo = Combo(**item)
        combo_manager.add(combo, (combo.hero1_id, combo.hero2_id))
    combo_manager.save()
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    if with_synergy:
        synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5))
    return SynergyCombosDialog(hero_manager, synergy_manager, combo_manager)


def _hero_index(dialog: SynergyCombosDialog, hero_id: int) -> int:
    for index in range(dialog._hero_filter.count()):
        if dialog._hero_filter.itemData(index) == hero_id:
            return index
    return 0


def test_hero_filter_narrows_to_pairs_containing_hero(tmp_path: Path) -> None:
    dialog = _dialog(tmp_path)
    dialog._hero_filter.setCurrentIndex(_hero_index(dialog, 1))  # 曹操

    assert dialog._table.rowCount() == 2
    # 评级降序：9 分 曹操-刘备 在 5 分 孙权-曹操 之前
    assert [dialog._table.item(0, c).text() for c in (1, 2)] == ["曹操", "刘备"]
    assert [dialog._table.item(1, c).text() for c in (1, 2)] == ["孙权", "曹操"]
    assert "筛选结果: 2 / 共 3 对" in dialog._summary_label.text()


def test_hero_filter_without_match_disables_accept(tmp_path: Path) -> None:
    dialog = _dialog(tmp_path)
    dialog._hero_filter.setCurrentIndex(_hero_index(dialog, 4))  # 吕布无配队

    assert dialog._table.rowCount() == 0
    assert not dialog._footer.accept_button.isEnabled()


def test_hero_filter_all_restores_full_list(tmp_path: Path) -> None:
    dialog = _dialog(tmp_path)
    dialog._hero_filter.setCurrentIndex(_hero_index(dialog, 1))
    assert dialog._table.rowCount() == 2

    dialog._hero_filter.setCurrentIndex(0)  # 全部武将

    assert dialog._table.rowCount() == 3


def test_hero_filter_combines_with_rating_and_status(tmp_path: Path) -> None:
    dialog = _dialog(tmp_path, with_synergy=True)
    dialog._hero_filter.setCurrentIndex(_hero_index(dialog, 1))  # 曹操
    dialog._rating_combo.setCurrentIndex(1)  # 9-10（顶级）
    assert dialog._table.rowCount() == 1

    dialog._rating_combo.setCurrentIndex(0)  # 全部评级
    dialog._status_combo.setCurrentText("已生成")  # 曹操-刘备已有相性
    assert dialog._table.rowCount() == 1
    assert [dialog._table.item(0, c).text() for c in (1, 2)] == ["曹操", "刘备"]

    dialog._hero_filter.setCurrentIndex(_hero_index(dialog, 3))  # 孙权
    assert dialog._table.rowCount() == 0  # 孙权的配对均未生成


def test_accept_collects_only_visible_pairs(tmp_path: Path) -> None:
    dialog = _dialog(tmp_path)
    dialog._hero_filter.setCurrentIndex(_hero_index(dialog, 3))  # 孙权

    dialog._on_accept()

    assert dialog.selected_pairs == [
        {"hero_a_id": 2, "hero_b_id": 3},  # 8 分 刘备-孙权
        {"hero_a_id": 3, "hero_b_id": 1},  # 5 分 孙权-曹操
    ]
