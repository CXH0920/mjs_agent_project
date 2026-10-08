"""竞态守卫原语（P0-3 收口）：世代、忙碌闸、一次性令牌。

全仓 ≥5 次同类竞态 fix（faac3c0/93b54ad/8374acc/e6d67c0/c0f2143/634e027）
年代，令牌/世代/忙碌守卫各模块自造（裸 int 比对、裸 bool 旗标）。三种
语义在此定稿为命名原语，调用方按语义显式选择；替换时不得顺手改语义
（语义变更 = 行为变更，超出重构范畴）。

线程约定：SessionGuard/OneShotToken 的单次读写依赖 GIL 原子性，调用方
在自有锁内或单线程（Qt 主循环）使用；BusyGate.acquire 内部加锁，检查与
置位原子，多线程争用下至多一个占用者。
"""

import threading


class SessionGuard:
    """世代计数器：生命周期边界（启动/停止）开新世代作废在途工作。

    token 即世代号，在途工作开始时取快照、关键写入点前校验；
    读写在调用方锁内进行（GIL 下单次自增/比较原子，无锁快照读安全）。
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


class BusyGate:
    """忙碌闸：单飞标记，忙时拒绝新请求（轮询在途拍、阶段推进等）。"""

    __slots__ = ("_busy", "_lock")

    def __init__(self) -> None:
        self._busy = False
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        """不忙则占用并返回 True；已忙返回 False（检查与置位原子）。"""
        with self._lock:
            if self._busy:
                return False
            self._busy = True
            return True

    def release(self) -> None:
        """解除占用（幂等；未占用时调用无效果）。"""
        self._busy = False

    @property
    def is_busy(self) -> bool:
        return self._busy


class OneShotToken:
    """一次性令牌：置位后不可复位，用于关闭/停机类只进不退的守卫。"""

    __slots__ = ("_spent",)

    def __init__(self) -> None:
        self._spent = False

    def mark(self) -> None:
        """置位（幂等；重复置位无效果）。"""
        self._spent = True

    @property
    def spent(self) -> bool:
        return self._spent
