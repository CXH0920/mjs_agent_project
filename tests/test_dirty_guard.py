"""草稿丢弃保护 mixin 的行为测试（值快照比对 + reject 拦截）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLineEdit
from src.ui.shared.dirty_guard import DirtyGuardDialogMixin


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _DraftDialog(DirtyGuardDialogMixin, QDialog):
    """最小草稿对话框：草稿 = 文本框内容 + 可变嵌套结构（验证基线隔离）。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit("base")
        self.nested = {"text": "base"}
        self.confirm_calls = 0
        self.confirm_answer = False

    def draft_state(self):
        return (self.edit.text(), self.nested)

    def _confirm_discard(self) -> bool:
        self.confirm_calls += 1
        return self.confirm_answer


def _dialog() -> _DraftDialog:
    _app()
    dialog = _DraftDialog()
    dialog.mark_draft_loaded()
    return dialog


def test_reject_without_changes_closes_without_confirmation() -> None:
    dialog = _dialog()

    dialog.reject()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.confirm_calls == 0


def test_dirty_reject_stays_open_when_discard_declined() -> None:
    dialog = _dialog()
    dialog.edit.setText("changed")

    dialog.reject()

    assert dialog.result() == 0  # 未进入 Rejected，留在对话框
    assert dialog.confirm_calls == 1


def test_dirty_reject_closes_after_confirmation() -> None:
    dialog = _dialog()
    dialog.edit.setText("changed")
    dialog.confirm_answer = True

    dialog.reject()

    assert dialog.result() == QDialog.DialogCode.Rejected


def test_reverting_to_baseline_is_not_dirty() -> None:
    dialog = _dialog()
    dialog.edit.setText("changed")
    dialog.edit.setText("base")  # 改回原值判净，不弹确认

    dialog.reject()

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.confirm_calls == 0


def test_mutable_draft_state_is_isolated_from_baseline() -> None:
    dialog = _dialog()
    dialog.nested["text"] = "mutated in place"  # draft_state 返回可变结构引用

    assert dialog.is_dirty() is True  # 基线 deepcopy 隔离，原地改不污染快照
