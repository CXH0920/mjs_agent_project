"""巅峰赛（2v2）选将实时识别循环：截图 → 卡位检测 → 牌面变化才 OCR。

与标准轮询并存：巅峰赛页与标准选将页共用"武将选择"标题模板，启动识别循环
即挂起标准轮询任务避免互触——hero_selection 整个会话保持挂起（停止识别才
恢复），match_guide 牌面出现期间挂起、自动退出时恢复原状态并由主窗口衔接
激活；自动退出另发 board_exited 供主窗口使用。
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
# 牌面签名量化步长：候选阶段卡面带 idle 浮动动画，实测剪影 bbox 逐拍漂移
# y ±3~4px、尺寸 ±5px（2026-09-06 日志实测），步长须覆盖 2 倍漂移幅度，
# 否则签名逐拍翻转误判"新牌面"（清确认+全量 OCR）；真实换牌表现为卡数
# 变化或整排重排（位移 ≥ 一个卡位宽），远超步长不会漏
SIGNATURE_POSITION_QUANTUM_PX = 8
SIGNATURE_SIZE_QUANTUM_PX = 16
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


def carry_over_resolutions(
    old_resolutions: dict[int, str],
    ocr_results: list[dict],
) -> dict[int, str]:
    """新牌面上按内容沿用人工确认：确认跟着武将走，不跟槽位走。

    沿用判据与 parse_pool 的生效判据对齐（确认名仍在该槽候选集内才保留），
    因此只可能保留仍然生效的确认：槽位未重排时原地保留，重排时迁移到内容
    匹配的槽位，内容消失（换人/选走）的确认自然失效。候选集同时命中多个
    已确认名的歧义槽位保守丢弃。
    """
    remaining = set(old_resolutions.values())
    carried: dict[int, str] = {}
    for slot, item in enumerate(ocr_results):
        if item.get("resolution") not in _CONFIRM_RESOLUTIONS:
            continue  # 已自动确认的槽位不接入人工确认
        candidates = {str(c) for c in (item.get("candidates") or [])}
        if not candidates:
            continue
        old_name = old_resolutions.get(slot)
        if old_name in candidates:
            carried[slot] = old_name
            continue
        hits = remaining & candidates
        if len(hits) == 1:
            name = next(iter(hits))
            carried[slot] = name
            remaining.discard(name)  # 同名不扩散到多个槽位
    return carried


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
    board_exited = Signal()

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
        # 会话世代：start/stop 各递增一次；在途识别拍在挂起、写签名、发布等
        # 关键写入点前校验世代，停止/重启后的旧拍直接放弃，防止旧拍反向挂起
        # 标准任务或把旧签名/旧快照写回新会话
        self._session = 0
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
            self._session += 1  # 作废上一会话的在途旧拍
            self._signature = None
            self._ban_names = ()
            self._resolutions = {}
            self._last_board = None
        self._miss_ticks = 0
        # 挂起在启动瞬间生效而非检测到牌面后：首拍之前标准轮询用固定 ROI 在
        # 巅峰页只会跑出垃圾结果，还可能误触冷却与自动跳页
        self._suspend_standard_tasks()
        self._ocr_service.clear_task_cooldown("hero_selection")
        self._ocr_service.clear_task_cooldown("match_guide")
        # 作废在途轮询：点击开始前已发出的那一轮会在挂起之后落地，
        # 其冷却/激活/跳转副作用会对抗刚刚建立的挂起状态
        self._ocr_service.invalidate_inflight_poll()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        # 先作废在途旧拍再恢复任务：未执行到挂起点的旧拍会因世代过期放弃，
        # 已挂起的由本次恢复收回，消除"停止后被旧拍重新挂起"的竞态
        with self._state_lock:
            self._session += 1
        self._restore_standard_tasks()

    # ── 识别循环 ──────────────────────────────────────────────────────

    def _on_tick(self) -> None:
        if not self._thread_lock.acquire(blocking=False):
            return  # 上一拍（截图+OCR）尚未完成，跳过本轮
        threading.Thread(target=self._do_work, daemon=True, name="peak-select-watch").start()

    def _do_work(self) -> None:
        session = self._session  # 本拍所属会话世代；start/stop 后旧拍即过期
        try:
            capture = self._capture_service.capture
            if not capture:
                self.status_changed.emit("未连接模拟器")
                return
            ok, result, failure_kind = self._capture_service.capture_for_poll(capture)
            if not ok:
                self.status_changed.emit(f"截图失败({failure_kind}): {result}")
                return
            with self._state_lock:
                if session != self._session:
                    return  # 停止/重启后的旧拍：不检测、不清理、不发布
            frame = cv2.cvtColor(np.array(result.convert("RGB")), cv2.COLOR_RGB2BGR)
            cards = detect_selection_cards(frame)
            if cards is None:
                self._handle_board_absent(session)
                return

            signature = board_signature(cards)
            with self._state_lock:
                if session != self._session:
                    return
                self._miss_ticks = 0
                # 每拍确认牌面存在即幂等挂起（含签名未变的拍）：外部如 ADB 重连
                # start_poll 会重新激活标准任务，若只在签名变化时挂起，unchanged
                # 短路会让垃圾轮询在巅峰页常驻
                self._suspend_standard_tasks()
                unchanged = signature == self._signature
            if unchanged:
                return  # 牌面未变化，沿用上一次结果
            ocr_results = self._recognize_board(result, cards)
            if ocr_results is None:
                # 识别失败清签名，下一拍强制重试；仅实时循环路径，图片导入不动签名
                with self._state_lock:
                    if session == self._session:
                        self._signature = None
                return
            with self._state_lock:
                if session != self._session:
                    return  # 停止/重启后的旧拍：不写签名、不沿用确认、不发布
                self._signature = signature
                # 新牌面：人工确认按内容沿用而非清空——候选阶段浮动动画会让
                # 签名假性翻转，无条件清空会反复丢确认；沿用基准取当前值，
                # 覆盖 OCR 期间用户对新牌面 pending 的点击
                self._resolutions = carry_over_resolutions(self._resolutions, ocr_results)
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
        """挂起当前活跃的标准轮询任务，可随牌面重现重复调用（幂等）。

        hero_selection 整个识别会话保持挂起（巅峰模式内标准选将推荐不参与，
        杜绝每局重进空窗期的垃圾轮询），仅停止识别时恢复；match_guide 牌面
        出现期间挂起，自动退出时恢复原状态并由主窗口按轮询状态衔接激活。
        """
        if self._saved_task_states is None:
            self._saved_task_states = {
                name: self._ocr_service.get_task_state(name).active
                for name in _STANDARD_POLL_TASKS
            }
        for name in _STANDARD_POLL_TASKS:
            if self._ocr_service.get_task_state(name).active:
                self._ocr_service.deactivate_task(name)
                logger.debug("巅峰赛识别期间挂起轮询任务: %s", name)

    def _restore_match_guide(self) -> None:
        """牌面自动退出时仅恢复 match_guide 原状态；hero_selection 留待停止识别恢复。"""
        if self._saved_task_states is None:
            return
        if self._saved_task_states["match_guide"]:
            self._ocr_service.activate_task("match_guide")
        else:
            self._ocr_service.deactivate_task("match_guide")

    def _restore_standard_tasks(self) -> None:
        """停止识别时恢复全部标准任务原状态。"""
        if self._saved_task_states is None:
            return
        for name, active in self._saved_task_states.items():
            if active:
                self._ocr_service.activate_task(name)
            else:
                self._ocr_service.deactivate_task(name)
        self._saved_task_states = None
        logger.debug("巅峰赛识别结束，标准轮询任务已恢复")

    def _handle_board_absent(self, session: int) -> None:
        with self._state_lock:
            if session != self._session:
                return  # 停止/重启后的旧拍：不累计缺席、不清理状态、不发信号
            self._miss_ticks += 1
            exiting = self._miss_ticks == BOARD_EXIT_TICKS
            self._signature = None
            if exiting:
                self._ban_names = ()
                self._resolutions = {}
        if exiting:
            self._restore_match_guide()
            # match_guide 的激活与跳转属界面策略，由主窗口的 board_exited 处理器完成
            self.board_exited.emit()
            self.status_changed.emit("未检测到巅峰赛选将页牌面")
