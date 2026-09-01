"""
名将杀 Agent - 攻略生成进度对话框

以进度条形式显示 AI 攻略生成进度，支持实时状态文字和错误展示。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from src.business.fetching.fetch_utils import parse_generation_event
from src.ui.shared.style import TONE_DANGER, TONE_SUCCESS, set_tone
from src.ui.shared.widgets import DialogFooter, PageHeader

logger = logging.getLogger(__name__)

# 静默超过该秒数后在进度条上显示已等待时长，避免长请求/冷却期被误认为卡死
_WAIT_HINT_THRESHOLD_SECONDS = 10


class GuideProgressDialog(QDialog):
    """攻略生成进度对话框

    以进度条展示 AI 攻略生成进度，同时显示当前正在处理的武将名称。
    进程结束时启用关闭按钮，如有错误则展示错误信息。
    """

    cancel_requested = Signal()

    def __init__(
        self,
        hero_count: int,
        title: str = "攻略生成进度",
        item_label: str = "攻略",
        parent=None,
    ):
        super().__init__(parent)
        self._item_label = item_label
        self._finished = False
        self.setWindowTitle(title)
        self.setMinimumWidth(480)
        self.setMinimumHeight(260)
        self.resize(520, 280)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self._setup_ui(hero_count)

    def _setup_ui(self, hero_count: int) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(PageHeader(self.windowTitle(), "任务运行期间可中止，完成后可关闭"))

        # 状态文字
        self._status_label = QLabel("正在准备...")
        self._status_label.setObjectName("progressStatusLabel")
        layout.addWidget(self._status_label)

        # 进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setMinimum(0)
        self._progress_bar.setMaximum(hero_count if hero_count > 0 else 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat(f"0 / {self._progress_bar.maximum()}")
        self._progress_bar.setTextVisible(True)
        layout.addWidget(self._progress_bar)

        # 静默计时：单条请求/冷却可达数分钟且无新日志行，显示已等待秒数
        self._waiting_seconds = 0
        self._wait_timer = QTimer(self)
        self._wait_timer.setInterval(1_000)
        self._wait_timer.timeout.connect(self._on_wait_tick)
        self._wait_timer.start()

        # 详情标签（显示当前武将名）
        self._detail_label = QLabel("")
        self._detail_label.setObjectName("progressDetailLabel")
        self._detail_label.setWordWrap(True)
        layout.addWidget(self._detail_label)

        # 错误标签（红色，初始隐藏）
        self._error_label = QLabel("")
        self._error_label.setObjectName("progressErrorLabel")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        layout.addWidget(self._error_label)

        layout.addStretch()

        self._footer = DialogFooter(accept_text="关闭", cancel_text="中止")
        self._close_btn = self._footer.accept_button
        self._cancel_btn = self._footer.cancel_button
        self._close_btn.setEnabled(False)
        self._footer.accepted.connect(self.accept)
        self._footer.rejected.connect(self._request_cancel)
        layout.addWidget(self._footer)

    def update_progress(self, current: int, total: int) -> None:
        """更新进度条"""
        self._reset_wait_hint()
        self._progress_bar.setMaximum(total)
        self._progress_bar.setValue(current)
        self._progress_bar.setFormat(f"{current} / {total}")

    def update_status(self, text: str) -> None:
        """更新当前状态文字：协议行解析统一走 fetch_utils，本方法只负责渲染。"""
        self._reset_wait_hint()
        event = parse_generation_event(text)
        if event is None:
            self._detail_label.setText(text.strip())
            return
        if event.kind == "start":
            self._status_label.setText(f"正在生成 {event.label} 的{self._item_label}...")
            self._detail_label.setText(f"当前请求：{event.current} / {event.total}")
        elif event.kind == "ok":
            self._status_label.setText(f"✓ 已完成 {event.label} 的{self._item_label}...")
            self.update_progress(event.current, event.total)
        elif event.kind == "fail":
            self._status_label.setText(f"✗ {event.label} 的{self._item_label}生成失败")
            self.update_progress(event.current, event.total)
        elif event.kind == "skip":
            self._status_label.setText(f"↷ 已跳过 {event.label}（已有{self._item_label}）")
            self.update_progress(event.current, event.total)
        elif event.kind == "retry":
            self._status_label.setText(
                f"⏳ 重试中（{event.retry_round}/{event.retry_max}），{event.wait_seconds} 秒后重试")
            self._detail_label.setText(
                f"当前进度 {self._progress_bar.value()} / {self._progress_bar.maximum()}，原因：{event.label}")
        elif event.kind == "rest":
            self._status_label.setText(
                f"冷却中（约 {event.wait_seconds} 秒），已完成 {self._progress_bar.value()} / {self._progress_bar.maximum()}")
            self._detail_label.setText("冷却结束后将继续下一组相性生成")

    def _on_wait_tick(self) -> None:
        """静默期在进度条上显示已等待秒数；新进度行到来即复位。"""
        self._waiting_seconds += 1
        if self._waiting_seconds < _WAIT_HINT_THRESHOLD_SECONDS:
            return
        bar = self._progress_bar
        bar.setFormat(f"{bar.value()} / {bar.maximum()} · 已等待 {self._waiting_seconds} 秒")

    def _reset_wait_hint(self) -> None:
        self._waiting_seconds = 0
        self._progress_bar.setFormat(f"{self._progress_bar.value()} / {self._progress_bar.maximum()}")

    def _request_cancel(self) -> None:
        """禁用重复操作，并由工作流请求服务中止子进程。"""
        if self._finished or not self._cancel_btn.isEnabled():
            return
        self._cancel_btn.setEnabled(False)
        self._status_label.setText("正在中止，请等待当前任务退出...")
        self.cancel_requested.emit()

    def set_error(self, message: str) -> None:
        """显示错误信息"""
        self._error_label.setText(f"⚠ {message}")
        self._error_label.show()
        set_tone(self._progress_bar, TONE_DANGER)

    def on_process_finished(self, success: bool, message: str = "") -> None:
        """进程结束时调用"""
        self._wait_timer.stop()
        self._finished = True
        self._close_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        if success:
            set_tone(self._progress_bar, TONE_SUCCESS)
            self._status_label.setText("生成完成 ✓")
            self._progress_bar.setValue(self._progress_bar.maximum())
            self._progress_bar.setFormat("完成")
        else:
            self._status_label.setText("生成失败 ✗")
            self.set_error(message)

    def on_process_cancelled(self) -> None:
        """任务被用户中止后允许关闭，已提交的数据已保留。"""
        self._wait_timer.stop()
        self._finished = True
        self._close_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._status_label.setText("已中止")
        self._detail_label.setText("已提交的数据已保留，未提交的当前项不会写入。")

    def reject(self) -> None:
        """运行中关闭窗口等同于请求中止，避免隐藏后台任务。"""
        if not self._finished:
            self._request_cancel()
            return
        super().reject()
