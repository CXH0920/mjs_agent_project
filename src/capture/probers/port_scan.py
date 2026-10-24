"""端口段兜底扫描（多品牌连接支持）。

实例发现两级策略的二级：console 工具不可用/失败时，只做 127.0.0.1
端口段 socket 试连；结果按所选品牌标注，不猜品牌（蓝叠与雷电默认端口
同为 5555，凭端口猜品牌必然误标）。
"""

from __future__ import annotations

import logging
import socket

from src.capture.probers.models import EmulatorDeviceInfo

logger = logging.getLogger(__name__)

# 各品牌公开文档的默认 adb 端口段：(基准端口, 步长, 实例数上限)。
# 夜神实测为 62001+24n（真机 Nox 7 校准，2026-10-24）。
# 蓝叠 adb 端口每实例动态分配（注册表 BstAdbPort），无固定端口段可扫描。
PORT_RULES: dict[str, tuple[int, int, int] | None] = {
    "mumu": (16384, 32, 16),
    "ldplayer": (5555, 2, 16),
    "nox": (62001, 24, 16),
    "memu": (21503, 10, 16),
    "bluestacks": None,
}


def candidate_ports(emulator_type: str) -> list[int]:
    """按品牌端口规则生成候选端口列表；无规则品牌返回空。"""
    rule = PORT_RULES.get(emulator_type)
    if not rule:
        return []
    base, step, count = rule
    return [base + step * n for n in range(count)]


def scan_open_ports(ports: list[int], timeout: float = 0.2) -> list[int]:
    """对 127.0.0.1 候选端口做 socket 试连，返回开放端口。"""
    open_ports: list[int] = []
    for port in ports:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout):
                open_ports.append(port)
        except OSError:
            continue
    return open_ports


def probe_devices_via_ports(emulator_type: str, label: str) -> list[EmulatorDeviceInfo]:
    """把端口段内开放端口组装为候选实例（序号 1 起，名称标注端口扫描来源）。"""
    devices = [
        EmulatorDeviceInfo(
            index=str(ordinal),
            name=f"{label} 实例 {ordinal}（端口扫描）",
            adb_port=port,
            is_running=True,
        )
        for ordinal, port in enumerate(scan_open_ports(candidate_ports(emulator_type)), start=1)
    ]
    if devices:
        logger.info("端口段扫描发现 %d 个 %s 候选实例", len(devices), label)
    return devices


def supplement_when_empty(emulator_type: str, label: str, devices: list[EmulatorDeviceInfo]) -> list[EmulatorDeviceInfo]:
    """console 枚举成功但零实例时的补充扫描（逍遥真机实测 2026-10-24）。

    部分 console 在特定版本下 list 返回成功但输出为空（解析为零实例），
    之前会静默报"未探测到设备"；此处品牌端口带内确有开放端口时以扫描
    结果补充。console 已给出实例时不重复扫描，带内无开放端口时维持原空结果。
    """
    if devices:
        return devices
    scanned = probe_devices_via_ports(emulator_type, label)
    if scanned:
        logger.warning("%s console 枚举为空但端口带内有开放端口，已以端口扫描结果补充", label)
        return scanned
    return devices
