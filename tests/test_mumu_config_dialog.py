"""MuMu 设备选择与连通性测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.capture.prober import MuMuDeviceInfo
from src.ui.mumu_config_dialog import MumuConfigDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_unique_running_device_keeps_auto_port(monkeypatch) -> None:
    _app()
    unique = MuMuDeviceInfo("1", "唯一实例", 16448, True)
    monkeypatch.setattr("src.ui.mumu_config_dialog.probe_all_devices", lambda: [unique])
    dialog = MumuConfigDialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0})

    assert dialog._device_combo.currentData() == unique
    assert dialog._config["mumu_adb_port"] == 0


def test_multiple_running_devices_require_explicit_selection(monkeypatch) -> None:
    _app()
    devices = [
        MuMuDeviceInfo("1", "实例 A", 16448, True),
        MuMuDeviceInfo("2", "实例 B", 16416, True),
    ]
    monkeypatch.setattr("src.ui.mumu_config_dialog.probe_all_devices", lambda: devices)
    dialog = MumuConfigDialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 0})

    assert dialog._device_combo.currentData() is None
    assert "请选择运行中的实例" in dialog._device_combo.currentText()


def test_saved_port_restores_matching_device(monkeypatch) -> None:
    _app()
    devices = [
        MuMuDeviceInfo("1", "实例 A", 16448, True),
        MuMuDeviceInfo("2", "实例 B", 16416, True),
    ]
    monkeypatch.setattr("src.ui.mumu_config_dialog.probe_all_devices", lambda: devices)
    dialog = MumuConfigDialog({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16416})

    assert dialog._device_combo.currentData() == devices[1]
