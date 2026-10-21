# -*- coding: utf-8 -*-
"""LLM 建议线程编排器：批量/单块建议的 worker 生命周期、进度计数与取消善后。

自 IndexRefinementDialog 下沉（F2）：对话框只接信号做渲染（回填编辑器、按钮
恢复、失败提示），本类持有 worker 引用链与 generator 善后，dialog 销毁不连带
析构运行中的线程。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, Signal
from src.business.rag.refinement_service import PendingBlock, suggest_one

logger = logging.getLogger("index_refinement")

# 持有运行中的 worker，防止 dialog 销毁后 Python 引用丢失导致 QThread 运行中被 GC 析构（#61）
LIVE_WORKERS: set = set()


class SuggestWorker(QThread):
    """后台批量建议线程：逐块调用 LLM，结果经信号回主线程（UI 不冻结）。

    测试通过注入同步替身替换本类：替身 start() 内联产出结果并直接发信号，
    与生产共用同一条 result_ready/finished 状态链。
    引用必须在 __init__ 即进 LIVE_WORKERS：若等 run() 内才 add，start() 与
    run() 执行之间的窗口里 GC 会析构 running QThread（Qt6 qFatal，同
    onboarding_dialog.EnvCheckWorker 的修正）；finished 后经 _retire 安全退场。
    """

    result_ready = Signal(object, object)  # (PendingBlock, RefinementUpdate | None)

    def __init__(self, blocks: list[PendingBlock], generator, parent=None):
        super().__init__(parent)
        self._blocks = list(blocks)
        self._generator = generator
        self._cancelled = False
        self._single = False  # 单块建议：结果需回填编辑器
        LIVE_WORKERS.add(self)
        self.finished.connect(self._retire)

    def run(self) -> None:
        for block in self._blocks:
            if self._cancelled:
                break
            update = suggest_one(block, self._generator)
            self.result_ready.emit(block, update)

    def _retire(self) -> None:
        """线程已结束（finished 语义），此时回收引用与 C++ 对象才安全。"""
        LIVE_WORKERS.discard(self)
        self.deleteLater()


class SuggestController(QObject):
    """批量/单块 LLM 建议编排器。

    对外信号：
    - result_ready(block, update, is_single)：逐块结果转发，进度计数已先行更新；
    - finished(is_single)：worker 正常结束且 generator 已释放后发出
      （取消/关闭路径不发出，由调用方在取消时自行收尾）。
    """

    result_ready = Signal(object, object, bool)
    finished = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker: SuggestWorker | None = None
        self._generator = None
        self._single = False
        self._running = False
        self._cancelled = False
        self._total = 0
        self._done = 0
        self._failed: list[PendingBlock] = []
        # 关闭时仍在运行的 worker 转入此列表持有引用，防止 QThread 运行中析构导致进程崩溃（#60）
        self._zombies: list[SuggestWorker] = []

    # ── 状态（对话框渲染总览与按钮可用性的依据） ──────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total(self) -> int:
        return self._total

    @property
    def done(self) -> int:
        return self._done

    @property
    def failed(self) -> list[PendingBlock]:
        return self._failed

    @property
    def current_worker(self) -> SuggestWorker | None:
        return self._worker

    # ── 启动 / 收尾 ──────────────────────────────────────────────────

    def start(self, blocks: list[PendingBlock], generator, *, single: bool = False) -> None:
        if self._running:
            return
        self._running = True
        self._cancelled = False
        self._single = single
        self._total = len(blocks)
        self._done = 0
        self._failed = []
        self._generator = generator
        worker = SuggestWorker(list(blocks), generator)  # parent=None：dialog 销毁不连带析构运行中线程
        worker._single = single
        worker.result_ready.connect(self._on_result_ready)
        worker.finished.connect(self._on_worker_finished)
        self._worker = worker
        worker.start()

    def cancel_and_shutdown(self) -> None:
        """中止在途建议：置取消标志并等线程收尾，线程确认退出后才释放
        generator（#61），仍在运行（如 HTTP 挂起）则转僵尸列表持有（#60），
        生成器释放挂到该 worker 自身的 finished 上延迟执行。"""
        self._cancelled = True
        worker = self._worker
        generator = self._generator
        self._running = False
        self._worker = None
        self._generator = None
        if worker is None:
            if generator is not None:
                self._release_generator(generator)
            return
        worker._cancelled = True
        worker_generator = getattr(worker, "_generator", None) or generator
        if worker_generator is not None:
            cancel = getattr(worker_generator, "cancel", None)
            if callable(cancel):
                cancel()
        # 不立即 close：worker 可能正处于 httpx 请求中，另一线程关闭 client 会
        # 破坏连接池（RuntimeError: client has been closed）——等线程退出后再释放
        worker.wait(1000)
        if not worker.isRunning():
            self._release_generator(worker_generator)
            return
        # 僵尸路径：持有引用防 QThread 在 run 未结束时析构导致整个应用崩溃（#60）；
        # 生成器释放闭包捕获本轮实例，不触碰此后新一轮的 self._generator
        self._zombies.append(worker)
        worker.finished.connect(self._on_zombie_finished)
        if worker_generator is not None:
            worker.finished.connect(
                lambda gen=worker_generator: self._release_generator(gen))

    def _on_result_ready(self, block: PendingBlock, update) -> None:
        self._done += 1
        if update is None:
            self._failed.append(block)
        self.result_ready.emit(block, update, self._single)

    def _on_worker_finished(self) -> None:
        # 只收尾当前轮 worker：取消后僵尸线程的迟到 finished 若不拦截，会清空
        # 新一轮的 worker 引用、关闭新生成器并误发 finished（提前"宣告完成"）
        if self.sender() is not self._worker:
            return
        self._worker = None
        generator = self._generator
        self._generator = None
        if generator is not None:
            self._release_generator(generator)
        # 先复位 _running 再发 finished：对话框收尾槽内的 _update_overview 读取
        # is_running 计算按钮可用性，必须看到"已结束"而非"运行中"；
        # 取消/关闭路径由 cancel_and_shutdown 置 _cancelled 抑制 finished
        # （等价原"已取消/已关闭，跳过收尾弹窗"守卫）
        cancelled = self._cancelled
        self._running = False
        self._cancelled = False
        if not cancelled:
            self.finished.emit(self._single)

    @staticmethod
    def _release_generator(generator) -> None:
        close = getattr(generator, "close", None)
        if callable(close):
            try:
                close()
            except Exception as error:  # noqa: BLE001
                logger.warning("释放生成器失败: %s", error)

    def _on_zombie_finished(self) -> None:
        """僵尸 worker 线程结束后移出持有列表（释放引用链，允许对象回收）。"""
        worker = self.sender()
        if worker in self._zombies:
            self._zombies.remove(worker)
