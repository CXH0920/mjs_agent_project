"""ADB 会话精确验证与离线失效测试。"""

from __future__ import annotations

import io
import logging
from types import SimpleNamespace

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
