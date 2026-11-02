"""模态编辑保存循环的重试上限与异常分级（第 11 批审查 P2-4 补测）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pydantic import ValidationError
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget
from src.ui.shared import persist as persist_module
from src.ui.shared.persist import run_edit_dialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class _DialogStub:
    """run_edit_dialog 只消费 exec()；预置返回序列驱动重试循环。"""

    def __init__(self, results: list[QDialog.DialogCode]) -> None:
        self._results = list(results)
        self.exec_calls = 0

    def exec(self) -> QDialog.DialogCode:
        self.exec_calls += 1
        if not self._results:
            return QDialog.DialogCode.Rejected
        return self._results.pop(0)


def _patch_feedback(monkeypatch) -> dict[str, list]:
    """弹窗与 toast 全部落桩记录，测试不弹任何真实模态框。"""
    calls: dict[str, list] = {"critical": [], "warning": [], "toast": []}
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a, **k: calls["critical"].append(a))
    )
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: calls["warning"].append(a))
    )
    monkeypatch.setattr(
        persist_module, "show_toast", lambda parent, msg: calls["toast"].append(msg)
    )
    return calls


def _accepted(times: int) -> list[QDialog.DialogCode]:
    return [QDialog.DialogCode.Accepted] * times


def test_reject_returns_false_without_persist(monkeypatch) -> None:
    _app()
    parent = QWidget()
    _patch_feedback(monkeypatch)
    dialog = _DialogStub([QDialog.DialogCode.Rejected])
    attempts: list[None] = []

    assert run_edit_dialog(dialog, lambda: attempts.append(None), parent=parent) is False
    assert attempts == []
    assert dialog.exec_calls == 1


def test_success_shows_toast_once(monkeypatch) -> None:
    _app()
    parent = QWidget()
    calls = _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(1))
    saved: list[bool] = []

    assert run_edit_dialog(dialog, lambda: saved.append(True), parent=parent, success_message="已保存") is True
    assert saved == [True]
    assert calls["toast"] == ["已保存"]
    assert calls["critical"] == []


def test_business_failure_retries_then_succeeds(monkeypatch) -> None:
    _app()
    parent = QWidget()
    calls = _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(2))

    def flaky() -> None:
        if len(calls["critical"]) == 0:
            raise OSError("磁盘占位失败")

    assert run_edit_dialog(dialog, flaky, parent=parent, success_message="已保存") is True
    assert dialog.exec_calls == 2
    assert len(calls["critical"]) == 1
    assert calls["critical"][0][2].startswith("磁盘占位失败")


def test_retry_limit_stops_with_warning(monkeypatch) -> None:
    _app()
    parent = QWidget()
    calls = _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(3))

    def always_fail() -> None:
        raise OSError("权限不足")

    result = run_edit_dialog(dialog, always_fail, parent=parent, attempts=3)

    assert result is False
    assert dialog.exec_calls == 3
    # 每次业务失败都先弹 critical（留痕），达上限后再叠加 warning 提示停止
    assert len(calls["critical"]) == 3
    assert calls["warning"][0][1] == "已停止重试"


def test_unexpected_exception_aborts_without_retry(monkeypatch) -> None:
    _app()
    parent = QWidget()
    calls = _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(5))  # 若误入重试循环将消费多次

    def programmer_error() -> None:
        raise RuntimeError("空指针模拟")

    assert run_edit_dialog(dialog, programmer_error, parent=parent) is False
    assert dialog.exec_calls == 1
    assert len(calls["critical"]) == 1
    assert "非预期" in calls["critical"][0][2]


def test_attempts_none_keeps_retrying_until_success(monkeypatch) -> None:
    _app()
    parent = QWidget()
    calls = _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(6))
    failures = {"count": 0}

    def stubborn_disk() -> None:
        failures["count"] += 1
        if failures["count"] <= 5:
            raise ValueError("校验失败")

    result = run_edit_dialog(dialog, stubborn_disk, parent=parent, attempts=None)

    assert result is True
    assert dialog.exec_calls == 6
    assert len(calls["critical"]) == 5
    assert calls["warning"] == []


def test_on_retry_invoked_after_each_business_failure(monkeypatch) -> None:
    _app()
    parent = QWidget()
    _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(2))
    retries: list[None] = []

    def flaky() -> None:
        if not retries:
            raise OSError("首次失败")

    assert run_edit_dialog(
        dialog, flaky, parent=parent, on_retry=lambda: retries.append(None)
    ) is True
    assert retries == [None]


def test_failure_hint_appended_to_critical_message(monkeypatch) -> None:
    _app()
    parent = QWidget()
    calls = _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(1))

    def fail() -> None:
        raise OSError("写入失败")

    run_edit_dialog(
        dialog, fail, parent=parent, failure_hint="编辑内容已保留", attempts=1
    )
    message = calls["critical"][0][2]
    assert message.startswith("写入失败")
    assert message.endswith("编辑内容已保留")


def test_validation_error_counts_as_retryable(monkeypatch) -> None:
    _app()
    parent = QWidget()
    calls = _patch_feedback(monkeypatch)
    dialog = _DialogStub(_accepted(2))

    def invalid_payload() -> None:
        if len(calls["critical"]) == 0:
            raise ValidationError.from_exception_data("Hero", [])

    assert run_edit_dialog(dialog, invalid_payload, parent=parent) is True
    assert dialog.exec_calls == 2
    assert len(calls["critical"]) == 1
