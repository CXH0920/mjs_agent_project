"""截图/文件导入请求的单飞锁（#E4）。

recommendation 与 match_guide 两个面板共享同一 CaptureService/OcrService，
各自用"pending 来源"防止上一项请求的回调覆盖下一项。此前来源标识是
两面板各自维护的裸字符串，本模块把合法来源收敛为枚举、锁语义收敛为一处。

捕获完成信号是服务级广播：多个面板共享同一把锁时，非发起面板的回调也
会到达，故 begin/finish 携带发起方（owner）标识——finish 的 owner 与
begin 登记的不一致时不释放不消费（否则槽执行顺序不利时非发起面板会
抢先释放锁，发起面板反而丢掉自己的结果，第 13 批审查 P1-1）。
"""

from __future__ import annotations

from enum import StrEnum


class CaptureSource(StrEnum):
    """一次捕获请求的来源（决定回调的处理分支）。"""

    ADB_RECOGNIZE = "adb_recognize"
    ADB_SAVE = "adb_save"
    FILE = "file"


class CaptureRequestLock:
    """同一时刻至多一个在途捕获请求；供多面板共享时以 owner 区分发起方。"""

    def __init__(self) -> None:
        self._current: CaptureSource | None = None
        self._owner: object | None = None

    @property
    def current(self) -> CaptureSource | None:
        """当前在途请求来源；空闲时返回 None。"""
        return self._current

    def begin(self, source: CaptureSource, owner: object | None = None) -> bool:
        """锁定一个新请求；已有在途请求时返回 False（调用方直接忽略本次触发）。"""
        if self._current is not None:
            return False
        self._current = source
        self._owner = owner
        return True

    def finish(self, owner: object | None = None) -> CaptureSource | None:
        """释放锁并返回刚完成的来源；空闲时返回 None（回调来自过期请求）。

        begin 登记过 owner 时，finish 须传同一 owner 才释放：非发起方的
        回调返回 None 且不动锁。owner 为 None 的旧用法保持无条件释放。
        """
        source = self._current
        if source is None:
            return None
        if owner is not None and self._owner is not None and owner != self._owner:
            return None
        self._current = None
        self._owner = None
        return source
