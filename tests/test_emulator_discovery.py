"""MuMu 设备探测的失败恢复测试（经统一入口按 mumu 分发）。"""

from __future__ import annotations

from types import SimpleNamespace

from src.capture.emulator_discovery import discover_devices_with_status


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

    monkeypatch.setattr("src.capture.emulator_discovery.mumu._find_mumu_root", lambda: tmp_path)
    monkeypatch.setattr("src.capture.emulator_discovery.mumu.subprocess.run", lambda *_args, **_kwargs: responses.pop(0))
    monkeypatch.setattr("src.capture.emulator_discovery.mumu.time.sleep", lambda _seconds: None)

    devices, error = discover_devices_with_status("mumu")

    assert error == ""
    assert [(device.name, device.adb_port, device.is_running) for device in devices] == [
        ("MuMu", 16448, True),
    ]


def test_mumu_find_adb_and_root_scan_candidate_paths(tmp_path, monkeypatch) -> None:
    """特征测试：find_adb/_find_mumu_root 按候选目录顺序命中（含 nx_main 子路径）。

    锚定候选扫描行为后，删除与 common_paths 完全重复的 legacy 双循环
    时以本用例 + 既有 probe 用例保证等价。
    """
    from src.capture.emulator_discovery import mumu as mumu_module

    root = tmp_path / "MuMu Player 12"
    (root / "nx_main").mkdir(parents=True)
    adb = root / "nx_main" / "adb.exe"
    adb.touch()
    monkeypatch.setattr(mumu_module, "_get_mumu_candidates", lambda: [root])

    assert mumu_module._find_mumu_root() == root
    assert mumu_module.find_adb() == str(adb.resolve())
