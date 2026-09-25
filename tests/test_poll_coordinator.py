"""轮询协调器的行为测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.ui.app.poll_coordinator import PollCoordinator, PollOutcome, PollResult, PollTaskResult


class _Signal:
    def __init__(self) -> None:
        self._handlers = []

    def connect(self, handler) -> None:
        self._handlers.append(handler)

    def emit(self, *args) -> None:
        for handler in self._handlers:
            handler(*args)


class _OcrService:
    def __init__(self) -> None:
        self.poll_tick = _Signal()
        self.poll_state_changed = _Signal()
        self.config = {"mumu_ocr_poll_mode": True, "mumu_ocr_poll_interval": 3}
        self.poll_generation = 4
        self.started: list[int] = []
        self.completed: list[tuple] = []
        self.stop_count = 0

    def start_poll(self, interval: int) -> None:
        self.started.append(interval)

    def stop_poll(self) -> None:
        self.stop_count += 1

    def complete_poll(self, *args) -> None:
        self.completed.append(args)


class _CaptureService:
    def __init__(self, capture) -> None:
        self.capture = capture
        self.connection_failures: list[tuple[object, str]] = []

    def sync_poll_connection_state(self, capture, detail: str) -> None:
        self.connection_failures.append((capture, detail))


def test_sync_with_connection_only_starts_poll_for_connected_capture() -> None:
    capture = SimpleNamespace(connected=True)
    ocr_service = _OcrService()
    coordinator = PollCoordinator(_CaptureService(capture), ocr_service, lambda: [])

    coordinator.sync_with_connection()
    capture.connected = False
    coordinator.sync_with_connection()

    assert ocr_service.started == [3000]
    assert ocr_service.stop_count == 1


def test_consume_result_discards_stale_result_and_notifies_after_completion() -> None:
    capture = object()
    capture_service = _CaptureService(capture)
    ocr_service = _OcrService()
    coordinator = PollCoordinator(capture_service, ocr_service, lambda: [])
    received: list[PollResult] = []
    coordinator.poll_result_ready.connect(received.append)

    coordinator._consume_poll_result(PollResult(
        4,
        PollOutcome.RETRYABLE_CONNECTION,
        "设备离线",
        capture,
    ))
    coordinator._consume_poll_result(PollResult(
        3,
        PollOutcome.MATCHED,
        capture=capture,
    ))

    assert ocr_service.completed == [(4, "retryable_connection", "设备离线")]
    assert capture_service.connection_failures == [(capture, "设备离线")]
    assert received == [PollResult(4, PollOutcome.RETRYABLE_CONNECTION, "设备离线", capture)]


def test_match_guide_requires_three_confirmed_names_before_navigation() -> None:
    insufficient = PollCoordinator._validate_match_guide_result(PollTaskResult(
        PollOutcome.MATCHED,
        ocr_results=[
            {"name": "曹操", "resolution": "exact"},
            {"name": "张辽", "resolution": "unique_similarity"},
            {"name": "", "raw_name": "夏侯", "resolution": "unresolved"},
        ],
    ))
    sufficient = PollCoordinator._validate_match_guide_result(PollTaskResult(
        PollOutcome.MATCHED,
        ocr_results=[
            {"name": "曹操", "resolution": "exact"},
            {"name": "张辽", "resolution": "unique_similarity"},
            {"name": "郭嘉", "resolution": "slot_unique"},
        ],
    ))

    assert insufficient.outcome is PollOutcome.HEALTHY_NO_MATCH
    assert "已确认角色不足: 2/3" in insufficient.detail
    assert sufficient.outcome is PollOutcome.MATCHED


def test_match_guide_confirmed_count_remains_compatible_with_legacy_results() -> None:
    result = PollCoordinator._validate_match_guide_result(PollTaskResult(
        PollOutcome.MATCHED,
        ocr_results=[{"name": "曹操"}, {"name": "张辽"}, {"name": "郭嘉"}],
    ))

    assert result.outcome is PollOutcome.MATCHED


def test_match_guide_fallback_clean_reads_still_navigate() -> None:
    """模板未命中但 ROI 与页面对齐（如对局页模板过期）时，兜底读数完整精确，
    达到质量门槛仍应触发对局攻略导入——2026-09-24 对局页实测形态。"""
    result = PollCoordinator._validate_match_guide_result(PollTaskResult(
        PollOutcome.MATCHED,
        ocr_results=[
            {"name": "刘协", "resolution": "exact", "length_mode": "complete", "confidence": 0.9993},
            {"name": "孟姚", "resolution": "exact", "length_mode": "complete", "confidence": 0.9998},
            {"name": "", "raw_name": "", "resolution": "unknown", "length_mode": "unknown"},
            {"name": "宋玉", "resolution": "exact", "length_mode": "complete", "confidence": 0.9978},
            {"name": "卢莫愁", "resolution": "exact", "length_mode": "complete", "confidence": 0.9994},
        ],
        template_matched=False,
    ))

    assert result.outcome is PollOutcome.MATCHED


def test_match_guide_fallback_degraded_reads_blocked() -> None:
    """模板未命中且读数靠碎片/低置信拼凑（错位 ROI 形态，2026-09-22 巅峰页
    实测）时，确认数再多也不得触发对局攻略自动跳转。"""
    result = PollCoordinator._validate_match_guide_result(PollTaskResult(
        PollOutcome.MATCHED,
        ocr_results=[
            # 相似度纠错读数（'庞媛'→庞煖），置信度低于门槛
            {"name": "庞煖", "resolution": "multi_similarity", "length_mode": "complete", "confidence": 0.89},
            # 碎片拼接槽位，未确认
            {"name": "", "raw_name": "西汉陈", "candidates": ["西汉陈"], "resolution": "unresolved"},
            # 完整但置信度不足的直读
            {"name": "张辽", "resolution": "exact", "length_mode": "complete", "confidence": 0.81},
            # 缺 length_mode 的旧形态读数
            {"name": "郭嘉", "resolution": "exact", "confidence": 0.99},
        ],
        template_matched=False,
    ))

    assert result.outcome is PollOutcome.HEALTHY_NO_MATCH
    assert "兜底读数质量不足: 0/3" in result.detail


def test_match_guide_template_hit_keeps_confirmed_count_rule() -> None:
    """模板真实命中时维持原确认数规则，不叠加读取质量门槛。"""
    result = PollCoordinator._validate_match_guide_result(PollTaskResult(
        PollOutcome.MATCHED,
        ocr_results=[
            {"name": "曹操", "resolution": "multi_similarity", "confidence": 0.7},
            {"name": "张辽", "resolution": "exact"},
            {"name": "郭嘉", "resolution": "slot_unique"},
        ],
        template_matched=True,
    ))

    assert result.outcome is PollOutcome.MATCHED


def test_fall_back_result_from_raw_defaults_to_template_matched() -> None:
    """旧载荷无 template_matched 字段时默认 True，兼容既有生产方与测试桩。"""
    result = PollTaskResult.from_raw({"outcome": "matched", "ocr_results": []})
    fallback = PollTaskResult.from_raw({
        "outcome": "matched", "ocr_results": [], "template_matched": False,
    })

    assert result.template_matched is True
    assert fallback.template_matched is False


class _TaskOcrService(_OcrService):
    """带任务状态 API 的桩：记录路由层对任务激活/失活/冷却的全部写点。"""

    def __init__(self) -> None:
        super().__init__()
        self.config.update({
            "mumu_ocr_auto_switch_tab": False,
            "mumu_hero_selection_cooldown": 180,
        })
        self.transitions: list[tuple] = []

    def set_task_cooldown(self, task_name: str, seconds: int | None = None) -> None:
        self.transitions.append(("cooldown", task_name, seconds))

    def clear_task_cooldown(self, task_name: str) -> None:
        self.transitions.append(("clear", task_name))

    def activate_task(self, task_name: str) -> None:
        self.transitions.append(("activate", task_name))

    def deactivate_task(self, task_name: str) -> None:
        self.transitions.append(("deactivate", task_name))


def _make_router() -> tuple:
    """只含路由状态的最小协调器；返回 (router, ocr, switched, loaded, guide_matched)。"""
    ocr = _TaskOcrService()
    router = PollCoordinator(_CaptureService(None), ocr, lambda: [])
    switched: list[str] = []
    loaded: list[list[dict]] = []
    guide_matched: list[object] = []
    router.page_switch_requested.connect(switched.append)
    router.hero_selection_matched.connect(loaded.append)
    router.match_guide_matched.connect(guide_matched.append)
    return router, ocr, switched, loaded, guide_matched


def test_route_hero_template_missing_deactivates_task_only() -> None:
    """hero_selection 模板缺失即失活该任务，不触碰 match_guide、不发任何界面信号。"""
    router, ocr, switched, loaded, guide_matched = _make_router()

    router._route_result(PollResult(
        4,
        PollOutcome.TEMPLATE_MISSING,
        task_results={"hero_selection": PollTaskResult(PollOutcome.TEMPLATE_MISSING)},
    ))

    assert ocr.transitions == [("deactivate", "hero_selection")]
    assert switched == []
    assert loaded == []
    assert guide_matched == []


def test_route_guide_template_missing_deactivates_and_resets_activation() -> None:
    """match_guide 模板缺失即失活并清激活时间戳，闲置超时随之失去作用点。"""
    router, ocr, switched, loaded, guide_matched = _make_router()
    router._match_guide_activated_at = 123.0

    router._route_result(PollResult(
        4,
        PollOutcome.TEMPLATE_MISSING,
        task_results={"match_guide": PollTaskResult(PollOutcome.TEMPLATE_MISSING)},
    ))

    assert ocr.transitions == [("deactivate", "match_guide")]
    assert router._match_guide_activated_at is None
    assert switched == []
    assert loaded == []
    assert guide_matched == []
