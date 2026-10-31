"""手动检查的冷却闸门：忙碌判定与最小间隔的单一事实源。

公告检查（announcement_service）与卡牌百科检查（card_sync）此前各自持有一份
逐行同构的实现——`CHECK_COOLDOWN_SECONDS = 60`、`_last_check_started_at`、
`is_busy`、`cooldown_remaining`、`check_now` 的"忙碌→冷却→置位→起线程"四步
（TD-A2-001）。两侧阈值一旦分叉，用户看到的"60 秒冷却"提示会与实际行为不符，
故收口于此。

线程模型：闸门自身加锁，使"检查忙碌→检查冷却→置位时间戳"成为原子操作。
此前该四步是无锁 check-then-act，安全性完全依赖"所有调用方都在 GUI 主线程"
这一只写在注释里的约定；一旦未来从 worker/定时器触发，双击即可起两个线程
并发写同一份数据文件（VUL-B3-001）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# 两次手动检查的最小间隔（秒）：官网全量拉取成本高且易触发风控
DEFAULT_COOLDOWN_SECONDS = 60


class CheckCooldown:
    """忙碌判定 + 冷却计时的可复用闸门。

    不持有线程对象本身，只持有"是否忙碌"的判定函数——公告侧有检查与候选
    准备两条线程，卡牌侧只有一条，由调用方决定忙碌口径。
    """

    def __init__(
        self,
        is_busy: Callable[[], bool],
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        *,
        label: str = "检查",
    ) -> None:
        self._is_busy = is_busy
        self._cooldown_seconds = cooldown_seconds
        self._label = label
        self._last_started_at: float | None = None
        self._lock = threading.Lock()

    @property
    def cooldown_seconds(self) -> float:
        return self._cooldown_seconds

    @property
    def is_busy(self) -> bool:
        return self._is_busy()

    @property
    def cooldown_remaining(self) -> float:
        """距离下次允许检查的剩余秒数；0 表示可立即检查。"""
        with self._lock:
            return self._remaining_locked()

    @property
    def last_started_at(self) -> float | None:
        """上次占用检查窗口的 monotonic 时刻；None 表示从未检查。

        暴露为只读属性供测试断言冷却是否被记录，生产代码不应写入。
        """
        return self._last_started_at

    def backdate(self, seconds: float) -> None:
        """把上次启动时刻前移 seconds 秒（模拟冷却已过）。

        仅供测试与"手动解除限制"场景；生产路径一律走 try_acquire。
        """
        with self._lock:
            if self._last_started_at is None:
                return
            self._last_started_at -= seconds

    def _remaining_locked(self) -> float:
        if self._last_started_at is None:
            return 0.0
        remaining = self._cooldown_seconds - (time.monotonic() - self._last_started_at)
        return max(0.0, remaining)

    def try_acquire(self) -> bool:
        """原子地"检查忙碌 + 检查冷却 + 占用本次检查窗口"。

        返回 False 表示被忙碌或冷却拦截（由调用方提示用户）；
        返回 True 时调用方必须随后启动线程，否则本次窗口白占一次冷却。
        """
        with self._lock:
            if self._is_busy():
                logger.warning("%s正在进行，忽略重复请求", self._label)
                return False
            remaining = self._remaining_locked()
            if remaining > 0:
                logger.warning("%s冷却中，剩余 %.1f 秒", self._label, remaining)
                return False
            self._last_started_at = time.monotonic()
            return True

    def reset(self) -> None:
        """清空冷却计时（测试与"手动解除限制"用）。"""
        with self._lock:
            self._last_started_at = None
