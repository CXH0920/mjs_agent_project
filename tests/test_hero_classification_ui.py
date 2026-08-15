# -*- coding: utf-8 -*-
"""武将分类维护面板 UI 测试：分类/克制链/武将归类交互。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from src.data.hero_classification_repository import HeroClassificationRepository
from src.ui.library.hero_classification_panel import CategoryEditDialog, HeroClassificationPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _panel(tmp_path: Path) -> tuple[HeroClassificationPanel, Path]:
    path = tmp_path / "hero_classification.json"
    _write(path, {
        "categories": [
            {"name": "高爆发型", "core_features": "一回合多段输出", "typical_heroes": ["庞煖"], "ratio": "~8%"},
            {"name": "防御/保核型", "core_features": "抗压", "typical_heroes": [], "ratio": ""},
        ],
        "hero_categories": {"庞煖": ["高爆发型"]},
        "counter_chain": {"高爆发型": "防御/保核型"},
    })
    repo = HeroClassificationRepository(path, hero_names={"庞煖", "典韦"})
    panel = HeroClassificationPanel(repo, {"庞煖": "攻击"})
    return panel, path


def test_panel_renders_tabs(tmp_path: Path) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    assert panel._tabs.count() == 3
    assert panel._category_list.count() == 2
    assert panel._hero_list.count() == 2
    assert panel._repo.list_unclassified() == ["典韦"]


def test_add_category_marks_dirty(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    dialog = CategoryEditDialog(None)
    dialog._name_edit.setText("控制/扰乱型")
    dialog._features_edit.setPlainText("打乱节奏")
    dialog._accept_if_valid()
    panel._repo.add_category(dialog.category())
    panel._current_category = "控制/扰乱型"
    panel._refresh_categories()
    panel._mark_dirty()
    assert panel._repo.get_category("控制/扰乱型") is not None
    assert "未保存修改" in panel._status_label.text()


def test_chain_edit_and_save_emits_signal(tmp_path: Path) -> None:
    _app()
    panel, path = _panel(tmp_path)
    signals = []
    panel.data_changed.connect(lambda: signals.append(True))
    panel._chain_category_combo.setCurrentText("高爆发型")
    panel._chain_edit.blockSignals(True)
    panel._chain_edit.setPlainText("防御/保核型（不给发育时间）")
    panel._chain_edit.blockSignals(False)
    panel._on_chain_text_changed()
    panel._save()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["counter_chain"]["高爆发型"] == "防御/保核型（不给发育时间）"
    assert signals == [True]
    assert "已保存" in panel._status_label.text()


def test_hero_categorization_filter_and_edit(tmp_path: Path) -> None:
    _app()
    panel, path = _panel(tmp_path)
    panel._hero_filter.setCurrentText("未归类")
    assert panel._hero_list.count() == 1
    assert panel._hero_list.item(0).data(0x0100) == "典韦"
    panel._hero_list.setCurrentRow(0)
    assert panel._current_hero == "典韦"
    panel._hero_combo.set_checked(["防御/保核型"])
    panel._on_hero_categories_changed()
    panel._save()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["hero_categories"]["典韦"] == ["防御/保核型"]
    panel._hero_filter.setCurrentText("已归类")
    assert panel._hero_list.count() == 2


def test_goto_next_unclassified(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a))
    panel._goto_next_unclassified()
    assert panel._hero_filter.currentText() == "未归类"
    assert panel._current_hero == "典韦"
    panel._repo.set_hero_categories("典韦", ["高爆发型"])
    panel._goto_next_unclassified()
    assert infos, "全部已归类时应提示完成"


def test_delete_category_cleans_references(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    panel._current_category = "高爆发型"
    panel._delete_category()
    assert panel._repo.get_category("高爆发型") is None
    assert panel._repo.get_chain_description("高爆发型") == ""
    assert panel._repo.get_hero_categories("庞煖") == []


def test_popup_callback_after_combo_destroyed() -> None:
    """组合框被销毁后，其弹出的多选面板仍触发勾选回调不应崩溃。"""
    _app()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidget, QVBoxLayout, QWidget

    from src.ui.shared.checkable_combo import CheckableComboBox

    host = QWidget()
    layout = QVBoxLayout(host)
    combo = CheckableComboBox()
    combo.set_items(["高爆发型", "防御/保核型"])
    layout.addWidget(combo)
    host.show()
    app = _app()
    app.processEvents()
    combo.showPopup()
    app.processEvents()
    assert combo._popup is not None
    assert combo._popup.parent() is host
    popup_list = combo._popup.findChild(QListWidget)
    assert popup_list is not None
    item = popup_list.item(0)
    # 销毁组合框（弹层挂在宿主窗口上仍存活）
    combo.deleteLater()
    app.processEvents()
    assert not combo._popup is None
    # 勾选切换仍触发旧回调（isValid 防护应直接返回）
    item.setCheckState(
        Qt.CheckState.Unchecked
        if item.checkState() == Qt.CheckState.Checked
        else Qt.CheckState.Checked
    )
    app.processEvents()
    host.close()
