"""PaddleOCR 加载入口及 Windows 子进程窗口抑制。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
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


def _frozen_ocr_model_dirs() -> dict:
    """frozen 下把打包的 OCR 模型复制到 %TEMP% 纯 ASCII 路径，规避 paddle C++ 的 ANSI fopen 限制。

    paddle 的 ifstream 不支持中文路径；%TEMP% 为纯 ASCII 时把 det/rec/cls 复制过去并指向，
    否则回退 BUNDLE_ROOT（仍可能因路径含中文失败，此时只能放纯英文路径）。
    开发态返回空 dict，沿用 PaddleOCR 默认（~/.paddleocr）。
    """
    from src.config.env import BUNDLE_ROOT, IS_FROZEN

    if not IS_FROZEN:
        return {}
    src = BUNDLE_ROOT / "paddleocr_models"
    if not src.is_dir():
        return {}  # 打包未含离线模型，回退默认
    tmp_root = Path(tempfile.gettempdir()) / "mjs_ocr_models"
    if not str(tmp_root).isascii():
        tmp_root = src  # %TEMP 非纯 ASCII，回退打包路径
    _sync_ocr_models(src, tmp_root)
    dirs = {}
    for key, sub in (("det_model_dir", "det"), ("rec_model_dir", "rec"), ("cls_model_dir", "cls")):
        if (src / sub).is_dir():
            dirs[key] = str(tmp_root / sub)
    return dirs


def _sync_ocr_models(src: Path, dst: Path) -> None:
    """复制打包的 OCR 模型到目标目录；已同步则跳过（源更新需删 dst 重新复制）。"""
    if (dst / ".synced").exists():
        return
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for sub in ("det", "rec", "cls"):
        s = src / sub
        if s.is_dir():
            shutil.copytree(s, dst / sub)
    (dst / ".synced").touch()


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

        kwargs.update(_frozen_ocr_model_dirs())
        return PaddleOCR(**kwargs)
