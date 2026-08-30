"""实战配队手工维护测试：数据层 CRUD、编辑表单校验与管理对话框。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton

from src.data.combo_manager import ComboManager
from src.data.models import Combo
from src.ui.library.combo_edit_dialog import ComboEditDialog
from src.ui.library.combo_management_dialog import ComboManagementDialog


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_hero_mgr():
    heroes = {
        1: SimpleNamespace(id=1, name="荆轲", faction="燕"),
        2: SimpleNamespace(id=2, name="君王后", faction="齐"),
        3: SimpleNamespace(id=3, name="蒙恬", faction="秦"),
        4: SimpleNamespace(id=4, name="芈月", faction="楚"),
    }
    mgr = SimpleNamespace(
        get_hero=heroes.get,
        list_heroes=lambda: list(heroes.values()),
        list_factions=lambda: [],
    )
    return mgr, heroes


def _combo(hero1_id, hero2_id, rating=5, manual=True, note=""):
    return Combo(
        hero1_name=f"武将{hero1_id}",
        hero2_name=f"武将{hero2_id}",
        hero1_id=hero1_id,
        hero2_id=hero2_id,
        rating=rating,
        note=note,
        manual=manual,
    )


# ── 数据层 ────────────────────────────────────────────────────────


def test_save_manual_combo_adds_and_persists(tmp_path):
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()

    manager.save_manual_combo(_combo(1, 2, rating=9))

    assert manager.get_combo(2, 1).rating == 9  # (A,B)/(B,A) 对称 key
    assert manager.get_combo(2, 1).manual is True
    fresh = ComboManager(tmp_path / "combos.json")
    fresh.load()
    assert fresh.get_combo(1, 2) is not None  # 已原子落盘


def test_save_manual_combo_updates_same_pair_in_place(tmp_path):
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    original = _combo(1, 2, rating=5)
    manager.save_manual_combo(original)

    updated = _combo(1, 2, rating=8, note="改评级")
    manager.save_manual_combo(updated, previous=original)

    assert manager.get_combo(1, 2).rating == 8
    assert len(manager.list_combos()) == 1


def test_save_manual_combo_migrates_key_when_pair_changes(tmp_path):
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    original = _combo(1, 2, rating=5)
    manager.save_manual_combo(original)

    manager.save_manual_combo(_combo(1, 3, rating=5), previous=original)

    assert manager.get_combo(1, 2) is None
    assert manager.get_combo(1, 3) is not None


def test_delete_combo_removes_record(tmp_path):
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    combo = _combo(1, 2)
    manager.save_manual_combo(combo)

    manager.delete_combo(combo)

    assert manager.get_combo(1, 2) is None


# ── 编辑表单 ──────────────────────────────────────────────────────


def test_edit_dialog_rejects_incomplete_or_same_hero(qapp, tmp_path, monkeypatch):
    _app()
    hero_mgr, heroes = _make_hero_mgr()
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    dialog = ComboEditDialog(hero_mgr, manager)
    warnings: list[str] = []
    monkeypatch.setattr(
        "src.ui.library.combo_edit_dialog.QMessageBox.warning",
        lambda _parent, _title, message: warnings.append(message),
    )

    dialog._on_save()
    assert warnings == ["请先选择两名武将"]
    assert dialog.result() != QDialog.DialogCode.Accepted

    dialog._hero1 = heroes[1]
    dialog._hero2 = heroes[1]
    dialog._on_save()
    assert warnings[-1] == "武将 1 与武将 2 不能相同"


def test_edit_dialog_saves_normalized_manual_combo(qapp, tmp_path):
    _app()
    hero_mgr, heroes = _make_hero_mgr()
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    dialog = ComboEditDialog(hero_mgr, manager)
    dialog._hero1 = heroes[3]  # 故意倒序传入，保存时应按 id 归一化
    dialog._hero2 = heroes[1]
    dialog._refresh_hero_slots()
    dialog._rating_spin.setValue(9)
    dialog._hero1_seat_checks[1].setChecked(True)  # 蒙恬 2号位
    dialog._hero2_seat_checks[0].setChecked(True)  # 荆轲 1号位
    dialog._note_edit.setPlainText("先手控场")

    dialog._on_save()

    saved = manager.get_combo(1, 3)
    assert saved is not None
    assert (saved.hero1_id, saved.hero2_id) == (1, 3)
    assert saved.hero1_name == "荆轲"
    assert saved.manual is True
    assert saved.position == "12"
    assert saved.hero2_seats == [2]


def test_edit_dialog_warns_before_overwriting_existing_pair(qapp, tmp_path, monkeypatch):
    _app()
    hero_mgr, heroes = _make_hero_mgr()
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    manager.save_manual_combo(_combo(1, 2, rating=5))
    dialog = ComboEditDialog(hero_mgr, manager)
    dialog._hero1 = heroes[1]
    dialog._hero2 = heroes[2]
    dialog._rating_spin.setValue(9)
    answers: list[str] = []
    monkeypatch.setattr(
        "src.ui.library.combo_edit_dialog.QMessageBox.question",
        lambda *_args, **_kwargs: answers.append("asked") or QMessageBox.StandardButton.No,
    )

    dialog._on_save()
    assert manager.get_combo(1, 2).rating == 5  # 选否：未覆盖

    monkeypatch.setattr(
        "src.ui.library.combo_edit_dialog.QMessageBox.question",
        lambda *_args, **_kwargs: answers.append("asked") or QMessageBox.StandardButton.Yes,
    )
    dialog._on_save()
    assert manager.get_combo(1, 2).rating == 9  # 选是：覆盖并转手工


def test_edit_dialog_prefill_and_key_migration(qapp, tmp_path):
    _app()
    hero_mgr, heroes = _make_hero_mgr()
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    original = _combo(1, 2, rating=6, note="原始备注")
    manager.save_manual_combo(original)

    dialog = ComboEditDialog(hero_mgr, manager, combo=original)
    assert dialog._hero1.name == "荆轲"
    assert dialog._rating_spin.value() == 6

    dialog._hero2 = heroes[3]
    dialog._on_save()

    assert manager.get_combo(1, 2) is None
    assert manager.get_combo(1, 3) is not None


# ── 管理对话框 ────────────────────────────────────────────────────


def _make_manager_with_combos(tmp_path) -> ComboManager:
    manager = ComboManager(tmp_path / "combos.json")
    manager.load()
    manager.save_manual_combo(_combo(1, 2, rating=9))
    manager.save_manual_combo(_combo(1, 3, rating=5))
    imported = _combo(2, 3, rating=7, manual=False)
    manager.update(imported, manager._combo_key(2, 3))
    return manager


def test_management_dialog_lists_counts_and_filters(qapp, tmp_path):
    _app()
    hero_mgr, _heroes = _make_hero_mgr()
    manager = _make_manager_with_combos(tmp_path)
    dialog = ComboManagementDialog(hero_mgr, manager)

    assert "共 3 条 · 手工 2 · 导入 1" in dialog._summary_label.text()
    row_texts = [
        label.text()
        for row_index in range(dialog._rows_layout.count())
        if (row := dialog._rows_layout.itemAt(row_index).widget()) is not None
        for label in row.findChildren(QLabel)
    ]
    assert any("★9" in text for text in row_texts)

    dialog._hero_filter.setCurrentIndex(1)  # 荆轲(id=1)
    assert "共 3 条" in dialog._summary_label.text()
    dialog._manual_only_check.setChecked(True)

    visible_rows = sum(
        1
        for row_index in range(dialog._rows_layout.count())
        if dialog._rows_layout.itemAt(row_index).widget() is not None
    )
    assert visible_rows == 2  # 荆轲的两条手工记录


def test_management_dialog_delete_emits_and_persists(qapp, tmp_path, monkeypatch):
    _app()
    hero_mgr, _heroes = _make_hero_mgr()
    manager = _make_manager_with_combos(tmp_path)
    dialog = ComboManagementDialog(hero_mgr, manager)
    changed = []
    dialog.combos_changed.connect(lambda: changed.append(1))
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    row = dialog._rows_layout.itemAt(0).widget()
    delete_button = next(button for button in row.findChildren(QPushButton) if button.text() == "删除")
    delete_button.click()

    assert changed == [1]
    assert manager.get_combo(1, 2) is None


def test_management_dialog_add_opens_edit_dialog_and_refreshes(qapp, tmp_path, monkeypatch):
    _app()
    from src.ui.library import combo_management_dialog as dialog_module

    hero_mgr, _heroes = _make_hero_mgr()
    manager = _make_manager_with_combos(tmp_path)
    dialog = ComboManagementDialog(hero_mgr, manager)
    changed = []
    dialog.combos_changed.connect(lambda: changed.append(1))

    class FakeEditDialog(QDialog):
        def __init__(self, hero_mgr_arg, manager_arg, combo=None, parent=None):
            super().__init__(parent)
            manager_arg.save_manual_combo(_combo(3, 4, rating=10))

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialog_module, "ComboEditDialog", FakeEditDialog)

    dialog._add_button.click()

    assert changed == [1]
    assert "共 4 条 · 手工 3 · 导入 1" in dialog._summary_label.text()
