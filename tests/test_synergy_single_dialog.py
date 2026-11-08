# -*- coding: utf-8 -*-
"""synergy_single_dialog 同名测试：相性选定武将对话框构造冒烟（D5 零覆盖补测）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.data.models import Hero
from src.ui.generation.synergy_single_dialog import SynergySingleDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _HeroManagerStub:
    """对话框只消费 list_heroes / list_factions 两个读取口（对齐基类测试桩）。"""

    def __init__(self, heroes: list[Hero]) -> None:
        self._heroes = heroes

    def list_heroes(self) -> list[Hero]:
        return list(self._heroes)

    def list_factions(self) -> list[str]:
        return sorted({hero.faction for hero in self._heroes})


def test_dialog_constructs_with_single_selection_mode() -> None:
    _app()
    manager = _HeroManagerStub([
        Hero(id=1, name="甲", faction="魏"),
        Hero(id=2, name="乙", faction="蜀"),
    ])

    dialog = SynergySingleDialog(manager)

    assert dialog.windowTitle() == "选定武将计算相性"
    assert dialog.selected_hero is None  # 未选择前选中为空（属性非方法）
