"""落后相性刷新对话框与共用落后判定测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
from src.data.hero_manager import HeroManager
from src.data.models import Hero, SynergyScore
from src.data.synergy_manager import SynergyManager, is_synergy_outdated
from src.ui.generation.synergy_pair_dialog import SynergyPairDialog, SynergyStatus
from src.ui.generation.synergy_refresh_dialog import SynergyRefreshDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _hero(id_: int, name: str, last_updated: str) -> Hero:
    return Hero(id=id_, name=name, faction="魏", last_updated=last_updated)


def _managers(tmp_path: Path) -> tuple[HeroManager, SynergyManager]:
    hero_manager = HeroManager(tmp_path / "heroes.json")
    for hero_id, name in enumerate(("曹操", "刘备", "孙权", "诸葛亮", "吕布"), 1):
        last = "日期未知" if hero_id == 3 else "2026-07-26"
        hero_manager.add_hero(Hero(id=hero_id, name=name, faction="魏", last_updated=last))
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    return hero_manager, synergy_manager


def _seed_outdated(synergy_manager: SynergyManager) -> None:
    """三落后（含日期未知）+ 一最新 + 一孤儿（hero 9 不存在）。"""
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5, last_updated="2026-07-26"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=3, score=4, last_updated="2026-07-18"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=2, hero_b_id=4, score=3, last_updated="2026-07-10"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=5, score=3, last_updated="日期未知"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=9, score=3, last_updated="2026-01-01"))


def test_is_synergy_outdated_matrix() -> None:
    pair = SynergyScore(hero_a_id=1, hero_b_id=2, score=5, last_updated="2026-07-18")
    assert is_synergy_outdated(pair, _hero(1, "甲", "2026-07-26"), _hero(2, "乙", "2026-07-10"))
    assert not is_synergy_outdated(pair, _hero(1, "甲", "2026-07-18"), _hero(2, "乙", "2026-07-10"))
    # 日期无法解析（武将或记录任一侧）保守视为落后
    assert is_synergy_outdated(pair, _hero(1, "甲", "日期未知"), _hero(2, "乙", "2026-07-10"))
    unknown = SynergyScore(hero_a_id=1, hero_b_id=2, score=5, last_updated="日期未知")
    assert is_synergy_outdated(unknown, _hero(1, "甲", "2026-01-01"), _hero(2, "乙", "2026-01-01"))


def test_list_outdated_synergies_skips_orphans(tmp_path: Path) -> None:
    hero_manager, synergy_manager = _managers(tmp_path)
    _seed_outdated(synergy_manager)
    lookup = {hero.id: hero for hero in hero_manager.list_heroes()}

    outdated = synergy_manager.list_outdated_synergies(lookup)

    assert {(p.hero_a_id, p.hero_b_id) for p in outdated} == {(1, 3), (2, 4), (1, 5)}


def test_refresh_dialog_lists_outdated_pairs_oldest_first(tmp_path: Path) -> None:
    _app()
    hero_manager, synergy_manager = _managers(tmp_path)
    _seed_outdated(synergy_manager)
    dialog = SynergyRefreshDialog(hero_manager, synergy_manager)

    # 日期未知视为最旧排最前，其余按最后生成日期升序；孤儿对不展示
    assert dialog._list_widget.count() == 3
    assert dialog._list_widget.item(0).text() == "曹操 ↔ 吕布  最后生成: 日期未知"
    assert dialog._list_widget.item(1).text() == "刘备 ↔ 诸葛亮  最后生成: 2026-07-10"
    assert dialog._list_widget.item(2).text() == "曹操 ↔ 孙权  最后生成: 2026-07-18"


def test_refresh_dialog_search_matches_either_hero_name(tmp_path: Path) -> None:
    _app()
    hero_manager, synergy_manager = _managers(tmp_path)
    _seed_outdated(synergy_manager)
    dialog = SynergyRefreshDialog(hero_manager, synergy_manager)

    dialog._search_input.setText("孙权")
    assert dialog._list_widget.count() == 1
    assert dialog._list_widget.item(0).text() == "曹操 ↔ 孙权  最后生成: 2026-07-18"

    dialog._search_input.setText("诸葛")
    assert dialog._list_widget.count() == 1
    assert dialog._list_widget.item(0).text() == "刘备 ↔ 诸葛亮  最后生成: 2026-07-10"

    dialog._search_input.setText("不存在")
    assert dialog._list_widget.count() == 0


def test_refresh_dialog_empty_state_without_outdated_pairs(tmp_path: Path) -> None:
    _app()
    hero_manager, synergy_manager = _managers(tmp_path)
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5, last_updated="2026-07-26"))
    dialog = SynergyRefreshDialog(hero_manager, synergy_manager)

    assert dialog.findChild(QWidget, "emptyState") is not None
    assert dialog.findChild(QWidget, "emptyStateTitle").text() == "当前没有落后的相性记录"
    assert not hasattr(dialog, "_list_widget")


def test_refresh_dialog_accept_collects_checked_pairs(tmp_path: Path) -> None:
    _app()
    hero_manager, synergy_manager = _managers(tmp_path)
    _seed_outdated(synergy_manager)
    dialog = SynergyRefreshDialog(hero_manager, synergy_manager)

    assert not dialog._ok_btn.isEnabled()

    dialog._list_widget.item(1).setCheckState(Qt.CheckState.Checked)
    dialog._list_widget.item(2).setCheckState(Qt.CheckState.Checked)
    assert "已选择: 2 组 · 预计刷新 2 组相性" in dialog._selection_label.text()
    assert dialog._ok_btn.text() == "刷新 2 组相性"

    dialog._on_accept()
    assert dialog.selected_pairs == [
        {"hero_a_id": 2, "hero_b_id": 4},
        {"hero_a_id": 1, "hero_b_id": 3},
    ]

    dialog._select_all_btn.click()
    assert len(dialog._selected_keys) == 3
    dialog._clear_selection_btn.click()
    assert not dialog._selected_keys
    assert not dialog._ok_btn.isEnabled()


def test_both_entries_share_outdated_detection(tmp_path: Path) -> None:
    """入口一的配对级状态判定与入口二的落后列表必须出自同一套逻辑。"""
    _app()
    hero_manager, synergy_manager = _managers(tmp_path)
    _seed_outdated(synergy_manager)
    pair_dialog = SynergyPairDialog(hero_manager, synergy_manager)
    refresh_dialog = SynergyRefreshDialog(hero_manager, synergy_manager)

    listed = {(p.hero_a_id, p.hero_b_id) for p in refresh_dialog._outdated_pairs}
    assert listed
    for hero_a_id, hero_b_id in listed:
        assert pair_dialog._pair_state(hero_a_id, hero_b_id) is SynergyStatus.OUTDATED
