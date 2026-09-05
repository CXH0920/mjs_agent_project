"""巅峰赛（2v2）选将实时识别循环：截图 → 卡位检测 → 牌面变化才 OCR。

与标准轮询并存：巅峰赛页与标准选将页共用"武将选择"标题模板，检测到巅峰赛
牌面期间挂起 hero_selection / match_guide 轮询任务避免互触，连续多拍未见
牌面或停止后恢复原任务状态。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from src.capture.image_validation import load_local_image
from src.ocr.card_grid_detector import derive_name_rois, detect_selection_cards
from src.ocr.roi_config import Roi

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 1500
OCR_WAIT_TIMEOUT_SECONDS = 15
# 连续多拍未检出牌面才判定离开巅峰赛页，避免翻页动画误恢复标准任务
BOARD_EXIT_TICKS = 2
# 牌面签名量化步长：位置与尺寸分开量化，吸收卡位检测的像素级抖动，
# 仅布局变化才触发 OCR
SIGNATURE_POSITION_QUANTUM_PX = 4
SIGNATURE_SIZE_QUANTUM_PX = 8
# 14 张为禁选阶段（双方尚未提交禁选），8~11 张为候选阶段
_BAN_PHASE_MIN_CARDS = 12
_STANDARD_POLL_TASKS = ("hero_selection", "match_guide")
_CONFIRM_RESOLUTIONS = {"unresolved", "unknown", "conflict"}


@dataclass(frozen=True)
class PoolSnapshot:
    """一次牌面识别的结构化结果，供面板渲染。"""

    card_count: int
    names: tuple[str, ...]
    pending: tuple[dict, ...]
    stage: str  # "ban" 禁选阶段 / "pick" 候选阶段
    overlap: int  # 候选阶段双方禁选撞车数（池大小 - 8）
    banned: tuple[str, ...]  # 相对禁选期已确认名单的差集


def parse_pool(
    ocr_results: list[dict],
    card_count: int,
    ban_names: tuple[str, ...] = (),
    resolutions: dict[int, str] | None = None,
) -> PoolSnapshot:
    """把 OCR 槽位结果整理为候选池快照：已确认名单 + 待确认槽位。

    resolutions 为人工确认（槽位序号 → 武将名），仅当该槽仍未自动确认且
    确认名确实在其候选内才生效，避免旧牌面的确认串到新牌面上。
    """
    names: list[str] = []
    pending: list[dict] = []
    for index, item in enumerate(ocr_results):
        name = str(item.get("name") or "").strip()
        if name and item.get("resolution") not in _CONFIRM_RESOLUTIONS:
            names.append(name)
            continue
        raw_name = str(item.get("raw_name") or "").strip()
        candidates = [str(c) for c in (item.get("candidates") or [])]
        if not (raw_name or candidates):
            continue
        manual = (resolutions or {}).get(index)
        if manual and manual in candidates:
            names.append(manual)
            continue
        pending.append({"slot": index, "raw_name": raw_name, "candidates": candidates})

    stage = "ban" if card_count >= _BAN_PHASE_MIN_CARDS else "pick"
    identified = set(names)
    banned = tuple(name for name in ban_names if name not in identified)
    overlap = card_count - 8 if stage == "pick" else 0
    return PoolSnapshot(
        card_count=card_count,
        names=tuple(names),
        pending=tuple(pending),
        stage=stage,
        overlap=overlap,
        banned=banned,
    )


def board_signature(cards: list[Roi]) -> tuple:
    """生成牌面布局签名：坐标全量量化，抖动不触发重复 OCR。"""
    return tuple(
        (
            round(x / SIGNATURE_POSITION_QUANTUM_PX),
            round(y / SIGNATURE_POSITION_QUANTUM_PX),
            round(w / SIGNATURE_SIZE_QUANTUM_PX),
            round(h / SIGNATURE_SIZE_QUANTUM_PX),
        )
        for x, y, w, h in cards
    )


class PeakSelectWatcher(QObject):
    """巅峰赛选将识别循环；识别结果通过 pool_updated 推送面板。"""

    pool_updated = Signal(object)
    status_changed = Signal(str)

    def __init__(self, capture_service, ocr_service, hero_names_provider, parent=None) -> None:
        super().__init__(parent)
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._hero_names_provider = hero_names_provider
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)
        self._thread_lock = threading.Lock()
        self._import_lock = threading.Lock()
        # 牌面状态（_signature/_ban_names/_resolutions/_last_board）被识别线程、
        # 图片导入线程与 GUI 线程（start/confirm_pending）三方并发读写；_thread_lock
        # 只保证识别拍单飞，覆盖不到 GUI 调用，统一用 _state_lock 串行化。
        # 锁内只做纯内存读写，不发 IO、不 emit 信号。
        self._state_lock = threading.Lock()
        self._signature: tuple | None = None
        self._ban_names: tuple[str, ...] = ()
        self._resolutions: dict[int, str] = {}
        self._last_board: tuple[list[dict], int] | None = None
        self._miss_ticks = 0
        self._saved_task_states: dict[str, bool] | None = None

    def is_running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        # 上一轮停止后可能仍有在途识别线程，重置须与其互斥
        with self._state_lock:
            self._signature = None
            self._ban_names = ()
            self._resolutions = {}
            self._last_board = None
        self._miss_ticks = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._restore_standard_tasks()

    # ── 识别循环 ──────────────────────────────────────────────────────

    def _on_tick(self) -> None:
        if not self._thread_lock.acquire(blocking=False):
            return  # 上一拍（截图+OCR）尚未完成，跳过本轮
        threading.Thread(target=self._do_work, daemon=True, name="peak-select-watch").start()

    def _do_work(self) -> None:
        try:
            capture = self._capture_service.capture
            if not capture:
                self.status_changed.emit("未连接模拟器")
                return
            ok, result, failure_kind = self._capture_service.capture_for_poll(capture)
            if not ok:
                self.status_changed.emit(f"截图失败({failure_kind}): {result}")
                return
            frame = cv2.cvtColor(np.array(result.convert("RGB")), cv2.COLOR_RGB2BGR)
            cards = detect_selection_cards(frame)
            if cards is None:
                self._handle_board_absent()
                return

            self._miss_ticks = 0
            signature = board_signature(cards)
            with self._state_lock:
                unchanged = signature == self._signature
                if not unchanged:
                    self._resolutions = {}  # 新牌面：人工确认不跨牌沿用
            if unchanged:
                return  # 牌面未变化，沿用上一次结果
            self._suspend_standard_tasks()
            ocr_results = self._recognize_board(result, cards)
            if ocr_results is None:
                # 识别失败清签名，下一拍强制重试；仅实时循环路径，图片导入不动签名
                with self._state_lock:
                    self._signature = None
                return
            with self._state_lock:
                self._signature = signature
            self._publish_pool(ocr_results, len(cards))
        except Exception:
            logger.exception("巅峰赛识别循环异常")
            self.status_changed.emit("识别异常，详见运行日志")
        finally:
            self._thread_lock.release()

    def _recognize_board(self, image, cards: list[Roi]) -> list[dict] | None:
        """经统一 OCR 队列识别名条，失败返回 None（签名重置由实时循环调用方处理）。"""
        hero_names = list(self._hero_names_provider())
        rois = [list(roi) for roi in derive_name_rois(cards)]
        task = self._capture_service.submit_ocr_task(
            image,
            hero_names=hero_names,
            template_name="hero_selection",
            rois=rois,
            match_template=False,
        )
        if not task.completed.wait(OCR_WAIT_TIMEOUT_SECONDS):
            logger.warning("巅峰赛 OCR 超时（%s 秒）", OCR_WAIT_TIMEOUT_SECONDS)
            self.status_changed.emit("识别超时，下一拍重试")
            return None
        outcome = (task.result or {}).get("outcome")
        if outcome != "matched":
            self.status_changed.emit(f"识别未完成（{outcome}），下一拍重试")
            return None
        return (task.result or {}).get("ocr_results") or []

    def _publish_pool(self, ocr_results: list[dict], card_count: int) -> None:
        """整理候选池快照并推送面板；禁选阶段快照留作已禁差集基准。

        组装全程持锁：本方法可由识别线程、导入线程与 GUI 确认并发进入，
        无锁时两个发布可能把 _last_board/_ban_names 交错成跨牌面组合。
        """
        with self._state_lock:
            self._last_board = (ocr_results, card_count)
            snapshot = parse_pool(ocr_results, card_count, self._ban_names, self._resolutions)
            if snapshot.stage == "ban":
                self._ban_names = snapshot.names
        self.pool_updated.emit(snapshot)

    def confirm_pending(self, slot: int, name: str) -> None:
        """人工确认一个待确认槽位；有效性由 parse_pool 校验，确认后立即重发快照。"""
        with self._state_lock:
            self._resolutions[slot] = name
            last_board = self._last_board
        if last_board is not None:
            self._publish_pool(*last_board)

    # ── 手动图片导入 ──────────────────────────────────────────────────

    def recognize_image_file(self, file_path: str) -> None:
        """对本地截图做一次完整识别（不影响循环签名与标准任务挂起状态）。"""
        threading.Thread(
            target=self._do_file_recognition,
            args=(file_path,),
            daemon=True,
            name="peak-select-import",
        ).start()

    def _do_file_recognition(self, file_path: str) -> None:
        if not self._import_lock.acquire(blocking=False):
            self.status_changed.emit("已有图片识别进行中，请稍候")
            return
        try:
            try:
                image = load_local_image(file_path)
            except Exception as error:
                logger.warning("巅峰赛导入图片加载失败 %s: %s", file_path, error)
                self.status_changed.emit(f"图片加载失败：{error}")
                return
            frame = cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)
            cards = detect_selection_cards(frame)
            if cards is None:
                self.status_changed.emit("未在图片中检测到巅峰赛牌面（需 8~14 张卡）")
                return
            self.status_changed.emit(f"检测到 {len(cards)} 张牌面，识别中…")
            ocr_results = self._recognize_board(image, cards)
            if ocr_results is None:
                self.status_changed.emit("图片识别未完成，请重试")
                return
            self.status_changed.emit("图片识别完成")
            self._publish_pool(ocr_results, len(cards))
        except Exception:
            logger.exception("巅峰赛图片导入识别异常")
            self.status_changed.emit("图片识别异常，详见运行日志")
        finally:
            self._import_lock.release()

    # ── 标准轮询任务协调 ──────────────────────────────────────────────

    def _suspend_standard_tasks(self) -> None:
        """首次进入巅峰赛牌面时挂起标准轮询任务，记住原状态便于恢复。"""
        if self._saved_task_states is not None:
            return
        self._saved_task_states = {name: self._ocr_service.get_task_state(name).active for name in _STANDARD_POLL_TASKS}
        for name, active in self._saved_task_states.items():
            if active:
                self._ocr_service.deactivate_task(name)
                logger.debug("巅峰赛识别期间挂起轮询任务: %s", name)

    def _restore_standard_tasks(self) -> None:
        if self._saved_task_states is None:
            return
        for name, active in self._saved_task_states.items():
            if active:
                self._ocr_service.activate_task(name)
            else:
                self._ocr_service.deactivate_task(name)
        self._saved_task_states = None
        logger.debug("巅峰赛识别结束，标准轮询任务已恢复")

    def _handle_board_absent(self) -> None:
        self._miss_ticks += 1
        exiting = self._miss_ticks == BOARD_EXIT_TICKS
        with self._state_lock:
            self._signature = None
            if exiting:
                self._ban_names = ()
                self._resolutions = {}
        if exiting:
            self._restore_standard_tasks()
            self.status_changed.emit("未检测到巅峰赛选将页牌面")
