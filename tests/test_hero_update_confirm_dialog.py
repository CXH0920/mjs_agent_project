"""武将数据更新确认对话框测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.ui.data_admin import hero_update_confirm_dialog as dialog_module
from src.ui.data_admin.hero_update_confirm_dialog import HeroUpdateConfirmDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _candidate(**overrides) -> dict:
    candidate = {
        "name": "贾诩",
        "hero_id": 161,
        "change": "增强",
        "source": "公告：8月13日停服更新预告",
        "known": True,
        "summary": ["定位：本地「共计」→ 官网「控制」"],
        "local_full": "本地全文",
        "official_full": "官网全文",
    }
    candidate.update(overrides)
    return candidate


def test_dialog_renders_candidates() -> None:
    _app()
    dialog = HeroUpdateConfirmDialog([
        _candidate(),
        _candidate(name="东方朔", hero_id=None, change="新增", known=False,
                   summary=["官网新增：东方朔（本地未收录，ID 188）"]),
    ])
    assert dialog._list.count() == 2
    assert "贾诩（增强）" in dialog._list.item(0).text()
    assert "东方朔（新增）·未收录" in dialog._list.item(1).text()
    assert "8月13日停服更新预告" in dialog._list.item(0).text()
    assert dialog._list.item(0).checkState() == Qt.CheckState.Checked
    dialog.close()


def test_clear_and_select_all() -> None:
    _app()
    dialog = HeroUpdateConfirmDialog([
        _candidate(),
        _candidate(name="马钧", hero_id=184, change="削弱"),
    ])
    dialog._clear_selection()
    assert all(
        dialog._list.item(index).checkState() == Qt.CheckState.Unchecked
        for index in range(2)
    )
    dialog._select_all()
    assert all(
        dialog._list.item(index).checkState() == Qt.CheckState.Checked
        for index in range(2)
    )
    dialog.close()


def test_accept_selection_excludes_unchecked_and_new() -> None:
    _app()
    dialog = HeroUpdateConfirmDialog([
        _candidate(),
        _candidate(name="东方朔", hero_id=None, change="新增", known=False),
    ])
    dialog._list.item(1).setCheckState(Qt.CheckState.Unchecked)
    dialog._accept_selection()
    assert dialog.selected_ids == [161]
    assert dialog.update_new is False


def test_accept_selection_includes_checked_new() -> None:
    _app()
    dialog = HeroUpdateConfirmDialog([
        _candidate(),
        _candidate(name="东方朔", hero_id=None, change="新增", known=False),
    ])
    dialog._accept_selection()
    assert dialog.selected_ids == [161]
    assert dialog.update_new is True


def test_diff_detail_dialog_has_tabs_and_diff_view() -> None:
    from src.ui.data_admin.hero_update_confirm_dialog import HeroDiffDetailDialog

    _app()
    dialog = HeroDiffDetailDialog("标题", "旧行\n相同行", "新行\n相同行")
    assert dialog._tabs.count() == 3
    assert dialog._tabs.tabText(0) == "差异对比"
    assert dialog._tabs.tabText(1) == "本地原文"
    assert dialog._tabs.tabText(2) == "官网原文"
    assert dialog._tabs.currentIndex() == 0
    html = dialog._diff_browser.toHtml()
    assert "-" in html and "+" in html
    assert "相同行" in dialog._local_browser.toPlainText()
    dialog.close()


def test_show_detail_opens_comparison(monkeypatch) -> None:
    _app()
    opened = []

    class _FakeDetailDialog:
        def __init__(self, title, local_text, official_text, parent=None):
            opened.append((title, local_text, official_text))

        def exec(self):
            return 0

    monkeypatch.setattr(dialog_module, "HeroDiffDetailDialog", _FakeDetailDialog)
    dialog = HeroUpdateConfirmDialog([_candidate()])
    dialog._list.setCurrentRow(0)
    dialog._show_detail()
    assert len(opened) == 1
    assert opened[0][0] == "贾诩 本地 vs 官网"
    assert opened[0][1] == "本地全文"
    assert opened[0][2] == "官网全文"
    dialog.close()


def test_summary_browser_shows_selected_candidate() -> None:
    _app()
    dialog = HeroUpdateConfirmDialog([_candidate()])
    dialog._list.setCurrentRow(0)
    assert "定位" in dialog._summary_browser.toPlainText()
    dialog.close()