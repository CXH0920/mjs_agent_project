"""品牌 console 工具调用与输出解码的共享工具（多品牌连接支持）。

各品牌管理工具（ldconsole/NoxConsole）输出编码随系统代码页变化（中文
Windows 为 GBK），统一先 UTF-8 后 GBK 解码；超时统一 10 秒。MuMuManager
输出 JSON 且自带重试语义，不走本模块（见 mumu.py）。find_process_dir
进程反查为 tasklist 门控 + PowerShell 合并查询两级实现（2026-10-25）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

CONSOLE_TIMEOUT_S = 10


def decode_console_bytes(raw: bytes) -> str:
    """按 UTF-8 → GBK 顺序解码 console 输出，均失败则容错替换。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def run_console_tool(args: list[str], timeout: int = CONSOLE_TIMEOUT_S) -> tuple[str | None, str]:
    """运行品牌 console 工具，返回 (解码 stdout | None, 错误消息)。"""
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "命令执行超时"
    except OSError as exc:
        return None, f"命令执行异常：{exc}"
    if result.returncode != 0:
        return None, f"退出码 {result.returncode}"
    return decode_console_bytes(result.stdout), ""


def find_process_dir(process_names: list[str]) -> Path | None:
    """通过运行中的模拟器进程反查安装目录（自定义/便携安装无注册表时的兜底）。

    返回进程可执行文件所在目录（即各品牌探测候选的基准目录）；进程未运行
    或查询失败返回 None。两级实现（2026-10-25 重构，替代逐名 wmic+PowerShell
    双查——冷启动 ~1s/次，未运行时最坏白付 ~3.7s）：
    1. tasklist 全量进程名门控（~0.25s，全系可用、无冷启动问题，替代已退役
       的 wmic）：请求名全不在场直接返回 None；
    2. 在场名一次 PowerShell 合并查询可执行路径（单次冷启动），按请求顺序
       取首个存在的 exe 目录（保持原逐名优先级语义）。
    """
    running = _running_process_names()
    if running and not any(name.lower() in running for name in process_names):
        return None
    return _process_exe_dir_via_powershell(process_names)


def _running_process_names() -> set[str]:
    """tasklist 全量进程名集合（小写）；查询失败返回空集（调用方回退直查）。"""
    try:
        result = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    if result.returncode != 0:
        return set()
    names: set[str] = set()
    for line in decode_console_bytes(result.stdout).splitlines():
        first = line.split('","', 1)[0].strip('"').strip()
        if first:
            names.add(first.lower())
    return names


def _process_exe_dir_via_powershell(process_names: list[str]) -> Path | None:
    """一次 PowerShell 合并查询在场进程的可执行路径，返回首个存在文件的目录。

    输出行 "进程名|路径"（ProcessName 不带 .exe）；系统进程路径可能为空，
    跳过即可。
    """
    base_names = ",".join(name.rsplit(".", 1)[0] for name in process_names)
    args = ["powershell", "-NoProfile", "-Command",
            f"Get-Process -Name {base_names} -ErrorAction SilentlyContinue "
            f"| ForEach-Object {{ \"$($_.ProcessName)|$($_.Path)\" }}"]
    try:
        result = subprocess.run(args, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    paths_by_name = {
        base.lower(): line.split("|", 1)[1].strip().strip('"')
        for line in decode_console_bytes(result.stdout).splitlines()
        if "|" in line
        for base in [line.split("|", 1)[0].strip()]
    }
    for name in process_names:
        candidate = paths_by_name.get(name.rsplit(".", 1)[0].lower(), "")
        if candidate.lower().endswith(".exe"):
            path = Path(candidate)
            if path.exists():
                return path.parent
    return None
