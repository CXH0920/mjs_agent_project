"""
截图业务服务

负责截图的业务编排：触发截图 → 可选 OCR → 返回结果。
不包含 UI 操作，通过 Qt 信号与主窗口通信。
截图操作直接在 Python 中执行（不通过 QProcess），
因为需要即时获取图像数据更新 UI。
"""

from __future__ import annotations

import logging
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from src.capture.adb_screen import AdbCapture
from src.capture.image_utils import save_image
from src.ocr.ocr_loader import get_template_manager, get_recognizer

logger = logging.getLogger(__name__)

# 截图默认保存目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
DEFAULT_SCREENSHOT_DATA_DIR = PROJECT_ROOT / "screenshot_data"


class CaptureService(QObject):
    """截图业务服务"""

    status_changed = Signal(str)
    capture_completed = Signal(dict)
    capture_failed = Signal(str)
    connection_changed = Signal(str, str)  # (状态, 详情)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._capture: AdbCapture | None = None
        self._config = {}  # 当前配置缓存
        self._connection_state = "unconfigured"
        self._connection_detail = ""
        self._poll_cooldown_until: float = 0.0  # 轮询冷却到期时间戳

    def _set_connection_state(self, state: str, detail: str = "") -> None:
        """更新并广播当前 ADB 会话状态。"""
        if (state, detail) == (self._connection_state, self._connection_detail):
            return
        self._connection_state = state
        self._connection_detail = detail
        self.connection_changed.emit(state, detail)

    @property
    def connection_state(self) -> tuple[str, str]:
        """返回当前 ADB 会话状态及详情。"""
        return self._connection_state, self._connection_detail

    # ── 配置 ──────────────────────────────────────────────────────────

    def update_config(self, config: dict) -> None:
        """更新配置并重建 AdbCapture（仅路径或端口变化时重建）。

        重建时如果旧的 AdbCapture 已连通同一设备，保留已有连接状态。
        在配置无变化时不重建实例。

        Args:
            config: {
                "mumu_adb_path": str,
                "mumu_adb_port": int,
                "mumu_ocr_enabled": bool,
                "mumu_ocr_match_threshold": float,
                "ocr_generals_roi": list[list[int]],
            }
        """
        path_changed = config.get("mumu_adb_path") != self._config.get("mumu_adb_path")
        port_changed = config.get("mumu_adb_port") != self._config.get("mumu_adb_port")

        self._config = config

        if not config.get("mumu_adb_path"):
            self._capture = None
            self._set_connection_state("unconfigured")
            return

        if path_changed or port_changed or self._capture is None:
            self._capture = AdbCapture(
                adb_path=config["mumu_adb_path"],
                adb_port=config.get("mumu_adb_port", 0),
            )
            self._set_connection_state("disconnected")
            logger.info("CaptureService 配置已更新，ADB: %s:%s",
                        config["mumu_adb_path"], config.get("mumu_adb_port", "auto"))
        else:
            logger.debug("CaptureService 配置已更新（仅 OCR 参数）")

    def set_target_port(self, port: int) -> None:
        """切换下一次连接使用的 ADB 端口，并废弃旧会话。"""
        if not self._config.get("mumu_adb_path"):
            self._set_connection_state("unconfigured")
            return
        config = dict(self._config)
        config["mumu_adb_port"] = port
        self.update_config(config)

    @property
    def config(self) -> dict:
        """返回当前截图配置的副本。"""
        return dict(self._config)

    @property
    def capture(self) -> AdbCapture | None:
        return self._capture

    @capture.setter
    def capture(self, cap: AdbCapture | None) -> None:
        self._capture = cap

    # ── 截图 ──────────────────────────────────────────────────────────

    def do_capture(self, hero_names: list[str] | None = None) -> None:
        """执行一次截图 → 保存 → 可选 OCR 的完整流程。

        通过 QTimer.singleShot(0, ...) 确保不阻塞 Qt 事件循环。

        Args:
            hero_names: 用于编辑距离矫正的武将名列表（可选，从 HeroManager 获取）。
        """
        QTimer.singleShot(0, lambda: self._execute_capture(hero_names))

    def do_capture_from_file(self, file_path: str | Path,
                              hero_names: list[str] | None = None) -> None:
        """从本地图片文件执行 OCR 识别。

        Args:
            file_path: 图片文件路径。
            hero_names: 用于编辑距离矫正的武将名列表。
        """
        QTimer.singleShot(0, lambda: self._execute_file_ocr(file_path, hero_names))

    def _execute_file_ocr(self, file_path: str | Path,
                          hero_names: list[str] | None = None) -> None:
        """从本地图片执行 OCR（在 QTimer 回调中运行）。"""
        try:
            from PIL import Image
            image = Image.open(str(file_path))
            image.load()
            logger.info("从文件加载图片: %s (%sx%s)", file_path, image.width, image.height)
        except Exception as e:
            logger.error("图片加载失败 %s: %s", file_path, e)
            self.capture_failed.emit(f"图片加载失败: {e}")
            return

        # 直接运行 OCR
        ocr_results, ocr_matched = self._run_ocr(image, hero_names)

        self.capture_completed.emit({
            "image": image,
            "save_path": str(file_path),
            "ocr_results": ocr_results,
            "ocr_matched": ocr_matched,
        })

    def _execute_capture(self, hero_names: list[str] | None = None) -> None:
        """实际截图执行（在 QTimer 回调中运行）。"""
        if not self._capture:
            self._set_connection_state("unconfigured")
            self.capture_failed.emit("ADB 未配置，请在 配置 → 模拟器配置 中设置")
            return

        if not self._capture.connected:
            ok, msg = self.connect_emulator()
            if not ok:
                self.capture_failed.emit(f"ADB 连接失败: {msg}")
                return

        # 1. 截图
        self.status_changed.emit("正在截图...")
        ok, result = self._capture.screencap_full()
        if not ok:
            self.sync_connection_state(str(result))
            self.capture_failed.emit(str(result))
            return

        image = result
        self.status_changed.emit(f"截图成功 ({image.width}x{image.height})")

        # 2. 保存截图
        save_dir = DEFAULT_SCREENSHOTS_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = save_dir / f"screenshot_{timestamp}.png"
        save_ok, save_msg = save_image(image, save_path)
        if save_ok:
            logger.info("截图已保存: %s", save_path)
        else:
            logger.warning("截图保存失败: %s", save_msg)

        # 3. OCR（轮询模式独立于 ocr_enabled 开关，强制匹配模板）
        ocr_results = None
        ocr_matched = False
        is_poll = self._config.get("mumu_ocr_poll_mode", False)
        should_ocr = self._config.get("mumu_ocr_enabled", False) or is_poll

        if should_ocr:
            # 轮询模式：检查冷却期
            if is_poll and self._poll_cooldown_until > __import__("time").time():
                logger.debug("轮询冷却中，跳过 OCR")
            else:
                ocr_results, ocr_matched = self._run_ocr(image, hero_names)
                if ocr_matched:
                    self.status_changed.emit("已识别武将选择页面")
                    if is_poll:
                        # 轮询匹配成功后设置 3 分钟冷却
                        self._poll_cooldown_until = __import__("time").time() + 180
                        logger.info("轮询 OCR 匹配成功，冷却 180 秒")

        # 4. 返回结果
        self.capture_completed.emit({
            "image": image,
            "save_path": save_path if save_ok else None,
            "ocr_results": ocr_results,
            "ocr_matched": ocr_matched,
        })

    # ── OCR ───────────────────────────────────────────────────────────

    def _run_ocr(self, image, hero_names: list[str] | None = None):
        """执行 OCR 识别。"""
        try:
            tm = get_template_manager()
            if not tm.is_loaded:
                logger.info("模板未加载，跳过 OCR")
                return None, False

            threshold = self._config.get("mumu_ocr_match_threshold", 0.8)
            matched, confidence = tm.match(image, threshold=threshold)
            if not matched:
                logger.debug("模板不匹配 (置信度=%.4f < 阈值=%.2f)", confidence, threshold)
                return None, False

            rois = self._config.get("ocr_generals_roi", None)
            recognizer = get_recognizer(
                rois,
                hero_names=hero_names,
                reference_size=tm.reference_size,
            )
            results = recognizer.recognize(image)

            # 保存 OCR 结果
            data_dir = DEFAULT_SCREENSHOT_DATA_DIR
            data_dir.mkdir(parents=True, exist_ok=True)
            from src.ocr.recognizer import GeneralRecognizer
            GeneralRecognizer.save_results(results, data_dir / "latest.json")

            logger.info("OCR 完成: %d 个武将识别", len([r for r in results if r.get("name")]))
            return results, True

        except Exception as e:
            logger.error("OCR 执行异常: %s", e)
            logger.debug(traceback.format_exc())
            return None, False

    # ── 连接管理 ──────────────────────────────────────────────────────

    def sync_connection_state(self, error_detail: str = "") -> None:
        """根据底层会话状态同步 ADB 状态，供截图和轮询失败路径调用。"""
        if not self._capture:
            self._set_connection_state("unconfigured")
        elif not self._capture.connected:
            self._set_connection_state("offline", error_detail)

    def sync_poll_connection_state(self, capture: AdbCapture, error_detail: str = "") -> None:
        """仅同步当前轮询会话的连接状态，忽略过期 capture。"""
        if capture is not self._capture:
            return
        if capture.connected:
            self._set_connection_state("connected", capture.device_serial)
        else:
            self._set_connection_state("offline", error_detail)

    def connect_emulator(self) -> tuple[bool, str]:
        """连接模拟器。

        Returns:
            (是否成功, 消息)
        """
        if not self._capture:
            self._set_connection_state("unconfigured")
            return False, "ADB 未配置"
        self._set_connection_state("connecting")
        self.status_changed.emit("正在连接模拟器...")
        ok, message = self._capture.connect()
        if ok:
            self._set_connection_state("connected", self._capture.device_serial)
            self.status_changed.emit(f"ADB 已连接：{self._capture.device_serial}")
        else:
            self._set_connection_state("disconnected", message)
        return ok, message

    def disconnect_emulator(self) -> tuple[bool, str]:
        """断开模拟器。"""
        if not self._capture:
            self._set_connection_state("unconfigured")
            return False, "ADB 未配置"
        ok, message = self._capture.disconnect()
        self._set_connection_state("disconnected")
        self.status_changed.emit("ADB 已断开")
        return ok, message

    @property
    def is_connected(self) -> bool:
        return self._capture.connected if self._capture else False

    # ── 公开接口（供外部调用，替代直接访问私有成员） ─────────────────

    def get_matching_threshold(self) -> float:
        """获取模板匹配阈值。"""
        return self._config.get("mumu_ocr_match_threshold", 0.8)

    def run_ocr_if_matched(self, image, hero_names: list[str] | None = None):
        """公开方法：模板匹配 → 若匹配则 OCR，供轮询调用。"""
        return self._run_ocr(image, hero_names)
