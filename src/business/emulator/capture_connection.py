"""ADB 连接域：配置热更、会话状态与连接/断开编排（P1-8 绞杀第三刀）。

从 CaptureService 出仓（df9df7f 审计 G8 已出官方导入/图像保存/任务协调
三模块，本刀继续）：持有 AdbCapture 实例、配置缓存与会话状态字段。
串行化规则自 CaptureService 原样迁移——_session_lock 护状态字段与实例
引用（GUI 快速路径争用，锁内只做纯内存操作，不发 IO 不 emit）；秒级
阻塞的 ADB IO（超时重试最坏约 45 秒）由 _adb_io_lock 单独串行化，两把
锁不嵌套持有。状态与状态栏消息经构造注入的回调广播（宿主为 QObject，
emit 必须留在 GUI 侧对象上）。
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from src.capture.adb_screen import AdbCapture

logger = logging.getLogger(__name__)


class CaptureConnectionManager:
    """ADB 连接会话管理；session_lock/adb_io_lock 只读暴露给宿主的截图域临界区。"""

    def __init__(
        self,
        on_state_change: Callable[[str, str], None],
        on_status: Callable[[str], None],
        on_event_status: Callable[[str], None],
    ) -> None:
        self._on_state_change = on_state_change
        self._on_status = on_status
        self._on_event_status = on_event_status
        self._capture: AdbCapture | None = None
        self._config: dict = {}  # 当前配置缓存
        self._connection_state = "unconfigured"
        self._connection_detail = ""
        # ADB 连接/截屏是秒级阻塞调用，不能在 _session_lock 内执行——该锁
        # 同时被 GUI 线程的 update_config/config 属性等快速路径争用；阻塞
        # IO 由 _adb_io_lock 单独串行化（两把锁不嵌套持有，避免顺序倒置）
        self._session_lock = threading.RLock()
        self._adb_io_lock = threading.RLock()

    # ── 状态与配置 ────────────────────────────────────────────────────

    def set_connection_state(self, state: str, detail: str = "") -> None:
        """更新并广播当前 ADB 会话状态（无变化不重复广播）。"""
        if (state, detail) == (self._connection_state, self._connection_detail):
            return
        self._connection_state = state
        self._connection_detail = detail
        self._on_state_change(state, detail)

    @property
    def connection_state(self) -> tuple[str, str]:
        """返回当前 ADB 会话状态及详情。"""
        with self._session_lock:
            return self._connection_state, self._connection_detail

    def update_config(self, config: dict) -> None:
        """更新配置并重建 AdbCapture（仅路径/端口/截图模式变化时重建）。

        重建时如果旧的 AdbCapture 已连通同一设备，保留已有连接状态；
        配置无变化时不重建实例。
        """
        with self._session_lock:
            path_changed = config.get("mumu_adb_path") != self._config.get("mumu_adb_path")
            port_changed = config.get("mumu_adb_port") != self._config.get("mumu_adb_port")
            mode_changed = config.get("mumu_screenshot_mode") != self._config.get("mumu_screenshot_mode")

            self._config = dict(config)

            if not config.get("mumu_adb_path"):
                self._capture = None
                self.set_connection_state("unconfigured")
                return

            if path_changed or port_changed or mode_changed or self._capture is None:
                self._capture = AdbCapture(
                    adb_path=config["mumu_adb_path"],
                    adb_port=config.get("mumu_adb_port", 0),
                    screenshot_mode=config.get("mumu_screenshot_mode", "auto"),
                )
                self.set_connection_state("disconnected")
                logger.info("CaptureService 配置已更新，ADB: %s:%s",
                            config["mumu_adb_path"], config.get("mumu_adb_port", "auto"))
            else:
                logger.debug("CaptureService 配置已更新（仅 OCR 参数）")

    def set_target_port(self, port: int) -> None:
        """切换下一次连接使用的 ADB 端口，并废弃旧会话。"""
        if not self._config.get("mumu_adb_path"):
            self.set_connection_state("unconfigured")
            return
        config = dict(self._config)
        config["mumu_adb_port"] = port
        self.update_config(config)

    @property
    def config(self) -> dict:
        """返回当前截图配置的副本。"""
        with self._session_lock:
            return dict(self._config)

    @property
    def capture(self) -> AdbCapture | None:
        with self._session_lock:
            return self._capture

    @capture.setter
    def capture(self, cap: AdbCapture | None) -> None:
        with self._session_lock:
            self._capture = cap

    def is_current(self, capture: AdbCapture) -> bool:
        """capture 是否仍是当前会话实例（连接期间配置热更会重建实例）。"""
        with self._session_lock:
            return capture is self._capture

    @property
    def session_lock(self) -> threading.RLock:
        """宿主截图域临界区共用的会话锁（可重入，详见模块 docstring）。"""
        return self._session_lock

    @property
    def adb_io_lock(self) -> threading.RLock:
        """宿主截图域阻塞 IO 共用的串行锁（可重入，不与 session_lock 嵌套）。"""
        return self._adb_io_lock

    # ── 连接管理 ──────────────────────────────────────────────────────

    def sync_connection_state(self, error_detail: str = "") -> None:
        """根据底层会话状态同步 ADB 状态，供截图和轮询失败路径调用。"""
        with self._session_lock:
            if not self._capture:
                self.set_connection_state("unconfigured")
            elif not self._capture.connected:
                self.set_connection_state("offline", error_detail)

    def sync_poll_connection_state(self, capture: AdbCapture, error_detail: str = "") -> None:
        """仅同步当前轮询会话的连接状态，忽略过期 capture。"""
        with self._session_lock:
            if capture is not self._capture:
                return
            if capture.connected:
                self.set_connection_state("connected", capture.device_serial)
            else:
                self.set_connection_state("offline", error_detail)

    def connect_emulator(self) -> tuple[bool, str]:
        """连接模拟器。"""
        with self._session_lock:
            capture = self._capture
            if capture is None:
                self.set_connection_state("unconfigured")
                return False, "ADB 未配置"
        return self.connect_capture(capture)

    def connect_capture(self, capture: AdbCapture) -> tuple[bool, str]:
        """连接给定 AdbCapture：阻塞 IO 在 _adb_io_lock 内，状态字段在 _session_lock 内更新。"""
        with self._session_lock:
            self.set_connection_state("connecting")
        self._on_status("正在连接模拟器...")
        with self._adb_io_lock:
            ok, message = capture.connect()
        with self._session_lock:
            if ok:
                self.set_connection_state("connected", capture.device_serial)
            else:
                self.set_connection_state("disconnected", message)
        if ok:
            self._on_event_status(f"ADB 已连接：{capture.device_serial}")
        else:
            # 失败必须常驻，否则左下角残留"正在连接模拟器..."直到下一条消息
            self._on_status(f"连接失败：{message}")
        return ok, message

    def disconnect_emulator(self) -> tuple[bool, str]:
        """断开模拟器。"""
        with self._session_lock:
            capture = self._capture
            if capture is None:
                self.set_connection_state("unconfigured")
                return False, "ADB 未配置"
        with self._adb_io_lock:
            ok, message = capture.disconnect()
        with self._session_lock:
            self.set_connection_state("disconnected")
        self._on_event_status("ADB 已断开")
        return ok, message
