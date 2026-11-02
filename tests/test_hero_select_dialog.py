"""武将选择对话框基类的选择状态机（多选上限回退/跨筛选保持/空态安全）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.data.models import Hero
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _HeroManagerStub:
    """BaseHeroSelectDialog 只消费 list_heroes / list_factions 两个读取口。"""

    def __init__(self, heroes: list[Hero]) -> None:
        self._heroes = heroes

    def list_heroes(self) -> list[Hero]:
        return list(self._heroes)

    def list_factions(self) -> list[str]:
        return sorted({hero.faction for hero in self._heroes})


def _heroes(count_per_faction: int = 3, factions: tuple[str, ...] = ("魏", "蜀")) -> list[Hero]:
    heroes: list[Hero] = []
    hero_id = 0
    for faction in factions:
        for index in range(count_per_faction):
            hero_id += 1
            heroes.append(Hero(id=hero_id, name=f"{faction}{index}将", faction=faction))
    return heroes


def test_empty_state_dialog_is_safe_to_call_state_methods() -> None:
    """空态分支提前退出后，状态方法必须是安全 no-op 而非 AttributeError。

    无数据入口（全新安装/数据损坏/私有仓未同步）命中空态；此前依赖
    「信号未连接」的巧合才不崩，任何新增空态路径都会触发属性缺失。
    """
    _app()
    dialog = BaseHeroSelectDialog(_HeroManagerStub([]))

    assert dialog._search_input is None
    assert dialog._faction_combo is None
    assert dialog._list_widget is None
    dialog._apply_filter()
    dialog._refresh_selection_ui()
    assert dialog._selected_ids_for_current_mode() == []


def test_multi_selection_respects_max_by_rolling_back_third_check() -> None:
    _app()
    dialog = BaseHeroSelectDialog(
        _HeroManagerStub(_heroes()),
        selection_mode=SelectionMode.MULTI_LIMIT,
        max_selection=2,
    )

    dialog._list_widget.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._list_widget.item(1).setCheckState(Qt.CheckState.Checked)
    assert len(dialog._selected_id_set) == 2

    # 超上限勾选被重入守卫回退：列表项回到未勾选，集合保持 2 个
    dialog._list_widget.item(2).setCheckState(Qt.CheckState.Checked)
    assert dialog._list_widget.item(2).checkState() == Qt.CheckState.Unchecked
    assert len(dialog._selected_id_set) == 2


def test_selection_survives_faction_refilter() -> None:
    _app()
    dialog = BaseHeroSelectDialog(_HeroManagerStub(_heroes()))

    dialog._list_widget.item(3).setCheckState(Qt.CheckState.Checked)  # 蜀0将
    selected_before = set(dialog._selected_id_set)

    dialog._faction_combo.set_checked({"蜀"})
    dialog._apply_filter()

    assert dialog._selected_id_set == selected_before
    assert dialog._count_label.text().startswith("已筛选: 3 / 6")
    shu_ids = {hero.id for hero in dialog._all_heroes if hero.faction == "蜀"}
    assert selected_before <= shu_ids
    assert dialog._list_widget.count() == 3
    for row in range(dialog._list_widget.count()):
        item = dialog._list_widget.item(row)
        expected = Qt.CheckState.Checked if item.data(Qt.ItemDataRole.UserRole) in selected_before else Qt.CheckState.Unchecked
        assert item.checkState() == expected


def test_select_all_current_respects_limit() -> None:
    _app()
    dialog = BaseHeroSelectDialog(
        _HeroManagerStub(_heroes()),
        selection_mode=SelectionMode.MULTI_LIMIT,
        max_selection=2,
    )

    first_id = dialog._all_heroes[0].id
    dialog._selected_id_set.add(first_id)
    dialog._select_all_current()

    assert len(dialog._selected_id_set) == 2
    assert first_id in dialog._selected_id_set


def test_single_mode_selects_from_list_without_tags() -> None:
    _app()
    dialog = BaseHeroSelectDialog(_HeroManagerStub(_heroes()), selection_mode=SelectionMode.SINGLE)

    assert dialog._selected_tags_layout is None
    dialog._list_widget.item(1).setSelected(True)

    assert dialog._selected_ids_for_current_mode() == [dialog._all_heroes[1].id]


def test_accept_button_gating_follows_min_selection() -> None:
    _app()
    dialog = BaseHeroSelectDialog(_HeroManagerStub(_heroes()))

    assert not dialog._ok_btn.isEnabled()

    dialog._list_widget.item(0).setCheckState(Qt.CheckState.Checked)

    assert dialog._ok_btn.isEnabled()
    assert dialog._ok_btn.text() == "确定"


def test_accept_populates_result_values() -> None:
    _app()
    dialog = BaseHeroSelectDialog(_HeroManagerStub(_heroes()))

    dialog._list_widget.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._list_widget.item(3).setCheckState(Qt.CheckState.Checked)
    dialog._on_accept()

    assert sorted(dialog.selected_ids) == sorted(
        [dialog._all_heroes[0].id, dialog._all_heroes[3].id]
    )
    assert len(dialog.selected_heroes) == 2
    first = dialog.selected_heroes[0]
    assert {"id", "name", "faction", "skills"} <= set(first)
    assert dialog.selected_hero is None  # 仅单一选择时填充
