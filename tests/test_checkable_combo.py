"""可勾选下拉控件的选择集合、标签折叠与弹层生命周期（第 11 批审查 P2-4 补测）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPushButton,
    QToolButton,
    QWidget,
)
from shiboken6 import isValid
from src.ui.shared.checkable_combo import CheckableComboBox, TagLineEdit


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _popup_buttons(combo: CheckableComboBox) -> dict[str, QPushButton]:
    return {button.text(): button for button in combo._popup.findChildren(QPushButton)}


def _popup_list(combo: CheckableComboBox) -> QListWidget:
    return combo._popup.findChildren(QListWidget)[0]


def _popup_search(combo: CheckableComboBox) -> QLineEdit:
    return combo._popup.findChildren(QLineEdit)[0]


def test_set_items_default_all_keeps_filter_semantics() -> None:
    """#53：势力筛选默认全选，单选/归类场景显式传 False 避免误全选。"""
    _app()
    combo = CheckableComboBox()

    combo.set_items(["魏", "蜀", "群"])
    assert combo.checked_values() == {"魏", "蜀", "群"}

    combo.set_items(["魏", "蜀", "群"], default_all=False)
    assert combo.checked_values() == set()


def test_set_checked_intersects_values_and_stays_silent() -> None:
    _app()
    combo = CheckableComboBox()
    combo.set_items(["魏", "蜀"], default_all=False)
    events: list[bool] = []
    combo.checked_values_changed.connect(lambda: events.append(True))

    combo.set_checked(["魏", "不存在势力"])

    assert combo.checked_values() == {"魏"}
    assert events == []


def test_checked_values_returns_copy() -> None:
    _app()
    combo = CheckableComboBox()
    combo.set_items(["魏", "蜀"], default_all=False)
    combo.set_checked(["魏"])

    snapshot = combo.checked_values()
    snapshot.discard("魏")

    assert combo.checked_values() == {"魏"}


def test_tag_line_edit_folds_tags_beyond_five() -> None:
    _app()
    tag = TagLineEdit()

    tag.set_tags(["魏", "蜀", "吴", "群", "晋", "楚"])

    texts = [button.text() for button in tag.findChildren(QToolButton)]
    assert "魏  ×" in texts
    assert "楚  ×" not in texts
    assert "+1" in texts


def test_tag_button_click_emits_tag_removed() -> None:
    _app()
    tag = TagLineEdit()
    tag.set_tags(["魏", "蜀"])
    received: list[str] = []
    tag.tag_removed.connect(received.append)

    for button in tag.findChildren(QToolButton):
        if button.text() == "魏  ×":
            button.click()

    assert received == ["魏"]


def test_remove_tag_updates_checked_and_emits() -> None:
    _app()
    combo = CheckableComboBox()
    combo.set_items(["魏", "蜀"])
    events: list[bool] = []
    combo.checked_values_changed.connect(lambda: events.append(True))

    combo._remove_tag("魏")

    assert combo.checked_values() == {"蜀"}
    assert events == [True]


def test_popup_cycle_releases_layer_and_reopens() -> None:
    """closePopup 必须 deleteLater 释放弹层（挂 window() 不随组件连坐，仅 close 会残留）。"""
    _app()
    top = QWidget()
    layout = QHBoxLayout(top)
    combo = CheckableComboBox()
    layout.addWidget(combo)
    top.show()
    combo.set_items(["魏", "蜀"])

    combo.showPopup()
    popup = combo._popup
    assert popup is not None
    assert popup.isVisible()

    combo.closePopup()
    assert combo._popup is None
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(popup)

    combo.showPopup()
    assert combo._popup is not None
    assert combo._popup is not popup
    combo.closePopup()


def test_popup_select_all_and_invert_apply_to_visible_scope() -> None:
    _app()
    combo = CheckableComboBox()
    combo.set_items(["魏", "蜀", "群"], default_all=False)

    combo.showPopup()
    search = _popup_search(combo)
    search.setText("魏")
    assert _popup_list(combo).count() == 1

    _popup_buttons(combo)["全选"].click()
    assert combo.checked_values() == {"魏"}

    search.setText("")
    _popup_buttons(combo)["反选"].click()
    assert combo.checked_values() == {"蜀", "群"}
    combo.closePopup()


def test_popup_item_check_state_syncs_selection() -> None:
    _app()
    combo = CheckableComboBox()
    combo.set_items(["魏", "蜀", "群"], default_all=False)

    combo.showPopup()
    faction_list = _popup_list(combo)
    faction_list.item(0).setCheckState(Qt.CheckState.Checked)

    assert combo.checked_values() == {"魏"}

    faction_list.item(0).setCheckState(Qt.CheckState.Unchecked)

    assert combo.checked_values() == set()
    combo.closePopup()
