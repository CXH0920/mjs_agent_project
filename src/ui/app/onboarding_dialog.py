# -*- coding: utf-8 -*-
"""首启向导：三段一页式体检对话框（环境自检 → 模拟器连接 → 试识别）。

合规边界（写死，LICENSE 附加条款）：向导只做三件事——只读检查文件与依赖、
通过 ADB 读屏、调用 OCR 识别；不做任何输入注入、点击游戏、进程访问、
内存读取、hook 或反作弊相关动作。本工具只读屏幕，不操作游戏。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from src.business.common.script_runner import ScriptRunner
from src.business.emulator.layout_resolution import compare_resolution
from src.business.emulator.mumu_config_coordinator import MumuConfigCoordinator
from src.config.env import IS_FROZEN, PROJECT_ROOT, get_mumu_config
from src.config.onboarding_state import mark_skipped, mark_step
from src.scripts.doctor import (
    FAIL,
    PASS,
    SKIP,
    WARN,
    check_config,
    check_rag_index,
    check_runtime_env,
    check_whitelist,
    run_checks,
)
from src.ui.app.onboarding_fixes import fix_action, sanitize_runtime_detail
from src.ui.shared.style import (
    ROLE_SECONDARY,
    TONE_DANGER,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    TONE_WARNING,
    set_ui_role,
)
from src.ui.shared.widgets import DialogFooter, PageHeader, StatusBadge, clear_layout

logger = logging.getLogger(__name__)

# 段 1 检查子集（离线；数据行读主窗状态拼接，不重跑 check_data 全量加载）
ONBOARDING_CHECKS = [
    ("环境", check_runtime_env),
    ("配置", check_config),
    ("白名单", check_whitelist),
    ("RAG索引", check_rag_index),
]

# 预热悬挂阈值：超时后状态机仍停 warming，而 warmup_ocr_model 对 warming 是
# no-op，重试无效——必须先经状态机复位接口重新入队（spec_onboarding §3.5）
WARMUP_HANG_MS = 150_000

# 持有段 1 检查线程的全程引用（构造即入集合，finished 后退出）：
# 调用方只持局部变量，窗口期内 GC 会析构 running QThread（Qt6 qFatal）
LIVE_WORKERS: set = set()

_TONE_BY_STATUS = {PASS: TONE_SUCCESS, WARN: TONE_WARNING, FAIL: TONE_DANGER}
_VERDICT_TONE = {"pass": TONE_SUCCESS, "warn": TONE_WARNING, "fail": TONE_DANGER}
_BADGE_LABEL = {PASS: "通过", WARN: "注意", FAIL: "未通过"}


class EnvCheckWorker(QThread):
    """段 1 后台检查线程（I/O 密集，不占 GUI 线程）。

    引用必须在 __init__ 即进 LIVE_WORKERS：若等 run() 内才 add，
    start() 与 run() 执行之间的窗口里调用方局部变量回收会析构
    running QThread（Qt6 qFatal，实测闪退）；finished 后经
    deleteLater 在事件循环中安全退场。
    """

    result_ready = Signal(object)  # list[Finding]
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        LIVE_WORKERS.add(self)
        self.finished.connect(self._retire)

    def run(self) -> None:
        try:
            self.result_ready.emit(run_checks(offline=True, checks=ONBOARDING_CHECKS))
        except Exception as error:  # noqa: BLE001 — 失败原因需回 GUI 呈现
            logger.error("首启向导环境检查失败", exc_info=True)
            self.failed.emit(str(error))

    def _retire(self) -> None:
        """线程已结束（finished 语义），此时回收引用与 C++ 对象才安全。"""
        LIVE_WORKERS.discard(self)
        self.deleteLater()


class OnboardingDialog(QDialog):
    """首启引导对话框：单页体检式三段布局，每段独立可跳过。"""

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._closing = False
        self._coordinator: MumuConfigCoordinator | None = None
        self._devices: tuple = ()
        self._recognize_pending = False
        self._warmup_timed_out = False
        self._device_action_kind = ""
        self._setup_ui()

        self._hang_timer = QTimer(self)
        self._hang_timer.setSingleShot(True)
        self._hang_timer.setInterval(WARMUP_HANG_MS)
        self._hang_timer.timeout.connect(self._on_warmup_hang)

        service = window._capture_service
        state_changed = getattr(service, "ocr_warmup_state_changed", None)
        if state_changed is not None:
            state_changed.connect(self._update_engine_state)
        service.capture_completed.connect(self._on_capture_completed)
        service.capture_failed.connect(self._on_capture_failed)
        self._update_engine_state(getattr(service, "ocr_warmup_state", "ready"))

        logger.info("首启向导构造")  # 发版 smoke 断言的构造埋点
        self._runner = ScriptRunner(self)
        self._runner.finished.connect(self._on_fix_finished)
        self._start_env_check()

    # ── UI 构建

    def _setup_ui(self) -> None:
        self.setWindowTitle("首启引导")
        self.setMinimumWidth(600)
        self.setMinimumHeight(560)
        root = QVBoxLayout(self)
        root.addWidget(PageHeader(
            "首启引导", "三步体检：运行环境 → 模拟器连接 → 试识别（本工具只读屏幕，不操作游戏）"))

        (self._env_container, self._env_badge, self._env_summary,
         self._env_button) = self._build_section(
            root, "① 运行环境", "检查依赖、模型与核心数据", "重新检查", self._start_env_check)

        (device_container, self._device_badge, self._device_summary,
         self._device_button) = self._build_section(
            root, "② 模拟器与画面", "探测 ADB、连接模拟器并体检分辨率", "连接并体检",
            self._start_device_check)

        picker_row = QHBoxLayout()
        self._device_combo = QComboBox()
        self._device_combo.setToolTip("检测到多个实例，请选择运行中的那一台")
        self._device_connect_button = QPushButton("连接所选实例")
        self._device_connect_button.clicked.connect(self._connect_selected)
        picker_row.addWidget(self._device_combo, 1)
        picker_row.addWidget(self._device_connect_button)
        device_container.addLayout(picker_row)
        self._device_combo.setVisible(False)
        self._device_connect_button.setVisible(False)
        self._device_action = QPushButton()
        set_ui_role(self._device_action, ROLE_SECONDARY)
        self._device_action.setVisible(False)
        self._device_action.clicked.connect(self._on_device_action)
        device_container.addWidget(self._device_action)

        (engine_container, self._engine_badge, self._engine_summary,
         self._recognize_button) = self._build_section(
            root, "③ 试识别", "请在游戏内打开【选将】页面后识别", "开始试识别",
            self._start_recognize)
        self._recognize_summary = QLabel("")
        self._recognize_summary.setWordWrap(True)
        engine_container.addWidget(self._recognize_summary)

        root.addStretch()
        footer = DialogFooter(accept_text="完成引导", cancel_text="跳过引导")
        footer.accepted.connect(self._on_accepted)
        footer.rejected.connect(self._on_skipped)
        root.addWidget(footer)

    def _build_section(self, root, title, hint, button_text, on_clicked):
        """构建一段体检区：标题行（徽标 + 主按钮）+ 摘要 + 动态内容容器。"""
        body = QVBoxLayout()
        header = QHBoxLayout()
        header.addWidget(QLabel(f"<b>{title}</b>"))
        badge = StatusBadge("待检", TONE_NEUTRAL)
        header.addWidget(badge)
        header.addStretch()
        button = QPushButton(button_text)
        button.clicked.connect(on_clicked)
        header.addWidget(button)
        body.addLayout(header)
        summary = QLabel(hint)
        summary.setWordWrap(True)
        body.addWidget(summary)
        container = QVBoxLayout()
        body.addLayout(container)
        frame = QWidget()
        frame.setLayout(body)
        root.addWidget(frame)
        return container, badge, summary, button

    # ── 段 1：运行环境（doctor 子集 + 数据三态）

    def _start_env_check(self) -> None:
        if self._closing:
            return
        self._env_badge.set_tone(TONE_INFO)
        self._env_badge.setText("检查中")
        self._env_summary.setText("正在检查运行环境…")
        clear_layout(self._env_container)
        self._env_button.setEnabled(False)
        worker = EnvCheckWorker()
        worker.result_ready.connect(self._on_env_checks_done)
        worker.failed.connect(self._on_env_checks_failed)
        worker.start()

    def _on_env_checks_failed(self, message: str) -> None:
        self._env_badge.set_tone(TONE_DANGER)
        self._env_badge.setText("检查失败")
        self._env_summary.setText(f"环境检查执行失败：{message}，可重试。")
        self._env_button.setEnabled(True)

    def _on_env_checks_done(self, findings) -> None:
        counts = {PASS: 0, WARN: 0, FAIL: 0}
        for finding in findings:
            if finding.status == SKIP:
                continue  # SKIP 项折叠不渲染
            counts[finding.status] = counts.get(finding.status, 0) + 1
            self._append_finding_row(finding.status, finding.name, finding.detail, finding.fix)
        status, name, detail, fix = self._data_finding()
        self._append_finding_row(status, name, detail, fix)
        counts[status] = counts.get(status, 0) + 1
        worst = FAIL if counts[FAIL] else (WARN if counts[WARN] else PASS)
        self._env_badge.set_tone(_TONE_BY_STATUS[worst])
        self._env_badge.setText(_BADGE_LABEL[worst])
        self._env_summary.setText(
            f"{counts[PASS]} 项通过 / {counts[WARN]} 项注意 / {counts[FAIL]} 项需处理；"
            "可处理后「重新检查」。")
        self._env_button.setEnabled(True)

    def _append_finding_row(self, status: str, name: str, detail: str, fix: str) -> None:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(StatusBadge(status, _TONE_BY_STATUS.get(status, TONE_WARNING)))
        label = QLabel(f"{name} · {detail}" if detail else name)
        label.setWordWrap(True)
        layout.addWidget(label, 1)
        button_text, module, tooltip = fix_action(fix, IS_FROZEN) if fix else ("", "", "")
        if button_text:
            button = QPushButton(button_text)
            set_ui_role(button, ROLE_SECONDARY)
            button.setToolTip(tooltip)
            if module:  # 可一键执行：ScriptRunner 走 exe 的 -m 分发通道
                button.clicked.connect(lambda _=False, m=module: self._run_fix_module(m))
            else:
                button.clicked.connect(
                    lambda _=False, text=tooltip: QMessageBox.information(self, "详情", text))
            layout.addWidget(button)
        self._env_container.addWidget(row)

    def _run_fix_module(self, module: str) -> None:
        """一键修复：frozen 与开发态同一套（exe 重入 -m），异步执行不阻塞向导。"""
        if self._runner.is_running():
            QMessageBox.information(self, "修复进行中", "已有修复任务在执行，请稍候。")
        elif self._runner.run(sys.executable, None, ["-m", module], PROJECT_ROOT):
            self._env_summary.setText("修复任务已启动，完成后可「重新检查」。")
        else:
            QMessageBox.warning(self, "无法启动", "修复任务启动失败，请查看日志。")

    def _on_fix_finished(self, code: int) -> None:
        outcome = "修复任务完成" if code == 0 else f"修复任务异常结束（退出码 {code}）"
        self._env_summary.setText(f"{outcome}，可「重新检查」。")

    def _data_finding(self):
        """数据行三态：加载失败 / 有关联问题 / 正常。

        last_load_report 在 load_all 抛异常时不会被覆盖（存在假阴性），
        失败态以 DialogCoordinator._data_error 标记为准（spec_onboarding §3.3）。
        """
        error = getattr(self._window._dialogs, "_data_error", None)
        if error:
            return FAIL, "核心数据", f"加载失败：{error}", f"数据文件加载失败：{error}"
        issues = self._window._data.last_load_report.issues
        if issues:
            lines = "\n".join(f"{issue.kind}: {issue.message}" for issue in issues)
            return WARN, "核心数据", f"加载有 {len(issues)} 项关联问题", lines
        return PASS, "核心数据", "加载正常", ""

    # ── 段 2：模拟器与画面（协调器接线 + 分辨率体检）

    def _ensure_coordinator(self) -> MumuConfigCoordinator:
        if self._coordinator is None:
            self._coordinator = MumuConfigCoordinator(
                config=get_mumu_config(),
                capture_service=self._window._capture_service,
                ocr_service=self._window._ocr_service,
                parent=self,
            )
            coordinator = self._coordinator
            coordinator.adb_detected.connect(self._on_adb_detected)
            coordinator.devices_changed.connect(self._on_devices_changed)
            coordinator.device_refresh_failed.connect(
                lambda message: self._set_device_status("fail", f"设备探测失败：{message}", "retry"))
            coordinator.connection_finished.connect(self._on_connection_finished)
            coordinator.roi_layout_screenshot_ready.connect(self._on_roi_screenshot_ready)
            coordinator.roi_layout_screenshot_failed.connect(self._on_roi_screenshot_failed)
        else:
            # 深链返回后配置可能已保存变更，重读同步协调器草稿
            self._coordinator.update_adb_path(get_mumu_config().get("mumu_adb_path", ""))
        return self._coordinator

    def _start_device_check(self) -> None:
        if self._closing:
            return
        coordinator = self._ensure_coordinator()
        self._device_combo.setVisible(False)
        self._device_connect_button.setVisible(False)
        self._device_action.setVisible(False)
        self._set_device_status("checking", "正在探测设备…", "")
        if not coordinator.config.get("mumu_adb_path"):
            self._device_summary.setText("未配置 ADB，正在自动探测…")
            coordinator.detect_adb()
            return
        coordinator.refresh_devices()

    def _on_adb_detected(self, success: bool, adb_path: str, message: str) -> None:
        if not success:
            self._set_device_status("fail", f"未找到 ADB：{message}", "retry")

    def _on_devices_changed(self, devices) -> None:
        self._devices = tuple(devices)
        running = [d for d in self._devices if d.is_running and d.adb_port]
        if len(running) == 1:
            self._connect_device(running[0])
            return
        if not running:
            self._set_device_status("fail", "未探测到运行中的模拟器，请先启动 MuMu 模拟器", "retry")
            return
        # 多台不擅自选择（协调器 _device_selection_error 硬约束）：下拉 + 占位提示
        self._device_combo.clear()
        for device in running:
            self._device_combo.addItem(f"{device.name}（端口 {device.adb_port}）", device)
        self._device_combo.setVisible(True)
        self._device_connect_button.setVisible(True)
        self._set_device_status("warn", f"检测到 {len(running)} 个运行中实例，请选择要使用的实例", "")

    def _connect_device(self, device) -> None:
        error = self._coordinator.connect(device, selected_explicitly=True)
        if error:
            self._set_device_status("fail", error, "retry")
        else:
            self._set_device_status("checking", f"正在连接 {device.name}（端口 {device.adb_port}）…", "")

    def _connect_selected(self) -> None:
        device = self._device_combo.currentData()
        if device is not None:
            self._connect_device(device)

    def _on_connection_finished(self, ok: bool, message: str) -> None:
        if not ok:
            self._set_device_status("fail", f"连接失败：{message}", "retry")
            return
        mark_step("device")
        self._set_device_status("checking", "已连接，正在体检画面分辨率…", "")
        self._coordinator.start_roi_layout_capture("hero_selection")

    def _on_roi_screenshot_ready(self, page_type: str, image) -> None:
        if page_type != "hero_selection" or self._closing:
            return
        reference = self._coordinator.roi_layout("hero_selection").reference_size
        verdict = compare_resolution(
            (int(image.width), int(image.height)), (int(reference[0]), int(reference[1])))
        self._set_device_status(verdict["status"], verdict["detail"],
                                "mumu_config" if verdict["hint"] else "",
                                verdict["hint"])

    def _on_roi_screenshot_failed(self, page_type: str, message: str) -> None:
        if page_type != "hero_selection":
            return
        self._set_device_status("fail", f"截图失败：{message}", "retry")

    def _set_device_status(self, status: str, text: str, action: str, action_text: str = "") -> None:
        tone = {"checking": TONE_INFO, "pass": TONE_SUCCESS,
                "warn": TONE_WARNING, "fail": TONE_DANGER}[status]
        label = {"checking": "检查中", "pass": "通过",
                 "warn": "注意", "fail": "未通过"}[status]
        self._device_badge.set_tone(tone)
        self._device_badge.setText(label)
        self._device_summary.setText(text)
        if action == "retry":
            self._device_action_kind = "retry"
            self._device_action.setText("重试")
            self._device_action.setVisible(True)
        elif action == "mumu_config":
            self._device_action_kind = "mumu_config"
            self._device_action.setText(action_text or "打开模拟器配置")
            self._device_action.setVisible(True)

    def _on_device_action(self) -> None:
        if self._device_action_kind == "retry":
            self._start_device_check()
        elif self._device_action_kind == "mumu_config":
            self._open_mumu_config()

    def _open_mumu_config(self) -> None:
        """深链模拟器配置（嵌套 exec，以主窗为父）；返回后重读配置并整段重跑段 2。"""
        self.hide()
        try:
            self._window._dialogs.open_mumu_config()
        finally:
            if not self._closing:
                self.show()
        self._start_device_check()

    # ── 段 3：试识别（预热门禁 + 悬挂复位 + 结果分派）

    def _update_engine_state(self, state: str, detail: str = "") -> None:
        if self._closing:
            return
        # 仅 warming 禁用（先例 peak_select_panel）：failed 态 warmup_ocr_model
        # 会重新入队，重试真实有效
        if state == "ready":
            self._engine_badge.set_tone(TONE_SUCCESS)
            self._engine_badge.setText("就绪")
            self._engine_summary.setText("识别引擎已就绪")
        elif state == "failed":
            self._engine_badge.set_tone(TONE_DANGER)
            self._engine_badge.setText("加载失败")
            detail = sanitize_runtime_detail(detail, IS_FROZEN)
            self._engine_summary.setText(f"识别引擎加载失败（{detail or '未知错误'}），可直接重试")
        elif state == "warming":
            self._hang_timer.start()
            self._engine_badge.set_tone(TONE_INFO)
            self._engine_badge.setText("加载中")
            self._engine_summary.setText("识别引擎加载中…（首次约 1–2 分钟）")
        else:
            self._engine_badge.set_tone(TONE_NEUTRAL)
            self._engine_badge.setText("未初始化")
            self._engine_summary.setText("识别引擎未初始化")
        if state != "warming":
            self._warmup_timed_out = False
            self._hang_timer.stop()
            if not self._recognize_pending:
                self._recognize_button.setText("开始试识别")
        self._recognize_button.setEnabled(state != "warming" or self._warmup_timed_out)

    def _on_warmup_hang(self) -> None:
        if self._closing:
            return
        if self._window._capture_service.ocr_warmup_state != "warming":
            return
        self._warmup_timed_out = True
        self._engine_badge.set_tone(TONE_DANGER)
        self._engine_badge.setText("初始化超时")
        self._engine_summary.setText("识别引擎初始化超时。可重新初始化，或先跳过引导。")
        self._recognize_button.setText("重新初始化")
        self._recognize_button.setEnabled(True)

    def _start_recognize(self) -> None:
        if self._closing or self._recognize_pending:
            return
        service = self._window._capture_service
        if self._warmup_timed_out:
            # 悬挂出口：先复位状态机再重新入队（warming 对 warmup_ocr_model 是 no-op；
            # capture_service.py 预算已满，复位接口不经其转发）
            self._warmup_timed_out = False
            service._ocr_coordinator.reset_warmup()
            service.warmup_ocr_model(self._hero_names())
            return
        if service.ocr_warmup_state == "warming":
            return
        self._recognize_pending = True
        self._recognize_button.setEnabled(False)
        self._recognize_summary.setText("正在识别…（OCR 链路无超时，慢机器可能较久）")
        try:
            service.do_capture(hero_names=self._hero_names(), force_ocr=True)
        except Exception as exc:  # noqa: BLE001 — 发起失败需回 UI 呈现
            logger.exception("首启向导试识别发起失败")
            self._recognize_pending = False
            self._set_recognize_result("fail", f"发起识别失败：{exc}")

    def _on_capture_completed(self, result: dict) -> None:
        if self._closing or not self._recognize_pending:
            return
        self._recognize_pending = False
        ocr_results = result.get("ocr_results")
        if ocr_results:
            mark_step("recognize")
            self._set_recognize_result(
                "pass",
                f"识别到 {len(ocr_results)} 个武将。下一步：在游戏里打开选将页，"
                "状态栏会自动显示识别结果。")
            self._window._reporter.show_event_message("首次识别成功")
            logger.info("首启向导试识别成功：%d 个武将", len(ocr_results))
        elif self._window._capture_service.ocr_warmup_state == "failed":
            self._set_recognize_result("fail", "识别引擎当前不可用，请重试或重新初始化。")
        else:
            self._set_recognize_result("warn", "未识别到选将页面，请在游戏里打开【选将】后再试。")

    def _on_capture_failed(self, message: str) -> None:
        if self._closing or not self._recognize_pending:
            return
        self._recognize_pending = False
        self._set_recognize_result("fail", f"截图失败：{message}")

    def _set_recognize_result(self, status: str, text: str) -> None:
        self._engine_badge.set_tone(_VERDICT_TONE[status])
        self._engine_badge.setText({"pass": "成功", "warn": "未命中", "fail": "失败"}[status])
        self._recognize_summary.setText(text)
        self._recognize_button.setEnabled(status != "pass")

    def _hero_names(self) -> list[str]:
        return [hero.name for hero in self._window._data.heroes.list_heroes()]

    # ── 收尾

    def _on_accepted(self) -> None:
        logger.info("首启向导完成")
        self.accept()

    def _on_skipped(self) -> None:
        mark_skipped()
        logger.info("首启向导已跳过")
        self.reject()

    def closeEvent(self, event) -> None:
        # 顺序不可颠倒：先置关闭标记停止发起新请求，再 shutdown——
        # shutdown 后 _submit/_complete 静默吞请求（OneShotToken 守卫），且
        # connection_changed 中继若不断开会撞 "Signal source has been deleted"
        self._closing = True
        if self._coordinator is not None:
            self._coordinator.shutdown()
        super().closeEvent(event)
