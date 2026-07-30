"""
截图业务服务

负责截图的业务编排：触发截图 → 可选 OCR → 返回结果。
不包含 UI 操作，通过 Qt 信号与主窗口通信。
截图操作直接在 Python 中执行（不通过 QProcess），
因为需要即时获取图像数据更新 UI。
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from src.capture.adb_screen import AdbCapture
from src.capture.image_validation import load_local_image
from src.capture.image_utils import save_image
from src.business.ocr_worker import OcrTask, OcrWorker
from src.ocr.roi_config import OcrRoiConfig, OcrRoiLayout, OcrRoiSlot

logger = logging.getLogger(__name__)

# 截图默认保存目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"


class CaptureService(QObject):
    """截图业务服务"""

    status_changed = Signal(str)
    capture_completed = Signal(dict)
    capture_failed = Signal(str)
    connection_changed = Signal(str, str)  # (状态, 详情)
    image_saved = Signal(dict)
    ocr_warmup_state_changed = Signal(str, str)
    _capture_ready = Signal(object)
    _image_save_ready = Signal(object)

    def __init__(self, parent=None, roi_config: OcrRoiConfig | None = None):
        super().__init__(parent)
        self._capture: AdbCapture | None = None
        self._config = {}  # 当前配置缓存
        self._roi_config = roi_config or OcrRoiConfig()
        self._connection_state = "unconfigured"
        self._connection_detail = ""
        self._ocr_warmup_state = "idle"
        self._poll_cooldown_until: float = 0.0  # 轮询冷却到期时间戳
        self._ocr_worker: OcrWorker | None = None
        self._pending_ocr_captures: dict[str, dict] = {}
        self._session_lock = threading.RLock()
        self._adb_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="adb-capture")
        self._image_save_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-save")
        self._closed = False
        self._capture_ready.connect(self._on_background_capture_ready)
        self._image_save_ready.connect(self._on_image_save_ready)

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
        with self._session_lock:
            return self._connection_state, self._connection_detail

    @property
    def ocr_warmup_state(self) -> str:
        """返回 OCR 预热状态：idle、warming、ready 或 failed。"""
        return self._ocr_warmup_state

    def _set_ocr_warmup_state(self, state: str, detail: str = "") -> None:
        if (state, detail) == (self._ocr_warmup_state, ""):
            return
        self._ocr_warmup_state = state
        self.ocr_warmup_state_changed.emit(state, detail)

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
            }
        """
        with self._session_lock:
            path_changed = config.get("mumu_adb_path") != self._config.get("mumu_adb_path")
            port_changed = config.get("mumu_adb_port") != self._config.get("mumu_adb_port")

            self._config = dict(config)

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
        with self._session_lock:
            return dict(self._config)

    @property
    def roi_config(self) -> OcrRoiConfig:
        """返回共享的 OCR ROI 配置，供配置页编辑后立即生效。"""
        return self._roi_config

    @property
    def capture(self) -> AdbCapture | None:
        with self._session_lock:
            return self._capture

    @capture.setter
    def capture(self, cap: AdbCapture | None) -> None:
        with self._session_lock:
            self._capture = cap

    # ── 截图 ──────────────────────────────────────────────────────────

    def do_capture(
        self,
        hero_names: list[str] | None = None,
        template_name: str = "hero_selection",
        force_ocr: bool = False,
        perform_ocr: bool = True,
    ) -> None:
        """执行一次截图 → 保存 → 可选 OCR 的完整流程。

        ADB 连接和截图在单一后台执行器中串行完成；结果回到 GUI 线程后再保存图片并提交 OCR。

        Args:
            hero_names: 用于编辑距离矫正的武将名列表（可选，从 HeroManager 获取）。
        """
        if self._closed:
            return
        request = {
            "hero_names": hero_names,
            "template_name": template_name,
            "force_ocr": force_ocr,
            "perform_ocr": perform_ocr,
        }
        future = self._adb_executor.submit(self.capture_screenshot)
        future.add_done_callback(
            lambda task, payload=request: self._capture_ready.emit((payload, task))
        )

    def do_capture_from_file(self, file_path: str | Path,
                              hero_names: list[str] | None = None,
                              template_name: str = "hero_selection",
                              force_ocr: bool = False,
                              perform_ocr: bool = True) -> None:
        """从本地图片文件执行 OCR 识别。

        Args:
            file_path: 图片文件路径。
            hero_names: 用于编辑距离矫正的武将名列表。
        """
        QTimer.singleShot(
            0,
            lambda: self._execute_file_ocr(
                file_path, hero_names, template_name, force_ocr, perform_ocr,
            ),
        )

    def _execute_file_ocr(self, file_path: str | Path,
                          hero_names: list[str] | None = None,
                          template_name: str = "hero_selection",
                          force_ocr: bool = False,
                          perform_ocr: bool = True) -> None:
        """从本地图片执行 OCR。"""
        try:
            image = load_local_image(file_path)
            logger.info("从文件加载图片: %s (%sx%s)", file_path, image.width, image.height)
        except Exception as e:
            logger.error("图片加载失败 %s: %s", file_path, e)
            self.capture_failed.emit(f"图片加载失败: {e}")
            return

        if perform_ocr:
            self._queue_capture_ocr(
                image=image,
                save_path=str(file_path),
                hero_names=hero_names,
                template_name=template_name,
                match_template=not force_ocr,
            )
            return

        self.capture_completed.emit({
            "image": image,
            "save_path": str(file_path),
            "ocr_results": None,
            "ocr_matched": False,
        })

    def _execute_capture(
        self,
        hero_names: list[str] | None = None,
        template_name: str = "hero_selection",
        force_ocr: bool = False,
        perform_ocr: bool = True,
    ) -> None:
        """实际截图执行。"""
        ok, result = self.capture_screenshot()
        self._handle_capture_result(ok, result, hero_names, template_name, force_ocr, perform_ocr)

    def _on_background_capture_ready(self, payload: object) -> None:
        """在 GUI 线程处理后台截图结果。"""
        if self._closed:
            return
        request, future = payload
        try:
            ok, result = future.result()
        except Exception as error:
            logger.exception("后台截图异常")
            ok, result = False, str(error)
        self._handle_capture_result(
            ok,
            result,
            request["hero_names"],
            request["template_name"],
            request["force_ocr"],
            request["perform_ocr"],
        )

    def _handle_capture_result(
        self,
        ok: bool,
        result: object,
        hero_names: list[str] | None,
        template_name: str,
        force_ocr: bool,
        perform_ocr: bool,
    ) -> None:
        """处理已完成的截图，后续文件和 OCR 操作始终在 GUI 线程执行。"""
        if not ok:
            self.capture_failed.emit(str(result))
            return

        image = result
        self.status_changed.emit(f"截图成功 ({image.width}x{image.height})")

        # 2. OCR 和 PNG 保存进入不同后台执行器，互不等待。
        is_poll = self.config.get("mumu_ocr_poll_mode", False)
        should_ocr = perform_ocr and (
            force_ocr or self.config.get("mumu_ocr_enabled", False) or is_poll
        )
        ocr_task = None
        if should_ocr:
            if is_poll and self._poll_cooldown_until > __import__("time").time():
                logger.debug("轮询冷却中，跳过 OCR")
            else:
                ocr_task = self._queue_capture_ocr(
                    image=image.copy(),
                    save_path=None,
                    hero_names=hero_names,
                    template_name=template_name,
                    is_poll=is_poll,
                    match_template=not force_ocr,
                )

        save_dir = DEFAULT_SCREENSHOTS_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = save_dir / f"screenshot_{timestamp}.png"
        save_future = self._schedule_image_save(image, save_path)

        if ocr_task is not None:
            pending = self._pending_ocr_captures.get(ocr_task.task_id)
            if pending is not None:
                pending["save_future"] = save_future
            return

        # 3. OCR 未启用或处于轮询冷却时，直接返回保存结果。
        self.capture_completed.emit({
            "image": image,
            "save_path": self._completed_save_path(save_future, save_path),
            "ocr_results": None,
            "ocr_matched": False,
        })

    # ── OCR ───────────────────────────────────────────────────────────

    def _ensure_ocr_worker(self) -> OcrWorker:
        if self._ocr_worker is None:
            self._ocr_worker = OcrWorker()
            self._ocr_worker.task_completed.connect(self._on_ocr_task_completed)
            self._ocr_worker.start()
        return self._ocr_worker

    def start_ocr_worker(self) -> None:
        """在 GUI 线程中初始化 OCR worker，供应用启动阶段调用。"""
        self._ensure_ocr_worker()

    def warmup_ocr_model(self, hero_names: list[str] | None = None) -> None:
        """在 OCR worker 中预热模型、推理算子和词表特征。"""
        if self._ocr_warmup_state in {"warming", "ready"}:
            return
        if self._ensure_ocr_worker().warmup_model(hero_names):
            self._set_ocr_warmup_state("warming")

    def submit_ocr_task(
        self,
        image,
        hero_names: list[str] | None = None,
        template_name: str = "hero_selection",
        recognize: bool = True,
        rois: list[list[int]] | None = None,
        match_template: bool = True,
        fallback_on_template_miss: bool = False,
    ) -> OcrTask:
        """将模板匹配和 OCR 加入唯一 worker 队列。"""
        config = self.config
        threshold_key = (
            "mumu_match_guide_threshold"
            if template_name == "match_guide"
            else "mumu_hero_selection_threshold"
        )
        layout = self._roi_config.layout_for(template_name)
        if rois is not None:
            layout = OcrRoiLayout(
                layout.reference_size,
                tuple(OcrRoiSlot(name_roi=tuple(roi)) for roi in rois),
            )
        task = OcrTask(
            image=image,
            hero_names=tuple(hero_names or ()),
            rois=None,
            template_name=template_name,
            threshold=config.get(
                threshold_key,
                config.get("mumu_ocr_match_threshold", 0.8),
            ),
            roi_layout=layout,
            recognize=recognize,
            match_template=match_template,
            fallback_on_template_miss=fallback_on_template_miss,
        )
        self._ensure_ocr_worker().submit(task)
        return task

    def _queue_capture_ocr(
        self,
        *,
        image,
        save_path: str | Path | None,
        hero_names: list[str] | None,
        template_name: str,
        is_poll: bool = False,
        match_template: bool = True,
    ) -> OcrTask:
        task = self.submit_ocr_task(
            image,
            hero_names,
            template_name,
            match_template=match_template,
        )
        self._pending_ocr_captures[task.task_id] = {
            "image": image,
            "save_path": save_path,
            "is_poll": is_poll,
            "template_name": template_name,
        }
        return task

    def _schedule_image_save(self, image, save_path: Path) -> Future:
        future = self._image_save_executor.submit(save_image, image, save_path)
        future.add_done_callback(
            lambda completed, source=image, path=save_path: self._image_save_ready.emit(
                (source, path, completed),
            ),
        )
        return future

    @staticmethod
    def _completed_save_path(future: Future | None, save_path: Path | str | None) -> Path | str | None:
        if future is None:
            return save_path
        if not future.done():
            return None
        try:
            saved, _detail = future.result()
        except Exception:
            return None
        return save_path if saved else None

    def _on_image_save_ready(self, payload: object) -> None:
        image, save_path, future = payload
        try:
            saved, detail = future.result()
        except Exception as exc:
            saved, detail = False, str(exc)
        if saved:
            logger.info("截图已保存: %s", save_path)
        else:
            logger.warning("截图保存失败: %s", detail)
        self.image_saved.emit({
            "image": image,
            "save_path": save_path if saved else None,
            "detail": detail,
        })

    def _on_ocr_task_completed(self, task: OcrTask) -> None:
        if task.warmup:
            result = task.result or {"outcome": "warmup_failed"}
            if result.get("outcome") == "warmed":
                self._set_ocr_warmup_state("ready")
            else:
                self._set_ocr_warmup_state("failed", result.get("detail", "未知错误"))
            return
        pending = self._pending_ocr_captures.pop(task.task_id, None)
        if pending is None:
            return

        result = task.result or {"outcome": "retryable_ocr"}
        ocr_matched = result.get("outcome") == "matched"
        if ocr_matched:
            page_name = "对局攻略页面" if pending["template_name"] == "match_guide" else "武将选择页面"
            self.status_changed.emit(f"已识别到{page_name}")
            if pending["is_poll"]:
                self._poll_cooldown_until = __import__("time").time() + 180
                logger.debug("轮询 OCR 匹配成功，冷却 180 秒")

        self.capture_completed.emit({
            "image": pending["image"],
            "save_path": self._completed_save_path(
                pending.get("save_future"), pending["save_path"],
            ),
            "ocr_results": result.get("ocr_results"),
            "ocr_matched": ocr_matched,
        })


    # ── 连接管理 ──────────────────────────────────────────────────────

    def sync_connection_state(self, error_detail: str = "") -> None:
        """根据底层会话状态同步 ADB 状态，供截图和轮询失败路径调用。"""
        with self._session_lock:
            if not self._capture:
                self._set_connection_state("unconfigured")
            elif not self._capture.connected:
                self._set_connection_state("offline", error_detail)

    def sync_poll_connection_state(self, capture: AdbCapture, error_detail: str = "") -> None:
        """仅同步当前轮询会话的连接状态，忽略过期 capture。"""
        with self._session_lock:
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
        with self._session_lock:
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
        with self._session_lock:
            if not self._capture:
                self._set_connection_state("unconfigured")
                return False, "ADB 未配置"
            ok, message = self._capture.disconnect()
            self._set_connection_state("disconnected")
            self.status_changed.emit("ADB 已断开")
            return ok, message

    def capture_screenshot(self) -> tuple[bool, object]:
        """使用共享 ADB 会话获取一张截图，不保存文件也不触发 OCR。"""
        with self._session_lock:
            if not self._capture:
                self._set_connection_state("unconfigured")
                return False, "ADB 未配置，请在 配置 → 模拟器配置 中设置"
            if not self._capture.connected:
                ok, message = self.connect_emulator()
                if not ok:
                    return False, f"ADB 连接失败: {message}"

            self.status_changed.emit("正在截图...")
            ok, result = self._capture.screencap_full()
            if not ok:
                self.sync_connection_state(str(result))
                return False, str(result)

            image = result
            self.status_changed.emit(f"截图成功 ({image.width}x{image.height})")
            return True, image

    def capture_for_poll(self, capture: AdbCapture) -> tuple[bool, object, str]:
        """经同一后台执行器完成轮询截图，避免与手动截图并发访问 ADB。"""
        if self._closed:
            return False, "截图服务已关闭", "capture"
        future = self._adb_executor.submit(self._capture_for_poll, capture)
        try:
            return future.result()
        except Exception as error:
            logger.exception("轮询截图异常")
            return False, str(error), "capture"

    def _capture_for_poll(self, capture: AdbCapture) -> tuple[bool, object, str]:
        with self._session_lock:
            if capture is not self._capture:
                return False, "ADB 配置已变更", "connection"
            if not capture.connected:
                ok, message = capture.connect()
                if not ok:
                    return False, message, "connection"
            ok, result = capture.screencap_full(log_success=False)
            return ok, result, "" if ok else "capture"

    @property
    def is_connected(self) -> bool:
        with self._session_lock:
            return self._capture.connected if self._capture else False

    # ── 公开接口（供外部调用，替代直接访问私有成员） ─────────────────

    def get_matching_threshold(self) -> float:
        """获取模板匹配阈值。"""
        return self._config.get("mumu_ocr_match_threshold", 0.8)

    def run_ocr_if_matched(self, image, hero_names: list[str] | None = None):
        """同步等待 OCR worker 的结果；仅供非 GUI 调度路径使用。"""
        task = self.submit_ocr_task(image, hero_names)
        task.completed.wait()
        result = task.result or {}
        return result.get("ocr_results"), result.get("outcome") == "matched"

    def shutdown(self) -> None:
        """停止截图执行器和 OCR worker，供应用退出时调用。"""
        self._closed = True
        self._adb_executor.shutdown(wait=False, cancel_futures=True)
        self._image_save_executor.shutdown(wait=False, cancel_futures=True)
        if self._ocr_worker is not None:
            if self._ocr_worker.shutdown():
                self._ocr_worker = None
