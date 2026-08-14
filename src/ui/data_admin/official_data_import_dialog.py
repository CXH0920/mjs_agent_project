"""官方数据导入对话框。"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from src.business.emulator.capture_service import CaptureService
from src.business.recognition.official_data_import_service import load_pending_session
from src.business.recognition.ocr_worker import OfficialImportTask
from src.ui.data_admin.official_import_review_dialog import OfficialImportReviewDialog
from src.ui.shared.widgets import DialogFooter, PageHeader, show_toast


class OfficialDataImportDialog(QDialog):
    """按显示顺序选择一组或两组官方榜单图片并执行导入。"""

    recommendation_indexes_stale = Signal()

    def __init__(self, capture_service: CaptureService, parent=None) -> None:
        super().__init__(parent)
        self._capture_service = capture_service
        self._task: OfficialImportTask | None = None
        self._capture_service.official_import_progress.connect(self._on_progress_changed)
        self._capture_service.official_import_completed.connect(self._on_completed)
        self._capture_service.official_import_failed.connect(self._on_failed)
        self.finished.connect(self._disconnect_capture_signals)
        self.setWindowTitle("官方数据导入")
        self.setMinimumWidth(560)
        self._path_controls = []
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "官方数据导入",
            "按榜单显示顺序添加图片，可分别导入或同时导入两类数据。",
        ))
        form = QFormLayout()
        self._paths = {
            "2v2": self._create_path_list(form, "2v2数据导入"),
            "exile": self._create_path_list(form, "武将放逐数据导入"),
        }
        layout.addLayout(form)
        self._progress_label = QLabel()
        self._progress_label.hide()
        layout.addWidget(self._progress_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.hide()
        layout.addWidget(self._progress_bar)
        self._footer = DialogFooter(accept_text="导入", cancel_text="取消")
        self._import_button = self._footer.accept_button
        self._cancel_button = self._footer.cancel_button
        self._footer.accepted.connect(self._start_import)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    def _create_path_list(self, form: QFormLayout, label: str) -> QListWidget:
        container = QVBoxLayout()
        path_list = QListWidget()
        path_list.setMinimumHeight(82)
        path_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        container.addWidget(path_list)
        buttons = QHBoxLayout()
        add_button = QPushButton("添加图片")
        add_button.clicked.connect(lambda: self._choose_files(path_list))
        remove_button = QPushButton("移除")
        remove_button.clicked.connect(lambda: self._remove_selected(path_list))
        up_button = QPushButton("上移")
        up_button.clicked.connect(lambda: self._move_current(path_list, -1))
        down_button = QPushButton("下移")
        down_button.clicked.connect(lambda: self._move_current(path_list, 1))
        for button in (add_button, remove_button, up_button, down_button):
            buttons.addWidget(button)
            self._path_controls.append(button)
        buttons.addStretch()
        container.addLayout(buttons)
        form.addRow(label, container)
        self._path_controls.append(path_list)
        return path_list

    @staticmethod
    def _natural_path_key(path: str) -> list[tuple[int, str | int]]:
        return [
            (0, int(part)) if part.isdigit() else (1, part.casefold())
            for part in re.split(r"(\d+)", Path(path).name)
        ]

    def _choose_files(self, path_list: QListWidget) -> None:
        current_paths = self._list_paths(path_list)
        start_dir = Path(current_paths[-1]).parent if current_paths else Path.home()
        file_paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择官方榜单图片",
            str(start_dir),
            "图片文件 (*.png *.jpg *.jpeg *.bmp)",
        )
        existing = set(current_paths)
        for file_path in sorted(file_paths, key=self._natural_path_key):
            if file_path in existing:
                continue
            item = QListWidgetItem(Path(file_path).name)
            item.setData(Qt.ItemDataRole.UserRole, file_path)
            item.setToolTip(file_path)
            path_list.addItem(item)
            existing.add(file_path)

    @staticmethod
    def _list_paths(path_list: QListWidget) -> list[str]:
        return [
            path_list.item(index).data(Qt.ItemDataRole.UserRole)
            for index in range(path_list.count())
        ]

    @staticmethod
    def _remove_selected(path_list: QListWidget) -> None:
        for item in path_list.selectedItems():
            path_list.takeItem(path_list.row(item))

    @staticmethod
    def _move_current(path_list: QListWidget, offset: int) -> None:
        row = path_list.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < path_list.count():
            return
        item = path_list.takeItem(row)
        path_list.insertItem(target, item)
        path_list.setCurrentRow(target)

    def _start_import(self) -> None:
        paths = {
            key: selected
            for key, widget in self._paths.items()
            if (selected := self._list_paths(widget))
        }
        if not paths:
            QMessageBox.warning(self, "未选择图片", "请至少选择一种官方榜单图片")
            return
        for control in self._path_controls:
            control.setEnabled(False)
        self._footer.set_busy(True, "正在识别...")
        self._progress_label.setText("正在准备导入...")
        self._progress_label.show()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.show()
        try:
            self._task = self._capture_service.submit_official_import(paths)
        except (RuntimeError, ValueError) as exc:
            self._on_failed(str(exc))

    def _on_progress_changed(self, status: str, current: int, total: int) -> None:
        self._progress_label.setText(status)
        if current < 0:
            return
        if total <= 0:
            self._progress_bar.setRange(0, 0)
            return
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)
        self._progress_bar.setFormat(f"{current} / {total}")

    def _on_completed(self, summaries: list[dict]) -> None:
        self._task = None
        self._progress_bar.setValue(self._progress_bar.maximum())
        self.recommendation_indexes_stale.emit()
        lines = [
            f"{item['name']}：{item['pages']} 张图片，已导入 {item['records']} 条，"
            f"待复核 {item['reviews']} 条"
            for item in summaries
        ]
        lines.append("推荐指数已标记为待重建，请在选将推荐页面确认后重建。")
        show_toast(self.parentWidget() or self, "\n".join(lines), duration=3000)
        self.accept()

    def _on_failed(self, message: str) -> None:
        self._task = None
        for control in self._path_controls:
            control.setEnabled(True)
        self._footer.set_busy(False)
        self._progress_label.hide()
        self._progress_bar.hide()
        QMessageBox.warning(self, "导入失败", message)
        pending = load_pending_session()
        if pending is None:
            return
        answer = QMessageBox.question(
            self,
            "存在待复核数据",
            "已生成待复核截图与修正会话，是否立即打开修正？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._open_review_dialog(pending)

    def _open_review_dialog(self, pending: dict) -> None:
        dialog = OfficialImportReviewDialog(pending, self)
        dialog.applied.connect(self.recommendation_indexes_stale)
        dialog.exec()

    def _disconnect_capture_signals(self) -> None:
        self._capture_service.official_import_progress.disconnect(self._on_progress_changed)
        self._capture_service.official_import_completed.disconnect(self._on_completed)
        self._capture_service.official_import_failed.disconnect(self._on_failed)

    def reject(self) -> None:
        if self._task and not self._task.completed.is_set():
            return
        super().reject()
