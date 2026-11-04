"""巅峰赛（2v2）选将实时识别循环：截图 → 卡位检测 → 牌面变化才 OCR。

与标准轮询并存：巅峰赛页与标准选将页共用"武将选择"标题模板，启动识别循环
即挂起标准轮询任务避免互触——hero_selection 整个会话保持挂起并加持有锁
（ADB 重连 start_poll、手动激活等外部入口在会话期间一律被拒，停止识别才
释放），match_guide 牌面出现期间挂起、自动退出时保持挂起，衔接激活由主窗口
经 board_exited 转交 PollCoordinator 负责（任务状态的写者收敛一处）。

牌面纯规则（候选池快照 parse_pool、人工确认存续验证 refresh_resolutions、
牌面签名判等 board_signature*）在 peak_board_rules.py；本文件只保留识别
循环状态机与锁/世代守卫。
"""

from __future__ import annotations

import logging
import threading

import cv2
import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal
from src.business.recognition.peak_board_rules import (
    board_signature,
    board_signature_equal,
    is_subset_in_place,
    parse_pool,
    refresh_resolutions,
    verified_resolutions_for_import,
)
from src.business.recognition.pending_stats import record_confirmation
from src.business.recognition.session_guard import SessionGuard
from src.capture.image_validation import load_local_image
from src.ocr.card_grid_detector import (
    board_row_sizes,
    derive_name_rois,
    detect_selection_cards,
    layout_is_plausible,
)
from src.ocr.roi_config import Roi

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 1500
OCR_WAIT_TIMEOUT_SECONDS = 15
# 连续多拍未检出牌面才判定离开巅峰赛页：禁将揭晓/转场会让牌面整块不可见
# （2026-10-12 实测转场连续 2 拍无卡状组件），2 拍阈值在该局误退出 3 次
# （20:46/20:50/20:57），每次都要手动重开识别；真实离开页面会持续无牌面，
# 放宽到 4 拍（约 6 秒）只让后续衔接晚约 3 秒
BOARD_EXIT_TICKS = 4
_STANDARD_POLL_TASKS = ("hero_selection", "match_guide")
# 人工确认连续未通过内容验证的拍数上限：候选阶段浮动动画会让单拍闭包
# 缺名、读数漂移，宽限期内确认保留但展示回退为识别结果；真换人/选走
# 的确认在连续失验后淘汰，防止旧确认顶在新牌上
_STALE_MISS_LIMIT = 3


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
        # 会话世代守卫（语义见 session_guard）：停止/重启后的旧拍在关键写入点前放弃
        self._session_guard = SessionGuard()
        self._signature: tuple | None = None
        self._ban_names: tuple[str, ...] = ()
        self._resolutions: dict[int, str] = {}
        # 确认槽位的读数原文指纹（确认时的 raw_name）：稳定错读的确认名可能
        # 永远不在候选闭包里，原文复现是它仍属于这张牌的验证信号
        self._resolution_raws: dict[int, str] = {}
        # 连续未通过内容验证的拍数（槽位 → 拍数），达到上限丢弃确认
        self._stale_rounds: dict[int, int] = {}
        self._last_board: tuple[list[dict], int] | None = None
        # 最近一次已发布牌面的卡位：本拍少卡且存活卡逐张原位时按它补齐（见
        # _restore_missing_cards）。禁将揭晓动画把相邻两卡焊成一块会让本拍
        # 只认回部分卡（2026-10-12 11 张盘面检出 9 张，面板跳变）
        self._last_cards: list[Roi] | None = None
        # 推迟发布的牌面（签名, OCR 结果, 卡数, 卡位）：只对卡数回升的拍扣一拍，
        # 签名复现才发布、未复现即丢弃。回升只可能是过场坏帧（模拟器旧帧/
        # 撕裂帧，2026-10-09 桑弘羊跳变事故）；回落是真实禁将/选走（游戏实测
        # 排布必然整排重排），照常立即发布，不再延迟一拍
        self._deferred_board: tuple[tuple, list[dict], int, list[Roi]] | None = None
        self._miss_ticks = 0
        self._saved_task_states: dict[str, bool] | None = None

    def is_running(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        # 上一轮停止后可能仍有在途识别线程，重置须与其互斥
        with self._state_lock:
            self._session_guard.begin()  # 作废上一会话的在途旧拍
            self._clear_session_state()
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
            self._session_guard.begin()
            self._clear_session_state(keep_signature=True, keep_miss_ticks=True)
        # 先解除持有再恢复：顺序反了恢复激活会被自己的持有拒绝
        self._ocr_service.set_task_hold("hero_selection", False)
        self._restore_standard_tasks()

    def _clear_session_state(self, *, keep_signature: bool = False, keep_miss_ticks: bool = False) -> None:
        """会话边界整体清空牌面上下文（调用方持 _state_lock）。

        槽位号是跨牌面不稳定键，残留确认会被后续拍或导入按槽位号盲目套用
        （2026-09-30 卓文君事故）；三处边界（start/stop/牌面退出）统一走本
        方法，新增会话字段只改这里。keep_* 为各边界既有豁免：stop 后无拍
        运行（保留无差异），牌面退出需缺席计数继续累加以精确等于判定退出。
        """
        if not keep_signature:
            self._signature = None
        self._ban_names = ()
        self._resolutions = {}
        self._resolution_raws = {}
        self._stale_rounds = {}
        self._last_board = None
        self._last_cards = None
        self._deferred_board = None
        if not keep_miss_ticks:
            self._miss_ticks = 0

    # ── 识别循环 ──────────────────────────────────────────────────────

    def _on_tick(self) -> None:
        if not self._thread_lock.acquire(blocking=False):
            return  # 上一拍（截图+OCR）尚未完成，跳过本轮
        threading.Thread(target=self._do_work, daemon=True, name="peak-select-watch").start()

    def _do_work(self) -> None:
        session = self._session_guard.current()  # 本拍所属会话世代；start/stop 后旧拍即过期
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
                if not self._session_guard.is_current(session):
                    return  # 停止/重启后的旧拍：不检测、不清理、不发布
            frame = cv2.cvtColor(np.array(result.convert("RGB")), cv2.COLOR_RGB2BGR)
            cards = detect_selection_cards(frame)
            if cards is None:
                self._handle_board_absent(session)
                return
            cards = self._restore_missing_cards(cards)
            if not layout_is_plausible(cards):
                row_sizes = board_row_sizes(cards) or (0, 0)
                # 排布不是游戏可能出现的形状（如 9 张成 5+4）：本拍漏检/拼接错误，
                # 既不发布也不计入缺席——面板保持上一拍内容，日志留痕便于回溯
                logger.info(
                    "巅峰赛牌面 %d 张排布不可信（上%d下%d，应为上%d下%d），本拍跳过不发",
                    len(cards), row_sizes[0], row_sizes[1], len(cards) // 2, len(cards) - len(cards) // 2,
                )
                return

            signature = board_signature(cards)
            with self._state_lock:
                if not self._session_guard.is_current(session):
                    return
                self._miss_ticks = 0
                # 每拍确认牌面存在即幂等挂起（含签名未变的拍）：外部如 ADB 重连
                # start_poll 会重新激活标准任务，若只在签名变化时挂起，unchanged
                # 短路会让垃圾轮询在巅峰页常驻
                self._suspend_standard_tasks()
                unchanged = board_signature_equal(signature, self._signature)
            if unchanged:
                with self._state_lock:
                    confirmed = self._deferred_board
                    self._deferred_board = None
                if confirmed is not None:
                    # 回升牌面在紧邻下一拍原样复现：非单拍坏帧，补发布
                    self._publish_pool(confirmed[1], confirmed[2], confirmed[3], session=session)
                return  # 牌面未变化，沿用上一次结果
            ocr_results = self._recognize_board(result, cards)
            if ocr_results is None:
                # 识别失败清签名，下一拍强制重试；仅实时循环路径，图片导入不动签名
                with self._state_lock:
                    if self._session_guard.is_current(session):
                        self._signature = None
                return
            with self._state_lock:
                if not self._session_guard.is_current(session):
                    return  # 停止/重启后的旧拍：不写签名、不沿用确认、不发布
                self._signature = signature
                # 人工确认逐拍做内容验证：单拍闭包缺名或自动结论翻转不清确认，
                # 连续多拍验证不到（真换人/选走）才淘汰
                self._refresh_resolutions(ocr_results)
                card_count = len(cards)
                unconfirmed = self._deferred_board
                self._deferred_board = None
                last_count = self._last_board[1] if self._last_board else None
                # 只扣回升：牌池不会凭空变多，回升必是过场坏帧；回落（禁将/选走
                # 推进）由 _restore_missing_cards 与排布校验判真伪后立即发布
                held = last_count is not None and card_count > last_count
                if held:
                    self._deferred_board = (signature, ocr_results, card_count, cards)
            if unconfirmed is not None:
                logger.info("巅峰赛扣住复核的 %d 张牌面未复现，按过场坏帧丢弃", unconfirmed[2])
            if held:
                logger.info(
                    "巅峰赛牌面 %d 张较已发布 %d 张回升，疑似过场坏帧，扣住待下一拍复核（复现即发布）",
                    card_count, last_count,
                )
                return
            self._publish_pool(ocr_results, card_count, cards, session=session)
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
            logger.warning("巅峰赛 OCR 超时（%s 秒），作废该任务", OCR_WAIT_TIMEOUT_SECONDS)
            # 弃单：置取消标志并释放整帧引用——worker 被官方导入占住期间，
            # 队列滞留任务到取出才释放的话内存峰值仍是 O(占用时长)
            task.abandon()
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

    def _restore_missing_cards(self, cards: list[Roi]) -> list[Roi]:
        """本拍少卡且存活卡逐张原位时，按上一拍已发布卡位补齐后返回。

        真实禁将/选走会让剩余卡整排重排（位移 ≥ 半个卡距 276/2），所以"存活卡
        原位对应"可判定为本拍漏检而非真变少；补齐后本拍签名与上一拍一致，走
        unchanged 短路，面板既不掉张也不多跑一次 OCR（2026-10-12 11→9 事故）。
        无重排的"真消失"与漏检在几何上不可区分，此处按漏检处理并留日志：
        游戏实测排布总会重排（8/9/10/11 卡局实测排布分别为 4+4/4+5/5+5/5+6）。
        """
        with self._state_lock:
            previous = self._last_cards
        if previous is None or len(cards) >= len(previous):
            return cards
        if not is_subset_in_place(cards, previous):
            return cards
        logger.info(
            "巅峰赛牌面 %d 张较已发布 %d 张少且存活卡原位一致，按上一拍卡位补齐",
            len(cards), len(previous),
        )
        return list(previous)

    def _publish_pool(
        self, ocr_results: list[dict], card_count: int, cards: list[Roi] | None = None, *, session: int,
    ) -> None:
        """整理候选池快照并推送面板；禁选阶段快照留作已禁差集基准。

        组装全程持锁：本方法可由识别线程、导入线程与 GUI 确认并发进入，
        无锁时两个发布可能把 _last_board/_ban_names 交错成跨牌面组合。
        cards 为本次发布牌面的卡位，供下一拍漏检补齐；重发同一牌面（人工确认）
        与离线图片导入传 None，表示沿用既有实时卡位。
        session 为发起方取的世代快照：锁内校验，停止/重启后的旧发布不写
        状态、不推送（否则旧拍会把 _last_board 复活，待确认与人工确认随之污染）。
        """
        with self._state_lock:
            if not self._session_guard.is_current(session):
                # 本应绝迹的竞态（旧拍/停止交错）：debug 留痕便于复现排查，不刷正常停止路径
                logger.debug("发布被会话世代守卫拦截（session=%s），不写状态不推送", session)
                return
            self._last_board = (ocr_results, card_count)
            if cards is not None:
                self._last_cards = list(cards)
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
        无在识别中的牌面（退出/停止后残留的待确认行）时拒绝确认：槽位号
        是跨牌面不稳定键，旧确认写入会污染后续识别与图片导入。
        """
        with self._state_lock:
            last_board = self._last_board
            accepted = last_board is not None
            if accepted:
                session = self._session_guard.current()
                self._resolutions[slot] = name
                self._stale_rounds.pop(slot, None)
                raw_name = ""
                slot_candidates: list[str] = []
                if 0 <= slot < len(last_board[0]):
                    raw_name = str(last_board[0][slot].get("raw_name", "")).strip()
                    slot_candidates = [
                        str(c) for c in (last_board[0][slot].get("candidates") or [])
                    ]
                self._resolution_raws[slot] = raw_name
        if not accepted:
            logger.info(
                "人工确认被拒绝：当前无在识别中的巅峰赛牌面（槽位 %d → %s）", slot + 1, name
            )
            self.status_changed.emit("牌面已不在识别中，确认未生效")
            return
        record_confirmation(raw_name, name, slot_candidates)
        self._publish_pool(*last_board, session=session)

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
        # 绑定导入发起时的世代：识别耗时数十秒，期间停止识别则不回写旧牌面
        session = self._session_guard.current()
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
            with self._state_lock:
                if not self._session_guard.is_current(session):
                    # 导入在途期间停止/重启换代：确认表已归新会话所有，旧图
                    # 校验回写会删除或错位新会话刚做的确认（与 _publish_pool
                    # 同一守卫口径，debug 留痕不刷正常路径）
                    logger.debug(
                        "图片导入回写被会话世代守卫拦截（session=%s），不写确认表", session,
                    )
                    return
                old_slots = set(self._resolutions)
                self._stale_rounds = {}
                (
                    self._resolutions,
                    self._resolution_raws,
                    dropped,
                ) = verified_resolutions_for_import(
                    self._resolutions, self._resolution_raws, ocr_results
                )
                migrated = len(set(self._resolutions) - old_slots)
            if dropped:
                self.status_changed.emit(
                    f"图片识别完成（{dropped} 条旧人工确认与该牌面不符，已丢弃）"
                )
            elif migrated:
                self.status_changed.emit(
                    f"图片识别完成（{migrated} 条旧人工确认已按牌面重新定位）"
                )
            else:
                self.status_changed.emit("图片识别完成")
            self._publish_pool(ocr_results, len(cards), session=session)
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
        出现期间挂起，自动退出时保持挂起，衔接激活由 PollCoordinator 按轮询
        状态统一负责。
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
            if not self._session_guard.is_current(session):
                return  # 停止/重启后的旧拍：不累计缺席、不清理状态、不发信号
            self._miss_ticks += 1
            exiting = self._miss_ticks == BOARD_EXIT_TICKS
            self._signature = None  # 缺席即无在识别牌面，退出拍随 clear 再清一次
            self._deferred_board = None  # 复核窗口只认紧邻下一拍，缺席即作废
            if exiting:
                self._clear_session_state(keep_miss_ticks=True)
        if exiting:
            # match_guide 的退出衔接（激活对局轮询）归 PollCoordinator.handle_peak_exit，
            # 经 board_exited 到达；watcher 退出路径不写任务状态
            self.board_exited.emit()
            self.status_changed.emit("未检测到巅峰赛选将页牌面")
