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
from src.capture.pc_client import PC_WINDOW_TITLE, PcClientCapture

logger = logging.getLogger(__name__)


_LEGACY_KEYS = {
    "emulator_adb_path": "mumu_adb_path",
    "emulator_adb_port": "mumu_adb_port",
    "emulator_screenshot_mode": "mumu_screenshot_mode",
}


def _conn_key(config: dict, key: str, default: object = "") -> object:
    """连接键读取：emulator_* 新键优先，mumu_* 旧键回退（升级过渡期）。"""
    value = config.get(key)
    if value in (None, ""):
        legacy = _LEGACY_KEYS.get(key)
        if legacy is not None:
            return config.get(legacy, default)
        return default
    return value


def _is_pc_client(config: dict) -> bool:
    """当前配置是否为官方 PC 客户端连接方式。"""
    return _conn_key(config, "emulator_type") == "pc_client"


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
        """更新配置并按需重建采集会话（模拟器与 PC 客户端双形态）。

        模拟器形态：路径/端口/品牌/截图模式变化时重建 AdbCapture；
        PC 客户端形态：不要求 ADB 配置，窗口标题变化或跨形态切换时重建
        PcClientCapture。重建时如果旧会话已连通同一目标，保留已有连接状态；
        配置无变化时不重建实例。读取兼容 mumu_* 旧键（升级过渡期）。
        """
        with self._session_lock:
            path_changed = _conn_key(config, "emulator_adb_path") != _conn_key(self._config, "emulator_adb_path")
            port_changed = _conn_key(config, "emulator_adb_port") != _conn_key(self._config, "emulator_adb_port")
            mode_changed = _conn_key(config, "emulator_screenshot_mode") != _conn_key(self._config, "emulator_screenshot_mode")
            type_changed = _conn_key(config, "emulator_type") != _conn_key(self._config, "emulator_type")
            title_changed = (_conn_key(config, "pc_window_title", PC_WINDOW_TITLE)
                             != _conn_key(self._config, "pc_window_title", PC_WINDOW_TITLE))

            self._config = dict(config)

            if _is_pc_client(config):
                if (type_changed or title_changed
                        or not isinstance(self._capture, PcClientCapture)):
                    window_title = str(_conn_key(config, "pc_window_title", PC_WINDOW_TITLE))
                    self._capture = PcClientCapture(window_title=window_title)
                    self.set_connection_state("disconnected")
                    logger.info("连接方式为官方 PC 客户端（窗口标题: %s）", window_title)
                return

            adb_path = _conn_key(config, "emulator_adb_path")
            if not adb_path:
                self._capture = None
                self.set_connection_state("unconfigured")
                return

            if (path_changed or port_changed or mode_changed or type_changed
                    or self._capture is None
                    or isinstance(self._capture, PcClientCapture)):
                self._capture = AdbCapture(
                    adb_path=adb_path,
                    adb_port=_conn_key(config, "emulator_adb_port", 0),
                    screenshot_mode=_conn_key(config, "emulator_screenshot_mode", "auto"),
                    emulator_type=_conn_key(config, "emulator_type", "mumu"),
                )
                self.set_connection_state("disconnected")
                logger.info("CaptureService 配置已更新，ADB: %s:%s",
                            adb_path, _conn_key(config, "emulator_adb_port", "auto"))
            else:
                logger.debug("CaptureService 配置已更新（仅 OCR 参数）")

    def set_target_port(self, port: int) -> None:
        """切换下一次连接使用的 ADB 端口，并废弃旧会话。"""
        if not _conn_key(self._config, "emulator_adb_path"):
            self.set_connection_state("unconfigured")
            return
        config = dict(self._config)
        config["emulator_adb_port"] = port
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
        """连接模拟器（用户触发路径：连通后截一帧校验成功才算连接成功）。"""
        with self._session_lock:
            capture = self._capture
            if capture is None:
                self.set_connection_state("unconfigured")
                return False, "ADB 未配置"
        return self.connect_capture(capture, validate_capture=True)

    def connect_capture(self, capture: AdbCapture | PcClientCapture,
                        validate_capture: bool = False) -> tuple[bool, str]:
        """连接给定采集会话：阻塞 IO 在 _adb_io_lock 内，状态字段在 _session_lock 内更新。

        validate_capture 透传给底层 capture；自动重连等高频路径保持
        False，免为每次重连多付一帧截图（后续截图本身就是验证）。
        """
        is_pc = isinstance(capture, PcClientCapture)
        with self._session_lock:
            self.set_connection_state("connecting")
        self._on_status("正在连接 PC 客户端..." if is_pc else "正在连接模拟器...")
        with self._adb_io_lock:
            ok, message = capture.connect(validate_capture=validate_capture)
        with self._session_lock:
            if ok:
                self.set_connection_state("connected", capture.device_serial)
            else:
                self.set_connection_state("disconnected", message)
        if ok:
            self._on_event_status(
                f"PC 客户端已连接：{capture.device_serial}" if is_pc
                else f"ADB 已连接：{capture.device_serial}")
        else:
            # 失败必须常驻，否则左下角残留"正在连接..."直到下一条消息
            self._on_status(f"连接失败：{message}")
        return ok, message

    def disconnect_emulator(self) -> tuple[bool, str]:
        """断开当前连接（模拟器 ADB 或 PC 客户端窗口会话）。"""
        with self._session_lock:
            capture = self._capture
            if capture is None:
                self.set_connection_state("unconfigured")
                return False, "ADB 未配置"
        is_pc = isinstance(capture, PcClientCapture)
        with self._adb_io_lock:
            ok, message = capture.disconnect()
        with self._session_lock:
            self.set_connection_state("disconnected")
        self._on_event_status("PC 客户端已断开" if is_pc else "ADB 已断开")
        return ok, message
