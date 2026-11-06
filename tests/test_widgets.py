# -*- coding: utf-8 -*-
"""ui/shared/widgets.close_after_toast 判活守卫（第 11 批审查 P3-6 补测）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid
from src.ui.shared.widgets import close_after_toast


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_close_after_toast_accepts_live_dialog() -> None:
    """正常路径：延迟到点后对话框以 accept 关闭。"""
    _app()
    dialog = QDialog()
    close_after_toast(dialog, "已保存", 10)
    QTest.qWait(80)
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_close_after_toast_skips_destroyed_dialog(monkeypatch) -> None:
    """延迟窗口内对话框已销毁：到点回调必须判活跳过，不得触碰已亡对象。

    无守卫时 dialog.accept() 抛 RuntimeError，PySide6 经 sys.excepthook 报告——
    捕获 excepthook 即可让缺守卫的本用例确定性失败。
    """
    _app()
    errors: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *args: errors.append(args))
    dialog = QDialog()
    close_after_toast(dialog, "已保存", 10)
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(dialog, QEvent.DeferredDelete)  # 同步完成销毁
    assert not isValid(dialog)

    QTest.qWait(80)  # 定时器到点

    assert errors == []
