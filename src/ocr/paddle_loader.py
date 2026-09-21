"""PaddleOCR 加载入口及 Windows 子进程窗口抑制。"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

logger = logging.getLogger(__name__)

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


# ── B2 复核引擎（RapidOCR / PP-OCRv6-small / ONNX）────────────────────────
# rapidocr==3.9.2 钉死的内置模型文件名（各小版本内置模型可能变化）
_RAPIDOCR_MODEL_FILES = (
    "PP-OCRv6_det_small.onnx",
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "PP-OCRv6_rec_small.onnx",
)

_RECHECK_LOCK = threading.Lock()
_RECHECK_ENGINE = None
_RECHECK_ENGINE_FAILED = False


class RapidOcrEngine:
    """RapidOCR 包装层：输出翻译为 paddleocr 2.x 风格的 ``[[box, (text, conf)], ...]``。

    生产管线（画布批量/逐槽回退/官方导入逐 cell）按 ``engine.ocr(img, cls=False)``
    消费结果；RapidOCR 各小版本结果对象结构有差异，属性读取保留防御性兼容。
    画布喂法是灰度图，RapidOCR 3.x 要求 HWC 三通道，这里统一转换。
    """

    def __init__(self, engine) -> None:
        self._engine = engine

    def ocr(self, img, cls=False):
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        result = self._engine(img, use_det=True, use_cls=False, use_rec=True)
        txts = getattr(result, "txts", None)
        boxes = getattr(result, "boxes", None)
        scores = getattr(result, "scores", None)
        txts = [] if txts is None else list(txts)
        boxes = [] if boxes is None else list(boxes)
        scores = [0.0] * len(txts) if scores is None else list(scores)
        lines = [
            # 框必须转纯 Python list：下游按 2.x 风格消费，np 数组会打穿
            # isinstance(line[0], (list, tuple)) 的行格式判别（官方导入逐 cell 路径）
            (box.tolist() if isinstance(box, np.ndarray) else box,
             (str(text).strip(), float(score)))
            for box, text, score in zip(boxes, txts, scores, strict=False)
        ]
        return [lines] if lines else [None]


def _rapidocr_model_dir() -> Path:
    """定位 rapidocr 内置 PP-OCRv6 模型目录；frozen 下复制到 %TEMP% 纯 ASCII 路径。

    复用 _frozen_ocr_model_dirs 的 %TEMP% 复制模式，规避打包路径含中文的风险。
    模型文件缺失时直接抛错（调用方熔断停用复核），绝不触发联网下载。
    """
    import rapidocr

    src = Path(rapidocr.__file__).resolve().parent / "models"
    missing = [name for name in _RAPIDOCR_MODEL_FILES if not (src / name).is_file()]
    if missing:
        raise FileNotFoundError(f"rapidocr 内置模型缺失: {missing}")
    from src.config.env import IS_FROZEN

    if not IS_FROZEN:
        return src
    tmp_root = Path(tempfile.gettempdir()) / "mjs_rapidocr_models"
    if not str(tmp_root).isascii():
        return src  # %TEMP% 非纯 ASCII，回退打包路径
    if not (tmp_root / ".synced").exists():
        tmp_root.mkdir(parents=True, exist_ok=True)
        for name in _RAPIDOCR_MODEL_FILES:
            shutil.copy2(src / name, tmp_root / name)
        (tmp_root / ".synced").touch()
    return tmp_root


def create_rapidocr_ocr():
    """构造 v6 复核引擎（RapidOCR/ONNX），输出经 RapidOcrEngine 适配。

    设备固定 CPU（GPU 已否决，见设计文档 §十一）：不配置 CUDA/DirectML EP，
    onnxruntime 走默认 CPU 执行提供者。det 参数显式写死，与生产画布同一喂法
    口径——默认 limit_type=min/736 会把短边不足的图强制放大冲出检测工作尺度
    （v5 实证 26/29 空框，设计文档 §6.2 必改点）。
    """
    models_dir = _rapidocr_model_dir()
    from rapidocr import RapidOCR

    return RapidOcrEngine(RapidOCR(params={
        "Det.limit_type": "max",
        "Det.limit_side_len": 960,
        # 显式指向内置模型文件，绕过 rapidocr 的在线下载检查（缺文件直接报错熔断）
        "Det.model_path": str(models_dir / "PP-OCRv6_det_small.onnx"),
        "Cls.model_path": str(models_dir / "ch_ppocr_mobile_v2.0_cls_mobile.onnx"),
        "Rec.model_path": str(models_dir / "PP-OCRv6_rec_small.onnx"),
    }))


def get_recheck_ocr_engine():
    """进程内共享的 v6 复核引擎，惰性加载、失败熔断；不可用时返回 None。

    B2 复核模式专用（仅未决槽触发，不进轮询热路径）；加载失败后熔断，
    后续调用快速返回 None，直到进程重启。
    """
    global _RECHECK_ENGINE, _RECHECK_ENGINE_FAILED
    with _RECHECK_LOCK:
        if _RECHECK_ENGINE is not None:
            return _RECHECK_ENGINE
        if _RECHECK_ENGINE_FAILED:
            return None
        try:
            started = time.perf_counter()
            engine = create_rapidocr_ocr()
            _RECHECK_ENGINE = engine
            logger.info(
                "v6 复核引擎（RapidOCR/PP-OCRv6-small）加载完成，耗时 %.1fms",
                (time.perf_counter() - started) * 1000,
            )
            return engine
        except Exception as exc:
            logger.warning("v6 复核引擎不可用，复核模式自动停用: %s", exc)
            logger.debug(traceback.format_exc())
            _RECHECK_ENGINE_FAILED = True
            return None
