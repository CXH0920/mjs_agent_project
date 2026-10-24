"""多品牌探测：注册表分发、console 输出解析与端口扫描兜底测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.capture.probers import (
    EMULATOR_LABELS,
    EMULATOR_PROBERS,
    EMULATOR_TYPES,
    bluestacks,
    ldplayer,
    memu,
    normalize_emulator_type,
    nox,
    port_scan,
    probe_all_devices_with_status,
)
from src.capture.probers.models import EmulatorDeviceInfo

FIXTURE_DIR = Path(__file__).parent / "fixtures_local" / "probers"


# ── 注册表与分发 ─────────────────────────────────────────────────────


def test_registry_covers_all_advertised_types() -> None:
    assert set(EMULATOR_TYPES) == set(EMULATOR_PROBERS) == set(EMULATOR_LABELS)


def test_normalize_unknown_type_falls_back_to_mumu() -> None:
    assert normalize_emulator_type("ldplayer") == "ldplayer"
    assert normalize_emulator_type("bogus") == "mumu"
    assert normalize_emulator_type(None) == "mumu"


def test_dispatch_routes_to_selected_brand(monkeypatch) -> None:
    def fake_probe(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
        return [EmulatorDeviceInfo("0", "雷电", 5555, True)], ""

    monkeypatch.setattr(ldplayer, "probe_devices", fake_probe)
    devices, error = probe_all_devices_with_status("ldplayer")

    assert error == ""
    assert devices[0].adb_port == 5555


# ── console 输出解析 ─────────────────────────────────────────────────


def test_ldplayer_parses_list2_output() -> None:
    text = "0,雷电模拟器,132942,132938,1,11356,13292816\n1,雷电模拟器-1,132940,132936,0,,\n"

    devices = ldplayer._parse_console_output(text)

    assert [(d.index, d.name, d.adb_port, d.is_running) for d in devices] == [
        ("0", "雷电模拟器", 5555, True),
        ("1", "雷电模拟器-1", 5557, False),
    ]


def test_nox_parses_list_output() -> None:
    text = "0,NoxPlayer,132604,131980,1,5584\n1,Nox_1,132600,131976,0,\n"

    devices = nox._parse_console_output(text)

    assert [(d.index, d.adb_port, d.is_running) for d in devices] == [
        ("0", 62001, True),
        ("1", 62002, False),
    ]


def test_memu_parses_list_output() -> None:
    text = "0,MEmu,2306536,2306524,1,10988\n1,MEmu_1,2306530,2306520,0,\n"

    devices = memu._parse_console_output(text)

    assert [(d.index, d.adb_port, d.is_running) for d in devices] == [
        ("0", 21503, True),
        ("1", 21513, False),
    ]


def test_console_parse_ignores_malformed_lines() -> None:
    for parse in (ldplayer._parse_console_output, nox._parse_console_output, memu._parse_console_output):
        assert parse("header,name,a,b,c,d") == []
        assert parse("garbage line") == []
        assert parse("") == []


# ── 端口扫描兜底 ─────────────────────────────────────────────────────


def test_candidate_ports_follow_brand_rules() -> None:
    assert port_scan.candidate_ports("mumu")[:2] == [16384, 16416]
    assert port_scan.candidate_ports("ldplayer")[:3] == [5555, 5557, 5559]
    assert port_scan.candidate_ports("nox")[:3] == [62001, 62002, 62003]
    assert port_scan.candidate_ports("memu")[:2] == [21503, 21513]
    assert port_scan.candidate_ports("bluestacks") == []


def test_scan_open_ports_probes_each_socket(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    class _FakeConn:
        def __init__(self, address: tuple[str, int], timeout: float | None = None) -> None:
            calls.append(address)

        def __enter__(self) -> "_FakeConn":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(port_scan.socket, "create_connection", _FakeConn)

    assert port_scan.scan_open_ports([5555, 62001]) == [5555, 62001]
    assert calls == [("127.0.0.1", 5555), ("127.0.0.1", 62001)]


def test_scan_open_ports_skips_closed_ports(monkeypatch) -> None:
    def refused(address: tuple[str, int], timeout: float | None = None) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(port_scan.socket, "create_connection", refused)

    assert port_scan.scan_open_ports([5555, 62001]) == []


def test_ldplayer_console_failure_falls_back_to_port_scan(monkeypatch) -> None:
    monkeypatch.setattr(ldplayer, "_find_console", lambda: None)
    monkeypatch.setattr(
        port_scan, "probe_devices_via_ports",
        lambda emulator_type, label: [EmulatorDeviceInfo("1", "雷电 实例 1（端口扫描）", 5555, True)],
    )

    devices, error = ldplayer.probe_devices()

    assert error == ""
    assert devices[0].adb_port == 5555
    assert "端口扫描" in devices[0].name


def test_console_failure_reports_error_when_scan_finds_nothing(monkeypatch) -> None:
    monkeypatch.setattr(ldplayer, "_find_console", lambda: None)
    monkeypatch.setattr(port_scan, "probe_devices_via_ports", lambda emulator_type, label: [])

    devices, error = ldplayer.probe_devices()

    assert devices == []
    assert "端口段扫描也未发现实例" in error


def test_mumu_manager_failure_falls_back_to_port_scan(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("src.capture.probers.mumu._find_mumu_root", lambda: None)
    monkeypatch.setattr(
        port_scan, "probe_devices_via_ports",
        lambda emulator_type, label: [EmulatorDeviceInfo("1", "MuMu 12 实例 1（端口扫描）", 16384, True)],
    )

    devices, error = probe_all_devices_with_status("mumu")

    assert error == ""
    assert devices[0].adb_port == 16384


# ── 蓝叠注册表路径 ───────────────────────────────────────────────────


def test_bluestacks_hints_adb_when_no_port_registered(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bluestacks, "_find_root", lambda: tmp_path)
    monkeypatch.setattr(bluestacks, "_list_registry_instances", lambda: [])

    devices, error = bluestacks.probe_devices()

    assert devices == []
    assert "Android 调试桥" in error


def test_bluestacks_reports_missing_install(monkeypatch) -> None:
    monkeypatch.setattr(bluestacks, "_find_root", lambda: None)
    monkeypatch.setattr(bluestacks, "_list_registry_instances", lambda: [])

    devices, error = bluestacks.probe_devices()

    assert devices == []
    assert "未找到蓝叠安装目录" in error


def test_bluestacks_returns_registry_instances(monkeypatch) -> None:
    monkeypatch.setattr(
        bluestacks, "_list_registry_instances",
        lambda: [EmulatorDeviceInfo("0", "BlueStacks", 5565, True)],
    )
    monkeypatch.setattr(bluestacks, "_find_root", lambda: None)

    devices, error = bluestacks.probe_devices()

    assert error == ""
    assert devices[0].adb_port == 5565


# ── 真机 console 输出回归（本地样本缺失自动跳过）────────────────────


@pytest.mark.parametrize("brand,parser", [
    ("ldplayer", ldplayer._parse_console_output),
    ("nox", nox._parse_console_output),
    ("memu", memu._parse_console_output),
])
def test_real_console_output_regression(brand: str, parser) -> None:
    """真机回归：真机 console 输出样本缺失时跳过；命中时至少解析出一个实例。"""
    path = FIXTURE_DIR / f"{brand}_list.txt"
    if not path.exists():
        pytest.skip(f"缺少本地真机样本: {path}")

    devices = parser(path.read_text(encoding="utf-8"))

    assert devices, f"真机样本应至少解析出一个实例: {path}"
