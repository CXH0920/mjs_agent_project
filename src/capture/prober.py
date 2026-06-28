"""
MuMu 模拟器实例探测模块

负责自动探测 MuMu 模拟器的安装路径、adb.exe 位置和运行中的实例端口。
函数式设计，无内部状态。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class MuMuDeviceInfo:
    """描述一个 MuMu 模拟器实例的信息。"""

    index: str          # MuMuManager 中的索引
    name: str           # 实例名称
    adb_port: int       # ADB 端口
    is_running: bool    # 是否正在运行
    is_main: bool = False


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

    # 2. Windows 注册表
    reg_path = _probe_mumu_registry()
    if reg_path:
        p = Path(reg_path)
        if p not in candidates:
            candidates.append(p)

    # 3. 常见安装路径
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


def _get_legacy_candidates() -> list[Path]:
    """兼容旧版的候选路径列表。"""
    return [
        Path("D:/模拟器/MuMu Player 12"),
        Path("D:/模拟器/MuMu 12"),
        Path("C:/Program Files/Netease/MuMu Player 12"),
        Path("C:/Program Files (x86)/Netease/MuMu Player 12"),
        Path("C:/Program Files/Netease/MuMuPlayer-12.0"),
        Path("C:/Program Files (x86)/Netease/MuMuPlayer-12.0"),
        Path("D:/Program Files/Netease/MuMu Player 12"),
        Path("D:/Program Files (x86)/Netease/MuMu Player 12"),
    ]


def _find_mumu_root() -> Path | None:
    """查找 MuMu 模拟器安装根目录。"""
    for root in _get_mumu_candidates():
        if root.exists() and (root / "nx_main").exists():
            return root
    for root in _get_legacy_candidates():
        if root.exists() and (root / "nx_main").exists():
            return root
    return None


def probe_mumu_adb() -> str:
    """自动探测 MuMu 模拟器的 adb.exe 路径。

    查找顺序：
      1. 系统 PATH 环境变量中的 adb
      2. MuMu 安装目录（注册表/环境变量/常见路径）下的 adb
      3. 旧版候选路径

    Returns:
        adb.exe 完整路径，找不到则返回空字符串。
    """
    candidates = _get_mumu_candidates()

    # 1. 先查 PATH 中的 adb
    import shutil
    path_adb = shutil.which("adb")
    if path_adb:
        return path_adb

    # 2. 再查 MuMu 安装目录下的 adb
    for root in candidates:
        for subpath in ["nx_main/adb.exe", "emulator/nemu/EmulatorShell/adb.exe"]:
            p = root / subpath
            if p.exists():
                return str(p.resolve())

    # 3. 最后查旧的候选路径（兼容）
    for root in _get_legacy_candidates():
        for subpath in ["nx_main/adb.exe", "emulator/nemu/EmulatorShell/adb.exe"]:
            p = root / subpath
            if p.exists():
                return str(p.resolve())

    logger.warning("未找到 MuMu 模拟器的 adb.exe，请在配置文件中手动指定 adb_path")
    return ""


def probe_all_devices() -> list[MuMuDeviceInfo]:
    """探测所有 MuMu 模拟器实例，返回列表。"""
    mumu_root = _find_mumu_root()
    if not mumu_root:
        logger.warning("未找到 MuMu 模拟器安装目录")
        return []

    manager_path = mumu_root / "nx_main" / "MuMuManager.exe"
    if not manager_path.exists():
        logger.warning("MuMuManager.exe 不存在")
        return []

    try:
        result = subprocess.run(
            [str(manager_path), "info", "--vmindex", "all"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            logger.warning("MuMuManager info 失败 (returncode=%d)", result.returncode)
            return []
        output = result.stdout.decode("utf-8", errors="replace")
        data = json.loads(output)
        devices: list[MuMuDeviceInfo] = []
        for index_str, vm_info in data.items():
            is_running = bool(vm_info.get("is_android_started"))
            port = int(vm_info["adb_port"]) if vm_info.get("adb_port") else 0
            devices.append(MuMuDeviceInfo(
                index=index_str,
                name=vm_info.get("name", f"实例 {index_str}"),
                adb_port=port,
                is_running=is_running,
                is_main=bool(vm_info.get("is_main")),
            ))
        logger.info("探测到 %d 个 MuMu 实例", len(devices))
        return devices
    except (json.JSONDecodeError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug("探测 MuMu 实例失败: %s", e)
        return []


def probe_mumu_port() -> int:
    """通过 MuMuManager 自动探测正在运行的模拟器实例的 ADB 端口。"""
    devices = probe_all_devices()
    for d in devices:
        if d.is_running and d.adb_port:
            logger.info("自动探测到运行中的模拟器实例 '%s' (端口: %s)", d.name, d.adb_port)
            return d.adb_port
    logger.info("未发现正在运行的 MuMu 模拟器实例")
    return 0


def test_adb_path(adb_path: str) -> tuple[bool, str]:
    """快速测试 ADB 路径是否可执行。"""
    p = Path(adb_path)
    if not p.exists():
        return False, f"文件不存在: {adb_path}"
    if not p.is_file():
        return False, f"不是文件: {adb_path}"
    try:
        result = subprocess.run(
            [str(p), "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            version_line = result.stdout.strip().split("\n")[0]
            return True, version_line
        return False, f"adb 执行失败 (returncode={result.returncode}): {result.stderr.strip()}"
    except FileNotFoundError:
        return False, f"找不到 adb: {adb_path}"
    except subprocess.TimeoutExpired:
        return False, "adb 版本查询超时"
    except OSError as e:
        return False, f"adb 执行异常: {e}"
