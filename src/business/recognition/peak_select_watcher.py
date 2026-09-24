"""巅峰赛（2v2）选将实时识别循环：截图 → 卡位检测 → 牌面变化才 OCR。

与标准轮询并存：巅峰赛页与标准选将页共用"武将选择"标题模板，启动识别循环
即挂起标准轮询任务避免互触——hero_selection 整个会话保持挂起并加持有锁
（ADB 重连 start_poll、手动激活等外部入口在会话期间一律被拒，停止识别才
释放），match_guide 牌面出现期间挂起、自动退出时恢复原状态并由主窗口衔接
激活；自动退出另发 board_exited 供主窗口使用。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from src.business.recognition.pending_stats import record_confirmation
from src.capture.image_validation import load_local_image
from src.ocr.card_grid_detector import derive_name_rois, detect_selection_cards
from src.ocr.roi_config import Roi

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 1500
OCR_WAIT_TIMEOUT_SECONDS = 15
# 连续多拍未检出牌面才判定离开巅峰赛页，避免翻页动画误恢复标准任务
BOARD_EXIT_TICKS = 2
# 牌面签名容差：候选阶段卡面带 idle 浮动动画，实测剪影 bbox 逐拍漂移
# y ±3~4px、尺寸 ±5px（2026-09-06 日志实测），容差取 2 倍幅度。不用分桶
# 量化：基点贴近量化边界时签名逐拍翻转误判"新牌面"（2026-09-21 实测
# x=1179↔1180 跨 round 量化 147.5 边界，54 秒内同一牌面被全量重识别约
# 15 次），逐卡容差比较无边界；真实换牌表现为卡数变化或整排重排（位移
# ≥ 一个卡位宽），远超容差不会漏
SIGNATURE_POSITION_TOLERANCE_PX = 8
SIGNATURE_SIZE_TOLERANCE_PX = 16
# 14 张为禁选阶段（双方尚未提交禁选），8~11 张为候选阶段
_BAN_PHASE_MIN_CARDS = 12
_STANDARD_POLL_TASKS = ("hero_selection", "match_guide")
_CONFIRM_RESOLUTIONS = {"unresolved", "unknown", "conflict"}
# 人工确认连续未通过内容验证的拍数上限：候选阶段浮动动画会让单拍闭包
# 缺名、读数漂移，宽限期内确认保留但展示回退为识别结果；真换人/选走
# 的确认在连续失验后淘汰，防止旧确认顶在新牌上
_STALE_MISS_LIMIT = 3


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

    resolutions 为人工确认（槽位序号 → 武将名），优先于一切自动结论：
    确认落定后即使本拍闭包缺名（读数抖动）或自动决胜出别的猜测结论，
    也按人工结果展示；过期确认由 watcher 的逐拍内容验证剔除后才会消失。
    """
    names: list[str] = []
    pending: list[dict] = []
    for index, item in enumerate(ocr_results):
        manual = (resolutions or {}).get(index)
        if manual:
            names.append(manual)
            continue
        name = str(item.get("name") or "").strip()
        if name and item.get("resolution") not in _CONFIRM_RESOLUTIONS:
            names.append(name)
            continue
        raw_name = str(item.get("raw_name") or "").strip()
        candidates = [str(c) for c in (item.get("candidates") or [])]
        if not (raw_name or candidates):
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


def refresh_resolutions(
    resolutions: dict[int, str],
    raws: dict[int, str],
    ocr_results: list[dict],
) -> tuple[dict[int, str], dict[int, str], set[int]]:
    """逐拍验证人工确认的内容存续，返回 (沿用映射, 读数指纹, 未验证槽位)。

    验证判据（满足其一即视为仍是同一张牌）：确认名仍在本槽候选闭包；
    确认时的读数原文在本槽复现（稳定错读的确认名可能永远不在候选闭包，
    读数原文是其内容指纹）；确认名或读数在其它槽位唯一命中（牌面重排
    后迁移槽位）。未验证的确认原槽保留进宽限，由调用方按连续未验证拍
    数淘汰——浮动动画导致的单拍闭包缺名不应丢确认。
    """
    slots = [
        (
            str(item.get("raw_name") or "").strip(),
            {str(c) for c in (item.get("candidates") or [])},
        )
        for item in ocr_results
    ]
    carried: dict[int, str] = {}
    carried_raws: dict[int, str] = {}
    unverified: set[int] = set()
    claimed: set[int] = set()
    all_names = set(resolutions.values())

    def locate(old_slot: int, name: str, raw: str) -> int | None:
        # 单字读数信息量不足，不同牌极易读出同字，不作指纹使用
        raw_matchable = len(raw) >= 2
        if old_slot < len(slots) and old_slot not in claimed:
            slot_raw, candidates = slots[old_slot]
            if name in candidates or (raw_matchable and slot_raw == raw):
                return old_slot
        # 闭包同时命中多个已确认名的歧义槽不猜测归属（无法断定哪张牌在哪）
        other_names = all_names - {name}
        closure_hits = [
            slot
            for slot, (_, candidates) in enumerate(slots)
            if slot != old_slot
            and slot not in claimed
            and name in candidates
            and not other_names & candidates
        ]
        if len(closure_hits) == 1:
            return closure_hits[0]
        if not raw_matchable:
            return None
        raw_hits = [
            slot
            for slot, (slot_raw, _) in enumerate(slots)
            if slot != old_slot and slot not in claimed and slot_raw == raw
        ]
        return raw_hits[0] if len(raw_hits) == 1 else None

    for old_slot, name in resolutions.items():
        raw = raws.get(old_slot, "")
        target = locate(old_slot, name, raw)
        if target is None:
            carried[old_slot] = name
            carried_raws[old_slot] = raw
            unverified.add(old_slot)
            continue
        carried[target] = name
        carried_raws[target] = raw
        claimed.add(target)
    return carried, carried_raws, unverified


def board_signature(cards: list[Roi]) -> tuple:
    """生成牌面布局签名（原始 bbox 元组）；判等必须用 board_signature_equal。"""
    return tuple(cards)


def board_signature_equal(left: tuple | None, right: tuple | None) -> bool:
    """逐卡容差比较：漂移在容差内判同板，卡数变化或真实位移判新板。"""
    if left is None or right is None or len(left) != len(right):
        return False
    return all(
        abs(a[0] - b[0]) <= SIGNATURE_POSITION_TOLERANCE_PX
        and abs(a[1] - b[1]) <= SIGNATURE_POSITION_TOLERANCE_PX
        and abs(a[2] - b[2]) <= SIGNATURE_SIZE_TOLERANCE_PX
        and abs(a[3] - b[3]) <= SIGNATURE_SIZE_TOLERANCE_PX
        for a, b in zip(left, right, strict=True)
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
        # 确认槽位的读数原文指纹（确认时的 raw_name）：稳定错读的确认名可能
        # 永远不在候选闭包里，原文复现是它仍属于这张牌的验证信号
        self._resolution_raws: dict[int, str] = {}
        # 连续未通过内容验证的拍数（槽位 → 拍数），达到上限丢弃确认
        self._stale_rounds: dict[int, int] = {}
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
            self._resolution_raws = {}
            self._stale_rounds = {}
            self._last_board = None
        self._miss_ticks = 0
        # 挂起在启动瞬间生效而非检测到牌面后：首拍之前标准轮询用固定 ROI 在
        # 巅峰页只会跑出垃圾结果，还可能误触冷却与自动跳页
        self._suspend_standard_tasks()
        # hero_selection 加持有锁：牌面缺席期（等候进局/对局间隙）每拍重挂起
        # 不生效，持有让 ADB 重连 start_poll 等外部激活入口在源头即被拒绝
        self._ocr_service.set_task_hold("hero_selection", True)
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
        # 先解除持有再恢复：顺序反了恢复激活会被自己的持有拒绝
        self._ocr_service.set_task_hold("hero_selection", False)
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
                unchanged = board_signature_equal(signature, self._signature)
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
                # 人工确认逐拍做内容验证：单拍闭包缺名或自动结论翻转不清确认，
                # 连续多拍验证不到（真换人/选走）才淘汰
                self._refresh_resolutions(ocr_results)
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
            # 独立页名：日志与错法频次 scene 归属巅峰渠道，不与选将轮询混淆；
            # 布局由本拍派生 rois 整页覆盖，此处页名仅用于日志/数据归档
            template_name="peak_board",
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

    def _refresh_resolutions(self, ocr_results: list[dict]) -> None:
        """逐拍验证人工确认存续（调用方持 _state_lock），连续失验达上限才丢弃。"""
        if not self._resolutions:
            return
        carried, carried_raws, unverified = refresh_resolutions(
            self._resolutions, self._resolution_raws, ocr_results
        )
        for slot in list(self._stale_rounds):
            if slot not in unverified:
                del self._stale_rounds[slot]  # 内容重新命中，解除宽限
        for slot in unverified:
            rounds = self._stale_rounds.get(slot, 0) + 1
            if rounds < _STALE_MISS_LIMIT:
                self._stale_rounds[slot] = rounds
                continue
            logger.info(
                "巅峰赛人工确认连续 %d 拍未在牌面验证到，剔除槽位 %d：%s",
                rounds, slot + 1, carried[slot],
            )
            del carried[slot]
            carried_raws.pop(slot, None)
            self._stale_rounds.pop(slot, None)
        self._resolutions = carried
        self._resolution_raws = carried_raws

    def _publish_pool(self, ocr_results: list[dict], card_count: int) -> None:
        """整理候选池快照并推送面板；禁选阶段快照留作已禁差集基准。

        组装全程持锁：本方法可由识别线程、导入线程与 GUI 确认并发进入，
        无锁时两个发布可能把 _last_board/_ban_names 交错成跨牌面组合。
        """
        with self._state_lock:
            self._last_board = (ocr_results, card_count)
            # 宽限期内内容存疑的确认不参与展示，回退为该槽识别结果
            display = {
                slot: name
                for slot, name in self._resolutions.items()
                if slot not in self._stale_rounds
            }
            snapshot = parse_pool(ocr_results, card_count, self._ban_names, display)
            if snapshot.stage == "ban":
                self._ban_names = snapshot.names
        self.pool_updated.emit(snapshot)

    def confirm_pending(self, slot: int, name: str) -> None:
        """人工确认一个待确认槽位；确认后立即重发快照。

        确认名与该槽读数原文一并登记：原文是这张牌的内容指纹，供后续拍
        验证确认仍然有效（稳定错读的确认名可能永远不在候选闭包里）。
        """
        with self._state_lock:
            self._resolutions[slot] = name
            self._stale_rounds.pop(slot, None)
            last_board = self._last_board
            raw_name = ""
            slot_candidates: list[str] = []
            if last_board is not None and 0 <= slot < len(last_board[0]):
                raw_name = str(last_board[0][slot].get("raw_name", "")).strip()
                slot_candidates = [
                    str(c) for c in (last_board[0][slot].get("candidates") or [])
                ]
            self._resolution_raws[slot] = raw_name
        record_confirmation(raw_name, name, slot_candidates)
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
                self._resolution_raws = {}
                self._stale_rounds = {}
        if exiting:
            self._restore_match_guide()
            # match_guide 的激活与跳转属界面策略，由主窗口的 board_exited 处理器完成
            self.board_exited.emit()
            self.status_changed.emit("未检测到巅峰赛选将页牌面")
