"""官方 PC 客户端采集模块测试：三态解析、异常文案、黑帧判定、双形态配置切换。

不依赖真机客户端运行——Win32 调用与 WGC 会话一律打桩（monkeypatch 模块级
函数属性），只锚定可自动化语义（R1 三态 / R4 异常文案 / 黑帧双指标）。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

import numpy as np
from PIL import Image
from src.capture import pc_client
from src.capture.pc_client import (
    MSG_BLACK_FRAME,
    MSG_CLIENT_NOT_RUNNING,
    MSG_MINIMIZED,
    MSG_MULTI_WINDOW,
    PcClientCapture,
    PcWindowInfo,
    is_black_frame,
)


def _window(hwnd: int = 100, width: int = 1920, height: int = 1080) -> PcWindowInfo:
    return PcWindowInfo(
        hwnd=hwnd, pid=1234, title="名将杀", process_name="mjs.exe",
        client_size=(width, height), iconic=False,
    )


def _solid_image(width: int = 64, height: int = 64, rgb: tuple = (200, 200, 200)) -> Image.Image:
    array = np.zeros((height, width, 3), dtype=np.uint8)
    array[:, :] = rgb
    return Image.fromarray(array)


# ── 黑帧判定 ─────────────────────────────────────────────────────────


def test_black_frame_detects_full_black() -> None:
    assert is_black_frame(_solid_image(rgb=(0, 0, 0)))


def test_black_frame_passes_normal_content() -> None:
    # 取证统计特征：正常画面灰度均值 90~111、近黑占比 ≤7%
    assert not is_black_frame(_solid_image(rgb=(100, 100, 100)))


def test_black_frame_passes_dark_but_not_black() -> None:
    # 暗色界面（均值 30）不得误杀：均值阈值 8 以下才判黑
    assert not is_black_frame(_solid_image(rgb=(30, 30, 30)))


def test_black_frame_requires_both_metrics() -> None:
    # 半黑半亮画面：均值 128 不命中，即使近黑占比 50% 也不判黑
    mixed = np.zeros((64, 64, 3), dtype=np.uint8)          # 上半黑
    mixed[:32] = (255, 255, 255)                             # 下半亮：均值 128，近黑 50%
    assert not is_black_frame(Image.fromarray(mixed))


# ── R1 三态：连接解析 ───────────────────────────────────────────────


def test_connect_reports_when_client_not_running(monkeypatch) -> None:
    cap = PcClientCapture()
    monkeypatch.setattr(pc_client, "probe_pc_windows", lambda: [])

    ok, message = cap.connect()

    assert not ok
    assert message == MSG_CLIENT_NOT_RUNNING


def test_connect_auto_selects_single_window(monkeypatch) -> None:
    cap = PcClientCapture()
    window = _window(hwnd=42)
    monkeypatch.setattr(pc_client, "probe_pc_windows", lambda: [window])
    monkeypatch.setattr(pc_client, "_client_metrics", lambda hwnd: ((1920, 1080), (0, 0)))
    monkeypatch.setattr(pc_client._USER32, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(pc_client._USER32, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(cap, "_start_wgc", lambda: None)
    monkeypatch.setattr(cap, "screencap_full", lambda **_kw: (True, _solid_image()))

    ok, message = cap.connect(validate_capture=True)

    assert ok, message
    assert cap._hwnd == 42  # 唯一窗口自动选定（R1.2）
    cap._disconnect_safe()


def test_connect_multiple_windows_requires_selection(monkeypatch) -> None:
    cap = PcClientCapture()
    monkeypatch.setattr(
        pc_client, "probe_pc_windows",
        lambda: [_window(hwnd=1), _window(hwnd=2)],
    )

    ok, message = cap.connect()

    assert not ok
    assert message == MSG_MULTI_WINDOW  # 多开不支持，由用户选择（R1.3）


def test_connect_honors_user_selected_window(monkeypatch) -> None:
    cap = PcClientCapture()
    monkeypatch.setattr(
        pc_client, "probe_pc_windows",
        lambda: [_window(hwnd=1), _window(hwnd=2)],
    )
    monkeypatch.setattr(pc_client, "_client_metrics", lambda hwnd: ((1920, 1080), (0, 0)))
    monkeypatch.setattr(pc_client._USER32, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(pc_client._USER32, "IsIconic", lambda _hwnd: False)
    monkeypatch.setattr(cap, "_start_wgc", lambda: None)
    monkeypatch.setattr(cap, "screencap_full", lambda **_kw: (True, _solid_image()))

    cap.set_target_window(2)  # UI 用户显式选择第二窗口
    ok, _message = cap.connect(validate_capture=True)

    assert ok
    assert cap._hwnd == 2
    cap._disconnect_safe()


# ── WGC 帧消费：通道顺序与裁剪偏移 ──────────────────────────────────


def test_wgc_crop_reverses_bgra_channels(monkeypatch) -> None:
    """WGC 帧为 Direct3D BGRA：纯红像素 BGRA=[0,0,255,255]，裁剪输出必须仍是
    RGB 红而不是蓝（通道互换会让游戏暖色调整体偏蓝紫，2026-10-26 真机事故）。"""
    frame = np.zeros((60, 100, 4), dtype=np.uint8)
    frame[:, :, 2] = 255  # BGRA 的 R 在索引 2
    monkeypatch.setattr(pc_client, "_client_metrics", lambda _hwnd: ((100, 60), (200, 300)))
    monkeypatch.setattr(pc_client, "_dwm_frame_bounds", lambda _hwnd: (200, 300, 300, 360))

    image = pc_client._crop_wgc_frame_to_client(frame, 42)

    pixel = np.asarray(image)[0, 0]
    assert pixel[0] == 255 and pixel[1] == 0 and pixel[2] == 0  # RGB：红归红


def test_wgc_crop_fallback_compensates_side_shadow(monkeypatch) -> None:
    """DWM 边框查询失败回退窗口矩形时，水平方向按（矩形宽-帧宽）/2 扣除左阴影。

    几何自洽数据：窗口矩形 (200,37,316,100)，可见边框/帧 (207,37) 起 102×56
    （左右阴影各 7、上 0、下 7），客户区 100×50 @ (210,40)——修正后裁剪起点
    left=3（210-207），不修正则为 10（210-200，多算 7px 左阴影致右侧缺宽）。
    """
    monkeypatch.setattr(pc_client, "_client_metrics", lambda _hwnd: ((100, 50), (210, 40)))
    monkeypatch.setattr(pc_client, "_dwm_frame_bounds", lambda _hwnd: None)

    def fake_get_window_rect(_hwnd, rect_ptr):
        rect = ctypes.cast(rect_ptr, ctypes.POINTER(wt.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = 200, 37, 316, 100
        return True

    monkeypatch.setattr(pc_client._USER32, "GetWindowRect", fake_get_window_rect)
    frame = np.full((56, 102, 4), 200, dtype=np.uint8)

    image = pc_client._crop_wgc_frame_to_client(frame, 42)

    assert image.size == (99, 50)  # 左起 3、上起 3，右侧被 2px 边框截齐


# ── R4 异常：最小化与黑帧 ───────────────────────────────────────────


def test_screencap_reports_minimized(monkeypatch) -> None:
    cap = PcClientCapture()
    cap._hwnd = 42
    cap._connected = True
    monkeypatch.setattr(pc_client._USER32, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(pc_client._USER32, "IsIconic", lambda _hwnd: True)

    ok, payload = cap.screencap_full()

    assert not ok
    assert payload == MSG_MINIMIZED


def test_screencap_reports_black_frame_after_both_paths_fail(monkeypatch) -> None:
    cap = PcClientCapture()
    cap._hwnd = 42
    cap._connected = True
    monkeypatch.setattr(pc_client._USER32, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(pc_client._USER32, "IsIconic", lambda _hwnd: False)
    # 主路径 WGC 帧与兜底 BitBlt 均产出黑帧 → R4 全屏黑屏引导文案
    monkeypatch.setattr(cap, "_wgc", pc_client._WgcSession(42))
    monkeypatch.setattr(
        cap._wgc, "latest_frame", lambda: np.zeros((10, 10, 4), dtype=np.uint8))
    monkeypatch.setattr(
        pc_client, "_crop_wgc_frame_to_client", lambda _frame, _hwnd: _solid_image(rgb=(0, 0, 0)))
    monkeypatch.setattr(
        pc_client, "_bitblt_client_capture", lambda _hwnd: _solid_image(rgb=(0, 0, 0)))

    ok, payload = cap.screencap_full()

    assert not ok
    assert payload == MSG_BLACK_FRAME


def test_screencap_falls_back_to_bitblt_when_wgc_black(monkeypatch) -> None:
    cap = PcClientCapture()
    cap._hwnd = 42
    cap._connected = True
    monkeypatch.setattr(pc_client._USER32, "IsWindow", lambda _hwnd: True)
    monkeypatch.setattr(pc_client._USER32, "IsIconic", lambda _hwnd: False)
    session = pc_client._WgcSession(42)
    monkeypatch.setattr(session, "latest_frame", lambda: np.zeros((10, 10, 4), dtype=np.uint8))
    cap._wgc = session
    monkeypatch.setattr(
        pc_client, "_crop_wgc_frame_to_client", lambda _frame, _hwnd: _solid_image(rgb=(0, 0, 0)))
    monkeypatch.setattr(
        pc_client, "_bitblt_client_capture", lambda _hwnd: _solid_image(rgb=(120, 120, 120)))

    ok, payload = cap.screencap_full()

    assert ok
    assert isinstance(payload, Image.Image)


def test_screencap_reports_not_running_after_window_closed(monkeypatch) -> None:
    cap = PcClientCapture()
    cap._hwnd = 42
    cap._connected = True
    monkeypatch.setattr(pc_client._USER32, "IsWindow", lambda _hwnd: False)

    ok, payload = cap.screencap_full()

    assert not ok
    assert payload == MSG_CLIENT_NOT_RUNNING
    assert not cap._connected  # 会话失效即断开


# ── 连接域双形态 ─────────────────────────────────────────────────────


def _make_manager() -> "pc_client.CaptureConnectionManager":  # noqa: F821 延迟导入避免风格漂移
    from src.business.emulator.capture_connection import CaptureConnectionManager

    states: list[tuple[str, str]] = []
    manager = CaptureConnectionManager(
        on_state_change=lambda state, detail: states.append((state, detail)),
        on_status=lambda _message: None,
        on_event_status=lambda _message: None,
    )
    return manager


def test_connection_manager_builds_pc_capture_for_pc_client_config() -> None:
    manager = _make_manager()

    manager.update_config({"emulator_type": "pc_client", "pc_window_title": "名将杀"})

    assert isinstance(manager.capture, PcClientCapture)
    assert manager.connection_state[0] == "disconnected"


def test_connection_manager_rebuilds_adb_after_switching_back() -> None:
    from src.capture.adb_screen import AdbCapture

    manager = _make_manager()
    manager.update_config({"emulator_type": "pc_client"})
    assert isinstance(manager.capture, PcClientCapture)

    manager.update_config({"emulator_type": "mumu", "emulator_adb_path": "adb.exe"})

    assert isinstance(manager.capture, AdbCapture)


def test_connection_manager_keeps_pc_session_when_only_ocr_keys_change() -> None:
    manager = _make_manager()
    manager.update_config({"emulator_type": "pc_client", "pc_window_title": "名将杀"})
    first = manager.capture

    manager.update_config({
        "emulator_type": "pc_client", "pc_window_title": "名将杀",
        "mumu_ocr_enabled": True,  # 仅 OCR 参数变化，不重建会话
    })

    assert manager.capture is first


# ── 窗口值对象 ───────────────────────────────────────────────────────


def test_window_info_label_contains_title_and_resolution() -> None:
    assert _window().label == "名将杀（1920×1080）"
