"""应用入口启动页测试。"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMainWindow
from src.main import _create_startup_splash
from src.ui.app.dialog_coordinator import DialogCoordinator
from src.ui.app.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_startup_splash_has_expected_size() -> None:
    _app()

    splash = _create_startup_splash()

    assert splash.pixmap().size().width() == 420
    assert splash.pixmap().size().height() == 220
    splash.close()


def test_main_window_starts_ocr_warmup_with_current_hero_names() -> None:
    warmed: list[list[str]] = []
    window = MainWindow.__new__(MainWindow)
    window._capture_service = SimpleNamespace(
        warmup_ocr_model=lambda hero_names: warmed.append(hero_names),
    )
    window._data = SimpleNamespace(
        heroes=SimpleNamespace(
            list_heroes=lambda: [SimpleNamespace(name="曹操"), SimpleNamespace(name="刘备")],
        ),
    )

    window.start_ocr_warmup()

    assert warmed == [["曹操", "刘备"]]


def test_official_import_temporarily_stops_and_restores_polling(monkeypatch) -> None:
    _app()
    calls: list[object] = []

    class Signal:
        def connect(self, callback) -> None:
            calls.append(callback)

    class Dialog:
        recommendation_indexes_stale = Signal()

        def __init__(self, capture_service, parent) -> None:
            calls.append((capture_service, parent))

        def exec(self) -> None:
            calls.append("exec")

    capture_service = object()
    ocr_service = SimpleNamespace(
        poll_state="running",
        stop_poll=lambda: calls.append("stop"),
    )
    coordinator = SimpleNamespace(
        sync_with_connection=lambda: calls.append("sync"),
    )
    window = MainWindow.__new__(MainWindow)
    window._capture_service = capture_service
    window._ocr_service = ocr_service
    window._poll_coordinator = coordinator
    window._recommendation = SimpleNamespace(
        mark_recommendation_indexes_stale=lambda: None,
    )
    window._dialogs = DialogCoordinator(window)
    monkeypatch.setattr("src.ui.app.dialog_coordinator.OfficialDataImportDialog", Dialog)

    window._dialogs.open_official_data_import()

    assert calls[-3:] == ["stop", "exec", "sync"]


def test_official_import_preserves_paused_poll_state(monkeypatch) -> None:
    calls: list[str] = []

    class Signal:
        @staticmethod
        def connect(callback) -> None:
            pass

    class Dialog:
        recommendation_indexes_stale = Signal()

        def __init__(self, capture_service, parent) -> None:
            pass

        def exec(self) -> None:
            calls.append("exec")

    window = MainWindow.__new__(MainWindow)
    window._capture_service = object()
    window._ocr_service = SimpleNamespace(
        poll_state="paused",
        stop_poll=lambda: calls.append("stop"),
    )
    window._poll_coordinator = SimpleNamespace(
        sync_with_connection=lambda: calls.append("sync"),
    )
    window._recommendation = SimpleNamespace(
        mark_recommendation_indexes_stale=lambda: None,
    )
    window._dialogs = DialogCoordinator(window)
    monkeypatch.setattr("src.ui.app.dialog_coordinator.OfficialDataImportDialog", Dialog)

    window._dialogs.open_official_data_import()

    assert calls == ["exec"]


def test_bootstrap_failure_writes_emergency_log_and_exits(monkeypatch, tmp_path) -> None:
    """回归（P2-1）：先于日志系统的引导段失败必须留下文件与弹窗线索，不静默闪退。"""
    import src.main as main_module

    _app()

    def _boom() -> None:
        raise OSError("磁盘已满")

    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(main_module, "_ensure_clean_runtime", _boom)
    monkeypatch.setattr(main_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        main_module, "QMessageBox",
        SimpleNamespace(critical=lambda parent, title, text: shown.append((title, text))),
    )

    with pytest.raises(SystemExit) as exc_info:
        main_module.main()

    assert exc_info.value.code == 1
    detail = (tmp_path / "logs" / "bootstrap_error.log").read_text(encoding="utf-8")
    assert "磁盘已满" in detail and "Traceback" in detail
    assert shown and shown[0][0] == "启动失败"


def test_deploy_marker_corrupt_triggers_full_redeploy(monkeypatch, tmp_path) -> None:
    """回归（P2-1 附带）：部署标记损坏按签名不符自愈，不因解码失败阻断启动。"""
    import src.main as main_module

    bundle = tmp_path / "bundle"
    (bundle / "data").mkdir(parents=True)
    (bundle / "data" / "heroes.json").write_text("[]", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(main_module, "IS_FROZEN", True)
    monkeypatch.setattr(main_module, "BUNDLE_ROOT", bundle)
    monkeypatch.setattr(main_module, "PROJECT_ROOT", runtime)

    main_module._ensure_clean_runtime()
    assert (runtime / "data" / "heroes.json").exists()

    (runtime / "data" / ".deployed").write_bytes(b"\xff\xfe\x00bad")  # 模拟断电半截

    main_module._ensure_clean_runtime()  # 不得抛 UnicodeDecodeError

    signature = main_module._bundle_data_signature(bundle / "data")
    assert (runtime / "data" / ".deployed").read_text(encoding="utf-8").strip() == signature


def test_open_onboarding_tracks_dialog_for_exit_cleanup(monkeypatch) -> None:
    """回归（P2-2）：打开的向导实例须登记，退出时才能终止其修复子进程。"""
    calls: list[str] = []

    class _Dialog:
        def __init__(self, window) -> None:
            calls.append("construct")

        def exec(self) -> None:
            calls.append("exec")

        def shutdown_fix_task(self) -> None:
            calls.append("shutdown_fix")

    monkeypatch.setattr("src.ui.app.onboarding_dialog.OnboardingDialog", _Dialog)
    window = SimpleNamespace(
        _poll_coordinator=SimpleNamespace(sync_with_connection=lambda: calls.append("sync")),
    )
    coordinator = DialogCoordinator(window)

    coordinator.open_onboarding()
    coordinator.shutdown_onboarding_fixes()

    assert calls == ["construct", "exec", "sync", "shutdown_fix"]
    assert len(coordinator._onboarding_dialogs) == 1


def test_close_event_runs_exit_protocol_including_onboarding_fix(monkeypatch) -> None:
    """回归（P2-2）：主窗关闭链末尾须终止向导修复子进程。"""
    _app()

    calls: list[str] = []
    window = MainWindow.__new__(MainWindow)
    window._poll_coordinator = SimpleNamespace(shutdown=lambda: calls.append("poll"))
    window._peak_select = SimpleNamespace(shutdown=lambda: calls.append("peak"))
    window._capture_service = SimpleNamespace(shutdown=lambda: calls.append("capture"))
    window._services = SimpleNamespace(shutdown_fetch_services=lambda: calls.append("fetch"))
    window._dialogs = SimpleNamespace(shutdown_onboarding_fixes=lambda: calls.append("onboarding"))
    monkeypatch.setattr(QMainWindow, "closeEvent", lambda self, event: None)

    window.closeEvent(SimpleNamespace())

    assert calls == ["poll", "peak", "capture", "fetch", "onboarding"]
