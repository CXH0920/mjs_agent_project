"""夜神 Nox 实例探测（多品牌连接支持；list 格式与端口按真机 Nox 7 校准）。

注册表契约：discover_devices(retries) / find_adb()。NoxConsole list 输出七列
（index,name,窗口标题,top句柄,bind句柄,PID,VM PID），没有运行标志列也没有
adb 端口列：运行状态由 PID/VM PID 非 0 推断；adb 端口优先用 netstat 把
LISTENING 端口与 VM PID 精确关联（用户自定义端口也能命中），无法关联时按
官方默认段 62001+24n 推导。console 不可用/失败时回退端口段扫描。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import src.capture.emulator_discovery.port_scan as port_scan
from src.capture.emulator_discovery.console_utils import (
    decode_console_bytes,
    find_process_dir,
    run_console_tool,
)
from src.capture.emulator_discovery.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)

_LABEL = "夜神"
# 端口规则单源于 port_scan.PORT_RULES：console 解析推导与兜底扫描带共用同一份
_ADB_PORT_BASE, _ADB_PORT_STEP, _PORT_BAND_COUNT = port_scan.PORT_RULES["nox"]
# 夜神 adb 端口默认带（62001+24n）；netstat 关联时用于多端口过滤
_NOX_PORT_BAND = range(_ADB_PORT_BASE, _ADB_PORT_BASE + _ADB_PORT_STEP * _PORT_BAND_COUNT)


def _probe_registry() -> str | None:
    """通过 Windows 注册表查找夜神标准安装路径（便携/自定义安装不写注册表）。"""
    try:
        import winreg
        for key_path in [
            r"SOFTWARE\BigNox\Nox",
            r"SOFTWARE\WOW6432Node\BigNox\Nox",
            r"SOFTWARE\Bignox",
            r"SOFTWARE\WOW6432Node\Bignox",
        ]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    for value in ("InstallDir", "InstallPath"):
                        try:
                            install_dir, _ = winreg.QueryValueEx(key, value)
                            if install_dir:
                                return install_dir
                        except OSError:
                            continue
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _get_candidates() -> list[Path]:
    """收集夜神安装路径候选（console 与 adb 均在 bin 下）。"""
    candidates: list[Path] = []

    home = os.environ.get("NOX_HOME")
    if home:
        candidates.append(Path(home))

    # 运行进程反查：自定义/便携安装（如 G:\xxx\Nox）无注册表无固定路径，
    # 模拟器开着时由 Nox.exe 位置直接定位 bin 目录
    process_dir = find_process_dir(["Nox.exe", "NoxPlayer.exe"])
    if process_dir is not None and process_dir not in candidates:
        candidates.append(process_dir)

    reg_path = _probe_registry()
    if reg_path:
        p = Path(reg_path)
        if p not in candidates:
            candidates.append(p)

    for cp in [
        "C:/Program Files/Nox/bin",
        "C:/Program Files (x86)/Nox/bin",
        "D:/Program Files/Nox/bin",
        "C:/Nox/bin",
        "D:/Nox/bin",
    ]:
        p = Path(cp)
        if p not in candidates:
            candidates.append(p)
    return candidates


def _find_console() -> Path | None:
    """查找 NoxConsole.exe。"""
    which = shutil.which("NoxConsole")
    if which:
        return Path(which)
    for root in _get_candidates():
        p = root / "NoxConsole.exe"
        if p.exists():
            return p
    return None


def find_adb() -> str:
    """探测夜神安装目录自带的 adb.exe（PATH 分支在包统一入口 resolve_adb_path）。"""
    for root in _get_candidates():
        p = root / "adb.exe"
        if p.exists():
            return str(p.resolve())
    return ""


def _parse_entries(text: str) -> list[tuple[EmulatorDeviceInfo, int]]:
    """解析 NoxConsole list：每行 -> (设备, VM PID)。"""
    entries: list[tuple[EmulatorDeviceInfo, int]] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.replace("\t", ",").split(",")]
        if len(fields) < 7 or not fields[0].isdigit():
            continue
        index = int(fields[0])
        pid = int(fields[5]) if fields[5].isdigit() else 0
        vm_pid = int(fields[6]) if fields[6].isdigit() else 0
        entries.append((EmulatorDeviceInfo(
            index=str(index),
            name=fields[1] or f"{_LABEL} 实例 {index}",
            adb_port=_ADB_PORT_BASE + _ADB_PORT_STEP * index,
            is_running=pid > 0 or vm_pid > 0,
        ), vm_pid))
    return entries


def _parse_console_output(text: str) -> list[EmulatorDeviceInfo]:
    """解析 list 输出为设备列表（端口为规则推导值，运行实例会被 netstat 关联覆写）。"""
    return [device for device, _vm_pid in _parse_entries(text)]


def _listen_ports_by_pid() -> dict[int, list[int]]:
    """netstat -ano 的 127.0.0.1 LISTENING 端口按属主 PID 归组。"""
    try:
        result = subprocess.run(["netstat", "-ano"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.debug("netstat 查询失败（夜神端口退回规则推导）：%s", exc)
        return {}
    ports: dict[int, list[int]] = {}
    for match in re.finditer(
            r"127\.0\.0\.1:(\d+)\s+\S+\s+LISTENING\s+(\d+)",
            decode_console_bytes(result.stdout),
    ):
        ports.setdefault(int(match.group(2)), []).append(int(match.group(1)))
    return ports


def _attach_adb_ports(entries: list[tuple[EmulatorDeviceInfo, int]]) -> None:
    """运行实例的端口精确关联：VM PID 名下的 LISTENING 端口优先取夜神带内的。"""
    ports_by_pid = _listen_ports_by_pid()
    if not ports_by_pid:
        return
    for device, vm_pid in entries:
        if not device.is_running or vm_pid not in ports_by_pid:
            continue
        ports = ports_by_pid[vm_pid]
        in_band = [port for port in ports if port in _NOX_PORT_BAND]
        if in_band:
            device.adb_port = in_band[0]
        elif len(ports) == 1:
            device.adb_port = ports[0]
        else:
            logger.warning("夜神实例 %s 的 VM 进程 %d 名下有多个监听端口 %s，保持规则推导端口 %d",
                           device.name, vm_pid, ports, device.adb_port)


def discover_devices(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所有夜神实例；console 失败时回退端口段扫描。"""
    console = _find_console()
    if console is None:
        return port_scan.fallback_result("nox", _LABEL, "未找到夜神 console 工具（NoxConsole）")

    output, error = run_console_tool([str(console), "list"])
    if output is None:
        return port_scan.fallback_result("nox", _LABEL, f"NoxConsole 查询失败：{error}")

    entries = _parse_entries(output)
    _attach_adb_ports(entries)
    devices = [device for device, _vm_pid in entries]
    devices = port_scan.supplement_when_empty("nox", _LABEL, devices)
    logger.info("探测到 %d 个实例（含端口扫描补充）", len(devices))
    return devices, ""
