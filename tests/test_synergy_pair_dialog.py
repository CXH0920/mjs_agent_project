"""相性指定配对计算对话框测试（自 test_guide_ui.py 对齐命名迁入）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.data.hero_manager import HeroManager
from src.data.models import Hero, SynergyScore
from src.data.synergy_manager import SynergyManager
from src.ui.generation.synergy_pair_dialog import SynergyPairDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_synergy_pair_shows_pair_counts_with_fixed_refresh(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    for hero_id, name in enumerate(("曹操", "刘备", "孙权"), 1):
        hero_manager.add_hero(Hero(id=hero_id, name=name, faction="魏", last_updated="2026-07-26"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5, last_updated="2026-07-18"))
    dialog = SynergyPairDialog(hero_manager, synergy_manager)

    # 控制维度只剩武将勾选：无状态筛选下拉、无策略单选面板
    assert not hasattr(dialog, "_status_combo")
    assert not hasattr(dialog, "_overwrite_radio")

    dialog._select_all_current()

    # 3 组 = 落后 1 + 缺失 2，固定策略缺啥补啥，只报总数不拆分
    assert "已选择: 3 / 8 个武将 · 共 3 组，将生成 3 组" in dialog._selection_label.text()
    assert dialog._ok_btn.text() == "下一步：生成 3 组相性"


def test_synergy_pair_marks_status_and_builds_precise_pairs(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏", last_updated="2026-07-26"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀", last_updated="2026-07-26"))
    hero_manager.add_hero(Hero(id=3, name="孙权", faction="吴", last_updated="日期未知"))
    hero_manager.add_hero(Hero(id=4, name="诸葛亮", faction="蜀", last_updated="2026-07-26"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5, last_updated="2026-07-26"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=3, score=4, last_updated="2026-07-18"))

    dialog = SynergyPairDialog(hero_manager, synergy_manager)

    # 列表全量展示（不可筛选），每名武将标注状态（纯信息展示）
    assert dialog._list_widget.count() == 4
    assert dialog._list_widget.item(0).text() == "曹操  [魏]  【待更新】"  # 1-3 落后
    assert dialog._list_widget.item(1).text() == "刘备  [蜀]  【已有相性】"
    assert dialog._list_widget.item(2).text() == "孙权  [吴]  【待更新】"  # 日期无法解析按保守落后处理
    assert dialog._list_widget.item(3).text() == "诸葛亮  [蜀]  【未生成】"

    dialog._list_widget.item(0).setCheckState(Qt.CheckState.Checked)  # 曹操
    dialog._list_widget.item(1).setCheckState(Qt.CheckState.Checked)  # 刘备
    dialog._list_widget.item(2).setCheckState(Qt.CheckState.Checked)  # 孙权

    # 共 3 组：1-2 已最新、1-3 落后、2-3 缺失 → 仅生成缺失与落后对
    assert "共 3 组，将生成 2 组" in dialog._selection_label.text()
    assert dialog._ok_btn.text() == "下一步：生成 2 组相性"

    dialog._on_accept()
    assert dialog.selected_pairs == [
        {"hero_a_id": 1, "hero_b_id": 3},
        {"hero_a_id": 2, "hero_b_id": 3},
    ]
