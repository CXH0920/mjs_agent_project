"""百科忽略名单管理对话框冒烟测试（offscreen）：经业务服务读写名单。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.data.baike_ignore_store import ignore_entry
from src.ui.data_admin.baike_ignore_manager_dialog import BaikeIgnoreManagerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_manager_dialog_lists_and_restores_entries(tmp_path) -> None:
    _app()
    ignore_path = tmp_path / "ignore.json"
    ignore_entry("heroes", "161", "贾诩", "modified", "h1", ignore_path)
    ignore_entry("cards", "5", "火杀", "removed", "", ignore_path)
    hero_service = _HeroServiceStub(ignore_path)
    card_service = _CardServiceStub(ignore_path)

    dialog = BaikeIgnoreManagerDialog(
        parent=None, announcement_service=hero_service, card_sync_service=card_service,
    )
    assert dialog._list.count() == 2
    texts = [dialog._list.item(row).text() for row in range(dialog._list.count())]
    assert any("武将 · 贾诩（修改）" in text for text in texts)
    assert any("卡牌 · 火杀（官网已删除）" in text for text in texts)

    dialog._list.item(0).setSelected(True)
    dialog._restore_selected()
    assert dialog._list.count() == 1

    dialog._restore_all()
    assert dialog._list.count() == 0


class _HeroServiceStub:
    def __init__(self, path) -> None:
        self._path = path

    def list_ignored_heroes(self):
        from src.data.baike_ignore_store import load_baike_ignores
        return load_baike_ignores(self._path).heroes

    def restore_heroes(self, entry_ids=None):
        from src.data.baike_ignore_store import remove_entry
        if entry_ids is None:
            entry_ids = list(self.list_ignored_heroes())
        for entry_id in entry_ids:
            remove_entry("heroes", entry_id, self._path)


class _CardServiceStub:
    def __init__(self, path) -> None:
        self._path = path

    def list_ignored_cards(self):
        from src.data.baike_ignore_store import load_baike_ignores
        return load_baike_ignores(self._path).cards

    def restore_cards(self, entry_ids=None):
        from src.data.baike_ignore_store import remove_entry
        if entry_ids is None:
            entry_ids = list(self.list_ignored_cards())
        for entry_id in entry_ids:
            remove_entry("cards", entry_id, self._path)
