# -*- coding: utf-8 -*-
"""武将分类维护面板 UI 测试：分类/克制链/武将归类交互。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QWidget
from src.data.hero_classification_repository import HeroClassificationRepository
from src.ui.maintenance.classification.category_tab import CategoryEditDialog
from src.ui.maintenance.hero_classification_panel import HeroClassificationPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _panel(tmp_path: Path) -> tuple[HeroClassificationPanel, Path]:
    path = tmp_path / "hero_classification.json"
    _write(path, {
        "categories": [
            {"name": "高爆发型", "core_features": "一回合多段输出", "ratio": "~8%"},
            {"name": "防御/保核型", "core_features": "抗压", "ratio": ""},
        ],
        "hero_categories": {"庞煖": ["高爆发型"]},
        "counter_chain": {"高爆发型": "防御/保核型"},
    })
    repo = HeroClassificationRepository(path, hero_names={"庞煖", "典韦"})
    panel = HeroClassificationPanel(repo, {"庞煖": "攻击"}, root=tmp_path)
    return panel, path


def _write_heroes(tmp_path: Path, names: list[str]) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    _write(data_dir / "heroes.json", [{"name": name} for name in names])


def test_panel_renders_tabs(tmp_path: Path) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    assert panel._tabs.count() == 3
    assert panel._category_tab.category_list.count() == 2
    assert panel._hero_tab.hero_list.count() == 2
    assert panel._repo.list_unclassified() == ["典韦"]


def test_add_category_marks_dirty(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    dialog = CategoryEditDialog(None)
    dialog._name_edit.setText("控制/扰乱型")
    dialog._features_edit.setPlainText("打乱节奏")
    dialog._accept_if_valid()
    panel._repo.add_category(dialog.category())
    panel._category_tab.current_category = "控制/扰乱型"
    panel._category_tab.refresh()
    panel._mark_dirty()
    assert panel._repo.get_category("控制/扰乱型") is not None
    assert "未保存修改" in panel._status_label.text()


def test_chain_edit_and_save_emits_signal(tmp_path: Path) -> None:
    _app()
    panel, path = _panel(tmp_path)
    signals = []
    panel.data_changed.connect(lambda: signals.append(True))
    panel._chain_tab.chain_category_combo.setCurrentText("高爆发型")
    panel._chain_tab.chain_edit.blockSignals(True)
    panel._chain_tab.chain_edit.setPlainText("防御/保核型（不给发育时间）")
    panel._chain_tab.chain_edit.blockSignals(False)
    panel._chain_tab.on_chain_text_changed()
    panel._save()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["counter_chain"]["高爆发型"] == "防御/保核型（不给发育时间）"
    assert signals == [True]
    assert "已保存" in panel._status_label.text()


def test_hero_categorization_filter_and_edit(tmp_path: Path) -> None:
    _app()
    panel, path = _panel(tmp_path)
    panel._hero_tab.hero_filter.setCurrentText("未归类")
    assert panel._hero_tab.hero_list.count() == 1
    assert panel._hero_tab.hero_list.item(0).data(0x0100) == "典韦"
    panel._hero_tab.hero_list.setCurrentRow(0)
    assert panel._hero_tab.current_hero == "典韦"
    panel._hero_tab.hero_combo.set_checked(["防御/保核型"])
    panel._hero_tab.on_hero_categories_changed()
    panel._save()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["hero_categories"]["典韦"] == ["防御/保核型"]
    panel._hero_tab.hero_filter.setCurrentText("已归类")
    assert panel._hero_tab.hero_list.count() == 2


def test_goto_next_unclassified(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a))
    panel._hero_tab.goto_next_unclassified()
    assert panel._hero_tab.hero_filter.currentText() == "未归类"
    assert panel._hero_tab.current_hero == "典韦"
    panel._repo.set_hero_categories("典韦", ["高爆发型"])
    panel._hero_tab.goto_next_unclassified()
    assert infos, "全部已归类时应提示完成"


def test_delete_category_cleans_references(tmp_path: Path, monkeypatch) -> None:
    _app()
    panel, _ = _panel(tmp_path)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    panel._category_tab.current_category = "高爆发型"
    panel._category_tab.delete_current()
    assert panel._repo.get_category("高爆发型") is None
    assert panel._repo.get_chain_description("高爆发型") == ""
    assert panel._repo.get_hero_categories("庞煖") == []


def _detail_surface(panel: HeroClassificationPanel) -> QWidget:
    """取分类详情区第一块 surface（QFrame），供收集其中 QLabel。"""
    return panel._category_tab._category_detail_layout.itemAt(0).widget()


def test_category_detail_shows_actual_members(tmp_path: Path) -> None:
    """详情区展示实际归类名单：典型后缀、badge 实际计数。"""
    _app()
    panel, _ = _panel(tmp_path)
    panel._repo.set_hero_categories("典韦", ["高爆发型"])
    panel._category_tab.current_category = "高爆发型"
    panel._category_tab.refresh()
    surface = _detail_surface(panel)
    texts = [label.text() for label in surface.findChildren(QLabel)]
    assert "已归类 2 名" in texts
    assert "典韦、庞煖" in texts
    assert surface.findChild(QLabel, "statusBadge").isHidden() is False
    # ≤8 人全量展示，无需展开按钮
    assert not any("展开全部" in b.text() for b in surface.findChildren(QPushButton))


def test_category_detail_empty_members_hides_zero_badge(tmp_path: Path) -> None:
    """空分类：空态文案、badge 隐藏，不出现「0 名」计数。"""
    _app()
    panel, _ = _panel(tmp_path)
    panel._category_tab.current_category = "防御/保核型"
    panel._category_tab.refresh()
    surface = _detail_surface(panel)
    texts = [label.text() for label in surface.findChildren(QLabel)]
    assert "该分类下暂无已归类武将" in texts
    assert surface.findChild(QLabel, "statusBadge").isHidden()
    # 隐藏的 badge 仍持有「已归类 0 名」文本，验收口径是可见文本中不出现 0 计数
    visible_texts = [label.text() for label in surface.findChildren(QLabel) if not label.isHidden()]
    assert not any("0 名" in text for text in visible_texts)


def test_hero_categorization_updates_category_detail(tmp_path: Path) -> None:
    """归类页签变更后，分类管理详情名单随内存态即时刷新（所见即所得）。"""
    _app()
    panel, _ = _panel(tmp_path)
    panel._category_tab.current_category = "高爆发型"
    panel._category_tab.refresh()
    panel._hero_tab.hero_filter.setCurrentText("未归类")
    panel._hero_tab.hero_list.setCurrentRow(0)
    panel._hero_tab.hero_combo.set_checked(["高爆发型"])
    panel._hero_tab.on_hero_categories_changed()
    texts = [label.text() for label in _detail_surface(panel).findChildren(QLabel)]
    assert "典韦、庞煖" in texts
    assert "已归类 2 名" in texts


def test_category_detail_member_list_collapsed_by_default(tmp_path: Path) -> None:
    """>8 人名单默认只列前 8 名，「展开全部」显示全量，「收起」还原；
    展开态按分类名单槽记忆，切走自然收起、切回保持展开。"""
    _app()
    panel, _ = _panel(tmp_path)
    panel._repo.update_hero_names({"庞煖"} | {f"武将{i}" for i in range(1, 10)})
    panel._repo.set_hero_categories("庞煖", ["高爆发型"])
    for i in range(1, 10):
        panel._repo.set_hero_categories(f"武将{i}", ["高爆发型"])
    panel._category_tab.current_category = "高爆发型"
    panel._category_tab.refresh()

    def _member_items() -> list[str]:
        surface = _detail_surface(panel)
        body = next(label for label in surface.findChildren(QLabel) if "、" in label.text())
        return body.text().split("、")

    def _toggle_button() -> QPushButton:
        return next(b for b in _detail_surface(panel).findChildren(QPushButton)
                    if b.text() in ("收起",) or "展开全部" in b.text())

    assert len(_member_items()) == 8
    assert "共 10 名" in _toggle_button().text()
    _toggle_button().click()
    assert len(_member_items()) == 10
    assert _toggle_button().text() == "收起"
    _toggle_button().click()
    assert len(_member_items()) == 8
    # 再次展开后切走再切回：展开态按分类名记忆（_expanded_category 单槽），
    # 切走的新分类自然收起，切回原分类保持展开
    _toggle_button().click()
    assert len(_member_items()) == 10
    panel._category_tab.current_category = "防御/保核型"
    panel._category_tab.refresh()
    panel._category_tab.current_category = "高爆发型"
    panel._category_tab.refresh()
    assert len(_member_items()) == 10


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


def test_refresh_confirms_before_discarding_dirty(tmp_path: Path, monkeypatch) -> None:
    """有未保存修改时刷新需确认；拒绝则保留（#14）。"""
    _app()
    panel, _ = _panel(tmp_path)
    panel._mark_dirty()

    def fake_question(parent, title, text, *args, **kwargs):
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    panel.reload_data()
    assert panel._dirty is True  # 拒绝刷新，修改保留


def test_refresh_yes_discards_dirty(tmp_path: Path, monkeypatch) -> None:
    """确认丢弃后重新加载并重置 dirty（#14）。"""
    _app()
    panel, _ = _panel(tmp_path)
    panel._mark_dirty()

    def fake_question(parent, title, text, *args, **kwargs):
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    panel.reload_data()
    assert panel._dirty is False


def test_load_error_disables_save_button(tmp_path: Path) -> None:
    """数据文件损坏时禁止保存并提示（#12/#13）。"""
    _app()
    path = tmp_path / "hero_classification.json"
    path.write_text("{broken", encoding="utf-8")
    repo = HeroClassificationRepository(path, hero_names={"庞煖"})
    panel = HeroClassificationPanel(repo, {}, root=tmp_path)
    assert not panel._save_button.isEnabled()
    assert "加载异常" in panel._status_label.text()


def test_roster_refresh_picks_up_new_heroes(tmp_path: Path) -> None:
    """爬虫更新 heroes.json 后 refresh_roster 使新武将可见、可归类。"""
    _app()
    panel, _ = _panel(tmp_path)
    assert panel._repo.hero_names == {"庞煖", "典韦"}
    _write_heroes(tmp_path, ["庞煖", "典韦", "王导"])
    panel.refresh_roster()
    assert panel._repo.hero_names == {"庞煖", "典韦", "王导"}
    listed = [panel._hero_tab.hero_list.item(i).data(0x0100) for i in range(panel._hero_tab.hero_list.count())]
    assert "王导" in listed
    assert panel._repo.list_unclassified() == ["典韦", "王导"]
    panel._repo.set_hero_categories("王导", ["高爆发型"])  # 新武将不再被校验拒绝
    assert panel._repo.get_hero_categories("王导") == ["高爆发型"]


def test_reload_data_refreshes_roster(tmp_path: Path) -> None:
    """面板「刷新」按钮与菜单 F5 共用的 reload_data 同步加载新武将名单。"""
    _app()
    panel, _ = _panel(tmp_path)
    _write_heroes(tmp_path, ["庞煖", "典韦", "王导"])
    panel.reload_data()
    assert "王导" in panel._repo.hero_names
    assert panel._repo.list_unclassified() == ["典韦", "王导"]


def test_focus_unclassified_finds_new_hero(tmp_path: Path) -> None:
    """审计「去归类」跳转前同步名单，新武将能被直接定位。"""
    _app()
    panel, _ = _panel(tmp_path)
    panel._repo.set_hero_categories("典韦", ["高爆发型"])  # 先归满现有武将
    _write_heroes(tmp_path, ["庞煖", "典韦", "祖逖"])
    panel.focus_unclassified()
    assert panel._hero_tab.current_hero == "祖逖"


def test_roster_refresh_keeps_names_when_heroes_missing(tmp_path: Path) -> None:
    """heroes.json 缺失/损坏时保留现有名单，面板不瘫痪。"""
    _app()
    panel, _ = _panel(tmp_path)  # tmp_path 下无 data/heroes.json
    panel.refresh_roster()
    assert panel._repo.hero_names == {"庞煖", "典韦"}
    assert panel._hero_tab.hero_list.count() == 2
    assert panel._repo.list_unclassified() == ["典韦"]
