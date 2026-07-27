"""官方数据导入对话框。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout

from src.business.official_data_import_service import OfficialDataImportWorker


class OfficialDataImportDialog(QDialog):
    """选择一张或两张官方榜单图片并执行导入。"""

    recommendation_indexes_stale = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._worker: OfficialDataImportWorker | None = None
        self.setWindowTitle("官方数据导入")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._paths = {
            "2v2": self._create_path_input(form, "2v2数据导入"),
            "exile": self._create_path_input(form, "武将放逐数据导入"),
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

    def _create_path_input(self, form: QFormLayout, label: str) -> QLineEdit:
        container = QHBoxLayout()
        path_input = QLineEdit()
        path_input.setReadOnly(True)
        choose_button = QPushButton("选择")
        choose_button.clicked.connect(lambda: self._choose_file(path_input))
        container.addWidget(path_input)
        container.addWidget(choose_button)
        form.addRow(label, container)
        return path_input

    def _choose_file(self, path_input: QLineEdit) -> None:
        start_dir = Path(path_input.text()).parent if path_input.text() else Path.home()
        file_path, _ = QFileDialog.getOpenFileName(self, "选择官方榜单图片", str(start_dir), "图片文件 (*.png *.jpg *.jpeg *.bmp)")
        if file_path:
            path_input.setText(file_path)

    def _start_import(self) -> None:
        paths = {key: widget.text() for key, widget in self._paths.items() if widget.text()}
        if not paths:
            QMessageBox.warning(self, "未选择图片", "请至少选择一种官方榜单图片")
            return
        self._import_button.setEnabled(False)
        self._import_button.setText("正在识别...")
        self._cancel_button.setEnabled(False)
        self._progress_label.setText("正在准备导入...")
        self._progress_label.show()
        self._progress_bar.setRange(0, 0)
        self._progress_bar.show()
        self._worker = OfficialDataImportWorker(paths, self)
        self._worker.progress_changed.connect(self._on_progress_changed)
        self._worker.completed.connect(self._on_completed)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

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
        self._progress_bar.setValue(self._progress_bar.maximum())
        self.recommendation_indexes_stale.emit()
        lines = [f"{item['name']}：已导入 {item['records']} 条，待复核 {item['reviews']} 条" for item in summaries]
        lines.append("推荐指数已标记为待重建，请在选将推荐页面确认后重建。")
        QMessageBox.information(self, "导入完成", "\n".join(lines))
        self.accept()

    def _on_failed(self, message: str) -> None:
        self._import_button.setEnabled(True)
        self._import_button.setText("导入")
        self._cancel_button.setEnabled(True)
        self._progress_label.hide()
        self._progress_bar.hide()
        QMessageBox.warning(self, "导入失败", message)

    def reject(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        super().reject()
