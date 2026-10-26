"""MuMu 12 模拟器实例探测（自 prober.py 平移，多品牌连接支持）。

负责自动探测 MuMu 安装路径、自带 adb.exe 位置和运行中的实例端口。
注册表契约（EMULATOR_PROBERS）：probe_devices(retries) 与 find_adb()。
console 主路径（MuMuManager）失败时回退默认端口段扫描（port_scan.py）。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

import src.capture.probers.port_scan as port_scan
from src.capture.probers.console_utils import find_process_dir
from src.capture.probers.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)


def _probe_mumu_registry() -> str | None:
    """通过 Windows 注册表查找 MuMu 12 安装路径。"""
    try:
        import winreg
        for key_path in [
            r"SOFTWARE\Netease\MuMuPlayer12",
            r"SOFTWARE\WOW6432Node\Netease\MuMuPlayer12",
        ]:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                    install_dir, _ = winreg.QueryValueEx(key, "InstallDir")
                    if install_dir:
                        return install_dir
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _get_mumu_candidates() -> list[Path]:
    """收集所有可能的 MuMu 安装路径候选。"""
    candidates: list[Path] = []

    # 1. 环境变量 MUMU_HOME
    mumu_home = os.environ.get("MUMU_HOME")
    if mumu_home:
        candidates.append(Path(mumu_home))

    # 2. 运行进程反查安装根（自定义安装路径无注册表时的兜底；MuMuPlayer.exe 在 nx_main 下）
    process_dir = find_process_dir(["MuMuPlayer.exe"])
    if process_dir is not None and process_dir.parent not in candidates:
        candidates.append(process_dir.parent)

    # 3. Windows 注册表
    reg_path = _probe_mumu_registry()
    if reg_path:
        p = Path(reg_path)
        if p not in candidates:
            candidates.append(p)

    # 4. 常见安装路径
    common_paths = [
        "D:/模拟器/MuMu Player 12",
        "D:/模拟器/MuMu 12",
        "C:/Program Files/Netease/MuMu Player 12",
        "C:/Program Files (x86)/Netease/MuMu Player 12",
        "C:/Program Files/Netease/MuMuPlayer-12.0",
        "C:/Program Files (x86)/Netease/MuMuPlayer-12.0",
        "D:/Program Files/Netease/MuMu Player 12",
        "D:/Program Files (x86)/Netease/MuMu Player 12",
    ]
    for cp in common_paths:
        p = Path(cp)
        if p not in candidates:
            candidates.append(p)

    return candidates


def _find_mumu_root() -> Path | None:
    """查找 MuMu 模拟器安装根目录。"""
    for root in _get_mumu_candidates():
        if root.exists() and (root / "nx_main").exists():
            return root
    return None


def find_adb() -> str:
    """探测 MuMu 安装目录自带的 adb.exe（PATH 分支在包统一入口 probe_adb）。

    查找顺序：MuMu 安装目录（环境变量/进程反查/注册表/常见路径）下的 adb。

    Returns:
        adb.exe 完整路径，找不到则返回空字符串。
    """
    candidates = _get_mumu_candidates()
    for root in candidates:
        for subpath in ["nx_main/adb.exe", "emulator/nemu/EmulatorShell/adb.exe"]:
            p = root / subpath
            if p.exists():
                return str(p.resolve())

    logger.warning("未找到 MuMu 模拟器的 adb.exe，请在配置文件中手动指定 adb_path")
    return ""


def probe_devices(retries: int = 1) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所有 MuMu 模拟器实例；console 失败时回退端口段扫描。"""
    mumu_root = _find_mumu_root()
    if not mumu_root:
        logger.warning("未找到 MuMu 模拟器安装目录")
        return port_scan.fallback_result("mumu", "MuMu", "未找到 MuMu 模拟器安装目录")

    manager_path = mumu_root / "nx_main" / "MuMuManager.exe"
    if not manager_path.exists():
        logger.warning("MuMuManager.exe 不存在")
        return port_scan.fallback_result("mumu", "MuMu", "MuMuManager.exe 不存在")

    error = ""
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                [str(manager_path), "info", "--vmindex", "all"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                error = f"MuMuManager 查询失败（退出码 {result.returncode}）"
            else:
                output = result.stdout.decode("utf-8", errors="replace")
                data = json.loads(output)
                devices: list[EmulatorDeviceInfo] = []
                for index_str, vm_info in data.items():
                    is_running = bool(vm_info.get("is_android_started"))
                    port = int(vm_info["adb_port"]) if vm_info.get("adb_port") else 0
                    devices.append(EmulatorDeviceInfo(
                        index=index_str,
                        name=vm_info.get("name", f"实例 {index_str}"),
                        adb_port=port,
                        is_running=is_running,
                        is_main=bool(vm_info.get("is_main")),
                    ))
                devices = port_scan.supplement_when_empty("mumu", "MuMu", devices)
                logger.info("探测到 %d 个 MuMu 实例（含端口扫描补充）", len(devices))
                return devices, ""
        except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as exc:
            error = f"MuMuManager 查询异常：{exc}"

        if attempt < retries:
            logger.warning("%s，正在重试", error)
            time.sleep(0.2)

    logger.warning("%s", error)
    return port_scan.fallback_result("mumu", "MuMu", error)
