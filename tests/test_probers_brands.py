"""多品牌探测：注册表分发、console 输出解析与端口扫描兜底测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from src.capture.probers import (
    EMULATOR_HINTS,
    EMULATOR_LABELS,
    EMULATOR_PROBERS,
    EMULATOR_TYPES,
    bluestacks,
    console_utils,
    ldplayer,
    memu,
    mumu,
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


def test_emulator_labels_use_plain_brand_names() -> None:
    """品牌展示名不带版本号（夜神/逍遥/MuMu/蓝叠/雷电，2026-10-25 定稿）。"""
    assert EMULATOR_LABELS == {
        "mumu": "MuMu",
        "ldplayer": "雷电",
        "nox": "夜神",
        "memu": "逍遥",
        "bluestacks": "蓝叠",
    }


def test_emulator_hints_cover_all_advertised_types() -> None:
    """R6：品牌特性提示覆盖全部品牌且非空（设备页 ⓘ 悬停文案）。"""
    assert set(EMULATOR_HINTS) == set(EMULATOR_TYPES)
    assert all(EMULATOR_HINTS[emulator_type] for emulator_type in EMULATOR_TYPES)


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


def test_nox_parses_real_list_output() -> None:
    """真机 Nox 7 实测格式：index,name,窗口标题,top句柄,bind句柄,PID,VM PID（无端口/运行列）。"""
    text = "1,Nox_1,夜神模拟器,02E610CA,009B10B6,9764,26344\r\n"

    devices = nox._parse_console_output(text)

    assert [(d.index, d.name, d.adb_port, d.is_running) for d in devices] == [
        ("1", "Nox_1", 62025, True),  # 端口为规则推导值 62001+24*1
    ]


def test_nox_stopped_instance_marks_not_running() -> None:
    text = "0,Nox,夜神模拟器,0,0,0,0\n"

    devices = nox._parse_console_output(text)

    assert devices[0].is_running is False
    assert devices[0].adb_port == 62001


def test_console_parse_ignores_malformed_lines() -> None:
    for parse in (ldplayer._parse_console_output, nox._parse_console_output):
        assert parse("header,name,a,b,c,d") == []
        assert parse("garbage line") == []
        assert parse("") == []

# ── 端口扫描兜底 ─────────────────────────────────────────────────────


def test_candidate_ports_follow_brand_rules() -> None:
    assert port_scan.candidate_ports("mumu")[:2] == [16384, 16416]
    assert port_scan.candidate_ports("ldplayer")[:3] == [5555, 5557, 5559]
    assert port_scan.candidate_ports("nox")[:3] == [62001, 62025, 62049]
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
    assert sorted(calls) == [("127.0.0.1", 5555), ("127.0.0.1", 62001)]  # 并行探测，顺序不作保证


def test_scan_open_ports_preserves_input_order_in_result(monkeypatch) -> None:
    """并行探测下结果仍按输入顺序返回。"""
    class _Open:
        def __enter__(self) -> "_Open":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    def selective(address: tuple[str, int], timeout: float | None = None) -> object:
        if address[1] == 5555:
            raise ConnectionRefusedError
        return _Open()

    monkeypatch.setattr(port_scan.socket, "create_connection", selective)

    assert port_scan.scan_open_ports([5555, 62001, 21503]) == [62001, 21503]


def test_scan_open_ports_skips_closed_ports(monkeypatch) -> None:
    def refused(address: tuple[str, int], timeout: float | None = None) -> None:
        raise ConnectionRefusedError

    monkeypatch.setattr(port_scan.socket, "create_connection", refused)

    assert port_scan.scan_open_ports([5555, 62001]) == []


def test_port_scan_candidates_marked_brand_unverified(monkeypatch) -> None:
    """R5：端口扫描候选行名标注"品牌未验证"（5555 等共用端口归属歧义）。"""
    monkeypatch.setattr(port_scan, "scan_open_ports", lambda ports, timeout=0.2: [5555])

    devices = port_scan.probe_devices_via_ports("ldplayer", "雷电")

    assert devices[0].name == "雷电 实例 1（端口扫描·品牌未验证）"


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
        lambda emulator_type, label: [EmulatorDeviceInfo("1", "MuMu 实例 1（端口扫描）", 16384, True)],
    )

    devices, error = probe_all_devices_with_status("mumu")

    assert error == ""
    assert devices[0].adb_port == 16384


# ── 蓝叠注册表与 bluestacks.conf（国内版实测驱动，2026-10-25）─────────


class _FakeRegistry:
    """dict 支撑的 winreg 桩：{(蜂巢, 子键): {值名: 数据}}，缺键/缺值抛 OSError。"""

    def __init__(self, keys: dict) -> None:
        self._keys = keys

    class _Handle:
        def __init__(self, values: dict) -> None:
            self.values = values

        def __enter__(self):
            return self

        def __exit__(self, *exc) -> bool:
            return False

    def open_key(self, root, sub_key, *args, **kwargs):
        if (root, sub_key) not in self._keys:
            raise FileNotFoundError(sub_key)
        return _FakeRegistry._Handle(self._keys[(root, sub_key)])

    def query_value_ex(self, key, value_name):
        if value_name not in key.values:
            raise FileNotFoundError(value_name)
        return key.values[value_name], 1


def _install_fake_registry(monkeypatch, keys: dict) -> None:
    winreg = pytest.importorskip("winreg")
    fake = _FakeRegistry(keys)
    monkeypatch.setattr(winreg, "OpenKey", fake.open_key)
    monkeypatch.setattr(winreg, "QueryValueEx", fake.query_value_ex)


def test_bluestacks_root_reads_cn_registry_key(monkeypatch, tmp_path) -> None:
    """R1.1：国内版键 BlueStacks_nxt_cn（HKCU 同样存在）的 InstallDir 可识别。"""
    winreg = pytest.importorskip("winreg")
    _install_fake_registry(monkeypatch, {
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\BlueStacks_nxt_cn"): {"InstallDir": str(tmp_path)},
    })
    monkeypatch.setattr(bluestacks, "find_process_dir", lambda names: None)

    assert bluestacks._find_root() == tmp_path


def test_bluestacks_conf_located_via_registry_user_defined_dir(monkeypatch, tmp_path) -> None:
    """R1.3：conf 定位优先取注册表 UserDefinedDir（国内版指向数据目录）。"""
    winreg = pytest.importorskip("winreg")
    data_dir = tmp_path / "UserData"
    data_dir.mkdir()
    conf = data_dir / "bluestacks.conf"
    conf.touch()
    _install_fake_registry(monkeypatch, {
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\BlueStacks_nxt_cn"): {"UserDefinedDir": str(data_dir)},
    })

    assert bluestacks._find_conf_path() == conf


def test_bluestacks_conf_disabled_keeps_enable_adb_hint(monkeypatch, tmp_path) -> None:
    """R1.2 门控：bst.enable_adb_access="0" 时维持"请先开启 ADB"提示语义。"""
    conf = tmp_path / "bluestacks.conf"
    conf.write_text('bst.enable_adb_access="0"\nbst.instance.Pie64.adb_port="5555"\n', encoding="utf-8")
    monkeypatch.setattr(bluestacks, "_find_root", lambda: tmp_path)
    monkeypatch.setattr(bluestacks, "_list_registry_instances", lambda: [])
    monkeypatch.setattr(bluestacks, "_find_conf_path", lambda: conf)

    devices, error = bluestacks.probe_devices()

    assert devices == []
    assert "Android 调试桥" in error


def test_bluestacks_conf_parses_instances_with_status_port_priority(monkeypatch, tmp_path) -> None:
    """R1.2：conf 实例枚举，status.adb_port（当前实际端口）优先于 adb_port。"""
    conf = tmp_path / "bluestacks.conf"
    conf.write_text(
        'bst.enable_adb_access="1"\n'
        'bst.instance.Pie64.adb_port="5556"\n'
        'bst.instance.Pie64.status.adb_port="5555"\n'
        'bst.instance.Nougat64.adb_port="5565"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(bluestacks, "_find_root", lambda: None)
    monkeypatch.setattr(bluestacks, "_list_registry_instances", lambda: [])
    monkeypatch.setattr(bluestacks, "_find_conf_path", lambda: conf)
    monkeypatch.setattr(bluestacks, "_port_open", lambda port: port == 5555)

    devices, error = bluestacks.probe_devices()

    assert error == ""
    assert [(d.name, d.adb_port, d.is_running) for d in devices] == [
        ("Nougat64", 5565, False),
        ("Pie64", 5555, True),
    ]


def test_bluestacks_conf_enabled_but_no_ports_reports_honestly(monkeypatch, tmp_path) -> None:
    """R1.4：ADB 已开启但读不到端口时，不得提示"请先开启 ADB"。"""
    conf = tmp_path / "bluestacks.conf"
    conf.write_text('bst.enable_adb_access="1"\n', encoding="utf-8")
    monkeypatch.setattr(bluestacks, "_find_root", lambda: tmp_path)
    monkeypatch.setattr(bluestacks, "_list_registry_instances", lambda: [])
    monkeypatch.setattr(bluestacks, "_find_conf_path", lambda: conf)

    devices, error = bluestacks.probe_devices()

    assert devices == []
    assert "未读到实例 ADB 端口" in error
    assert "开启后重试" not in error


def test_bluestacks_root_without_conf_reports_missing_conf(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(bluestacks, "_find_root", lambda: tmp_path)
    monkeypatch.setattr(bluestacks, "_list_registry_instances", lambda: [])
    monkeypatch.setattr(bluestacks, "_find_conf_path", lambda: None)

    devices, error = bluestacks.probe_devices()

    assert devices == []
    assert "未找到蓝叠实例配置" in error


def test_bluestacks_reports_missing_install(monkeypatch) -> None:
    monkeypatch.setattr(bluestacks, "_find_root", lambda: None)
    monkeypatch.setattr(bluestacks, "_list_registry_instances", lambda: [])
    monkeypatch.setattr(bluestacks, "_find_conf_path", lambda: None)

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


def test_parse_conf_ports_filters_invalid_values_and_tracks_gate() -> None:
    text = (
        'bst.enable_adb_access="1"\n'
        'bst.instance.Pie64.adb_port="5555"\n'
        'bst.instance.Bad.adb_port=""\n'
        'bst.instance.Worse.adb_port="abc"\n'
    )

    ports, adb_enabled = bluestacks._parse_conf_ports(text)

    assert ports == {"Pie64": 5555}
    assert adb_enabled is True

    _ports, adb_enabled = bluestacks._parse_conf_ports('bst.enable_adb_access="0"\n')
    assert adb_enabled is False


def test_ldplayer_registry_reads_ldplayer9_key(monkeypatch, tmp_path) -> None:
    """R2：LDPlayer 9 实际写 SOFTWARE\\leidian\\ldplayer9（值 InstallDir）。"""
    winreg = pytest.importorskip("winreg")
    _install_fake_registry(monkeypatch, {
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\leidian\ldplayer9"): {"InstallDir": str(tmp_path)},
    })

    assert ldplayer._probe_registry() == str(tmp_path)


# ── 真机 console 输出回归（本地样本缺失自动跳过）────────────────────


@pytest.mark.parametrize("brand,parser", [
    ("ldplayer", ldplayer._parse_console_output),
    ("nox", nox._parse_console_output),
])
def test_real_console_output_regression(brand: str, parser) -> None:
    """真机回归：真机 console 输出样本缺失时跳过；命中时至少解析出一个实例。"""
    path = FIXTURE_DIR / f"{brand}_list.txt"
    if not path.exists():
        pytest.skip(f"缺少本地真机样本: {path}")

    devices = parser(path.read_text(encoding="utf-8"))

    assert devices, f"真机样本应至少解析出一个实例: {path}"


# ── 进程反查两级实现（tasklist 门控 + 合并 PowerShell，2026-10-25）────


def _install_fake_subprocess(monkeypatch, *, tasklist_names: list[str] | None,
                             ps_lines: list[str], calls: list[str],
                             ps_returncode: int = 0) -> None:
    """替身 subprocess.run：tasklist 返回进程名 CSV；PowerShell 返回 名|路径 行。

    tasklist_names 传 None 模拟 tasklist 不可用（非零退出码）；
    ps_returncode 默认 0，可置 1 模拟请求名部分不在场的部分成功形态。
    """
    class _Result:
        def __init__(self, returncode: int, stdout: bytes) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = b""

    def fake_run(args, capture_output=True, timeout=10, **kwargs):
        calls.append(args[0])
        if args[0] == "tasklist":
            if tasklist_names is None:
                return _Result(1, b"")
            csv_lines = "".join(f'"{name}","123","Console","1","1,000 K"\r\n' for name in tasklist_names)
            return _Result(0, csv_lines.encode("gbk"))
        if args[0] == "powershell":
            return _Result(ps_returncode, ("\r\n".join(ps_lines)).encode("gbk"))
        raise AssertionError(f"不应调用其它命令: {args}")

    monkeypatch.setattr(console_utils.subprocess, "run", fake_run)


def test_find_process_dir_gate_skips_powershell_when_absent(monkeypatch, tmp_path) -> None:
    """tasklist 门控：请求名全不在场时直接否决，不发起 PowerShell 查询。"""
    calls: list[str] = []
    _install_fake_subprocess(monkeypatch, tasklist_names=["notepad.exe"], ps_lines=[], calls=calls)

    assert console_utils.find_process_dir(["dnplayer.exe", "Ld9Box.exe"]) is None
    assert calls == ["tasklist"]


def test_find_process_dir_merged_query_keeps_request_priority(monkeypatch, tmp_path) -> None:
    """在场名合并为一次 PowerShell 查询；目录按请求顺序取首个（优先级语义）。"""
    memu_dir = tmp_path / "MEmu"
    console_dir = tmp_path / "MEmuConsole"
    memu_dir.mkdir(); console_dir.mkdir()
    (memu_dir / "MEmu.exe").touch(); (console_dir / "MEmuConsole.exe").touch()
    calls: list[str] = []
    # PS 返回顺序与请求顺序相反，且混入路径为空的行——均不应影响结果
    _install_fake_subprocess(
        monkeypatch,
        tasklist_names=["MEmu.exe", "MEmuConsole.exe"],
        ps_lines=[
            f"MEmuConsole|{console_dir / 'MEmuConsole.exe'}",
            "ProtectedProc|",
            f"MEmu|{memu_dir / 'MEmu.exe'}",
        ],
        calls=calls,
    )

    result = console_utils.find_process_dir(["MEmu.exe", "MEmuConsole.exe"])

    assert result == memu_dir
    assert calls == ["tasklist", "powershell"]  # 单次合并查询


def test_find_process_dir_tasklist_failure_falls_back_to_query(monkeypatch, tmp_path) -> None:
    """tasklist 不可用（门控空集）时不否决，仍做 PowerShell 合并查询。"""
    nox_dir = tmp_path / "Nox"
    nox_dir.mkdir()
    (nox_dir / "Nox.exe").touch()
    calls: list[str] = []
    _install_fake_subprocess(
        monkeypatch, tasklist_names=None,
        ps_lines=[f"Nox|{nox_dir / 'Nox.exe'}"], calls=calls,
    )

    assert console_utils.find_process_dir(["Nox.exe"]) == nox_dir
    assert calls == ["tasklist", "powershell"]


def test_find_process_dir_parses_partial_powershell_output(monkeypatch, tmp_path) -> None:
    """回归：请求名部分不在场时 powershell 退出码为 1 但输出有效，不得按 rc 丢弃。"""
    nox_dir = tmp_path / "Nox"
    nox_dir.mkdir()
    (nox_dir / "Nox.exe").touch()
    calls: list[str] = []
    _install_fake_subprocess(
        monkeypatch,
        tasklist_names=["Nox.exe"],
        ps_lines=[f"Nox|{nox_dir / 'Nox.exe'}"],
        calls=calls,
        ps_returncode=1,  # NoxPlayer 不在场 → SilentlyContinue 仍置退出码 1
    )

    assert console_utils.find_process_dir(["Nox.exe", "NoxPlayer.exe"]) == nox_dir


# ── 夜神端口精确关联与进程定位（真机 Nox 7 校准，2026-10-24）─────────


def test_nox_attach_ports_prefers_band_and_adopts_unique_custom(monkeypatch) -> None:
    """VM PID 名下多个监听端口取夜神带内的；带外唯一端口（用户自定义）也采纳。"""
    entries = [(EmulatorDeviceInfo("1", "Nox_1", 62025, True), 26344)]
    monkeypatch.setattr(nox, "_listen_ports_by_pid", lambda: {26344: [58000, 62025]})
    nox._attach_adb_ports(entries)
    assert entries[0][0].adb_port == 62025

    custom = [(EmulatorDeviceInfo("2", "Nox_2", 62049, True), 111)]
    monkeypatch.setattr(nox, "_listen_ports_by_pid", lambda: {111: [59001]})
    nox._attach_adb_ports(custom)
    assert custom[0][0].adb_port == 59001


def test_nox_probe_reports_netstat_matched_port(monkeypatch, tmp_path) -> None:
    console = tmp_path / "NoxConsole.exe"
    console.touch()
    monkeypatch.setattr(nox, "_find_console", lambda: console)
    monkeypatch.setattr(
        nox, "run_console_tool",
        lambda args, timeout=10: ("1,Nox_1,夜神模拟器,02E610CA,009B10B6,9764,26344\n", ""),
    )
    monkeypatch.setattr(nox, "_listen_ports_by_pid", lambda: {26344: [62025]})

    devices, error = nox.probe_devices()

    assert error == ""
    assert devices[0].adb_port == 62025
    assert devices[0].is_running


def test_nox_discovers_console_via_running_process(monkeypatch, tmp_path) -> None:
    """自定义/便携安装（无注册表）时由运行中的 Nox.exe 反查 bin 目录。"""
    (tmp_path / "NoxConsole.exe").touch()
    monkeypatch.setattr(nox, "find_process_dir", lambda names: tmp_path)

    assert nox._find_console() == tmp_path / "NoxConsole.exe"


# ── 进程定位兜底的接线完整性（防"调用加了、导入漏了"的 NameError 断线）──


def test_discovery_wiring_calls_find_process_dir(monkeypatch) -> None:
    """五品牌候选链都必须真正调用 find_process_dir；断链在此以 AttributeError 暴露。"""
    seen: list[list[str]] = []

    def fake_find_process_dir(process_names):
        seen.append(process_names)
        return None

    for module in (mumu, ldplayer, nox, memu, bluestacks):
        monkeypatch.setattr(module, "find_process_dir", fake_find_process_dir)

    mumu._get_mumu_candidates()
    ldplayer._get_candidates()
    nox._get_candidates()
    memu._get_candidates()
    bluestacks._find_root()

    assert len(seen) == 5


# ── 逍遥 .memu 配置直读（真机 9.5.8.0 实测驱动，2026-10-25）────────────


_MEMU_CONF_HEAD = (
    '<?xml version="1.0"?>\n'
    '<MemuHyperv xmlns="http://www.memuhyperv.org/">\n'
    "  <Network>\n"
)
_MEMU_CONF_FORWARDING = (
    '    <Forwarding name="{name}" proto="1" hostip="127.0.0.1" hostport="{host}" '
    'guestip="10.0.2.15" guestport="{guest}"/>\n'
)
_MEMU_CONF_TAIL = "  </Network>\n</MemuHyperv>\n"


def _make_memu_instance(vms_dir: Path, name: str, adb_port: int, *, with_adb: bool = True) -> None:
    lines = [_MEMU_CONF_HEAD, _MEMU_CONF_FORWARDING.format(name="MVD", host=21501, guest=21501)]
    if with_adb:
        lines.append(_MEMU_CONF_FORWARDING.format(name="ADB", host=adb_port, guest=5555))
    lines.append(_MEMU_CONF_TAIL)
    instance = vms_dir / name
    instance.mkdir(parents=True)
    (instance / f"{name}.memu").write_text("".join(lines), encoding="utf-8")


def test_memu_config_reads_real_ports_and_running_state(monkeypatch, tmp_path) -> None:
    """:.memu 直读真实端口（非端口带推导）与运行态；衍生配置不干扰。"""
    vms_dir = tmp_path / "MEmuHyperv VMs"
    _make_memu_instance(vms_dir, "MEmu", adb_port=21517)          # 自定义端口，证明非推导
    _make_memu_instance(vms_dir, "MEmu_1", adb_port=21523)
    (vms_dir / "MEmu" / "done.memu").write_text("", encoding="utf-8")  # 0B 衍生文件
    monkeypatch.setattr(memu, "_find_vms_dirs", lambda: [vms_dir])
    monkeypatch.setattr(
        port_scan, "scan_open_ports",
        lambda ports, timeout=0.2: [port for port in ports if port == 21517])

    devices, error = memu.probe_devices()

    assert error == ""
    assert [(d.index, d.name, d.adb_port, d.is_running) for d in devices] == [
        ("0", "MEmu", 21517, True),
        ("1", "MEmu_1", 21523, False),
    ]


def test_memu_config_without_adb_forwarding_reports_config_error(monkeypatch, tmp_path) -> None:
    """VMs 目录在但读不到 ADB 转发 → 显性报配置错误，不误报未安装。"""
    vms_dir = tmp_path / "MEmuHyperv VMs"
    _make_memu_instance(vms_dir, "MEmu", adb_port=21503, with_adb=False)
    monkeypatch.setattr(memu, "_find_vms_dirs", lambda: [vms_dir])

    devices, error = memu.probe_devices()

    assert devices == []
    assert "未读到实例 ADB 端口" in error


def test_memu_missing_config_dir_falls_back_to_port_scan(monkeypatch) -> None:
    """配置目录定位失败 → 端口段扫描兜底（有产出视为成功）；扫不到报错。"""
    monkeypatch.setattr(memu, "_find_vms_dirs", lambda: [])
    monkeypatch.setattr(
        port_scan, "probe_devices_via_ports",
        lambda emulator_type, label: [EmulatorDeviceInfo("1", "逍遥 实例 1（端口扫描·品牌未验证）", 21503, True)],
    )

    devices, error = memu.probe_devices()

    assert error == ""
    assert devices[0].adb_port == 21503

    monkeypatch.setattr(port_scan, "probe_devices_via_ports", lambda emulator_type, label: [])

    devices, error = memu.probe_devices()

    assert devices == []
    assert "MEmuHyperv VMs" in error


def test_memu_vms_dir_mapped_from_headless_process(monkeypatch, tmp_path) -> None:
    """VM 运行而 GUI 关闭时由 MEmuHeadless 进程目录映射回安装根。"""
    vms = tmp_path / "MEmu" / "MEmuHyperv VMs"
    vms.mkdir(parents=True)
    monkeypatch.delenv("MEMU_HOME", raising=False)
    monkeypatch.setattr(memu, "_probe_registry", lambda: None)
    monkeypatch.setattr(memu, "find_process_dir", lambda names: tmp_path / "MEmuHyperv")

    assert memu._find_vms_dirs() == [vms]


def test_memu_vms_dir_cheap_candidates_skip_process_query(monkeypatch, tmp_path) -> None:
    """MEMU_HOME 等廉价候选已命中时短路，不再付进程反查开销。"""
    vms = tmp_path / "MEmuHyperv VMs"
    vms.mkdir(parents=True)
    monkeypatch.setenv("MEMU_HOME", str(tmp_path))
    monkeypatch.setattr(memu, "_probe_registry", lambda: None)
    monkeypatch.setattr(
        memu, "find_process_dir",
        lambda names: (_ for _ in ()).throw(AssertionError("不应触发进程反查")))

    assert memu._find_vms_dirs() == [vms]


def test_memu_parse_tolerates_broken_configs(monkeypatch, tmp_path) -> None:
    """损坏/非 XML 的 .memu 被跳过，不中断整体探测。"""
    vms_dir = tmp_path / "MEmuHyperv VMs"
    _make_memu_instance(vms_dir, "MEmu", adb_port=21503)
    broken = vms_dir / "MEmu_Broken"
    broken.mkdir()
    (broken / "MEmu_Broken.memu").write_text("not-xml <<<", encoding="utf-8")
    monkeypatch.setattr(memu, "_find_vms_dirs", lambda: [vms_dir])
    monkeypatch.setattr(port_scan, "scan_open_ports", lambda ports, timeout=0.2: [])

    devices, error = memu.probe_devices()

    assert error == ""
    assert [d.name for d in devices] == ["MEmu"]
