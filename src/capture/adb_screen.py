"""
ADB 连接与截图模块

通过 ADB 连接 MuMu 模拟器，执行 screencap 命令获取屏幕截图。
"""

from __future__ import annotations

import io
import logging
import subprocess
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

_ADB_TIMEOUT = 15


class AdbCapture:
    """封装 ADB 连接与截图操作，支持多设备。"""

    def __init__(self, adb_path: str, adb_port: int = 7555) -> None:
        """
        Args:
            adb_path: adb.exe 的完整路径。
            adb_port: MuMu 模拟器的 ADB 端口。
        """
        self._adb_path = adb_path
        self._adb_port = adb_port
        self._device_serial: str = ""
        self._connected = False

    # ── 设备序列号 ─────────────────────────────────────────────────────

    @property
    def device_serial(self) -> str:
        """当前连接的设备序列号，如 127.0.0.1:16448。"""
        return self._device_serial

    @device_serial.setter
    def device_serial(self, serial: str) -> None:
        """切换目标设备（连接前设置）。"""
        self._device_serial = serial
        if ":" in serial:
            parts = serial.split(":")
            if len(parts) == 2 and parts[1].isdigit():
                self._adb_port = int(parts[1])

    # ── 连接管理 ──────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> tuple[bool, str]:
        """连接到 MuMu 模拟器的 ADB。

        Returns:
            (是否成功, 消息)
        """
        if self._connected:
            return True, "已处于连接状态"

        ok, msg = self._check_adb_valid()
        if not ok:
            return False, msg

        target = self._device_serial or f"127.0.0.1:{self._adb_port}"

        ok, msg = self._run_adb("connect", target)
        if not ok:
            logger.error("ADB 连接失败: %s", msg)
            return False, f"ADB 连接失败: {msg}"

        # 验证设备列表
        dev_ok, dev_msg = self._get_devices()
        if not dev_ok:
            self._disconnect_safe()
            logger.error("未检测到 ADB 设备: %s", dev_msg)
            return False, f"未检测到设备: {dev_msg}"

        self._connected = True
        self._device_serial = dev_msg
        logger.info("ADB 连接成功 (设备 %s)", self._device_serial)
        return True, f"连接成功 (设备: {dev_msg})"

    def disconnect(self) -> tuple[bool, str]:
        """断开 ADB 连接。"""
        self._disconnect_safe()
        return True, "已断开连接"

    def _disconnect_safe(self) -> None:
        """静默断开连接。"""
        target = self._device_serial or f"127.0.0.1:{self._adb_port}"
        self._connected = False
        self._device_serial = ""
        try:
            self._run_adb("disconnect", target, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("断开 ADB 连接异常（可忽略）: %s", e)

    def reconnect(self) -> tuple[bool, str]:
        """强制重连。"""
        self._disconnect_safe()
        return self.connect()

    # ── 设备检测 ──────────────────────────────────────────────────────

    def check_device(self) -> tuple[bool, str]:
        """检查设备是否在线。"""
        return self._get_devices()

    # ── 截图 ──────────────────────────────────────────────────────────

    def screencap_full(self) -> tuple[bool, Image.Image | str]:
        """截取模拟器全屏，返回 PIL Image。

        Returns:
            (是否成功, Image 对象或错误消息)
        """
        if not self._connected or not self._device_serial:
            return False, "尚未连接，请先连接模拟器"

        try:
            result = subprocess.run(
                [self._adb_path, "-s", self._device_serial, "exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=_ADB_TIMEOUT,
            )
        except FileNotFoundError:
            return False, f"找不到 adb: {self._adb_path}"
        except subprocess.TimeoutExpired:
            logger.error("截图命令执行超时")
            return False, "截图命令执行超时"
        except OSError as e:
            logger.error("截图命令执行异常: %s", e)
            return False, f"截图命令执行异常: {e}"

        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            logger.error("screencap 失败 (returncode=%d): %s", result.returncode, err)
            return False, f"screencap 失败: {err}"

        if not result.stdout:
            logger.error("截图返回空数据")
            return False, "截图返回空数据"

        try:
            image = Image.open(io.BytesIO(result.stdout))
            image.load()
            logger.info("截图成功: %s x %s", image.width, image.height)
            return True, image
        except Exception as e:
            logger.error("解析截图图像失败: %s", e)
            return False, f"解析截图图像失败: {e}"

    @staticmethod
    def list_connected_devices(adb_path: str) -> list[str]:
        """查询当前 ADB 已连接的所有设备列表。

        Returns:
            设备序列号列表。
        """
        try:
            result = subprocess.run(
                [adb_path, "devices"],
                capture_output=True,
                timeout=10,
            )
            output = result.stdout.decode("utf-8", errors="replace").strip()
            lines = [l.strip() for l in output.split("\n") if l.strip()]
            devices = []
            for line in lines:
                if line.startswith("List") or "offline" in line:
                    continue
                if "\tdevice" in line:
                    serial = line.split("\t")[0]
                    devices.append(serial)
            return devices
        except Exception as e:
            logger.warning("获取设备列表异常: %s", e)
            return []

    # ── 内部方法 ──────────────────────────────────────────────────────

    def _check_adb_valid(self) -> tuple[bool, str]:
        """检查 ADB 可执行文件是否有效。"""
        p = Path(self._adb_path)
        if not p.exists():
            return False, f"ADB 文件不存在: {self._adb_path}"
        if not p.is_file():
            return False, f"ADB 路径不是文件: {self._adb_path}"
        return True, ""

    def _check_device_serial_safe(self, serial: str) -> str | None:
        """设备序列号合法性校验。"""
        serial = serial.strip()
        if not serial:
            return None
        if ":" in serial:
            parts = serial.split(":")
            if len(parts) != 2 or not parts[1].isdigit():
                logger.warning("设备序列号格式异常: %s", serial)
                return None
            port = int(parts[1])
            if port < 1 or port > 65535:
                logger.warning("设备端口号超出范围: %s", serial)
                return None
        return serial

    def _run_adb(self, *args: str, timeout: int = 10) -> tuple[bool, str]:
        """执行单条 ADB 命令。

        Args:
            *args: ADB 子命令参数。
            timeout: 超时秒数。

        Returns:
            (是否成功, 输出消息)
        """
        try:
            result = subprocess.run(
                [self._adb_path, *args],
                capture_output=True,
                timeout=timeout,
            )
            output = result.stdout.decode("utf-8", errors="replace").strip()
            error = result.stderr.decode("utf-8", errors="replace").strip()
            if result.returncode != 0:
                return False, error or output or f"returncode={result.returncode}"
            return True, output
        except FileNotFoundError:
            return False, f"找不到 adb: {self._adb_path}"
        except subprocess.TimeoutExpired:
            return False, "命令执行超时"
        except OSError as e:
            return False, f"命令执行异常: {e}"

    def _get_devices(self) -> tuple[bool, str]:
        """获取连接的设备列表。"""
        ok, msg = self._run_adb("devices")
        if not ok:
            return False, msg
        lines = [l.strip() for l in msg.split("\n") if l.strip()]
        devices = [l for l in lines if l and not l.startswith("List") and "device" in l]
        if not devices:
            return False, "没有检测到设备"
        raw_serial = devices[0].split("\t")[0] if "\t" in devices[0] else devices[0]
        safe_serial = self._check_device_serial_safe(raw_serial) or raw_serial
        return True, safe_serial

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return f"AdbCapture(path={self._adb_path}, port={self._adb_port}, {status})"
