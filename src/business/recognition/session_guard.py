"""会话世代守卫：生命周期边界（启动/停止）开新世代作废在途工作。

此前以裸 int（``self._session += 1`` / ``session != self._session``）散布在
peak_select_watcher 的多个写入点比对，世代语义无命名、无复用点；收口为
对象后，后续批次可将 capture_service / UI 各自自造的守卫统一至此。
"""


class SessionGuard:
    """世代计数器。

    读写在调用方锁内进行（GIL 下单次自增/比较原子，无锁快照读安全）；
    token 即世代号，在途工作开始时取快照、关键写入点前校验。
    """

    __slots__ = ("_generation",)

    def __init__(self) -> None:
        self._generation = 0

    def begin(self) -> None:
        """开新世代：此前世代全部作废（start/stop 调用）。"""
        self._generation += 1

    def current(self) -> int:
        """当前世代号（在途工作开始时取快照）。"""
        return self._generation

    def is_current(self, token: int) -> bool:
        """token 是否仍是当前世代（过期即在途工作属旧会话，应放弃写入）。"""
        return token == self._generation
