# -*- coding: utf-8 -*-
"""只读文本查看对话框测试：元信息渲染与简易查找（命中/回绕/无匹配）。"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from src.ui.maintenance.readonly_viewer_dialog import ReadonlyViewerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _dialog(body: str = "") -> ReadonlyViewerDialog:
    return ReadonlyViewerDialog(
        "标题", "副标题", "元信息一行", body or "alpha\nbeta\ngamma")


def test_renders_meta_note_and_body() -> None:
    _app()
    dialog = _dialog()
    assert dialog.meta_label.text() == "元信息一行"
    assert not hasattr(dialog, "note_label")  # note 为空时不创建
    assert "beta" in dialog.body.toPlainText()
    dialog.deleteLater()


def test_note_shown_with_warning_prefix() -> None:
    _app()
    dialog = ReadonlyViewerDialog("t", "s", "m", "body", note="正文为预览")
    assert dialog.note_label.text().startswith("⚠ 正文为预览")
    dialog.deleteLater()


def test_find_next_wraps_and_reports_no_match() -> None:
    _app()
    dialog = ReadonlyViewerDialog("t", "s", "m", "foo\nbar\nfoo")
    dialog.search_edit.setText("foo")
    dialog._find_next()  # 命中第一处
    cursor = dialog.body.textCursor()
    assert cursor.selectedText() == "foo"
    dialog._find_next()  # 命中第二处
    dialog._find_next()  # 尾后回绕到开头仍命中
    assert dialog._find_status.text() == ""
    dialog.search_edit.setText("不存在的词")
    dialog._find_next()
    assert dialog._find_status.text() == "无匹配"
    dialog.search_edit.clear()
    dialog._find_next()
    assert dialog._find_status.text() == ""
    dialog.deleteLater()
