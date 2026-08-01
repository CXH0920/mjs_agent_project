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

    def hidden_init(self, *args, **kwargs) -> None:
        kwargs["creationflags"] = (kwargs.get("creationflags") or 0) | no_window
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = hidden_init
    try:
        yield
    finally:
        subprocess.Popen.__init__ = original_init


def create_paddle_ocr(**kwargs):
    """构造 PaddleOCR，并抑制其首次导入时的 Windows 控制台闪窗。"""
    with _LOAD_LOCK, _hide_windows_child_consoles():
        from paddleocr import PaddleOCR

        return PaddleOCR(**kwargs)
