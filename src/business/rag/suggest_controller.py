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
    parent=None + LIVE_WORKERS 持有 + finished→deleteLater：生命周期与 dialog 解耦，
    dialog 销毁不连带析构运行中的线程。
    """

    result_ready = Signal(object, object)  # (PendingBlock, RefinementUpdate | None)

    def __init__(self, blocks: list[PendingBlock], generator, parent=None):
        super().__init__(parent)
        self._blocks = list(blocks)
        self._generator = generator
        self._cancelled = False
        self._single = False  # 单块建议：结果需回填编辑器

    def run(self) -> None:
        LIVE_WORKERS.add(self)
        try:
            for block in self._blocks:
                if self._cancelled:
                    break
                update = suggest_one(block, self._generator)
                self.result_ready.emit(block, update)
        finally:
            LIVE_WORKERS.discard(self)


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
        worker.finished.connect(worker.deleteLater)  # 自回收（dialog 已销毁时也能释放）
        self._worker = worker
        worker.start()

    def cancel_and_shutdown(self) -> None:
        """中止在途建议：worker 置取消、generator 先 cancel 再 close（#61），
        短时等待收尾，仍在运行则转僵尸列表持有（#60）。"""
        self._cancelled = True
        worker = self._worker
        generator = self._generator
        self._running = False
        self._generator = None
        if worker is not None:
            worker._cancelled = True
            worker_generator = getattr(worker, "_generator", None) or generator
            if worker_generator is not None:
                cancel = getattr(worker_generator, "cancel", None)
                if callable(cancel):
                    cancel()
                self._release_generator(worker_generator)
            # 等待线程短时收尾；仍在运行（如 HTTP 挂起）则转入僵尸列表持有引用，
            # 防止 QThread 在 run 未结束时析构导致整个应用崩溃（#60）
            worker.wait(1000)
            if worker.isRunning():
                self._zombies.append(worker)
                worker.finished.connect(worker.deleteLater)
                worker.finished.connect(self._on_zombie_finished)
        elif generator is not None:
            self._release_generator(generator)

    def _on_result_ready(self, block: PendingBlock, update) -> None:
        self._done += 1
        if update is None:
            self._failed.append(block)
        self.result_ready.emit(block, update, self._single)

    def _on_worker_finished(self) -> None:
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
            except Exception:  # noqa: BLE001
                pass

    def _on_zombie_finished(self) -> None:
        """僵尸 worker 线程结束后移出持有列表（释放引用链，允许对象回收）。"""
        worker = self.sender()
        if worker in self._zombies:
            self._zombies.remove(worker)
