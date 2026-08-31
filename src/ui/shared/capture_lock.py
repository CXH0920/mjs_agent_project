"""截图/文件导入请求的单飞锁（#E4）。

recommendation 与 match_guide 两个面板共享同一 CaptureService/OcrService，
各自用"pending 来源"防止上一项请求的回调覆盖下一项。此前来源标识是
两面板各自维护的裸字符串，本模块把合法来源收敛为枚举、锁语义收敛为一处。
"""

from __future__ import annotations

from enum import StrEnum


class CaptureSource(StrEnum):
    """一次捕获请求的来源（决定回调的处理分支）。"""

    ADB_RECOGNIZE = "adb_recognize"
    ADB_SAVE = "adb_save"
    FILE = "file"


class CaptureRequestLock:
    """同一时刻至多一个在途捕获请求。"""

    def __init__(self) -> None:
        self._current: CaptureSource | None = None

    @property
    def current(self) -> CaptureSource | None:
        """当前在途请求来源；空闲时为 None。"""
        return self._current

    def begin(self, source: CaptureSource) -> bool:
        """锁定一个新请求；已有在途请求时返回 False（调用方直接忽略本次触发）。"""
        if self._current is not None:
            return False
        self._current = source
        return True

    def finish(self) -> CaptureSource | None:
        """释放锁并返回刚完成的来源；空闲时返回 None（回调来自过期请求）。"""
        source = self._current
        self._current = None
        return source
