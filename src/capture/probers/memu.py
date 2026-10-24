"""逍遥 MEmu 实例探测（多品牌连接支持）。

注册表契约：probe_devices(retries) / find_adb()。MEmuConsole/MEmuManager
list 不上报 adb 端口，按官方默认端口段 21503+10n 推导（用户自定义端口时
以连接校验为准）；console 不可用/失败时回退端口段扫描（port_scan.py）。
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

import src.capture.probers.port_scan as port_scan
from src.capture.probers.console_utils import run_console_tool
from src.capture.probers.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)

_LABEL = "逍遥"
_ADB_PORT_BASE = 21503
_ADB_PORT_STEP = 10


def _probe_registry() -> str | None:
    """通过 Windows 注册表查找逍遥安装路径。"""
    try:
        import winreg
        for key_path in [
            r"SOFTWARE\Microvirt\MEmu",
            r"SOFTWARE\WOW6432Node\Microvirt\MEmu",
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
    """收集所有可能的逍遥安装路径候选。"""
    candidates: list[Path] = []

    home = os.environ.get("MEMU_HOME")
    if home:
        candidates.append(Path(home))

    reg_path = _probe_registry()
    if reg_path:
        p = Path(reg_path)
        if p not in candidates:
            candidates.append(p)

    for cp in [
        "C:/Program Files/Microvirt/MEmu",
        "C:/Program Files (x86)/Microvirt/MEmu",
        "D:/Program Files/Microvirt/MEmu",
        "D:/Microvirt/MEmu",
    ]:
        p = Path(cp)
        if p not in candidates:
            candidates.append(p)
    return candidates


def _find_console() -> Path | None:
    """查找逍遥管理工具（MEmuConsole 主名；MEmuManager 为备用名）。"""
    for name in ("MEmuConsole", "MEmuManager"):
        which = shutil.which(name)
        if which:
            return Path(which)
    for root in _get_candidates():
        for name in ("MEmuConsole.exe", "MEmuManager.exe"):
            p = root / name
            if p.exists():
                return p
    return None


def find_adb() -> str:
    """探测逍遥安装目录自带的 adb.exe（PATH 分支在包统一入口 probe_adb）。"""
    for root in _get_candidates():
        p = root / "adb.exe"
        if p.exists():
            return str(p.resolve())
    return ""


def _parse_console_output(text: str) -> list[EmulatorDeviceInfo]:
    """解析逍遥管理工具 list 输出：index,name,top_handle,bind_handle,is_running,pid。

    首列为实例名的机型也按行序回退编号（真机样本入 fixtures_local 后校准）。
    """
    devices: list[EmulatorDeviceInfo] = []
    for ordinal, line in enumerate(text.splitlines()):
        fields = [field.strip() for field in line.replace("\t", ",").split(",")]
        if len(fields) < 5 or not fields[4] in ("0", "1"):
            continue
        index = int(fields[0]) if fields[0].isdigit() else ordinal
        devices.append(EmulatorDeviceInfo(
            index=str(index),
            name=fields[1] or f"{_LABEL} 实例 {index}",
            adb_port=_ADB_PORT_BASE + _ADB_PORT_STEP * index,
            is_running=fields[4] == "1",
        ))
    return devices


def probe_devices(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所有逍遥实例；console 失败时回退端口段扫描。"""
    console = _find_console()
    if console is None:
        return _fallback("未找到逍遥 console 工具（MEmuConsole/MEmuManager）")

    output, error = run_console_tool([str(console), "list"])
    if output is None:
        return _fallback(f"逍遥 console 查询失败：{error}")

    devices = _parse_console_output(output)
    logger.info("探测到 %d 个逍遥实例", len(devices))
    return devices, ""


def _fallback(error: str) -> tuple[list[EmulatorDeviceInfo], str]:
    """console 路径失败后的端口段兜底；兜底有产出视为成功刷新（降级记日志）。"""
    devices = port_scan.probe_devices_via_ports("memu", _LABEL)
    if devices:
        logger.warning("%s，已回退端口段扫描发现 %d 个候选实例", error, len(devices))
        return devices, ""
    return [], f"{error}（端口段扫描也未发现实例）"
