"""
名将杀 Agent - QProcess 管道基类

提取 3 个 FetchService 共享的 QProcess 生命周期管理代码：
  _start_process → _on_stdout_ready → _on_stderr_ready → _on_finished / _on_error

子类覆写 _on_stdout_line / _on_process_finished / _cleanup_context /
_on_process_error 来表达差异。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
from subprocess import Popen

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from src.business.common.task_ledger import record_task
from src.business.fetching.fetch_utils import (
    cancel_process,
    get_qprocess_error_name,
    is_generation_progress_line,
    is_process_busy,
    log_process_error,
)
from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)
_OUTPUT_BUDGET_EXHAUSTED_MESSAGE = "思考过程耗尽输出额度"
# CLI 结束前输出的失败摘要行（[错误] 生成失败：N 项；成功项已提交，失败项保留旧数据）
_FAILURE_SUMMARY_PATTERN = re.compile(r"^\s*\[错误\]\s*生成失败：(.+)$", re.MULTILINE)


def _process_failure_message(exit_code: int, stdout: str, stderr: str) -> str:
    """优先取 CLI 的失败摘要（部分成功时比单个错误标记更准确）。"""
    combined_output = f"{stdout}\n{stderr}"
    matched = _FAILURE_SUMMARY_PATTERN.search(combined_output)
    if matched:
        return f"生成失败：{matched.group(1).strip()}"
    if _OUTPUT_BUDGET_EXHAUSTED_MESSAGE in combined_output:
        return _OUTPUT_BUDGET_EXHAUSTED_MESSAGE
    return f"进程退出码: {exit_code}"


class BaseFetchService(QObject):
    """QProcess 管道基类

    提供 _start_process / _on_stdout_ready / _on_stderr_ready /
    cancel / _on_finished / _on_error 的默认实现。
    子类通过覆写 hook 方法（_on_stdout_line / _on_process_finished 等）实现差异化。
    """

    status_changed = Signal(str)
    error_occurred = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._context: dict | None = None
        log_namespace = self._subprocess_log_namespace
        self._log_stdout = logging.getLogger(f"{log_namespace}.stdout")
        self._log_stderr = logging.getLogger(f"{log_namespace}.stderr")
        self._stdout_buffer = bytearray()
        self._stdout_line_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._failed_items: list[str] = []
        self._cancel_requested = False
        self._cancel_cleanup_process: Popen | None = None
        self._cancel_cleanup_polls = 0

    # ---------------------------------------------------------------
    # 钩子：子类覆写
    # ---------------------------------------------------------------

    @property
    def _service_name(self) -> str:
        """返回服务中文名（用于日志消息）"""
        return "子进程"

    @property
    def _subprocess_log_namespace(self) -> str:
        """返回父进程用于分流 stdout/stderr 的 logger 前缀。"""
        return "subprocess.unclassified"

    @property
    def failed_items(self) -> list[str]:
        """本次任务中解析到的失败项（武将名/相性对名）。"""
        return self._failed_items

    def _on_stdout_line(self, line: str) -> None:
        """覆写以解析每行 stdout（如 [i/N] 进度）"""
        pass

    def _on_process_finished(self, exit_code: int) -> None:
        """覆写以处理完成回调的不同行为（消息文本等）"""
        pass

    def _cleanup_context(self) -> None:
        """默认清理 _context 中的临时 heroes 文件；无资源时为 no-op。"""
        self._cleanup_tmp_file()

    def _cleanup_tmp_file(self) -> None:
        """清理 _context 中记录的临时 heroes 文件（guide/synergy 共用）。"""
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
                logger.debug("已清理临时文件: %s", tmp_path)
            except OSError as e:
                logger.warning("清理临时文件失败 %s: %s", tmp_path, e)

    def _on_process_error(self, error_name: str, full_msg: str) -> None:
        """覆写以自定义错误处理行为"""
        pass

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    def cancel(self) -> None:
        """请求中止当前子进程，收尾和状态通知仍由 finished 信号统一处理。"""
        if not self._process or self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._cancel_requested = True
        self.status_changed.emit(f"正在中止{self._service_name}...")
        # Starting 状态下 processId() 为 0，树杀会退化为 kill()，而 Qt6 对尚处
        # Starting 的进程 kill() 会在事件循环内原生 abort（VUL-B2-001）。此时只
        # 置标记，等 started 信号到达后再真正终止。
        if self._process.state() == QProcess.ProcessState.Starting:
            self._process.started.connect(self._on_started_after_cancel)
            return
        self._cancel_cleanup_process = cancel_process(self._process)

    def _on_started_after_cancel(self) -> None:
        """取消请求在 Starting 期间到达：进程真正起来后再补一次树杀。"""
        if self._process:
            try:
                self._process.started.disconnect(self._on_started_after_cancel)
            except (RuntimeError, TypeError):
                pass
        if not self._cancel_requested or not self._process:
            return
        if self._process.state() == QProcess.ProcessState.NotRunning:
            return
        self._cancel_cleanup_process = cancel_process(self._process)

    def _is_busy(self) -> bool:
        """检查是否正在运行"""
        if self._cancel_requested:
            logger.warning("%s 正在清理中止任务，忽略重复请求", self._service_name)
            return True
        return is_process_busy(self._process, self._service_name)

    @property
    def is_busy(self) -> bool:
        """当前是否有正在执行的子进程。"""
        return self._is_busy()

    # ---------------------------------------------------------------
    # 子进程管理
    # ---------------------------------------------------------------

    def _release_process(self) -> None:
        """置空引用并释放已结束的 QProcess 对象（ScriptRunner 同款收尾）。

        QProcess 挂在 service 父对象下不会随任务结束自动回收，不释放则
        对象与信号连接随任务轮次持续累积（TD-A8-001）。仅在进程确定
        终结后调用：finished 收尾，或 FailedToStart（Qt 不发 finished）。
        """
        process, self._process = self._process, None
        if process is not None:
            process.deleteLater()

    def _start_process(self, args: list[str]) -> None:
        """启动子进程并连接信号"""
        self._cancel_requested = False
        self._cancel_cleanup_process = None
        self._cancel_cleanup_polls = 0
        self._stdout_buffer.clear()
        self._stdout_line_buffer.clear()
        self._stderr_buffer.clear()
        self._failed_items.clear()
        self._release_process()
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        # 子进程以 -m 相对模块名启动，cwd 必须锚定项目根：快捷方式/打包态从
        # 其他目录启动时，按当前目录解析模块会直接报"找不到模块"（TD-A6-001）
        self._process.setWorkingDirectory(str(PROJECT_ROOT))
        self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
        self._process.readyReadStandardError.connect(self._on_stderr_ready)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        process_env = QProcessEnvironment.systemEnvironment()
        process_env.insert("MJS_QPROCESS_CHILD", "1")
        self._process.setProcessEnvironment(process_env)
        logger.info("启动子进程: python %s", " ".join(args))
        self._process.start(sys.executable, args)
        self._task_started = time.monotonic()
        self._start_watchdog()

    # 卡死看门狗：每条进度行（START/OK/FAIL/SKIP/重试/休息）都会重置计时，
    # 30 分钟上限只拦"无任何进度的黑洞化"——正常任务单条 1~3 分钟属正常，
    # browser 模式配对间还有反风控随机休息，长清单全程超 30 分钟也必须放行；
    # 否则 _is_busy 永久 True，只有用户手点取消才能恢复
    _WATCHDOG_TIMEOUT_MS = 30 * 60 * 1000

    def _start_watchdog(self) -> None:
        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setSingleShot(True)
        self._watchdog_timer.timeout.connect(self._on_watchdog_timeout)
        self._watchdog_timer.start(self._WATCHDOG_TIMEOUT_MS)

    def _feed_watchdog(self) -> None:
        """有进度时重置计时：把"总时长上限"收敛为"无进度上限"。

        Qt 6 对已激活的同间隔定时器 start() 是 no-op，必须 stop 后重启。
        """
        timer = getattr(self, "_watchdog_timer", None)
        if timer is not None:
            timer.stop()
            timer.start(self._WATCHDOG_TIMEOUT_MS)

    def _stop_watchdog(self) -> None:
        timer = getattr(self, "_watchdog_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._watchdog_timer = None

    def _on_watchdog_timeout(self) -> None:
        process = self._process
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        logger.error(
            "%s 子进程超过 %d 分钟无进度，看门狗强制终止",
            self._service_name, self._WATCHDOG_TIMEOUT_MS // 60000,
        )
        # 走树杀而非 self.cancel()：browser 模式的 Playwright 子进程树只杀主进程
        # 会孤儿化；且 cancel() 会置 _cancel_requested，把"超时失败"记成"用户取消"
        cancel_process(process)

    # ---------------------------------------------------------------
    # stdout / stderr 读取
    # ---------------------------------------------------------------

    def _on_stdout_ready(self) -> None:
        """读取 stdout，并只分发完整的 UTF-8 行。"""
        self._read_stdout()

    def _read_stdout(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardOutput())
        if not data:
            return
        self._stdout_buffer.extend(data)
        self._stdout_line_buffer.extend(data)
        self._dispatch_stdout_lines()

    def _dispatch_stdout_lines(self, flush: bool = False) -> None:
        """按换行分隔字节，避免 QProcess 分块破坏 UTF-8 或进度行。"""
        while b"\n" in self._stdout_line_buffer:
            line, _, remaining = self._stdout_line_buffer.partition(b"\n")
            self._stdout_line_buffer[:] = remaining
            self._dispatch_stdout_line(line)

        if flush and self._stdout_line_buffer:
            self._dispatch_stdout_line(bytes(self._stdout_line_buffer))
            self._stdout_line_buffer.clear()

    def _dispatch_stdout_line(self, raw_line: bytes) -> None:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            return
        self._log_stdout.info("%s", line)
        # 进度行说明任务在推进，喂看门狗推迟无进度超时
        if is_generation_progress_line(line):
            self._feed_watchdog()
        # 收集失败项（攻略/相性统一 FAIL 行：[i/total] 名字 FAIL）
        m = re.search(r"\[(\d+)/(\d+)\]\s+(.+?)\s+FAIL(?:\s|$)", line)
        if m:
            self._failed_items.append(m.group(3))
        self._on_stdout_line(line)

    def _on_stderr_ready(self) -> None:
        """读取 stderr → 缓冲 → 日志"""
        self._read_stderr()

    def _read_stderr(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardError())
        if not data:
            return
        self._stderr_buffer.extend(data)
        text = data.decode("utf-8", errors="replace")
        if not text.strip():
            return
        if "%|" in text:
            # transformers 的 tqdm 进度条走 stderr，属正常加载进度而非故障
            # （原一律 warning 曾贡献 ai_generation.log 89% 的 WARNING 噪声）
            self._log_stderr.debug("%s", text.strip())
        else:
            self._log_stderr.warning("%s", text.strip())

    # ---------------------------------------------------------------
    # 完成 / 错误回调
    # ---------------------------------------------------------------

    def _on_finished(self, exit_code: int) -> None:
        """子进程完成回调：清理资源 → 子类钩子 → 日志"""
        self._stop_watchdog()
        self._read_stdout()
        self._read_stderr()
        self._dispatch_stdout_lines(flush=True)
        self._cleanup_context()

        full_stdout = bytes(self._stdout_buffer).decode("utf-8", errors="replace")
        full_stderr = bytes(self._stderr_buffer).decode("utf-8", errors="replace")
        self._stdout_buffer.clear()
        self._stdout_line_buffer.clear()
        self._stderr_buffer.clear()

        msg = _process_failure_message(exit_code, full_stdout, full_stderr)
        logger.info("%s 子进程结束，%s", self._service_name, msg)
        record_task(
            self._service_name,
            ok=exit_code == 0 and not self._cancel_requested,
            exit_code=exit_code,
            failed=len(self._failed_items),
            duration_s=time.monotonic() - getattr(self, "_task_started", time.monotonic()),
            reason=msg,
        )

        if self._cancel_requested:
            self._finish_cancellation()
            return

        if exit_code == 0:
            self.status_changed.emit(f"{self._service_name}完成")
        else:
            logger.warning("%s失败: %s", self._service_name, msg)
            self.status_changed.emit(f"{self._service_name}失败")
            self.error_occurred.emit(msg)

        finished_process = self._process
        self._on_process_finished(exit_code)
        self._context = None
        # 钩子可能经直连信号同步发起下一阶段（公告多阶段更新链），此时
        # _process 已是重入启动的新进程，不能当作本轮残留释放——否则新
        # QProcess 析构时会强杀仍在运行的子进程（退出码 0xF291=62097）
        if self._process is finished_process:
            self._release_process()

    # 进程树清理最多等待轮次（50ms/轮 ≈ 5 秒）：taskkill 被安全软件拦截或
    # PID 已复用时可能永不返回，无上限轮询会让 _cancel_requested 永不复位，
    # 服务进入只有重启才能解除的永久忙碌态（与 TD-A5-001 同症状）。
    _CANCEL_CLEANUP_MAX_POLLS = 100

    def _finish_cancellation(self) -> None:
        """等待 Windows 进程树清理结束后，再通知 UI 可安全继续操作。

        幂等：收尾后 _cancel_requested 已复位，重复调用（如超时路径与
        _on_finished 先后到达）不得重复广播 cancelled。
        """
        if not self._cancel_requested and self._cancel_cleanup_process is None:
            return

        cleanup_process = self._cancel_cleanup_process
        if cleanup_process and cleanup_process.poll() is None:
            self._cancel_cleanup_polls += 1
            if self._cancel_cleanup_polls < self._CANCEL_CLEANUP_MAX_POLLS:
                QTimer.singleShot(50, self._finish_cancellation)
                return
            logger.warning(
                "%s 进程树清理超过 %d 秒仍未结束，强制收尾",
                self._service_name,
                self._CANCEL_CLEANUP_MAX_POLLS * 50 // 1000,
            )

        if cleanup_process and cleanup_process.returncode not in (0, None):
            logger.warning("%s 进程树清理退出码: %s", self._service_name, cleanup_process.returncode)
        self._cancel_cleanup_process = None
        self._cancel_cleanup_polls = 0
        self._cancel_requested = False
        self.status_changed.emit(f"{self._service_name}已中止")
        self.cancelled.emit()
        self._context = None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        """子进程出错回调"""
        self._stop_watchdog()
        if self._cancel_requested and error == QProcess.ProcessError.Crashed:
            return
        self._read_stdout()
        self._read_stderr()
        self._dispatch_stdout_lines(flush=True)
        self._cleanup_context()
        self._stdout_buffer.clear()
        self._stdout_line_buffer.clear()
        self._stderr_buffer.clear()
        error_name = get_qprocess_error_name(error)
        full_msg = log_process_error(error_name, self._process)
        # FailedToStart 等错误 Qt 不发 finished，此处不记则台账缺失该类失败
        record_task(
            self._service_name,
            ok=False,
            failed=len(self._failed_items),
            duration_s=time.monotonic() - getattr(self, "_task_started", time.monotonic()),
            reason=full_msg,
        )
        # 取消期间的非 Crashed 错误（典型是 Starting 阶段的 FailedToStart）
        # 同样不会再有 finished 收尾：不在此复位取消标记，_is_busy 会因
        # _cancel_requested 恒为 True 而永久拒绝后续请求（TD-A5-001）。
        if self._cancel_requested:
            logger.warning("%s 中止期间出错: %s", self._service_name, full_msg)
            if error == QProcess.ProcessError.FailedToStart:
                self._release_process()  # FailedToStart 无 finished 跟随，不等收尾信号
            self._finish_cancellation()
            return
        self.status_changed.emit(f"{self._service_name}出错")
        self.error_occurred.emit(full_msg)
        self._on_process_error(error_name, full_msg)
        self._context = None
        if error == QProcess.ProcessError.FailedToStart:
            self._release_process()
