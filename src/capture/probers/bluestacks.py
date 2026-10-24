"""蓝叠 BlueStacks 5 实例探测（多品牌连接支持）。

注册表契约：probe_devices(retries) / find_adb()。实例 adb 端口每实例
动态分配（注册表 Guests\\<实例>\\BstAdbPort），无固定端口段可扫描，
不设 port_scan 兜底；蓝叠 ADB 默认关闭，未读到端口时提示用户先开启。
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path

from src.capture.probers.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)

_PORT_PROBE_TIMEOUT_S = 0.2


def _find_root() -> Path | None:
    """通过 Windows 注册表查找蓝叠 5 安装根目录（BlueStacks_nxt）。"""
    try:
        import winreg
        for key_path in [
            r"SOFTWARE\BlueStacks_nxt",
            r"SOFTWARE\WOW6432Node\BlueStacks_nxt",
            r"SOFTWARE\BlueStacks_x",
        ]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                    if install_dir:
                        return Path(install_dir)
            except OSError:
                continue
    except ImportError:
        pass
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
    """枚举注册表 Guests 各实例的 BstAdbPort；端口可连通即视为运行中。"""
    devices: list[EmulatorDeviceInfo] = []
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\BlueStacks_nxt\Guests",
        ) as guests_key:
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
    except OSError as exc:
        logger.warning("蓝叠注册表枚举失败：%s", exc)
    return devices


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_PORT_PROBE_TIMEOUT_S):
            return True
    except OSError:
        return False


def probe_devices(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所有蓝叠实例；注册表读到端口即为发现，读不到则提示开启 ADB。"""
    devices = _list_registry_instances()
    if devices:
        logger.info("探测到 %d 个蓝叠实例", len(devices))
        return devices, ""
    if _find_root() is not None:
        return [], "未在注册表找到蓝叠 ADB 端口，请先在蓝叠 设置 → 高级 → Android 调试桥(ADB) 中开启后重试"
    return [], "未找到蓝叠安装目录（注册表 BlueStacks_nxt）"
