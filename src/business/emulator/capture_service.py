"""
截图业务服务

负责截图的业务编排：触发截图 → 可选 OCR → 返回结果。
不包含 UI 操作，通过 Qt 信号与主窗口通信。
截图操作直接在 Python 中执行（不通过 QProcess），
因为需要即时获取图像数据更新 UI。
"""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from src.business.emulator.capture_connection import CaptureConnectionManager
from src.business.emulator.image_save_scheduler import ImageSaveScheduler
from src.business.emulator.ocr_task_coordinator import OcrTaskCoordinator
from src.business.emulator.official_import_gateway import OfficialImportGateway
from src.business.recognition.ocr_worker import OcrTask, OcrWorker, OfficialImportTask
from src.business.recognition.session_guard import OneShotToken
from src.capture.adb_screen import AdbCapture
from src.capture.image_validation import load_local_image
from src.config.env import SCREENSHOTS_DIR
from src.ocr.roi_config import OcrRoiConfig

logger = logging.getLogger(__name__)

# 截图默认保存目录（与 match 面板共用 env.SCREENSHOTS_DIR，#E8）
DEFAULT_SCREENSHOTS_DIR = SCREENSHOTS_DIR


class CaptureService(QObject):
    """截图业务服务"""

    status_changed = Signal(str)
    # 完成/事件类瞬时消息（UI 侧短暂停留后回落默认文案）；status_changed 语义
    # 保持"进行中/失败常驻"，两类消息分信号发射，避免 UI 侧做文案分类
    event_status_changed = Signal(str)
    capture_completed = Signal(dict)
    capture_failed = Signal(str)
    connection_changed = Signal(str, str)  # (状态, 详情)
    image_saved = Signal(dict)
    ocr_warmup_state_changed = Signal(str, str)
    official_import_progress = Signal(str, int, int)
    official_import_completed = Signal(object)
    official_import_failed = Signal(str)
    _capture_ready = Signal(object)

    def __init__(self, parent=None, roi_config: OcrRoiConfig | None = None):
        super().__init__(parent)
        self._roi_config = roi_config or OcrRoiConfig()
        # 连接域（配置热更/会话状态/连接编排）已出仓 CaptureConnectionManager
        # （P1-8）；状态与消息经回调回发，emit 留在本 QObject 上
        self._connection = CaptureConnectionManager(
            on_state_change=self.connection_changed.emit,
            on_status=self.status_changed.emit,
            on_event_status=self.event_status_changed.emit,
        )
        self._pending_ocr_captures: dict[str, dict] = {}
        self._official_gateway = OfficialImportGateway(parent=self)
        self._official_gateway.progress.connect(self.official_import_progress)
        self._official_gateway.completed.connect(self.official_import_completed)
        self._official_gateway.failed.connect(self.official_import_failed)
        self._ocr_coordinator = OcrTaskCoordinator(
            parent=self,
            config_provider=lambda: self.config,
            roi_config=self._roi_config,
            on_worker_created=self._on_worker_created,
        )
        self._ocr_coordinator.warmup_state_changed.connect(self.ocr_warmup_state_changed)
        # ADB 并发防护由 _adb_executor 单线程执行器保证（会话/IO 锁随连接域出仓）
        self._adb_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="adb-capture")
        self._save_scheduler = ImageSaveScheduler(parent=self)
        self._save_scheduler.image_saved.connect(self.image_saved)
        self._shutdown_token = OneShotToken()  # 关停一次性守卫（P0-3 收口）
        self._capture_ready.connect(self._on_background_capture_ready)

    # ── 连接域委托（实现见 CaptureConnectionManager，P1-8 出仓） ──────

    @property
    def connection_state(self) -> tuple[str, str]:
        """返回当前 ADB 会话状态及详情。"""
        return self._connection.connection_state

    @property
    def ocr_warmup_state(self) -> str:
        """返回 OCR 预热状态：idle、warming、ready 或 failed。"""
        return self._ocr_coordinator.warmup_state

    def update_config(self, config: dict) -> None:
        """更新配置并按需重建 AdbCapture（详见 CaptureConnectionManager.update_config）。"""
        self._connection.update_config(config)

    def set_target_port(self, port: int) -> None:
        """切换下一次连接使用的 ADB 端口，并废弃旧会话。"""
        self._connection.set_target_port(port)

    @property
    def config(self) -> dict:
        """返回当前截图配置的副本。"""
        return self._connection.config

    @property
    def _config(self) -> dict:
        """配置缓存直通（测试直写兼容缝；生产代码走 update_config）。"""
        return self._connection._config

    @_config.setter
    def _config(self, value: dict) -> None:
        self._connection._config = value

    @property
    def roi_config(self) -> OcrRoiConfig:
        """返回共享的 OCR ROI 配置，供配置页编辑后立即生效。"""
        return self._roi_config

    @property
    def capture(self) -> AdbCapture | None:
        return self._connection.capture

    @capture.setter
    def capture(self, cap: AdbCapture | None) -> None:
        self._connection.capture = cap

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
        if self._shutdown_token.spent:
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

    def _on_background_capture_ready(self, payload: object) -> None:
        """在 GUI 线程处理后台截图结果。"""
        if self._shutdown_token.spent:
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
        self.event_status_changed.emit(f"截图成功 ({image.width}x{image.height})")

        # 2. OCR 和 PNG 保存进入不同后台执行器，互不等待。
        should_ocr = perform_ocr and (
            force_ocr or self.config.get("mumu_ocr_enabled", False)
        )
        ocr_task = None
        if should_ocr:
            ocr_task = self._queue_capture_ocr(
                image=image.copy(),
                save_path=None,
                hero_names=hero_names,
                template_name=template_name,
                match_template=not force_ocr,
            )

        save_dir = DEFAULT_SCREENSHOTS_DIR
        save_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = save_dir / f"screenshot_{timestamp}.png"
        save_future = self._save_scheduler.schedule(image, save_path)

        if ocr_task is not None:
            pending = self._pending_ocr_captures.get(ocr_task.task_id)
            if pending is not None:
                pending["save_future"] = save_future
            return

        # 3. OCR 未启用时，直接返回保存结果。
        self.capture_completed.emit({
            "image": image,
            "save_path": self._completed_save_path(save_future, save_path),
            "ocr_results": None,
            "ocr_matched": False,
        })

    # ── OCR ───────────────────────────────────────────────────────────

    def _ensure_ocr_worker(self) -> OcrWorker:
        return self._ocr_coordinator.ensure_worker()

    def _on_worker_created(self, worker: OcrWorker) -> None:
        """为新建 OCR worker 接线：完成回执与官方导入进度（同步先于 start）。"""
        worker.task_completed.connect(self._on_ocr_task_completed)
        worker.task_completed.connect(self._official_gateway.on_task_completed)
        worker.official_progress.connect(self._official_gateway.on_worker_progress)

    def start_ocr_worker(self) -> None:
        """在 GUI 线程中初始化 OCR worker，供应用启动阶段调用。"""
        self._ensure_ocr_worker()

    def reset_ocr_recognizer_cache(self) -> None:
        """丢弃 OCR recognizer 缓存，使下次识别读到新的用户层白名单。"""
        self._ocr_coordinator.reset_recognizer_cache()

    def warmup_ocr_model(self, hero_names: list[str] | None = None) -> None:
        """在 OCR worker 中预热模型、推理算子和词表特征。"""
        if self.ocr_warmup_state in {"warming", "ready"}:
            return
        task = self._ensure_ocr_worker().warmup_model(hero_names)
        self._ocr_coordinator.note_warmup_submitted(task)

    def wait_ocr_warmup(self, timeout_ms: int = 15_000) -> bool:
        """阻塞等待启动阶段预热完成；超时或未启动预热时返回 False。"""
        return self._ocr_coordinator.wait_warmup(timeout_ms)

    def submit_ocr_task(
        self,
        image,
        hero_names: list[str] | None = None,
        template_name: str = "hero_selection",
        recognize: bool = True,
        rois: list[list[int]] | None = None,
        match_template: bool = True,
        fallback_on_template_miss: bool = False,
        allow_result_reuse: bool = False,
    ) -> OcrTask:
        """将模板匹配和 OCR 加入唯一 worker 队列。"""
        task = self._ocr_coordinator.build_task(
            image,
            hero_names=hero_names,
            template_name=template_name,
            recognize=recognize,
            rois=rois,
            match_template=match_template,
            fallback_on_template_miss=fallback_on_template_miss,
            allow_result_reuse=allow_result_reuse,
        )
        self._ensure_ocr_worker().submit(task)
        return task

    def submit_official_import(self, paths: dict[str, list[str]]) -> OfficialImportTask:
        """将整批官方榜单导入加入唯一 OCR worker 队列。"""
        return self._official_gateway.submit(self._ensure_ocr_worker(), paths)

    def _queue_capture_ocr(
        self,
        *,
        image,
        save_path: str | Path | None,
        hero_names: list[str] | None,
        template_name: str,
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
            "template_name": template_name,
        }
        return task

    @staticmethod
    def _completed_save_path(future: Future | None, save_path: Path | str | None) -> Path | str | None:
        if future is None:
            return save_path
        if not future.done():
            return None
        try:
            saved, _detail = future.result()
        except Exception as error:
            logger.warning("截图存盘失败（%s）: %s", save_path, error)
            return None
        return save_path if saved else None

    def _on_ocr_task_completed(self, task: OcrTask | OfficialImportTask) -> None:
        if isinstance(task, OfficialImportTask):
            return  # 官方导入结果由 OfficialImportGateway 分派
        if task.warmup:
            self._ocr_coordinator.on_warmup_completed(task)
            return
        pending = self._pending_ocr_captures.pop(task.task_id, None)
        if pending is None:
            return

        result = task.result or {"outcome": "retryable_ocr"}
        ocr_matched = result.get("outcome") == "matched"
        if ocr_matched:
            page_name = "对局攻略页面" if pending["template_name"] == "match_guide" else "武将选择页面"
            self.event_status_changed.emit(f"已识别到{page_name}")

        self.capture_completed.emit({
            "image": pending["image"],
            "save_path": self._completed_save_path(
                pending.get("save_future"), pending["save_path"],
            ),
            "ocr_results": result.get("ocr_results"),
            "ocr_matched": ocr_matched,
        })


    # ── 连接管理（实现已出仓 CaptureConnectionManager） ───────────────

    def sync_connection_state(self, error_detail: str = "") -> None:
        """根据底层会话状态同步 ADB 状态，供截图和轮询失败路径调用。"""
        self._connection.sync_connection_state(error_detail)

    def sync_poll_connection_state(self, capture: AdbCapture, error_detail: str = "") -> None:
        """仅同步当前轮询会话的连接状态，忽略过期 capture。"""
        self._connection.sync_poll_connection_state(capture, error_detail)

    def connect_emulator(self) -> tuple[bool, str]:
        """连接模拟器。"""
        return self._connection.connect_emulator()

    def disconnect_emulator(self) -> tuple[bool, str]:
        """断开模拟器。"""
        return self._connection.disconnect_emulator()

    def capture_screenshot(self) -> tuple[bool, object]:
        """使用共享 ADB 会话获取一张截图，不保存文件也不触发 OCR。"""
        connection = self._connection
        with connection.session_lock:
            capture = connection.capture
            if capture is None:
                connection.set_connection_state("unconfigured")
                return False, "ADB 未配置，请在 配置 → 模拟器配置 中设置"
            need_connect = not capture.connected

        if need_connect:
            ok, message = connection.connect_capture(capture)
            if not ok:
                return False, f"ADB 连接失败: {message}"
            # 连接期间配置可能已变更并重建 AdbCapture，旧引用不再可信
            if not connection.is_current(capture):
                return False, "ADB 配置已变更，请重试"

        self.status_changed.emit("正在截图...")
        with connection.adb_io_lock:
            ok, result = capture.screencap_full()
        if not ok:
            self.sync_connection_state(str(result))
            return False, str(result)

        self.event_status_changed.emit(f"截图成功 ({result.width}x{result.height})")
        return True, result

    def capture_for_poll(self, capture: AdbCapture) -> tuple[bool, object, str]:
        """经同一后台执行器完成轮询截图，避免与手动截图并发访问 ADB。"""
        if self._shutdown_token.spent:
            return False, "截图服务已关闭", "capture"
        future = self._adb_executor.submit(self._capture_for_poll, capture)
        try:
            return future.result()
        except Exception as error:
            logger.exception("轮询截图异常")
            return False, str(error), "capture"

    def _capture_for_poll(self, capture: AdbCapture) -> tuple[bool, object, str]:
        connection = self._connection
        with connection.session_lock:
            if capture is not connection.capture:
                return False, "ADB 配置已变更", "connection"
            need_connect = not capture.connected
        if need_connect:
            with connection.adb_io_lock:
                ok, message = capture.connect()
            if not ok:
                return False, message, "connection"
        with connection.adb_io_lock:
            ok, result = capture.screencap_full(log_success=False)
        return ok, result, "" if ok else "capture"

    # ── 公开接口（供外部调用，替代直接访问私有成员） ─────────────────

    def shutdown(self) -> None:
        """停止截图执行器和 OCR worker，供应用退出时调用。

        OCR worker 仅通知停止并立即返回（不在 GUI 线程同步等待），
        线程由 OcrWorker 的退役列表持有并在进程退出前收尾，避免窗口卡死。
        """
        self._shutdown_token.mark()
        self._adb_executor.shutdown(wait=False, cancel_futures=True)
        self._save_scheduler.shutdown()
        self._ocr_coordinator.shutdown()
