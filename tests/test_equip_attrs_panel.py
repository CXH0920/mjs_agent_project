# -*- coding: utf-8 -*-
"""装备属性维护面板 UI 测试。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from src.data.equip_attrs_repository import EquipAttrsRepository
from src.ui.maintenance.equip_attrs_panel import EquipAttrsPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _repo(tmp_path: Path) -> EquipAttrsRepository:
    path = tmp_path / "equip_attrs.json"
    _write(path, [
        {"name": "赤兔", "subtype": "坐骑", "attack_range": None, "distance_mod": -1, "note": "距离-1"},
        {"name": "亮银枪", "subtype": "武器", "attack_range": 3, "distance_mod": None, "note": "范围3"},
    ])
    repo = EquipAttrsRepository(path)
    repo.load()
    return repo


def test_panel_renders_rows(tmp_path: Path) -> None:
    _app()
    panel = EquipAttrsPanel(_repo(tmp_path))
    assert panel._table.rowCount() == 2
    assert panel._table.item(0, 0).text() == "赤兔"
    assert panel._table.item(1, 2).text() == "3"


def test_save_persists_edits(tmp_path: Path) -> None:
    _app()
    repo = _repo(tmp_path)
    panel = EquipAttrsPanel(repo)
    panel._table.item(1, 2).setText("4")
    changed = []
    panel.data_changed.connect(lambda: changed.append(1))
    panel._save()
    assert repo.get_equip("亮银枪").attack_range == 4
    assert changed == [1]
    assert json.loads((tmp_path / "equip_attrs.json").read_text(encoding="utf-8"))[1]["attack_range"] == 4


def test_save_rejects_invalid_subtype(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel = EquipAttrsPanel(_repo(tmp_path))
    panel._table.item(0, 1).setText("飞船")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    panel._save()
    assert warnings, "非法细分类型应触发校验警告"


def test_save_rejects_invalid_range(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel = EquipAttrsPanel(_repo(tmp_path))
    panel._table.item(1, 2).setText("abc")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    panel._save()
    assert warnings, "非法攻击范围应触发校验警告"


def test_save_rejects_invalid_distance(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel = EquipAttrsPanel(_repo(tmp_path))
    panel._table.item(0, 3).setText("2")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    panel._save()
    assert warnings, "非法距离修正应触发校验警告"


def test_name_and_note_columns_readonly(tmp_path: Path) -> None:
    _app()
    panel = EquipAttrsPanel(_repo(tmp_path))
    assert not (panel._table.item(0, 0).flags() & Qt.ItemFlag.ItemIsEditable)
    assert not (panel._table.item(0, 4).flags() & Qt.ItemFlag.ItemIsEditable)
