"""逍遥 MEmu 实例探测（多品牌连接支持）。

注册表契约：probe_devices(retries) / find_adb()。实例真实 ADB 端口直读
`MEmuHyperv VMs\\<实例名>\\<实例名>.memu` 配置（Forwarding name="ADB" 的
hostport，2026-10-25 真机 9.5.8.0 实证）；MEmuConsole 为 GUI 程序无
stdout（PE 子系统 2，真机取证 list 挂起不退出），console 调用属永远
超时的死路径，已移除（原实现每次探测固定烧 10 秒超时后走端口扫描）。
配置定位失败时回退端口段扫描（port_scan.py）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from xml.etree import ElementTree as ET

import src.capture.probers.port_scan as port_scan
from src.capture.probers.console_utils import find_process_dir
from src.capture.probers.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)

_LABEL = "逍遥"
_VMS_DIRNAME = "MEmuHyperv VMs"


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

    # 运行进程反查安装根（自定义安装路径无注册表时的兜底）
    process_dir = find_process_dir(["MEmu.exe"])
    if process_dir is not None and process_dir not in candidates:
        candidates.append(process_dir)

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


def find_adb() -> str:
    """探测逍遥安装目录自带的 adb.exe（PATH 分支在包统一入口 probe_adb）。"""
    for root in _get_candidates():
        p = root / "adb.exe"
        if p.exists():
            return str(p.resolve())
    return ""


def _find_vms_dirs() -> list[Path]:
    """收集已存在的 MEmuHyperv VMs 目录（实例 .memu 配置所在）。

    廉价候选（MEMU_HOME/注册表/常见路径，无子进程开销）先行短路；全部
    落空才做进程反查，避免模拟器未运行时白付 wmic/PowerShell 查询。反查
    含 MEmuHeadless（VM 运行而 GUI 关闭时唯一存活的进程，位于安装根同级
    的 MEmuHyperv 目录，映射回安装根）。
    """
    roots: list[Path] = []
    home = os.environ.get("MEMU_HOME")
    if home:
        roots.append(Path(home))
    reg_path = _probe_registry()
    if reg_path:
        roots.append(Path(reg_path))
    for cp in [
        "C:/Program Files/Microvirt/MEmu",
        "C:/Program Files (x86)/Microvirt/MEmu",
        "D:/Program Files/Microvirt/MEmu",
        "D:/Microvirt/MEmu",
    ]:
        roots.append(Path(cp))
    dirs = [root / _VMS_DIRNAME for root in roots if (root / _VMS_DIRNAME).is_dir()]
    if dirs:
        return dirs
    process_dir = find_process_dir(["MEmu.exe", "MEmuHeadless.exe", "MEmuConsole.exe"])
    if process_dir is None:
        return []
    root = process_dir.parent / "MEmu" if process_dir.name == "MEmuHyperv" else process_dir
    vms = root / _VMS_DIRNAME
    return [vms] if vms.is_dir() else []


def _parse_adb_hostport(config: Path) -> int:
    """读取单个 .memu 配置中 ADB 转发的 hostport；读不到/异常返回 0。

    标签按本地名匹配（真实配置带 xmlns 命名空间，真机 9.5.8.0 实证
    root 为 {http://www.memuhyperv.org/}MemuHyperv）。
    """
    try:
        tree = ET.parse(config)
    except (ET.ParseError, OSError) as exc:
        logger.debug("逍遥实例配置解析失败 %s：%s", config, exc)
        return 0
    for element in tree.getroot().iter():
        if element.tag.rsplit("}", 1)[-1] != "Forwarding" or element.get("name") != "ADB":
            continue
        try:
            return int(element.get("hostport", ""))
        except ValueError:
            return 0
    return 0


def _read_adb_port(instance_dir: Path) -> int:
    """读取实例的 ADB 端口：优先与目录同名的主配置，再扫其余 .memu。

    done.memu 等衍生文件无 ADB 转发，天然被跳过。
    """
    primary = instance_dir / f"{instance_dir.name}.memu"
    others = [p for p in sorted(instance_dir.glob("*.memu")) if p != primary]
    for config in [primary, *others]:
        port = _parse_adb_hostport(config)
        if port:
            return port
    return 0


def _list_config_instances(vms_dirs: list[Path]) -> list[EmulatorDeviceInfo]:
    """解析各 VMs 目录下的实例目录，组装配真实端口与运行态的设备列表。"""
    devices: list[EmulatorDeviceInfo] = []
    for vms_dir in vms_dirs:
        for instance_dir in sorted(p for p in vms_dir.iterdir() if p.is_dir()):
            port = _read_adb_port(instance_dir)
            if not port:
                continue
            devices.append(EmulatorDeviceInfo(
                index=str(len(devices)),
                name=instance_dir.name,
                adb_port=port,
                is_running=bool(port_scan.scan_open_ports([port])),
            ))
    return devices


def probe_devices(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所有逍遥实例：直读 .memu 配置拿真实端口；定位失败回退端口段扫描。"""
    vms_dirs = _find_vms_dirs()
    if vms_dirs:
        devices = _list_config_instances(vms_dirs)
        if devices:
            logger.info("探测到 %d 个逍遥实例（.memu 配置直读）", len(devices))
            return devices, ""
        return [], '逍遥配置中未读到实例 ADB 端口（MEmuHyperv VMs 下 .memu 的 ADB 转发）'
    return port_scan.fallback_result("memu", _LABEL, "未找到逍遥实例配置目录（MEmuHyperv VMs）")
