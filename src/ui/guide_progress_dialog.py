"""
名将杀 Agent - 攻略生成进度对话框

以进度条形式显示 AI 攻略生成进度，支持实时状态文字和错误展示。
"""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class GuideProgressDialog(QDialog):
    """攻略生成进度对话框

    以进度条展示 AI 攻略生成进度，同时显示当前正在处理的武将名称。
    进程结束时启用关闭按钮，如有错误则展示错误信息。
    """

    def __init__(self, hero_count: int, title: str = "攻略生成进度", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.setFixedHeight(200)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui(hero_count)

    def _setup_ui(self, hero_count: int) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 状态文字
        self._status_label = QLabel("正在准备...")
        self._status_label.setStyleSheet("font-size: 14px;")
        layout.addWidget(self._status_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(hero_count if hero_count > 0 else 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setStyleSheet(
            "QProgressBar {"
            "  border: 1px solid #ccc;"
            "  border-radius: 4px;"
            "  text-align: center;"
            "  height: 24px;"
            "}"
            "QProgressBar::chunk {"
            "  background-color: #4caf50;"
            "  border-radius: 3px;"
            "}"
        )
        layout.addWidget(self._progress_bar)

        # 详情标签（显示当前武将名）
        self._detail_label = QLabel("")
        self._detail_label.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(self._detail_label)

        # 错误标签（红色，初始隐藏）
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #d32f2f; font-size: 13px; font-weight: bold;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        layout.addStretch()

        # 关闭按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self._close_btn = QPushButton("关闭")
        self._close_btn.setEnabled(False)
        self._close_btn.setStyleSheet("padding: 6px 24px;")
        self._close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self._close_btn)
        layout.addLayout(btn_layout)

    def update_progress(self, current: int, total: int) -> None:
        """更新进度条"""
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._progress_bar.setFormat(f"{current} / {total}")

    def update_status(self, text: str) -> None:
        """更新当前状态文字"""
        # 成功进度行: "[i/total] 武将名 OK"
        m = re.search(r"\[(\d+)/(\d+)\]\s*(.+?)\s+(?:OK|FAIL)", text)
        if m:
            self._status_label.setText(f"已生成 {m.group(3)} 的攻略...")
            self.update_progress(int(m.group(1)), int(m.group(2)))
        else:
            self._detail_label.setText(text.strip())

    def set_error(self, message: str) -> None:
        """显示错误信息"""
        self._error_label.setText(f"⚠ {message}")
        self._error_label.show()
        self._progress_bar.setStyleSheet(
            "QProgressBar {"
            "  border: 1px solid #d32f2f;"
            "  border-radius: 4px;"
            "  text-align: center;"
            "  height: 24px;"
            "}"
            "QProgressBar::chunk {"
            "  background-color: #f44336;"
            "  border-radius: 3px;"
            "}"
        )

    def on_process_finished(self, success: bool, message: str = "") -> None:
        """进程结束时调用"""
        self._close_btn.setEnabled(True)
        if success:
            self._status_label.setText("攻略生成完成 ✓")
            self._progress_bar.setValue(self._progress_bar.maximum())
            self._progress_bar.setFormat("完成")
        else:
            self._status_label.setText("攻略生成失败 ✗")
            self.set_error(message)
