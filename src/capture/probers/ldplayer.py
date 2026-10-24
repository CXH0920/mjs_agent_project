"""雷电 LDPlayer 9 实例探测（多品牌连接支持）。

注册表契约：probe_devices(retries) / find_adb()。ldconsole list2 不上报
adb 端口，按官方默认端口段 5555+2n 推导（用户自定义端口时以连接校验
为准）；console 不可用/失败时回退端口段扫描（port_scan.py）。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import src.capture.probers.port_scan as port_scan
from src.capture.probers.console_utils import find_process_dir, run_console_tool
from src.capture.probers.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)

_LABEL = "雷电 9"
_ADB_PORT_BASE = 5555
_ADB_PORT_STEP = 2


def _probe_registry() -> str | None:
    """通过 Windows 注册表查找雷电安装路径。"""
    try:
        import winreg
        for key_path in [
            r"SOFTWARE\leidian\ldrun",
            r"SOFTWARE\WOW6432Node\leidian\ldrun",
            r"SOFTWARE\leidian",
            r"SOFTWARE\WOW6432Node\leidian",
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
    """收集所有可能的雷电安装路径候选。"""
    candidates: list[Path] = []

    home = os.environ.get("LDPLAYER_HOME")
    if home:
        candidates.append(Path(home))

    # 运行进程反查安装根（自定义安装路径无注册表时的兜底）
    process_dir = find_process_dir(["dnplayer.exe", "Ld9Box.exe"])
    if process_dir is not None and process_dir not in candidates:
        candidates.append(process_dir)

    reg_path = _probe_registry()
    if reg_path:
        p = Path(reg_path)
        if p not in candidates:
            candidates.append(p)

    for cp in [
        "C:/leidian/LDPlayer9",
        "D:/leidian/LDPlayer9",
        "E:/leidian/LDPlayer9",
        "C:/Program Files/leidian/LDPlayer9",
        "C:/Program Files (x86)/leidian/LDPlayer9",
        "D:/Program Files/leidian/LDPlayer9",
    ]:
        p = Path(cp)
        if p not in candidates:
            candidates.append(p)
    return candidates


def _find_console() -> Path | None:
    """查找 ldconsole.exe（LDPlayer 9 主名；dnconsole 为旧版名兜底）。"""
    which = shutil.which("ldconsole")
    if which:
        return Path(which)
    for root in _get_candidates():
        for name in ("ldconsole.exe", "dnconsole.exe"):
            p = root / name
            if p.exists():
                return p
    return None


def find_adb() -> str:
    """探测雷电安装目录自带的 adb.exe（PATH 分支在包统一入口 probe_adb）。"""
    for root in _get_candidates():
        p = root / "adb.exe"
        if p.exists():
            return str(p.resolve())
    return ""


def _parse_console_output(text: str) -> list[EmulatorDeviceInfo]:
    """解析 ldconsole list2 输出：index,name,top_handle,bind_handle,is_running,pid,vbox_pid。"""
    devices: list[EmulatorDeviceInfo] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.replace("\t", ",").split(",")]
        if len(fields) < 5 or not fields[0].isdigit():
            continue
        index = int(fields[0])
        devices.append(EmulatorDeviceInfo(
            index=str(index),
            name=fields[1] or f"{_LABEL} 实例 {index}",
            adb_port=_ADB_PORT_BASE + _ADB_PORT_STEP * index,
            is_running=fields[4] == "1",
        ))
    return devices


def probe_devices(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所有雷电实例；console 失败时回退端口段扫描。"""
    console = _find_console()
    if console is None:
        return _fallback("未找到雷电 console 工具（ldconsole）")

    output, error = run_console_tool([str(console), "list2"])
    if output is None:
        return _fallback(f"ldconsole 查询失败：{error}")

    devices = _parse_console_output(output)
    devices = port_scan.supplement_when_empty("ldplayer", _LABEL, devices)
    logger.info("探测到 %d 个实例（含端口扫描补充）", len(devices))
    return devices, ""


def _fallback(error: str) -> tuple[list[EmulatorDeviceInfo], str]:
    """console 路径失败后的端口段兜底；兜底有产出视为成功刷新（降级记日志）。"""
    devices = port_scan.probe_devices_via_ports("ldplayer", _LABEL)
    if devices:
        logger.warning("%s，已回退端口段扫描发现 %d 个候选实例", error, len(devices))
        return devices, ""
    return [], f"{error}（端口段扫描也未发现实例）"
