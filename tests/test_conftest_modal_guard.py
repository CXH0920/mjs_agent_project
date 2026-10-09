# -*- coding: utf-8 -*-
"""conftest 模态弹窗守卫回归：真实弹窗必须立即报错，用例内替身覆盖仍要生效。

真实 QMessageBox 静态方法在无头测试中 exec() 永久阻塞，曾令 xdist worker 被
pytest-timeout 强杀、失败记在无关用例上（2026-10-09 Actions #178/#179）。
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox


def test_real_modal_raises_instead_of_blocking() -> None:
    with pytest.raises(AssertionError, match="真实模态弹窗 QMessageBox.warning"):
        QMessageBox.warning(None, "标题", "内容")


def test_per_test_stub_overrides_guard(monkeypatch) -> None:
    """用例内 monkeypatch 覆盖守卫后按替身返回：撤销为 LIFO，
    存量校验"弹窗文案/答案"语义的用例不受守卫影响。"""
    monkeypatch.setattr(
        QMessageBox, "question",
        lambda *a, **k: QMessageBox.StandardButton.Yes)
    assert QMessageBox.question(None, "标题", "内容") == QMessageBox.StandardButton.Yes
