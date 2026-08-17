"""PaddleOCR 加载入口及 Windows 子进程窗口抑制。"""

from __future__ import annotations

import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import Iterator


_LOAD_LOCK = threading.Lock()


@contextmanager
def _hide_windows_child_consoles() -> Iterator[None]:
    """隐藏 Paddle 依赖探测产生的短命令窗口，并在加载后恢复全局状态。"""
    if sys.platform != "win32":
        yield
        return

    original_init = subprocess.Popen.__init__
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    owner_tid = threading.get_ident()  # 仅本线程触发的子进程生效

    def hidden_init(self, *args, **kwargs) -> None:
        if threading.get_ident() == owner_tid:
            kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | no_window
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = hidden_init
    try:
        yield
    finally:
        subprocess.Popen.__init__ = original_init


def create_paddle_ocr(**kwargs):
    """构造 PaddleOCR，并抑制其首次导入时的 Windows 控制台闪窗。

    推理设备由 config.env 的 ``MUMU_OCR_USE_GPU`` 控制（默认 false 走 CPU，
    避免 GPU 驱动异常导致整机卡顿）；CPU 模式限制线程数并启用 MKLDNN，
    防止推理打满全部逻辑核心。调用方显式传入 ``use_gpu`` 时优先尊重显式值。
    """
    from src.config.env import get_mumu_config

    cfg = get_mumu_config()
    use_gpu = kwargs.pop("use_gpu", None)
    if use_gpu is None:
        use_gpu = cfg.get("mumu_ocr_use_gpu", False)
    kwargs["use_gpu"] = bool(use_gpu)
    if not kwargs["use_gpu"]:
        kwargs.setdefault("cpu_threads", cfg.get("mumu_ocr_cpu_threads", 6))
        kwargs.setdefault("enable_mkldnn", True)
    with _LOAD_LOCK, _hide_windows_child_consoles():
        from paddleocr import PaddleOCR

        return PaddleOCR(**kwargs)
