"""CaptureService 连接状态测试。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from PIL import Image
from PySide6.QtCore import QObject, Signal
from src.business.emulator import capture_service as capture_service_module
from src.business.emulator import ocr_task_coordinator
from src.business.emulator.capture_service import CaptureService
from src.business.recognition.ocr_worker import OcrTask, OfficialImportTask


def test_config_change_discards_previous_connection() -> None:
    service = CaptureService()
    states: list[tuple[str, str]] = []
    service.connection_changed.connect(lambda state, detail: states.append((state, detail)))

    service.update_config({"mumu_adb_path": "adb-a.exe", "mumu_adb_port": 16448})
    service.capture._connected = True
    service.capture._device_serial = "127.0.0.1:16448"
    service.update_config({"mumu_adb_path": "adb-a.exe", "mumu_adb_port": 16416})

    assert service.connection_state[0] == "disconnected"
    assert service.capture.device_serial == ""
    assert states[-1][0] == "disconnected"


def test_sync_connection_state_marks_offline() -> None:
    service = CaptureService()
    states: list[tuple[str, str]] = []
    service.connection_changed.connect(lambda state, detail: states.append((state, detail)))
    service.update_config({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448})
    service.capture._connected = False

    service.sync_connection_state("error: device offline")

    assert states[-1] == ("offline", "error: device offline")


def test_capture_can_skip_ocr_and_return_saved_image(monkeypatch, tmp_path) -> None:
    service = CaptureService()
    completed: list[dict] = []
    service.capture_completed.connect(completed.append)
    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr("src.business.emulator.image_save_scheduler.save_image", lambda image, path: (True, ""))

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=False, perform_ocr=False,
    )

    assert service._ocr_coordinator._worker is None
    assert completed[0]["ocr_results"] is None
    assert not completed[0]["ocr_matched"]
    service.shutdown()


def test_capture_result_emits_failed_when_screenshot_dir_unwritable(monkeypatch, tmp_path) -> None:
    """回归 TD-A5-001：结果处理抛异常时必须发 capture_failed 收尾。

    原缺陷：mkdir/schedule 在异常保护之外，目录不可写（如程序装在
    Program Files 下）时异常逃逸进 Qt 槽，completed/failed 都不发射，
    调用方的在途锁永不释放，界面永久停在"正在截图…"。
    """
    service = CaptureService()
    completed: list[dict] = []
    failures: list[str] = []
    service.capture_completed.connect(completed.append)
    service.capture_failed.connect(failures.append)

    def boom(*_args, **_kwargs):
        raise PermissionError("screenshots 目录不可写")

    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr(service._save_scheduler, "schedule", boom)

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=False, perform_ocr=False,
    )

    assert completed == []
    assert len(failures) == 1
    assert "截图处理失败" in failures[0]
    service.shutdown()


def test_capture_result_emits_failed_when_ocr_coordinator_retired(monkeypatch, tmp_path) -> None:
    """OCR 协调器已退役（ensure_worker 拒绝）同样要走 capture_failed 收尾。"""
    service = CaptureService()
    failures: list[str] = []
    service.capture_failed.connect(failures.append)
    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    service._ocr_coordinator.shutdown()  # 退役后首次创建会被拒绝

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=True, perform_ocr=True,
    )

    assert len(failures) == 1 and "截图处理失败" in failures[0]
    service.shutdown()


def test_manual_ocr_skips_template_matching(monkeypatch, tmp_path) -> None:
    service = CaptureService()
    submitted: list[dict] = []
    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr("src.business.emulator.image_save_scheduler.save_image", lambda image, path: (True, ""))
    monkeypatch.setattr(
        service,
        "_queue_capture_ocr",
        lambda **kwargs: submitted.append(kwargs),
    )

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=True, perform_ocr=True,
    )

    assert submitted[0]["match_template"] is False
    service.shutdown()


def test_manual_capture_not_gated_by_poll_cooldown(monkeypatch, tmp_path) -> None:
    """回归：轮询模式下连续手动识别不受轮询冷却影响，首次命中后再次识别仍提交 OCR。"""
    service = CaptureService()
    service._config = {"mumu_ocr_poll_mode": True}
    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr("src.business.emulator.image_save_scheduler.save_image", lambda image, path: (True, ""))
    submitted_tasks: list[SimpleNamespace] = []

    def make_task(*_args, **_kwargs) -> SimpleNamespace:
        task = SimpleNamespace(
            task_id=f"manual-{len(submitted_tasks)}", warmup=False, result={"outcome": "matched"},
        )
        submitted_tasks.append(task)
        return task

    monkeypatch.setattr(service, "submit_ocr_task", make_task)

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=True, perform_ocr=True,
    )
    service._on_ocr_task_completed(submitted_tasks[0])
    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=True, perform_ocr=True,
    )
    service.shutdown()

    assert len(submitted_tasks) == 2


def test_poll_mode_alone_does_not_enable_manual_ocr(monkeypatch, tmp_path) -> None:
    """回归：仅开轮询模式不构成手动截图的 OCR 依据，仍需 OCR 开关或强制识别。"""
    service = CaptureService()
    service._config = {"mumu_ocr_poll_mode": True}
    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr("src.business.emulator.image_save_scheduler.save_image", lambda image, path: (True, ""))
    submitted: list[dict] = []
    monkeypatch.setattr(service, "_queue_capture_ocr", lambda **kwargs: submitted.append(kwargs))

    service._handle_capture_result(
        True, Image.new("RGB", (10, 20)), None, "hero_selection", force_ocr=False, perform_ocr=True,
    )
    service.shutdown()

    assert submitted == []


def test_capture_queues_ocr_copy_before_saving_image(monkeypatch, tmp_path) -> None:
    image = Image.new("RGB", (10, 20))

    service = CaptureService()
    service._config = {"mumu_ocr_enabled": True}
    events: list[str] = []
    submitted: dict = {}

    def queue(**kwargs):
        events.append("ocr")
        submitted.update(kwargs)
        return type("Task", (), {"task_id": "test-task"})()

    monkeypatch.setattr("src.business.emulator.capture_service.DEFAULT_SCREENSHOTS_DIR", tmp_path)
    monkeypatch.setattr(service, "_queue_capture_ocr", queue)
    monkeypatch.setattr(
        "src.business.emulator.image_save_scheduler.save_image",
        lambda _image, _path: (events.append("save") is None, ""),
    )

    service._handle_capture_result(
        True, image, None, "hero_selection", force_ocr=False, perform_ocr=True,
    )
    service.shutdown()

    assert events[0] == "ocr"
    assert "save" in events
    assert submitted["image"] is not image


def test_capture_service_reports_ocr_warmup_states_and_allows_retry(monkeypatch) -> None:
    service = CaptureService()
    states: list[tuple[str, str]] = []
    service.ocr_warmup_state_changed.connect(lambda state, detail: states.append((state, detail)))
    worker = SimpleNamespace(warmup_model=lambda _names: True)
    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: worker)

    service.warmup_ocr_model(["曹操"])
    service._on_ocr_task_completed(SimpleNamespace(warmup=True, result={"outcome": "warmup_failed", "detail": "失败"}))
    service.warmup_ocr_model(["曹操"])
    service._on_ocr_task_completed(SimpleNamespace(warmup=True, result={"outcome": "warmed"}))

    assert states == [("warming", ""), ("failed", "失败"), ("warming", ""), ("ready", "")]
    assert service.ocr_warmup_state == "ready"


def test_capture_service_routes_official_import_through_shared_worker(monkeypatch) -> None:
    service = CaptureService()
    submitted: list[OfficialImportTask] = []
    worker = SimpleNamespace(submit=submitted.append)
    progress: list[tuple[str, int, int]] = []
    completed: list[list[dict]] = []
    failures: list[str] = []
    service.official_import_progress.connect(
        lambda status, current, total: progress.append((status, current, total))
    )
    service.official_import_completed.connect(completed.append)
    service.official_import_failed.connect(failures.append)
    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: worker)

    task = service.submit_official_import({"exile": ["page1.png", "page2.png"]})
    service._official_gateway.on_worker_progress(task.task_id, "正在识别", 2, 10)
    task.result = {
        "outcome": "official_imported",
        "summaries": [{"name": "武将放逐数据", "records": 100}],
    }
    service._official_gateway.on_task_completed(task)

    assert submitted == [task]
    assert task.paths == {"exile": ("page1.png", "page2.png")}
    assert progress == [("正在等待 OCR 队列...", 0, 0), ("正在识别", 2, 10)]
    assert completed == [[{"name": "武将放逐数据", "records": 100}]]
    assert failures == []


def test_cancelled_official_import_completes_silently(monkeypatch) -> None:
    """回归（TD-A8-001）：应用关闭排空队列时被取消的导入不报"未知错误"。

    取消不是失败：静默清掉排他集合即可，误导性错误弹窗会让用户误以为
    数据损坏而重试。
    """
    service = CaptureService()
    completed: list[list[dict]] = []
    failures: list[str] = []
    service.official_import_completed.connect(completed.append)
    service.official_import_failed.connect(failures.append)
    monkeypatch.setattr(
        service, "_ensure_ocr_worker", lambda: SimpleNamespace(submit=lambda task: None)
    )

    task = service.submit_official_import({"2v2": ["page1.png"]})
    task.result = {"outcome": "cancelled"}  # worker 停止时 _drain_pending_tasks 的产物
    service._official_gateway.on_task_completed(task)

    assert completed == []
    assert failures == []
    assert service._official_gateway._pending == set()


def test_wait_warmup_abandons_task_on_timeout() -> None:
    """回归（VUL-B3-002 审查缺口 2）：启动等待超时即弃单滞留的预热任务。

    弃单后 worker 取出时短路，不再在启动后补跑过期预热挤占首次识别。
    """
    coordinator = ocr_task_coordinator.OcrTaskCoordinator(on_worker_created=lambda worker: None)
    task = OcrTask(
        image=None, hero_names=(), rois=None,
        template_name="hero_selection", threshold=0.0, match_template=False, warmup=True,
    )
    coordinator._warmup_task = task

    assert coordinator.wait_warmup(timeout_ms=10) is False

    assert task.cancelled is True
    assert task.image is None
    assert coordinator._warmup_task is task  # 语义不变：超时不清引用，复位归 reset_warmup


def test_capture_service_rejects_overlapping_official_import(monkeypatch) -> None:
    service = CaptureService()
    monkeypatch.setattr(service, "_ensure_ocr_worker", lambda: SimpleNamespace(submit=lambda task: None))

    service.submit_official_import({"2v2": ["page1.png"]})

    try:
        service.submit_official_import({"exile": ["page1.png"]})
    except RuntimeError as exc:
        assert "已有官方榜单导入任务" in str(exc)
    else:
        raise AssertionError("应拒绝重叠的官方榜单导入任务")


def test_official_import_not_stranded_when_worker_created_off_gui_thread(
    monkeypatch, qapp
) -> None:
    """后台线程首建 worker 后，官方导入完成回执不经事件循环即可分派。

    回归接线竞态：worker 首建通知曾是 Qt 信号，跨线程发射退化为 Queued，
    start() 先于宿主接线执行；窗口期内完成的官方导入任务回执丢失，
    OfficialImportGateway._pending 永不清理，官方导入永久锁死。
    """
    created: list[QObject] = []

    class _StubOcrWorker(QObject):
        task_completed = Signal(object)
        official_progress = Signal(str, str, int, int)

        def __init__(self) -> None:
            super().__init__()
            created.append(self)

        def start(self) -> None:
            pass

        def submit(self, task) -> None:
            pass

        def retire(self) -> None:
            pass

    monkeypatch.setattr(ocr_task_coordinator, "OcrWorker", _StubOcrWorker)
    service = CaptureService()
    completed: list[list[dict]] = []
    failures: list[str] = []
    service.official_import_completed.connect(completed.append)
    service.official_import_failed.connect(failures.append)

    creator = threading.Thread(target=service._ensure_ocr_worker)
    creator.start()
    creator.join()

    task = service.submit_official_import({"exile": ["page1.png"]})
    task.result = {
        "outcome": "official_imported",
        "summaries": [{"name": "武将放逐数据", "records": 100}],
    }
    # 不经 processEvents：接线必须已同步完成，完成回执立即送达
    created[0].task_completed.emit(task)

    assert completed == [[{"name": "武将放逐数据", "records": 100}]]
    assert failures == []
    service.submit_official_import({"2v2": ["page1.png"]})  # _pending 已清理，不再拒绝重叠导入
    service.shutdown()


def test_ensure_worker_wires_before_start_from_background_thread(monkeypatch) -> None:
    """协调器契约：无论哪个线程首建 worker，接线回调必须先于 start() 执行。"""
    order: list[str] = []

    class _StubWorker:
        def start(self) -> None:
            order.append("start")

    monkeypatch.setattr(ocr_task_coordinator, "OcrWorker", _StubWorker)
    coordinator = ocr_task_coordinator.OcrTaskCoordinator(
        on_worker_created=lambda worker: order.append("wired")
    )

    creator = threading.Thread(target=coordinator.ensure_worker)
    creator.start()
    creator.join()

    assert order == ["wired", "start"]


def test_capture_service_returns_none_until_async_image_save_completes() -> None:
    from concurrent.futures import Future

    future: Future = Future()
    assert CaptureService._completed_save_path(future, "screenshot.png") is None
    future.set_result((True, ""))
    assert CaptureService._completed_save_path(future, "screenshot.png") == "screenshot.png"


def test_file_import_with_forced_ocr_skips_template_matching(monkeypatch, tmp_path) -> None:
    service = CaptureService()
    image_path = tmp_path / "match-guide.png"
    Image.new("RGB", (10, 20)).save(image_path)
    submitted: list[dict] = []
    monkeypatch.setattr(
        service,
        "_queue_capture_ocr",
        lambda **kwargs: submitted.append(kwargs),
    )

    service._execute_file_ocr(image_path, template_name="match_guide", force_ocr=True)

    assert submitted[0]["match_template"] is False
    assert submitted[0]["template_name"] == "match_guide"


def test_file_import_rejects_invalid_image_without_submitting_ocr(tmp_path) -> None:
    service = CaptureService()
    invalid_path = tmp_path / "invalid.png"
    invalid_path.write_bytes(b"not an image")
    failures: list[str] = []
    submitted: list[dict] = []
    service.capture_failed.connect(failures.append)
    service._queue_capture_ocr = lambda **kwargs: submitted.append(kwargs)

    service._execute_file_ocr(invalid_path)

    assert failures and "图片加载失败" in failures[0]
    assert submitted == []


def test_shutdown_prevents_worker_revival(monkeypatch) -> None:
    """回归 VUL-B2-001：shutdown 后迟到的 OCR 入口不得复活新 QThread。

    原缺陷：shutdown 把 _worker 置 None 后，退出阶段迟到的
    warmup/submit/reset 调用经 ensure_worker 重新创建并启动一个无人
    retire 的 QThread，进程退出时触发 "Destroyed while thread is
    still running" 崩溃。
    """
    created: list[object] = []

    class _StubWorker:
        def __init__(self) -> None:
            created.append(self)

        def start(self) -> None:
            pass

        def retire(self) -> None:
            pass

    monkeypatch.setattr(ocr_task_coordinator, "OcrWorker", _StubWorker)
    coordinator = ocr_task_coordinator.OcrTaskCoordinator(on_worker_created=lambda worker: None)
    first = coordinator.ensure_worker()

    coordinator.shutdown()
    revived = coordinator.ensure_worker()

    assert revived is first
    assert len(created) == 1


def test_shutdown_before_worker_creation_rejects_late_ocr_calls() -> None:
    """VUL-B2-001 边界：关闭后才出现的首次 OCR 需求明确拒绝，不复活线程。"""
    import pytest

    coordinator = ocr_task_coordinator.OcrTaskCoordinator(on_worker_created=lambda worker: None)
    coordinator.shutdown()

    with pytest.raises(RuntimeError, match="已关闭"):
        coordinator.ensure_worker()


def test_shutdown_during_ensure_worker_does_not_revive_thread(monkeypatch) -> None:
    """回归 VUL-B3-001：shutdown 落在"锁外检查"与"取锁"之间时不得创建 worker。

    原缺陷：锁内只复检 _worker，不复检 _worker_retired，于是 shutdown 完成后
    仍会 OcrWorker() + start()，产生一个不在 _RETIRED_WORKERS 里、atexit
    也等不到的 QThread —— 正是 VUL-B2-001 "Destroyed while thread is still
    running" 崩溃的复发路径。

    用可注入的锁钩子在"取锁瞬间"完成 shutdown，确定性复现该窗口。
    """
    import pytest

    created: list[object] = []

    class _StubWorker:
        def __init__(self) -> None:
            created.append(self)

        def start(self) -> None:
            pass

        def retire(self) -> None:
            pass

    monkeypatch.setattr(ocr_task_coordinator, "OcrWorker", _StubWorker)
    coordinator = ocr_task_coordinator.OcrTaskCoordinator(on_worker_created=lambda worker: None)

    real_lock = coordinator._worker_lock

    class _ShutdownOnAcquire:
        """模拟并发：进入临界区之前，另一线程已完成 shutdown。"""

        def __enter__(self):
            coordinator._worker_retired = True
            return real_lock.__enter__()

        def __exit__(self, *exc_info):
            return real_lock.__exit__(*exc_info)

    coordinator._worker_lock = _ShutdownOnAcquire()

    with pytest.raises(RuntimeError, match="已关闭"):
        coordinator.ensure_worker()
    assert created == []  # 未创建任何 QThread


def test_capture_screenshot_reuses_and_connects_shared_session() -> None:
    class FakeCapture:
        connected = False
        device_serial = "127.0.0.1:16448"

        def __init__(self) -> None:
            self.connect_calls = 0

        def connect(self, *, validate_capture: bool = False):
            self.connect_calls += 1
            self.connected = True
            return True, "连接成功"

        @staticmethod
        def screencap_full(log_success: bool = True):
            return True, Image.new("RGB", (10, 20))

    service = CaptureService()
    capture = FakeCapture()
    service.capture = capture

    ok, image = service.capture_screenshot()

    assert ok
    assert image.size == (10, 20)
    assert capture.connect_calls == 1
    assert service.connection_state == ("connected", "127.0.0.1:16448")


def test_capture_screenshot_connect_failure_keeps_message_format() -> None:
    """锚定手动路径连接失败的消息格式（公共可观察行为，重构不得改变）。"""
    class FailingCapture:
        connected = False
        device_serial = ""

        def connect(self, *, validate_capture: bool = False):
            return False, "超时"

    service = CaptureService()
    service.capture = FailingCapture()

    ok, message = service.capture_screenshot()

    assert not ok
    assert message == "ADB 连接失败: 超时"


def test_poll_capture_rejects_foreign_capture_upfront() -> None:
    """锚定轮询入口的时效检查：提交时已过期的 capture 直接拒绝，不碰 ADB。"""
    class ForeignCapture:
        connected = True
        device_serial = "127.0.0.1:9999"

        def screencap_full(self, log_success: bool = True):
            raise AssertionError("过期 capture 不得执行截图")

    service = CaptureService()
    service.update_config({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448})

    ok, _message, kind = service._capture_for_poll(ForeignCapture())

    assert not ok
    assert kind == "connection"


def test_poll_capture_connect_updates_connection_state() -> None:
    """回归 TD-A2-001a：轮询自愈连接成功后，会话状态须同步为 connected。

    原缺陷：轮询路径用裸 capture.connect() 重连，截图恢复正常但状态
    长期停留在 offline，用户被误导以为连接断开。
    """
    class FakeCapture:
        connected = False
        device_serial = "127.0.0.1:16448"

        def connect(self, *, validate_capture: bool = False):
            self.connected = True
            return True, "连接成功"

        @staticmethod
        def screencap_full(log_success: bool = True):
            return True, Image.new("RGB", (10, 20))

    service = CaptureService()
    service.update_config({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448})
    capture = FakeCapture()
    service.capture = capture

    ok, _image, kind = service._capture_for_poll(capture)

    assert ok
    assert kind == ""
    assert service.connection_state == ("connected", "127.0.0.1:16448")


def test_poll_capture_rejects_stale_capture_after_reconnect(monkeypatch) -> None:
    """回归 TD-A2-001b：连接期间配置热更重建会话后，旧 capture 不得继续截图。

    原缺陷：轮询路径连接后没有 is_current 校验，热更竞态窗口内会拿
    旧设备画面送 OCR，识别结果静默错乱。
    """
    class FakeCapture:
        connected = False
        device_serial = ""

        def __init__(self) -> None:
            self.screencap_calls = 0

        def connect(self, *, validate_capture: bool = False):
            self.connected = True
            return True, "连接成功"

        def screencap_full(self, log_success: bool = True):
            self.screencap_calls += 1
            return True, Image.new("RGB", (10, 20))

    service = CaptureService()
    service.update_config({"mumu_adb_path": "adb.exe", "mumu_adb_port": 16448})
    capture = FakeCapture()
    service.capture = capture
    monkeypatch.setattr(service._connection, "is_current", lambda _capture: False)

    ok, _message, kind = service._capture_for_poll(capture)

    assert not ok
    assert kind == "connection"
    assert capture.screencap_calls == 0


def test_capture_for_poll_times_out_when_executor_stuck(monkeypatch) -> None:
    """回归 VUL-B3-002：轮询截图等待有上界，执行器被占住时按可重试失败返回。

    原缺陷：future.result() 无超时，执行器积压（手动截图连续卡 45 秒级
    超时）时轮询线程无限期静默，心跳停摆无自愈。
    """
    monkeypatch.setattr(capture_service_module, "_POLL_CAPTURE_TIMEOUT_S", 0.2)
    service = CaptureService()
    release = threading.Event()
    # 占住单线程执行器：不 release 则第一个任务永不完成
    service._adb_executor.submit(lambda: release.wait(timeout=5))

    try:
        started = time.monotonic()
        ok, message, kind = service.capture_for_poll(None)

        assert not ok
        assert kind == "capture"
        assert "超时" in message
        assert time.monotonic() - started < 5  # 没有跟着占位任务等满
    finally:
        release.set()
    service.shutdown()

