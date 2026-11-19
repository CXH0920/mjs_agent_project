# -*- coding: utf-8 -*-
"""批量 LLM 建议进度小窗：进度条 + 失败计数/最新完成条目 + 取消。

纯被动渲染组件（P1-2）：进度数据由宿主（IndexRefinementDialog）在逐块结果
回调中喂给 update_progress()，本组件不感知 SuggestController，便于直接单测。
非模态 show()：建议后台化（#21）期间宿主对话框仍可浏览清单/原文；去掉标题
栏关闭按钮只留「取消」——小窗的退出只有「取消任务」（善后在宿主）与任务
正常完成（宿主关闭）两条路径，点 X 关窗会绕开取消善后。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)
from src.ui.shared.style import ROLE_SECONDARY, TONE_NEUTRAL, set_tone, set_ui_role


class SuggestProgressDialog(QDialog):
    """批量建议进行中的非模态进度小窗；取消经 cancelled 信号交宿主善后。"""

    cancelled = Signal()

    def __init__(self, total: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量 LLM 建议")
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setFixedSize(380, 150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self._progress = QProgressBar()
        self._progress.setObjectName("suggestProgress")
        self._progress.setRange(0, max(total, 1))
        self._progress.setValue(0)
        self._progress.setTextVisible(True)
        self._progress.setFormat("建议中 %v/%m")
        set_tone(self._progress, TONE_NEUTRAL)
        layout.addWidget(self._progress)
        # 最近一次成功块名：失败块传 None 只累计失败，不冲掉成功记录
        self._latest_name: str | None = None

        self._status_label = QLabel("已启动，等待第一条结果…")
        self._status_label.setObjectName("scriptLogMeta")
        layout.addWidget(self._status_label, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._cancel_button = QPushButton("取消")
        set_ui_role(self._cancel_button, ROLE_SECONDARY)
        self._cancel_button.clicked.connect(self.cancelled.emit)
        footer.addWidget(self._cancel_button)
        layout.addLayout(footer)

    def update_progress(self, done: int, total: int, failed: int,
                        latest_name: str | None) -> None:
        """宿主逐块结果回调时喂进度：done/total 驱动进度条，状态行拼失败与最新完成。

        latest_name 仅成功块传入（None 表示失败块/无新成功），不冲掉已记录的
        最近成功条目。
        """
        self._progress.setRange(0, max(total, 1))
        self._progress.setValue(min(done, max(total, 1)))
        if latest_name:
            self._latest_name = latest_name
        parts = []
        if failed:
            parts.append(f"失败 {failed}")
        if self._latest_name:
            parts.append(f"最新完成：{self._latest_name}")
        self._status_label.setText(" · ".join(parts) if parts else "进行中…")
