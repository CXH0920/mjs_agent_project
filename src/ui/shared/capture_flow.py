# -*- coding: utf-8 -*-
"""截图/导入请求全生命周期状态（审计 G5 切片 4.4b）。

从 RecommendationPanel 抽出的共用件：单飞锁（CaptureRequestLock）+ 忙碌
控件清单 + 页面状态文案记忆。三个触发入口（识别/保存/导入）共享同一把
锁；回调侧经 finish() 取回来源并恢复控件。纯控制器，非 QWidget；宿主
保留两行委托方法与既有测试锚点。对局攻略面板接入为后续独立切片。
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget
from src.ui.shared.capture_lock import CaptureRequestLock, CaptureSource
from src.ui.shared.style import TONE_NEUTRAL

_BEGIN_STATUS = {
    "adb_recognize": "正在识别当前阵容...",
    "adb_save": "正在保存截图...",
    "file": "正在导入图片...",
}


class CaptureRequestFlow:
    """一次捕获请求的单飞锁、忙碌使能与状态文案记忆。"""

    def __init__(self, default_status: str = "") -> None:
        self._lock = CaptureRequestLock()
        self._controls: list[QWidget] = []
        self._actions: list[QAction] = []
        self._status_text = default_status
        self._status_tone = TONE_NEUTRAL

    def bind_controls(self, controls: Sequence[QWidget], actions: Sequence[QAction]) -> None:
        """登记请求进行期间需要禁用的控件与菜单项（宿主建好 UI 后调用一次）。"""
        self._controls = list(controls)
        self._actions = list(actions)

    @property
    def lock(self) -> CaptureRequestLock:
        """在途请求锁（宿主测试锚点透传）。"""
        return self._lock

    @property
    def current(self) -> CaptureSource | None:
        """当前在途请求来源；空闲为 None。"""
        return self._lock.current

    @property
    def last_status(self) -> tuple[str, str]:
        """最近记忆的页面状态文案与色调（请求结束回落用）。"""
        return self._status_text, self._status_tone

    def remember_status(self, text: str, tone: str) -> None:
        """记忆页面稳定状态文案。"""
        self._status_text = text
        self._status_tone = tone

    def begin(self, source: str) -> bool:
        """锁定新请求并禁用忙碌控件；已有在途请求时返回 False。"""
        if not self._lock.begin(CaptureSource(source)):
            return False
        self._set_enabled(False)
        return True

    def begin_status(self, source: str) -> str:
        """请求发起时的过程文案。"""
        return _BEGIN_STATUS[source]

    def finish(self) -> str | None:
        """结束在途请求：恢复忙碌控件并返回来源；无在途请求返回 None。"""
        source = self._lock.finish()
        if source is None:
            return None
        self._set_enabled(True)
        return source

    def _set_enabled(self, enabled: bool) -> None:
        for control in self._controls:
            control.setEnabled(enabled)
        for action in self._actions:
            action.setEnabled(enabled)
