"""
ADB 连接与截图模块：仅提供屏幕读取能力。

通过 ADB 连接 MuMu 模拟器，执行 screencap 命令获取屏幕截图。
本模块**故意不实现**以下功能，遵守项目法律红线（见 CLAUDE.md 与 AGENTS.md）：
- ADB 输入（tap、click、input）
- 自动点击、自动选将、自动战斗
- 游戏进程注入、内存修改、hook
- 反作弊绕过
任何请求此类功能的 Issue/PR 都将被拒绝合并。
如需扩展，请先更新 LICENSE（附加使用条款）、TERMS.md、CLAUDE.md、AGENTS.md，
并通过开发者本人审批。
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from src.capture.image_validation import load_png_image_bytes

logger = logging.getLogger(__name__)

_ADB_TIMEOUT = 15
_SCREENSHOT_RETRIES = 3
_SCREENSHOT_RETRY_DELAY = 0.15
SCREENSHOT_MODES = ("auto", "raw", "png")
# Android screencap raw 帧：16 字节头（宽/高/像素格式/色彩空间，各 u32 小端）+ 裸像素
_RAW_HEADER_BYTES = 16
_RAW_BYTES_PER_PIXEL = 4
# HAL_PIXEL_FORMAT：1=RGBA_8888，2=RGBX_8888（X 为无效字节，按 RGBA 处理）
_RAW_FORMAT_RGBA = 1
_RAW_FORMAT_RGBX = 2
_MAX_RAW_DIMENSION = 8192


class AdbCapture:
    """封装 ADB 连接与截图操作，支持多设备。"""

    def __init__(self, adb_path: str, adb_port: int = 7555,
                 screenshot_mode: str = "auto") -> None:
        """
        Args:
            adb_path: adb.exe 的完整路径。
            adb_port: MuMu 模拟器的 ADB 端口。
            screenshot_mode: auto=raw 优先失败回退 PNG / raw=仅 raw / png=仅 PNG。
        """
        self._adb_path = adb_path
        self._adb_port = adb_port
        if screenshot_mode not in SCREENSHOT_MODES:
            logger.warning("未知截图模式 %r，回退 auto", screenshot_mode)
            screenshot_mode = "auto"
        self._screenshot_mode = screenshot_mode
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
        target, target_error = self._resolve_target()
        if target_error:
            return False, target_error

        if self._connected:
            ok, msg = self.check_device()
            if ok:
                return True, "已处于连接状态"
            logger.info("缓存的 ADB 会话已失效: %s", msg)

        ok, msg = self._check_adb_valid()
        if not ok:
            return False, msg

        ok, msg = self._run_adb("connect", target)
        if not ok:
            logger.error("ADB 连接失败: %s", msg)
            return False, f"ADB 连接失败: {msg}"

        # 验证请求的目标设备，不能误选其它在线设备
        dev_ok, dev_msg = self._get_device_state(target)
        if not dev_ok:
            self._disconnect_safe()
            logger.error("目标 ADB 设备不可用: %s", dev_msg)
            return False, f"目标设备不可用: {dev_msg}"
        if dev_msg != "device":
            self._disconnect_safe()
            logger.error("目标 ADB 设备状态异常: %s", dev_msg)
            return False, f"目标设备状态异常: {dev_msg}"

        self._connected = True
        self._device_serial = target
        logger.info("ADB 连接成功 (设备 %s)", self._device_serial)
        return True, f"连接成功 (设备: {target})"

    def disconnect(self) -> tuple[bool, str]:
        """断开 ADB 连接。"""
        self._disconnect_safe()
        return True, "已断开连接"

    def _resolve_target(self) -> tuple[str, str]:
        """解析本次连接的精确 ADB 目标。"""
        if self._device_serial:
            return self._device_serial, ""
        if self._adb_port > 0:
            return f"127.0.0.1:{self._adb_port}", ""

        from src.capture.prober import probe_running_devices

        devices = probe_running_devices()
        if len(devices) == 1:
            return f"127.0.0.1:{devices[0].adb_port}", ""
        if not devices:
            return "", "未检测到运行中的 MuMu 实例，请先启动模拟器"
        return "", "检测到多个运行中的 MuMu 实例，请在模拟器配置中选择设备"

    def _disconnect_safe(self) -> None:
        """静默断开连接。"""
        target = self._device_serial or f"127.0.0.1:{self._adb_port}"
        self._connected = False
        self._device_serial = ""
        try:
            self._run_adb("disconnect", target, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            logger.warning("断开 ADB 连接异常（可忽略）: %s", e)

    # ── 设备检测 ──────────────────────────────────────────────────────

    def check_device(self) -> tuple[bool, str]:
        """检查当前连接的设备是否在线。"""
        if not self._connected or not self._device_serial:
            return False, "尚未连接"
        ok, state = self._get_device_state(self._device_serial)
        if not ok or state != "device":
            detail = state if ok else state
            self._invalidate_connection()
            return False, f"设备不可用: {detail}"
        return True, "设备在线"

    # ── 截图 ──────────────────────────────────────────────────────────

    def screencap_full(self, *, log_success: bool = True) -> tuple[bool, Image.Image | str]:
        """截取模拟器全屏，返回 PIL Image。

        Returns:
            (是否成功, Image 对象或错误消息)。
            ``log_success`` 为 False 时不记录成功日志，供高频轮询使用。
        """
        if not self._connected or not self._device_serial:
            return False, "尚未连接，请先连接模拟器"

        for attempt in range(1, _SCREENSHOT_RETRIES + 1):
            # 命令级失败（超时/设备离线）直接终止；数据级失败（空数据/解析失败）重试
            image, error, command_ms, decode_ms, mode = self._capture_and_decode()
            if image is not None:
                if log_success:
                    logger.info(
                        "截图成功: %s x %s，ADB命令=%.1fms，图像解码=%.1fms（%s）",
                        image.width, image.height, command_ms, decode_ms, mode,
                    )
                return True, image

            if attempt < _SCREENSHOT_RETRIES:
                logger.warning(
                    "截图数据无效，将重试 (%d/%d): %s",
                    attempt, _SCREENSHOT_RETRIES, error,
                )
                time.sleep(_SCREENSHOT_RETRY_DELAY)
            else:
                logger.error("截图失败: %s", error)
                return False, error

        return False, "截图失败"

    def _capture_and_decode(self) -> tuple[Image.Image | None, str, float, float, str]:
        """单轮截图尝试：按模式执行 raw/PNG 截图并解码。

        Returns:
            (图像或 None, 错误消息, ADB命令耗时ms, 解码耗时ms, 实际生效模式)。
        """
        if self._screenshot_mode in ("auto", "raw"):
            ok, payload, command_ms = self._run_screencap([])
            if not ok:
                return None, str(payload), command_ms, 0.0, "raw"
            decode_started = time.perf_counter()
            image = self._decode_raw_screencap(payload)
            decode_ms = (time.perf_counter() - decode_started) * 1000
            if image is not None:
                return image, "", command_ms, decode_ms, "raw"
            if self._screenshot_mode == "raw":
                return None, "raw 帧解析失败", command_ms, decode_ms, "raw"
            logger.debug("raw 帧解析失败（%s 字节），本轮回退 PNG 模式", len(payload))

        ok, payload, command_ms = self._run_screencap(["-p"])
        if not ok:
            return None, str(payload), command_ms, 0.0, "png"
        if not payload:
            return None, "截图返回空数据", command_ms, 0.0, "png"
        try:
            decode_started = time.perf_counter()
            image = load_png_image_bytes(payload)
            decode_ms = (time.perf_counter() - decode_started) * 1000
            return image, "", command_ms, decode_ms, "png"
        except Exception as e:
            return None, f"解析截图图像失败: {e}", command_ms, 0.0, "png"

    def _run_screencap(self, extra_args: list[str]) -> tuple[bool, bytes | str, float]:
        """执行一条 screencap 命令。

        Returns:
            (是否成功, 字节数据或错误消息, 命令耗时ms)。
            设备不可达时清除失效会话。
        """
        try:
            started = time.perf_counter()
            result = subprocess.run(
                [self._adb_path, "-s", self._device_serial, "exec-out", "screencap", *extra_args],
                capture_output=True,
                timeout=_ADB_TIMEOUT,
            )
            command_elapsed_ms = (time.perf_counter() - started) * 1000
        except FileNotFoundError:
            return False, f"找不到 adb: {self._adb_path}", 0.0
        except subprocess.TimeoutExpired:
            logger.error("截图命令执行超时")
            return False, "截图命令执行超时", _ADB_TIMEOUT * 1000.0
        except OSError as e:
            logger.error("截图命令执行异常: %s", e)
            return False, f"截图命令执行异常: {e}", 0.0

        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace").strip()
            logger.error("screencap 失败 (returncode=%d): %s", result.returncode, err)
            if self._is_device_unavailable(err):
                self._invalidate_connection()
            return False, f"screencap 失败: {err}", command_elapsed_ms
        return True, result.stdout, command_elapsed_ms

    @staticmethod
    def _decode_raw_screencap(data: bytes) -> Image.Image | None:
        """解析 Android screencap raw 帧（16 字节头 + RGBA_8888/RGBX_8888 裸像素）。

        任一校验不满足（头/格式/尺寸/字节数）返回 None，由调用方回退 PNG。
        """
        if len(data) < _RAW_HEADER_BYTES:
            return None
        width, height, pixel_format, _colorspace = np.frombuffer(
            data[:_RAW_HEADER_BYTES], dtype="<u4",
        )
        if pixel_format not in (_RAW_FORMAT_RGBA, _RAW_FORMAT_RGBX):
            return None
        if not (0 < width <= _MAX_RAW_DIMENSION and 0 < height <= _MAX_RAW_DIMENSION):
            return None
        if len(data) != _RAW_HEADER_BYTES + int(width) * int(height) * _RAW_BYTES_PER_PIXEL:
            return None
        pixels = np.frombuffer(data[_RAW_HEADER_BYTES:], dtype=np.uint8).reshape(
            height, width, _RAW_BYTES_PER_PIXEL,
        )
        rgb = cv2.cvtColor(pixels, cv2.COLOR_RGBA2RGB)
        return Image.fromarray(rgb)

    # ── 内部方法 ──────────────────────────────────────────────────────

    def _check_adb_valid(self) -> tuple[bool, str]:
        """检查 ADB 可执行文件是否有效。"""
        p = Path(self._adb_path)
        if not p.exists():
            return False, f"ADB 文件不存在: {self._adb_path}"
        if not p.is_file():
            return False, f"ADB 路径不是文件: {self._adb_path}"
        return True, ""

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

    def _invalidate_connection(self) -> None:
        """清除已失效的 ADB 会话缓存，保留重连所需配置。"""
        self._connected = False
        self._device_serial = ""

    @staticmethod
    def _is_device_unavailable(message: str) -> bool:
        """判断 ADB 错误是否明确表示目标设备已不可达。"""
        normalized = message.lower()
        markers = (
            "device offline",
            "device not found",
            "no devices/emulators found",
            "transport",
            "closed",
        )
        return any(marker in normalized for marker in markers)

    def _get_device_state(self, serial: str) -> tuple[bool, str]:
        """查询指定设备的精确 ADB 状态。"""
        ok, msg = self._run_adb("-s", serial, "get-state")
        if not ok:
            return False, msg
        return True, msg.strip() or "未检测到目标设备"

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return f"AdbCapture(path={self._adb_path}, port={self._adb_port}, {status})"
