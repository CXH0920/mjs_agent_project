"""卡牌百科更新对话框冒烟测试（offscreen）。"""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.business.card_sync import CardSyncService
from src.data.card_catalog import CardRepository
from src.ui.data_admin.card_sync_dialog import CardSyncDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _make_repo(tmp_path, cards: list[dict]) -> CardRepository:
    (tmp_path / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    repo = CardRepository(tmp_path / "cards.json")
    repo.load()
    return repo


def _official(card_id: int, desc: str) -> dict:
    return {"id": card_id, "name": f"卡{card_id}", "card_type": "行动牌",
            "card_desc": desc, "card_detail": "<p>详解</p>"}


def test_dialog_lists_diff_and_apply_updates_repo(tmp_path, monkeypatch) -> None:
    _app()
    repo = _make_repo(tmp_path, [
        {"id": "1", "name": "卡1", "card_type": "行动牌",
         "card_desc": "旧描述", "card_detail": "详解", "card_amount": 3},
        {"id": "2", "name": "卡2", "card_type": "战法牌",
         "card_desc": "同样旧", "card_detail": "详解", "card_amount": 1},
    ])
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official(1, "旧描述"), _official(2, "官网新描述")])
    # 首跑建基线，再取一次带 diff 的检查结果，直接喂给对话框（不走真线程）
    service._finalize_check(service._do_check())
    result = service._do_check()
    service._finalize_check(result)

    dialog = CardSyncDialog(service, repo, card_point_names=[], parent=None, auto_check=False)
    dialog._on_check_finished(result)

    # 两个候选（卡1 一致不出现；卡2 修改）默认全选
    assert dialog._list.count() == 1
    assert dialog._checked_candidates()[0]["card_id"] == "2"

    dialog._apply_selected()

    assert repo.get_card("2").card_desc == "官网新描述"
    assert repo.get_card("2").card_amount == 1  # 数量保留
    assert dialog._list.count() == 0
    assert dialog.applied_count == 1
    assert not dialog._apply_button.isEnabled()
    records = json.loads((tmp_path / "changes.json").read_text(encoding="utf-8"))
    assert records == [{"date": records[0]["date"], "applied_ids": ["2"], "added_ids": []}]


def test_dialog_marks_removed_cards_as_display_only(tmp_path, monkeypatch) -> None:
    _app()
    repo = _make_repo(tmp_path, [
        {"id": "1", "name": "卡1", "card_type": "行动牌",
         "card_desc": "旧描述", "card_detail": "详解", "card_amount": 1},
        {"id": "2", "name": "卡2", "card_type": "行动牌",
         "card_desc": "x", "card_detail": "y", "card_amount": 1},
    ])
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official(1, "旧描述")])
    service._finalize_check(service._do_check())
    # 官网只剩卡1：卡2 为 removed
    result = service._do_check()
    service._finalize_check(result)

    dialog = CardSyncDialog(service, repo, card_point_names=["卡2"], parent=None, auto_check=False)
    dialog._on_check_finished(result)

    assert dialog._list.count() == 1
    item = dialog._list.item(0)
    candidate = item.data(Qt.ItemDataRole.UserRole)
    assert candidate["change"] == "removed"
    assert not (item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert "点数表" in candidate["summary"][0]  # 点数提醒
    assert not dialog._apply_button.isEnabled()
