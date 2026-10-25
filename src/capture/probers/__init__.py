"""模拟器品牌探测统一入口（prober.py 拆包，多品牌连接支持）。

函数式注册表：每品牌子模块实现统一签名——probe_devices(retries) 与
find_adb()——本入口按 emulator_type 分发；不引入 Protocol，签名约束由
注册表注释与单测锚定。adb 来源顺序统一为「PATH 的 adb 优先、品牌自带
adb 兜底」（旧版 prober 既有决策，多品牌推广）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

from src.capture.probers import bluestacks, ldplayer, memu, mumu, nox
from src.capture.probers.models import EmulatorDeviceInfo as EmulatorDeviceInfo

logger = logging.getLogger(__name__)

# 品牌标识（config.env EMULATOR_TYPE 取值域）与展示名（UI 下拉用，不带版本号）
EMULATOR_TYPES: tuple[str, ...] = ("mumu", "ldplayer", "nox", "memu", "bluestacks")
EMULATOR_LABELS: dict[str, str] = {
    "mumu": "MuMu",
    "ldplayer": "雷电",
    "nox": "夜神",
    "memu": "逍遥",
    "bluestacks": "蓝叠",
}
# 品牌特性提示：设备与连接页品牌行 ⓘ 悬停展示，随所选品牌联动
#（2026-10-25 四品牌真机实测 + 官方资料定稿，见
# docs/design/emulator_multi_brand_requirements.md 第五节）
EMULATOR_HINTS: dict[str, str] = {
    "mumu": "默认开启 ADB；端口默认 16384（多开 +32 递增），可在模拟器右上角菜单 → 问题诊断 中查看。",
    "ldplayer": "推荐使用稳定版雷电 9；默认端口 5555（多开 +2 递增）。",
    "nox": "默认开启 ADB；端口默认 62001，多开为 62025、62026…依次递增，可在夜神多开器中查看。",
    "memu": "默认开启 ADB；端口默认 21503（多开 +10 递增），可在逍遥多开器中查看。",
    "bluestacks": "使用前需在蓝叠 设置 → 高级 中开启 ADB（Android 调试桥）。",
}
DEFAULT_EMULATOR_TYPE = "mumu"

# 品牌注册表：模块需实现 probe_devices(retries=1) -> tuple[list[EmulatorDeviceInfo], str]
# 与 find_adb() -> str（品牌自带 adb 路径，不含 PATH 分支）
EMULATOR_PROBERS: dict[str, ModuleType] = {
    "mumu": mumu,
    "ldplayer": ldplayer,
    "nox": nox,
    "memu": memu,
    "bluestacks": bluestacks,
}


def normalize_emulator_type(value: object) -> str:
    """未知/空品牌值回退默认品牌，杜绝 KeyError 扩散到配置与 UI。"""
    return value if isinstance(value, str) and value in EMULATOR_PROBERS else DEFAULT_EMULATOR_TYPE


def probe_adb(emulator_type: str = DEFAULT_EMULATOR_TYPE) -> str:
    """自动探测 adb.exe：PATH 优先，其次所选品牌安装目录自带 adb。

    Returns:
        adb.exe 完整路径，找不到则返回空字符串。
    """
    path_adb = shutil.which("adb")
    if path_adb:
        return path_adb
    return EMULATOR_PROBERS[normalize_emulator_type(emulator_type)].find_adb()


def probe_all_devices_with_status(
    emulator_type: str = DEFAULT_EMULATOR_TYPE,
    retries: int = 1,
) -> tuple[list[EmulatorDeviceInfo], str]:
    """探测所选品牌的全部实例，并区分空结果与探测失败。"""
    return EMULATOR_PROBERS[normalize_emulator_type(emulator_type)].probe_devices(retries=retries)


def probe_all_devices(emulator_type: str = DEFAULT_EMULATOR_TYPE) -> list[EmulatorDeviceInfo]:
    """探测所选品牌的全部实例，返回列表。"""
    devices, _ = probe_all_devices_with_status(emulator_type)
    return devices


def probe_running_devices(emulator_type: str = DEFAULT_EMULATOR_TYPE) -> list[EmulatorDeviceInfo]:
    """返回具备有效 ADB 端口的运行中实例。"""
    return [device for device in probe_all_devices(emulator_type)
            if device.is_running and device.adb_port > 0]


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
