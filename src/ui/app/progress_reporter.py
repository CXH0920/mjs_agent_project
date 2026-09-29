# -*- coding: utf-8 -*-
"""状态栏全局消息文本与业务进度条：业务流程进度的统一渲染出口（自 MainWindow 抽取）。

业务侧只调 show_* API（或把服务信号直接连到 API），不直接摸状态栏控件；
常驻服务状态 chips 在 status_chips.StatusChips，与本部件互不覆盖。
"""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget

# 完成/事件类消息的停留时长；到期后消息文本回落到默认统计文案
EVENT_MESSAGE_DURATION_MS = 5000


class ProgressReporter(QWidget):
    """状态栏消息文本 + 业务进度条的唯一渲染出口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._default_message = ""
        self._overrides_default = False
        self._message_label = QLabel()
        self._reset_timer = QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self._restore_default_message)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(220)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._message_label)
        layout.addWidget(self._progress_bar)

    @property
    def message_label(self) -> QLabel:
        return self._message_label

    @property
    def progress_bar(self) -> QProgressBar:
        return self._progress_bar

    def set_default_message(self, text: str) -> None:
        """记录默认文案（统计类）；消息显示期间（常驻或计时）只暂存不覆盖。"""
        self._default_message = text
        if not self._overrides_default:
            self._message_label.setText(text)

    def show_message(self, text: str, duration_ms: int | None = None) -> None:
        """渲染全局消息文本（原状态栏 label 的全部写点）。

        契约：任何新消息（常驻或计时）都先取消未到期的回落计时——否则常驻
        进行中消息会被上一条事件消息的计时器覆盖回默认文案；仅 duration_ms
        有值才重新启动计时，到期回落 set_default_message 设置的默认文案。
        """
        self._reset_timer.stop()
        self._overrides_default = True
        self._message_label.setText(text)
        if duration_ms is not None:
            self._reset_timer.start(duration_ms)

    def show_event_message(self, text: str) -> None:
        """完成/事件类消息：停留 EVENT_MESSAGE_DURATION_MS 后回落默认文案。"""
        self.show_message(text, EVENT_MESSAGE_DURATION_MS)

    def _restore_default_message(self) -> None:
        self._overrides_default = False
        self._message_label.setText(self._default_message)

    def show_indeterminate(self, text: str) -> None:
        """显示不确定进度（动画），用于无法精确计数的联网阶段。"""
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFormat(text)
        self._progress_bar.show()

    def show_progress(self, current: int, total: int, text: str) -> None:
        """显示确定进度（子进程 [n/N] 阶段）。"""
        total = max(total, 1)
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(min(current, total))
        self._progress_bar.setFormat(text)
        self._progress_bar.show()

    def set_progress_text(self, text: str) -> None:
        """仅更新进度条文字（进度范围由其它调用方维护）。"""
        self._progress_bar.setFormat(text)

    def hide_progress(self) -> None:
        """隐藏进度条（消息文本保留）。"""
        self._progress_bar.hide()
