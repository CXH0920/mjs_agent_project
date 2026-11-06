"""CaptureConnectionManager 连接域测试：connect_capture 的状态回写时序。

锁定 P2-1 修复：连接在途（阻塞 IO 期间）配置热更重建实例后，过期连接
结果不得把新会话标成 connected（对照 _reconnect_if_needed / 
sync_poll_connection_state 的既有 is_current 守卫口径）。
"""

from __future__ import annotations

from src.business.emulator.capture_connection import CaptureConnectionManager


def _make_manager() -> tuple[CaptureConnectionManager, dict[str, list]]:
    events: dict[str, list] = {
        "states": [],
        "status": [],
        "event_status": [],
    }
    manager = CaptureConnectionManager(
        on_state_change=lambda state, detail: events["states"].append((state, detail)),
        on_status=lambda message: events["status"].append(message),
        on_event_status=lambda message: events["event_status"].append(message),
    )
    return manager, events


class _FakeAdbCapture:
    """鸭子类型 ADB 会话；on_connect 钩子模拟"连接阻塞期间"发生的事情。"""

    connected = False
    device_serial = "127.0.0.1:16448"

    def __init__(self, on_connect=None) -> None:
        self._on_connect = on_connect

    def connect(self, *, validate_capture: bool = False):
        if self._on_connect is not None:
            self._on_connect()
        self.connected = True
        return True, "连接成功"


class _FailingCapture:
    connected = False
    device_serial = ""

    def connect(self, *, validate_capture: bool = False):
        return False, "超时"


def test_stale_connect_result_does_not_overwrite_rebuilt_session_state() -> None:
    """P2-1 复现：连接期间热更重建实例，旧实例的成功结果不得覆盖状态。

    原缺陷：connect_capture 完成后无条件回写 connected + 旧 device_serial，
    而 update_config 已重建实例并置 disconnected——状态栏被过期结果误导。
    """
    manager, events = _make_manager()
    manager.update_config({"emulator_adb_path": "adb.exe", "emulator_adb_port": 16448})

    # 模拟连接在途实例：其 connect 阻塞期间（返回前）配置热更换了端口，
    # update_config 据此重建 _capture 并置 disconnected
    inflight = _FakeAdbCapture(on_connect=lambda: manager.update_config(
        {"emulator_adb_path": "adb.exe", "emulator_adb_port": 21503}))
    manager.capture = inflight

    ok, message = manager.connect_capture(inflight)

    assert not ok
    assert message == "连接配置已变更，请重试"
    # 状态保持 update_config 置的 disconnected，未被旧实例结果覆盖
    assert manager.connection_state == ("disconnected", "")
    assert manager.capture is not inflight
    # 左下角的"正在连接..."须被常驻失败消息清掉（与失败路径同语义）
    assert events["status"][-1] == "连接失败：连接配置已变更，请重试"


def test_connect_capture_success_writes_connected_state() -> None:
    manager, events = _make_manager()
    manager.update_config({"emulator_adb_path": "adb.exe", "emulator_adb_port": 16448})
    capture = _FakeAdbCapture()
    manager.capture = capture

    ok, message = manager.connect_capture(capture)

    assert ok
    assert manager.connection_state == ("connected", "127.0.0.1:16448")
    # 成功是瞬时事件消息：status 只留"正在连接..."，结果走 event_status
    assert events["status"] == ["正在连接模拟器..."]
    assert events["event_status"] == ["ADB 已连接：127.0.0.1:16448"]


def test_connect_capture_failure_keeps_disconnected_and_status_message() -> None:
    manager, events = _make_manager()
    manager.update_config({"emulator_adb_path": "adb.exe", "emulator_adb_port": 16448})
    capture = _FailingCapture()
    manager.capture = capture

    ok, message = manager.connect_capture(capture)

    assert not ok
    assert message == "超时"
    assert manager.connection_state == ("disconnected", "超时")
    assert events["status"][-1] == "连接失败：超时"


def test_bind_pc_window_binds_target_and_returns_snapshot() -> None:
    """锁内绑定 PC 目标窗口并返回实例快照，供连接入队（第 7 批审查 P3-4）。"""
    from src.capture.pc_client import PcClientCapture

    manager, _events = _make_manager()
    capture = PcClientCapture(window_title="测试窗口")
    manager.capture = capture

    returned = manager.bind_pc_window(4242)

    assert returned is capture
    assert capture._hwnd == 4242


def test_bind_pc_window_ignores_invalid_hwnd_and_non_pc_session() -> None:
    """hwnd 无效或当前非 PC 会话：不绑定，原样返回当前实例（与旧 connect 行为一致）。"""
    manager, _events = _make_manager()
    adb = _FakeAdbCapture()
    manager.capture = adb

    assert manager.bind_pc_window(0) is adb
    assert manager.bind_pc_window(4242) is adb


def test_set_target_port_updates_port_and_unconfigured_guard() -> None:
    """端口切换：未配置 ADB 时置 unconfigured；已配置时更新端口（读取全程持锁）。"""
    manager, events = _make_manager()

    manager.set_target_port(21503)
    assert manager.connection_state[0] == "unconfigured"  # 初始即 unconfigured：无变化不广播

    manager.update_config({"emulator_adb_path": "C:/adb.exe", "emulator_adb_port": 16384,
                           "emulator_type": "mumu"})
    manager.set_target_port(21503)
    assert manager.config["emulator_adb_port"] == 21503
