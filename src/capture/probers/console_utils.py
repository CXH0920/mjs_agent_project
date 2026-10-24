"""品牌 console 工具调用与输出解码的共享工具（多品牌连接支持）。

各品牌管理工具（ldconsole/NoxConsole/MEmuConsole）输出编码随系统代码页
变化（中文 Windows 为 GBK），统一先 UTF-8 后 GBK 解码；超时统一 10 秒。
MuMuManager 输出 JSON 且自带重试语义，不走本模块（见 mumu.py）。
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
    或查询失败返回 None。wmic 在部分新系统已退役，PowerShell 兜底。
    """
    for name in process_names:
        base_name = name.rsplit(".", 1)[0]
        queries = (
            ["wmic", "process", "where", f"name='{name}'", "get", "ExecutablePath"],
            ["powershell", "-NoProfile", "-Command",
             f"(Get-Process -Name {base_name} -ErrorAction SilentlyContinue "
             f"| Select-Object -First 1).Path"],
        )
        for args in queries:
            try:
                result = subprocess.run(args, capture_output=True, timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                continue
            if result.returncode != 0:
                continue
            output = result.stdout
            if b"\x00" in output:  # wmic 常输出 UTF-16LE
                output = output.decode("utf-16-le", errors="ignore").encode("utf-8", errors="ignore")
            for line in decode_console_bytes(output).splitlines():
                candidate = line.strip().strip('"')
                if candidate.lower().endswith(".exe"):
                    path = Path(candidate)
                    if path.exists():
                        return path.parent
    return None
