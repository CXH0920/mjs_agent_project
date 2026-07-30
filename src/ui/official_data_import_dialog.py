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

from src.business.capture_service import CaptureService
from src.business.ocr_worker import OfficialImportTask


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
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._import_button = QPushButton("导入")
        self._import_button.clicked.connect(self._start_import)
        buttons.addWidget(self._import_button)
        self._cancel_button = QPushButton("取消")
        self._cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self._cancel_button)
        layout.addLayout(buttons)

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
        self._import_button.setEnabled(False)
        self._import_button.setText("正在识别...")
        self._cancel_button.setEnabled(False)
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
        QMessageBox.information(self, "导入完成", "\n".join(lines))
        self.accept()

    def _on_failed(self, message: str) -> None:
        self._task = None
        for control in self._path_controls:
            control.setEnabled(True)
        self._import_button.setEnabled(True)
        self._import_button.setText("导入")
        self._cancel_button.setEnabled(True)
        self._progress_label.hide()
        self._progress_bar.hide()
        QMessageBox.warning(self, "导入失败", message)

    def _disconnect_capture_signals(self) -> None:
        self._capture_service.official_import_progress.disconnect(self._on_progress_changed)
        self._capture_service.official_import_completed.disconnect(self._on_completed)
        self._capture_service.official_import_failed.disconnect(self._on_failed)

    def reject(self) -> None:
        if self._task and not self._task.completed.is_set():
            return
        super().reject()
