"""蓝叠 BlueStacks 5 实例探测（多品牌连接支持）。

注册表契约：probe_devices(retries) / find_adb()。实例 adb 端口两级读取：
注册表 Guests\\<实例>\\BstAdbPort（国际版契约）→ bluestacks.conf 解析
（国内版 5.22 注册表无 Guests 子键，实例配置在 conf，真机取证 2026-10-25）。
端口每实例动态分配，无固定端口段可扫描，不设 port_scan 兜底。
"""

from __future__ import annotations

import logging
import re
import socket
from pathlib import Path

from src.capture.probers.console_utils import find_process_dir
from src.capture.probers.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)

_PORT_PROBE_TIMEOUT_S = 0.2
# 产品注册表键：国际版 nxt 与国内版 nxt_cn 并存兼容（真机取证 2026-10-25）
_REGISTRY_PRODUCTS = ("BlueStacks_nxt", "BlueStacks_nxt_cn", "BlueStacks_x")


def _query_registry_value(value_name: str) -> str | None:
    """按产品键 × 蜂巢（HKLM/HKCU，含 WOW6432Node）逐一读取首个非空值。"""
    try:
        import winreg
    except ImportError:
        return None
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for prefix in ("SOFTWARE", r"SOFTWARE\WOW6432Node"):
            for product in _REGISTRY_PRODUCTS:
                try:
                    with winreg.OpenKey(hive, rf"{prefix}\{product}") as key:
                        value, _ = winreg.QueryValueEx(key, value_name)
                        if value:
                            return str(value)
                except OSError:
                    continue
    return None


def _find_root() -> Path | None:
    """查找蓝叠 5 安装根目录：注册表（标准安装）→ 运行进程反查（自定义路径兜底）。"""
    process_dir = find_process_dir(["HD-Player.exe"])
    if process_dir is not None and (process_dir / "HD-Adb.exe").exists():
        return process_dir
    install_dir = _query_registry_value("InstallDir")
    if install_dir:
        return Path(install_dir)
    return None


def find_adb() -> str:
    """探测蓝叠安装目录自带的 HD-Adb.exe（PATH 分支在包统一入口 probe_adb）。"""
    root = _find_root()
    if root is not None:
        p = root / "HD-Adb.exe"
        if p.exists():
            return str(p.resolve())
    return ""


def _list_registry_instances() -> list[EmulatorDeviceInfo]:
    """枚举注册表 Guests 各实例的 BstAdbPort；端口可连通即视为运行中。

    国际版契约为主（SOFTWARE\\BlueStacks_nxt\\Guests）；国内版键一并尝试，
    但真机国内版无 Guests 子键（此路径为空，实例走 bluestacks.conf）。
    各产品键逐一尝试，单键缺失（OSError）属正常不告警。
    """
    devices: list[EmulatorDeviceInfo] = []
    try:
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for prefix in ("SOFTWARE", r"SOFTWARE\WOW6432Node"):
                for product in _REGISTRY_PRODUCTS:
                    try:
                        with winreg.OpenKey(hive, rf"{prefix}\{product}\Guests") as guests_key:
                            for i in range(winreg.QueryInfoKey(guests_key)[0]):
                                name = winreg.EnumKey(guests_key, i)
                                try:
                                    with winreg.OpenKey(guests_key, name) as instance_key:
                                        port, _ = winreg.QueryValueEx(instance_key, "BstAdbPort")
                                    port = int(port)
                                except (OSError, ValueError):
                                    continue
                                devices.append(EmulatorDeviceInfo(
                                    index=str(i),
                                    name=name,
                                    adb_port=port,
                                    is_running=_port_open(port),
                                ))
                    except OSError:
                        continue
                    if devices:
                        return devices
    except ImportError:
        pass
    return devices


def _find_conf_path() -> Path | None:
    """定位 bluestacks.conf：注册表 UserDefinedDir / DataDir → ProgramData 兜底。

    国内版 UserDefinedDir 指向数据目录而非安装目录（真机实测），conf 随数据目录走。
    """
    for value_name in ("UserDefinedDir", "DataDir"):
        directory = _query_registry_value(value_name)
        if directory:
            candidate = Path(directory) / "bluestacks.conf"
            if candidate.exists():
                return candidate
    for common in (
        Path("C:/ProgramData/BlueStacks_nxt/bluestacks.conf"),
        Path("C:/ProgramData/BlueStacks_nxt_cn/bluestacks.conf"),
    ):
        if common.exists():
            return common
    return None


def _to_port(value: str) -> int:
    """conf 端口值转 int；异常值（空/非数字）归 0 由调用方过滤。"""
    try:
        return int(value)
    except ValueError:
        return 0


def _parse_conf_ports(text: str) -> tuple[dict[str, int], bool]:
    """解析 bluestacks.conf：返回 (实例名 → adb 端口, ADB 访问是否开启)。

    bst.instance.<名>.adb_port 为配置端口；bst.instance.<名>.status.adb_port
    为当前实际端口，两者并存时 status 优先（真机国内版 5.22 实测）。
    """
    configured: dict[str, int] = {}
    status: dict[str, int] = {}
    adb_enabled = True
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"')
        if key == "bst.enable_adb_access":
            adb_enabled = value == "1"
            continue
        match = re.fullmatch(r"bst\.instance\.([^.]+)\.status\.adb_port", key)
        if match:
            status[match.group(1)] = _to_port(value)
            continue
        match = re.fullmatch(r"bst\.instance\.([^.]+)\.adb_port", key)
        if match:
            configured[match.group(1)] = _to_port(value)
    ports = {**configured, **status}
    return {name: port for name, port in ports.items() if port > 0}, adb_enabled


def _list_conf_instances() -> tuple[list[EmulatorDeviceInfo], str]:
    """解析 bluestacks.conf 组装实例列表；读不到实例时返回原因（供失败提示）。"""
    conf = _find_conf_path()
    if conf is None:
        return [], ""
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("读取蓝叠配置失败：%s", exc)
        return [], ""
    ports, adb_enabled = _parse_conf_ports(text)
    if not adb_enabled:
        return [], "蓝叠 ADB 访问未开启，请先在蓝叠 设置 → 高级 → Android 调试桥(ADB) 中开启后重试"
    if not ports:
        return [], "蓝叠 ADB 已开启，但配置文件中未读到实例 ADB 端口（bluestacks.conf）"
    devices = [
        EmulatorDeviceInfo(
            index=str(ordinal), name=name, adb_port=port, is_running=_port_open(port),
        )
        for ordinal, (name, port) in enumerate(sorted(ports.items()), start=1)
    ]
    logger.info("bluestacks.conf 解析到 %d 个蓝叠实例", len(devices))
    return devices, ""


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_PORT_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def probe_devices(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所有蓝叠实例：注册表 Guests → bluestacks.conf → 按取证给失败原因。

    ADB 已开启但读不到端口时不再提示"请先开启 ADB"（R1.4，真机取证该提示误导）。
    """
    devices = _list_registry_instances()
    if devices:
        logger.info("探测到 %d 个蓝叠实例", len(devices))
        return devices, ""
    devices, conf_error = _list_conf_instances()
    if devices:
        return devices, ""
    if conf_error:
        return [], conf_error
    if _find_root() is not None:
        return [], "未找到蓝叠实例配置（bluestacks.conf），无法读取 ADB 端口"
    return [], "未找到蓝叠安装目录（注册表 BlueStacks_nxt / BlueStacks_nxt_cn）"
