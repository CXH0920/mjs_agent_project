"""官方数据导入对话框调度测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QApplication, QDialog, QListWidgetItem, QMessageBox

from src.business.recognition.ocr_worker import OfficialImportTask
from src.ui.data_admin.official_data_import_dialog import OfficialDataImportDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dialog_submits_ordered_pages_to_capture_service(monkeypatch) -> None:
    _app()
    submitted: list[dict[str, list[str]]] = []
    failures: list[str] = []

    class FakeCaptureService(QObject):
        official_import_progress = Signal(str, int, int)
        official_import_completed = Signal(object)
        official_import_failed = Signal(str)

        def submit_official_import(self, paths):
            submitted.append(paths)
            return OfficialImportTask({
                key: tuple(selected) for key, selected in paths.items()
            })

    service = FakeCaptureService()
    dialog = OfficialDataImportDialog(service)
    for path in ("page1.jpg", "page2.jpg"):
        item = QListWidgetItem(path)
        item.setData(Qt.ItemDataRole.UserRole, path)
        dialog._paths["exile"].addItem(item)
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda _parent, _title, message: failures.append(message),
    )

    dialog._start_import()
    service.official_import_progress.emit("正在识别", 3, 10)
    dialog._task.completed.set()
    service.official_import_completed.emit([{
        "name": "武将放逐数据",
        "pages": 2,
        "records": 100,
        "reviews": 1,
    }])
    service.official_import_failed.emit("不应发送到已关闭的对话框")

    assert submitted == [{"exile": ["page1.jpg", "page2.jpg"]}]
    assert dialog._progress_label.text() == "正在识别"
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert "2 张图片" in dialog._shared_toast_overlay.text()
    assert failures == []

class _FakeCaptureService(QObject):
    official_import_progress = Signal(str, int, int)
    official_import_completed = Signal(object)
    official_import_failed = Signal(str)

    def submit_official_import(self, paths):
        return OfficialImportTask({
            key: tuple(selected) for key, selected in paths.items()
        })


def test_failed_import_offers_review_when_pending_session_exists(monkeypatch) -> None:
    _app()
    from src.ui.data_admin import official_data_import_dialog as dialog_module

    opened: list[dict] = []
    pending = {"key": "2v2", "outputs": {}}
    monkeypatch.setattr(dialog_module, "load_pending_session", lambda: pending)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        dialog_module.OfficialDataImportDialog,
        "_open_review_dialog",
        lambda self, session: opened.append(session),
    )

    dialog = OfficialDataImportDialog(_FakeCaptureService())
    dialog._on_failed("导入失败")

    assert opened == [pending]


def test_failed_import_without_pending_does_not_ask_review(monkeypatch) -> None:
    _app()
    from src.ui.data_admin import official_data_import_dialog as dialog_module

    questions: list = []
    monkeypatch.setattr(dialog_module, "load_pending_session", lambda: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: questions.append(1) or QMessageBox.StandardButton.No,
    )

    dialog = OfficialDataImportDialog(_FakeCaptureService())
    dialog._on_failed("导入失败")

    assert questions == []
