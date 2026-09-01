"""官方榜单导入待复核修正对话框。"""

from __future__ import annotations

import logging

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.business.recognition.official_data_import_service import OfficialDataImportService
from src.ui.shared.widgets import DialogFooter, PageHeader, show_toast

logger = logging.getLogger(__name__)

COLUMN_HEADERS = (
    "输出",
    "排名",
    "OCR原文",
    "修正后武将",
    "置信度",
    "异常原因",
    "行截图",
)
COMBO_COLUMN = 3


class OfficialImportReviewDialog(QDialog):
    """逐行复核 OCR 名称：选择词表内武将后应用写入正式 CSV。"""

    applied = Signal()

    def __init__(self, pending: dict, parent=None) -> None:
        super().__init__(parent)
        self._pending = pending
        self._service = OfficialDataImportService()
        self._rows: list[tuple[str, int, dict, str]] = []
        self.setWindowTitle("官方榜单待复核修正")
        self.setMinimumSize(1020, 600)
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "官方榜单待复核修正",
            "为未确认行选择正确武将；全部确认后才会写入正式 CSV。",
        ))
        self._summary_label = QLabel()
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)
        self._table = QTableWidget(0, len(COLUMN_HEADERS))
        self._table.setHorizontalHeaderLabels(COLUMN_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, 1)
        footer = DialogFooter(accept_text="应用修正", cancel_text="取消")
        footer.accepted.connect(self._accept)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    def _populate(self) -> None:
        for output_name, batch in self._pending["outputs"].items():
            records = {int(record["排名"]): record for record in batch["records"]}
            for review in batch["reviews"]:
                rank = int(review["期望排名"])
                record = records.get(rank, {})
                current = record.get("武将") or review.get("OCR名称") or ""
                self._rows.append((output_name, rank, review, current))
        self._table.setRowCount(len(self._rows))
        for row_index, (output_name, rank, review, current) in enumerate(self._rows):
            ocr_name = review.get("OCR名称") or ""
            self._table.setItem(row_index, 0, QTableWidgetItem(output_name))
            self._table.setItem(row_index, 1, QTableWidgetItem(str(rank)))
            self._table.setItem(row_index, 2, QTableWidgetItem(ocr_name))
            combo = QComboBox()
            candidates = self._service.review_candidates(ocr_name, current or None)
            for candidate in candidates:
                combo.addItem(candidate)
            default_index = candidates.index(current) if current in candidates else 0
            combo.setCurrentIndex(default_index)
            self._table.setCellWidget(row_index, COMBO_COLUMN, combo)
            self._table.setItem(row_index, 4, QTableWidgetItem(review.get("置信度", "")))
            reason = review.get("异常原因", "")
            reason_item = QTableWidgetItem(reason)
            reason_item.setToolTip(reason)
            self._table.setItem(row_index, 5, reason_item)
            self._table.setCellWidget(row_index, 6, self._crop_label(review.get("行截图路径", "")))
        unresolved = sum(
            1 for _output, _rank, _review, current in self._rows
            if not self._service.is_known_hero_name(current)
        )
        self._summary_label.setText(
            f"共 {len(self._rows)} 行待复核，其中 {unresolved} 行未确认，请在“修正后武将”列选择。"
        )

    @staticmethod
    def _crop_label(crop_path: str) -> QLabel:
        label = QLabel()
        path = Path(crop_path)
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                label.setPixmap(pixmap.scaledToHeight(32))
                label.setToolTip(crop_path)
        else:
            label.setText("无截图")
        return label

    def _collect_corrections(self) -> dict[tuple[str, int], str]:
        corrections: dict[tuple[str, int], str] = {}
        for row_index, (output_name, rank, _review, current) in enumerate(self._rows):
            widget = self._table.cellWidget(row_index, COMBO_COLUMN)
            value = widget.currentText().strip() if isinstance(widget, QComboBox) else current
            if value and value != current:
                corrections[(output_name, rank)] = value
        return corrections

    def _accept(self) -> None:
        corrections = self._collect_corrections()
        try:
            summary = self._service.apply_reviewed_records(self._pending, corrections)
        except ValueError as exc:
            QMessageBox.warning(self, "校验未通过", str(exc))
            return
        except OSError as exc:
            logger.exception("写入正式榜单数据失败")
            QMessageBox.critical(self, "写入失败", f"无法写入正式榜单数据：\n{exc}")
            return
        self.applied.emit()
        show_toast(self, f"已写入 {summary['records']} 条正式榜单数据", duration=3000)
        self.accept()
