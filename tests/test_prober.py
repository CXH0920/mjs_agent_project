"""MuMu 设备探测的失败恢复测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.capture.prober import probe_all_devices_with_status


def test_device_probe_retries_after_manager_failure(tmp_path, monkeypatch) -> None:
    manager_path = tmp_path / "nx_main" / "MuMuManager.exe"
    manager_path.parent.mkdir()
    manager_path.touch()
    responses = [
        SimpleNamespace(returncode=3221226505, stdout=b""),
        SimpleNamespace(
            returncode=0,
            stdout=b'{"1": {"name": "MuMu", "adb_port": 16448, "is_android_started": true}}',
        ),
    ]

    monkeypatch.setattr("src.capture.prober._find_mumu_root", lambda: tmp_path)
    monkeypatch.setattr("src.capture.prober.subprocess.run", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr("src.capture.prober.time.sleep", lambda _seconds: None)

    devices, error = probe_all_devices_with_status()

    assert error == ""
    assert [(device.name, device.adb_port, device.is_running) for device in devices] == [
        ("MuMu", 16448, True),
    ]
