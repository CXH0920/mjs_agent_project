# -*- coding: utf-8 -*-
"""状态栏服务状态 chips：模拟器 ADB 与 OCR 轮询的常驻状态胶囊（批次6步骤3，自 MainWindow 抽取）。

点击任一 chip 发出 mumu_config_requested，由主窗口连接到打开模拟器配置的动作。
全局消息文本与业务进度条不在此层——它们的写点遍布业务回调，归 MainWindow。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

_EMULATOR_STYLES = {
    "unconfigured": ("模拟器：未配置", "#777", "#ececec"),
    "disconnected": ("模拟器：ADB 未连接", "#777", "#ececec"),
    "connecting": ("模拟器：正在连接…", "#8a5a00", "#fff3cd"),
    "connected": ("模拟器：ADB 已连接", "#176b36", "#e4f5e8"),
    "offline": ("模拟器：设备离线", "#a12622", "#fde8e8"),
}
_POLL_STYLES = {
    "stopped": ("OCR轮询：未启用", "#777", "#ececec"),
    "running": ("OCR轮询：运行中", "#176b36", "#e4f5e8"),
    "backing_off": ("OCR轮询：恢复中", "#8a5a00", "#fff3cd"),
    "cooldown": ("OCR轮询：冷却中", "#165a9e", "#e7f1fd"),
    "paused": ("OCR轮询：已暂停", "#a12622", "#fde8e8"),
}


class StatusChips(QWidget):
    """模拟器 ADB / OCR 轮询两个常驻状态胶囊（点击请求打开模拟器配置）。"""

    mumu_config_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._emulator_label = QLabel()
        self._emulator_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._emulator_label.mousePressEvent = lambda _: self.mumu_config_requested.emit()
        self._poll_label = QLabel()
        self._poll_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._poll_label.mousePressEvent = lambda _: self.mumu_config_requested.emit()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self._emulator_label)
        layout.addWidget(self._poll_label)

    @property
    def emulator_label(self) -> QLabel:
        return self._emulator_label

    @property
    def poll_label(self) -> QLabel:
        return self._poll_label

    def set_emulator_state(self, state: str, detail: str = "") -> None:
        """渲染不受业务进度覆盖的常驻 ADB 状态。"""
        text, color, background = _EMULATOR_STYLES.get(state, _EMULATOR_STYLES["disconnected"])
        self._apply_chip(self._emulator_label, text, color, background, detail)

    def set_poll_state(self, state: str, detail: str = "") -> None:
        """渲染不受业务进度覆盖的常驻 OCR 轮询状态。"""
        text, color, background = _POLL_STYLES.get(state, _POLL_STYLES["stopped"])
        self._apply_chip(self._poll_label, text, color, background, detail)

    @staticmethod
    def _apply_chip(label: QLabel, text: str, color: str,
                    background: str, detail: str) -> None:
        """状态栏彩色胶囊 chip 的统一渲染（模拟器/轮询两组状态共用）。"""
        label.setText(text)
        label.setToolTip(detail or "点击打开模拟器配置")
        label.setStyleSheet(
            f"color: {color}; background-color: {background}; padding: 3px 8px; "
            "border-radius: 8px; font-weight: bold;"
        )
