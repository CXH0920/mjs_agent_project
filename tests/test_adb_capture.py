"""ADB 会话精确验证与离线失效测试。"""

from __future__ import annotations

import io
import logging
import struct
from types import SimpleNamespace

import numpy as np
from PIL import Image
from src.capture.adb_screen import AdbCapture


def test_connect_requires_requested_target_device(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448)
    monkeypatch.setattr(cap, "_check_adb_valid", lambda: (True, ""))
    monkeypatch.setattr(cap, "_run_adb", lambda *args, **kwargs: (True, "connected"))
    monkeypatch.setattr(
        cap,
        "_get_device_state",
        lambda serial: (True, "offline") if serial == "127.0.0.1:16448" else (True, "device"),
    )
    monkeypatch.setattr(cap, "_disconnect_safe", lambda: None)

    ok, message = cap.connect()

    assert not ok
    assert "目标设备状态异常" in message
    assert not cap.connected


def test_check_device_offline_invalidates_cached_session(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448)
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    monkeypatch.setattr(cap, "_get_device_state", lambda serial: (True, "offline"))

    ok, message = cap.check_device()

    assert not ok
    assert "offline" in message
    assert not cap.connected
    assert cap.device_serial == ""


def test_screencap_device_offline_invalidates_session(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448)
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    monkeypatch.setattr(
        "src.capture.adb_screen.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr=b"error: device offline", stdout=b""),
    )

    ok, message = cap.screencap_full()

    assert not ok
    assert "device offline" in message
    assert not cap.connected
    assert cap.device_serial == ""


def test_screencap_decode_error_keeps_session(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448)
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    monkeypatch.setattr(
        "src.capture.adb_screen.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b"", stdout=b"not an image"),
    )

    ok, _ = cap.screencap_full()

    assert not ok


def test_screencap_rejects_non_png_output(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448)
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="JPEG")
    monkeypatch.setattr(
        "src.capture.adb_screen.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b"", stdout=buffer.getvalue()),
    )
    monkeypatch.setattr("src.capture.adb_screen.time.sleep", lambda _: None)

    ok, message = cap.screencap_full()

    assert not ok
    assert "PNG" in message


def test_screencap_retries_truncated_output(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448)
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    valid_png = buffer.getvalue()
    outputs = [valid_png[:-8], valid_png]

    monkeypatch.setattr(
        "src.capture.adb_screen.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stderr=b"", stdout=outputs.pop(0)
        ),
    )
    monkeypatch.setattr("src.capture.adb_screen.time.sleep", lambda _: None)

    ok, result = cap.screencap_full()

    assert ok
    assert result.size == (2, 2)


def test_screencap_retries_empty_output(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448)
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "blue").save(buffer, format="PNG")
    outputs = [b"", buffer.getvalue()]

    monkeypatch.setattr(
        "src.capture.adb_screen.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stderr=b"", stdout=outputs.pop(0)
        ),
    )
    monkeypatch.setattr("src.capture.adb_screen.time.sleep", lambda _: None)

    ok, result = cap.screencap_full()

    assert ok
    assert result.size == (1, 1)


def test_screencap_can_suppress_success_log(monkeypatch, caplog) -> None:
    cap = AdbCapture("adb.exe", 16448)
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "green").save(buffer, format="PNG")
    monkeypatch.setattr(
        "src.capture.adb_screen.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stderr=b"", stdout=buffer.getvalue()),
    )

    with caplog.at_level(logging.INFO, logger="src.capture.adb_screen"):
        ok, _ = cap.screencap_full(log_success=False)

    assert ok
    assert "截图成功" not in caplog.text


def _raw_screencap_frame(rgb: Image.Image) -> bytes:
    """按 Android screencap raw 帧格式构造字节：16 字节头 + RGBA_8888 像素。"""
    pixels = np.asarray(rgb.convert("RGBA"), dtype=np.uint8)
    height, width = pixels.shape[:2]
    header = struct.pack("<IIII", width, height, 1, 1)
    return header + pixels.tobytes()


def test_decode_raw_screencap_matches_png_pixels() -> None:
    rgb = Image.new("RGB", (3, 2))
    rgb.putdata([(i * 30 % 256, i * 70 % 256, i * 110 % 256) for i in range(6)])

    decoded = AdbCapture._decode_raw_screencap(_raw_screencap_frame(rgb))

    assert decoded is not None
    assert decoded.mode == "RGB"
    assert np.asarray(decoded).tolist() == np.asarray(rgb).tolist()


def test_decode_raw_screencap_rejects_bad_frames() -> None:
    rgb = Image.new("RGB", (2, 2), "red")
    frame = _raw_screencap_frame(rgb)
    wrong_format = struct.pack("<IIII", 2, 2, 3, 1) + frame[16:]
    wrong_size = struct.pack("<IIII", 2, 3, 1, 1) + frame[16:]
    truncated = frame[:-1]

    assert AdbCapture._decode_raw_screencap(b"\x00" * 8) is None
    assert AdbCapture._decode_raw_screencap(wrong_format) is None
    assert AdbCapture._decode_raw_screencap(wrong_size) is None
    assert AdbCapture._decode_raw_screencap(truncated) is None


def test_screencap_raw_mode_returns_image(monkeypatch, caplog) -> None:
    cap = AdbCapture("adb.exe", 16448, screenshot_mode="raw")
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    frame = _raw_screencap_frame(Image.new("RGB", (2, 2), "blue"))
    captured_args: list[tuple] = []

    def fake_run(*args, **kwargs):
        captured_args.append(args[0])
        return SimpleNamespace(returncode=0, stderr=b"", stdout=frame)

    monkeypatch.setattr("src.capture.adb_screen.subprocess.run", fake_run)

    with caplog.at_level(logging.INFO, logger="src.capture.adb_screen"):
        ok, result = cap.screencap_full()

    assert ok
    assert result.size == (2, 2)
    assert captured_args[0][-1] != "-p"  # raw 模式不带 -p
    assert "（raw）" in caplog.text


def test_screencap_auto_falls_back_to_png_when_raw_undecodable(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448, screenshot_mode="auto")
    cap._connected = True
    cap._device_serial = "127.0.0.1:16448"
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "red").save(buffer, format="PNG")
    captured_args: list[tuple] = []

    def fake_run(*args, **kwargs):
        captured_args.append(args[0])
        return SimpleNamespace(returncode=0, stderr=b"", stdout=buffer.getvalue())

    monkeypatch.setattr("src.capture.adb_screen.subprocess.run", fake_run)

    ok, result = cap.screencap_full()

    assert ok
    assert result.size == (2, 2)
    assert captured_args[0][-1] != "-p"  # 第一轮先尝试 raw
    assert captured_args[-1][-1] == "-p"  # 回退 PNG


def test_screencap_invalid_mode_falls_back_to_auto(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 16448, screenshot_mode="bogus")

    assert cap._screenshot_mode == "auto"


def test_connect_with_auto_port_uses_unique_running_instance(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 0)
    monkeypatch.setattr(cap, "_check_adb_valid", lambda: (True, ""))
    calls: list[tuple[str, ...]] = []

    def fake_run(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("-s", "127.0.0.1:16448"):
            return True, "device"
        return True, "connected"

    monkeypatch.setattr(cap, "_run_adb", fake_run)
    monkeypatch.setattr(
        "src.capture.prober.probe_running_devices",
        lambda: [SimpleNamespace(adb_port=16448)],
    )

    ok, _ = cap.connect()

    assert ok
    assert cap.device_serial == "127.0.0.1:16448"
    assert ("connect", "127.0.0.1:16448") in calls
    assert ("-s", "127.0.0.1:16448", "get-state") in calls


def test_connect_with_auto_port_rejects_multiple_running_instances(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 0)
    monkeypatch.setattr(
        "src.capture.prober.probe_running_devices",
        lambda: [SimpleNamespace(adb_port=16448), SimpleNamespace(adb_port=16416)],
    )

    ok, message = cap.connect()

    assert not ok
    assert "多个运行中的 MuMu 实例" in message


def test_connect_with_auto_port_rejects_no_running_instance(monkeypatch) -> None:
    cap = AdbCapture("adb.exe", 0)
    monkeypatch.setattr("src.capture.prober.probe_running_devices", lambda: [])

    ok, message = cap.connect()

    assert not ok
    assert "未检测到运行中的 MuMu 实例" in message
