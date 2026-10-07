"""OCR 引擎装载层（RapidOCR/ONNX：PP-OCRv6 主引擎 + PP-OCRv4 复核引擎）。"""

from __future__ import annotations

import hashlib
import importlib.metadata
import logging
import shutil
import tempfile
import threading
import time
import traceback
from contextlib import suppress
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ── 模型套件 ──────────────────────────────────────────────────────────────
# rapidocr==3.9.2 钉死的内置模型文件名（各小版本内置模型可能变化）；
# v4 三件套由 src/scripts/fetch_recheck_models.py 从官方清单预取到同目录。
# sha 基线（完整 sha256）作 wheel 重构建/上游漂移哨兵：加载时比对，不符告警。
_V6_SUITE = {
    "label": "RapidOCR/PP-OCRv6-small",
    "det": "PP-OCRv6_det_small.onnx",
    "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "rec": "PP-OCRv6_rec_small.onnx",
    "keys": None,
    "sha": {
        "det": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
        "rec": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
    },
}
_V4_SUITE = {
    "label": "RapidOCR/PP-OCRv4-mobile",
    "det": "ch_PP-OCRv4_det_mobile.onnx",
    "cls": "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
    "rec": "ch_PP-OCRv4_rec_mobile.onnx",
    "keys": "ppocr_keys_v1.txt",
    # 与 src/scripts/fetch_recheck_models.py 钉死的官方分发哈希同源
    "sha": {
        "det": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
        "rec": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
    },
}
_SUITES = {"v6": _V6_SUITE, "v4": _V4_SUITE}
# %TEMP% 同步清单 = 两套件并集（同目录共享一份副本，指纹任一变更整体重拷）
_ALL_MODEL_FILES = tuple(
    sorted({_V6_SUITE["det"], _V6_SUITE["cls"], _V6_SUITE["rec"],
            _V4_SUITE["det"], _V4_SUITE["rec"], _V4_SUITE["keys"]})
)

_ENGINE_LOCK = threading.Lock()
_ENGINES: dict[str, object] = {}
_FAILED_SUITES: set[str] = set()


class RapidOcrEngine:
    """RapidOCR 包装层：输出翻译为 paddleocr 2.x 风格的 ``[[box, (text, conf)], ...]``。

    生产管线（画布批量/逐槽回退/官方导入逐 cell）按 ``engine.ocr(img, cls=False)``
    消费结果；RapidOCR 各小版本结果对象结构有差异，属性读取保留防御性兼容。
    画布喂法是灰度图，RapidOCR 3.x 要求 HWC 三通道，这里统一转换。
    """

    def __init__(self, engine) -> None:
        self._engine = engine
        self._det_calls = 0
        self._det_empty_calls = 0

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
        # 空检统计：det 零框调用。rapidocr 的逐次 WARNING 已被
        # _EmptyDetFilter 静音，聚合摘要经识别收尾的阶段耗时日志输出
        self._det_calls += 1
        if not lines:
            self._det_empty_calls += 1
        return [lines] if lines else [None]

    def reset_call_stats(self) -> None:
        """清零空检统计，供识别任务开头对齐统计窗口。"""
        self._det_calls = 0
        self._det_empty_calls = 0

    def drain_call_stats(self) -> tuple[int, int]:
        """返回 (det 调用数, 空检数) 并清零，供识别收尾聚合输出。"""
        stats = (self._det_calls, self._det_empty_calls)
        self._det_calls = 0
        self._det_empty_calls = 0
        return stats


def primary_suite() -> str:
    """主引擎套件名：config 的 ``MUMU_OCR_PRIMARY_ENGINE``（默认 v6，非法值回退 v6）。"""
    from src.config.env import get_mumu_config

    value = str(get_mumu_config().get("mumu_ocr_primary_engine", "v6")).strip().lower()
    if value not in _SUITES:
        if value and value != "v6":
            logger.warning("MUMU_OCR_PRIMARY_ENGINE=%r 非法，回退 v6", value)
        return "v6"
    return value


def _suite_model_dir(spec: dict) -> Path:
    """定位套件模型目录；frozen 下复制到 %TEMP% 纯 ASCII 路径。

    模型文件缺失时直接抛错（调用方熔断），绝不触发联网下载。%TEMP% 副本带
    源文件指纹（大小+mtime），源模型更新后自动重拷，避免旧副本滞留。
    """
    import rapidocr
    from src.config.env import IS_FROZEN

    src = Path(rapidocr.__file__).resolve().parent / "models"
    required = [spec[name] for name in ("det", "cls", "rec")]
    if spec["keys"]:
        required.append(spec["keys"])
    missing = [name for name in required if not (src / name).is_file()]
    if missing:
        hint = ""
        if any(name in _V4_SUITE.values() for name in missing):
            hint = "；请先运行 python -m src.scripts.fetch_recheck_models"
        raise FileNotFoundError(f"rapidocr 模型缺失: {missing}{hint}")
    if not IS_FROZEN:
        return src
    tmp_root = Path(tempfile.gettempdir()) / "mjs_rapidocr_models"
    if not str(tmp_root).isascii():
        return src  # %TEMP% 非纯 ASCII，回退打包路径
    return _sync_temp_models(src, tmp_root)


def _sync_temp_models(src: Path, tmp_root: Path) -> Path:
    """按源指纹同步全部模型到 %TEMP%；指纹不变时零拷贝。"""
    lines = []
    for name in _ALL_MODEL_FILES:
        path = src / name
        if path.is_file():
            stat = path.stat()
            lines.append(f"{name}:{stat.st_size}:{stat.st_mtime_ns}")
    fingerprint = "\n".join(lines)
    marker = tmp_root / ".synced"
    if not marker.is_file() or marker.read_text(encoding="utf-8") != fingerprint:
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)
        tmp_root.mkdir(parents=True, exist_ok=True)
        for name in _ALL_MODEL_FILES:
            if (src / name).is_file():
                shutil.copy2(src / name, tmp_root / name)
        marker.write_text(fingerprint, encoding="utf-8")
    return tmp_root


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cpu_threads() -> int:
    from src.config.env import get_mumu_config

    return int(get_mumu_config().get("mumu_ocr_cpu_threads", 6))


def _rapidocr_version() -> str:
    with suppress(importlib.metadata.PackageNotFoundError):
        return importlib.metadata.version("rapidocr")
    return "未知"


class _EmptyDetFilter(logging.Filter):
    """只滤 rapidocr 逐次抛出的空检测告警；空检聚合统计经识别收尾摘要输出。"""

    def filter(self, record: logging.LogRecord) -> bool:
        return "The text detection result is empty" not in record.getMessage()


def _silence_empty_det_warning() -> None:
    """静音 RapidOCR logger 的空检测逐条告警（幂等挂载）。

    兜底 OCR 在非对局页整链空转时每轮会刷十几条，单条毫无诊断价值；
    其余 rapidocr 告警不受影响。
    """
    rapidocr_logger = logging.getLogger("RapidOCR")
    if not any(isinstance(item, _EmptyDetFilter) for item in rapidocr_logger.filters):
        rapidocr_logger.addFilter(_EmptyDetFilter())


def create_rapidocr_ocr(suite: str = "v6"):
    """构造指定套件的 RapidOCR 引擎（v6=主引擎、v4=复核引擎），经 RapidOcrEngine 适配。

    设备固定 CPU（GPU 已否决，见设计文档 §十一）。det 参数显式写死 max/960，
    与生产画布同一喂法口径——默认 limit_type=min/736 会把短边不足的图强制放大
    冲出检测工作尺度（v5 实证 26/29 空框，设计文档 §6.2 必改点）。onnxruntime
    线程钉为 config 的 ``mumu_ocr_cpu_threads``（默认 6），防止推理吃满全部
    物理核心与模拟器抢核。
    """
    spec = _SUITES.get(suite)
    if spec is None:
        raise ValueError(f"未知模型套件: {suite!r}（可选 {sorted(_SUITES)}）")
    models_dir = _suite_model_dir(spec)
    sha = {role: _sha256(models_dir / name) for role, name in (("det", spec["det"]), ("rec", spec["rec"]))}
    drift = [
        f"{role} sha={sha[role][:8]}… 预期 {spec['sha'][role][:8]}…"
        for role in ("det", "rec") if sha[role] != spec["sha"][role]
    ]
    if drift:
        logger.warning("模型指纹与钉死基线不符（上游 wheel/模型漂移，行为需复测）: %s", drift)
    # 成功路径也落指纹：事后仅凭日志即可定位"当时加载的是哪份模型/哪个 rapidocr 版本"
    logger.info(
        "%s 套件模型指纹 det=%s rec=%s（rapidocr %s）",
        suite, sha["det"][:8], sha["rec"][:8], _rapidocr_version(),
    )
    from rapidocr import RapidOCR

    _silence_empty_det_warning()

    params = {
        "Det.limit_type": "max",
        "Det.limit_side_len": 960,
        # 显式指向模型文件，绕过 rapidocr 的在线下载检查（缺文件直接报错熔断）
        "Det.model_path": str(models_dir / spec["det"]),
        "Cls.model_path": str(models_dir / spec["cls"]),
        "Rec.model_path": str(models_dir / spec["rec"]),
        "EngineConfig.onnxruntime.intra_op_num_threads": _cpu_threads(),
    }
    if spec["keys"]:
        params["Rec.rec_keys_path"] = str(models_dir / spec["keys"])
    return RapidOcrEngine(RapidOCR(params=params))


def _get_shared_engine(suite: str) -> object | None:
    """套件级惰性单例 + 失败熔断；不可用时返回 None，直到进程重启。"""
    with _ENGINE_LOCK:
        if suite in _ENGINES:
            return _ENGINES[suite]
        if suite in _FAILED_SUITES:
            return None
        spec = _SUITES[suite]
        try:
            started = time.perf_counter()
            engine = create_rapidocr_ocr(suite)
            _ENGINES[suite] = engine
            logger.info(
                "%s 套件引擎（%s）加载完成，耗时 %.1fms",
                suite, spec["label"], (time.perf_counter() - started) * 1000,
            )
            return engine
        except Exception as exc:
            logger.warning("%s 套件引擎（%s）不可用: %s", suite, spec["label"], exc)
            logger.debug(traceback.format_exc())
            _FAILED_SUITES.add(suite)
            return None


def get_primary_ocr_engine():
    """进程内共享的主 OCR 引擎（默认 v6；``MUMU_OCR_PRIMARY_ENGINE=v4`` 为回滚档）。

    主引擎不可用意味着识别停摆，调用方（recognizer/官方导入）需按各自熔断
    语义处理 None/False。
    """
    return _get_shared_engine(primary_suite())


def get_recheck_ocr_engine():
    """进程内共享的复核引擎；与主引擎互为异构（主 v6 时复核 v4，反之亦然）。"""
    suite = "v4" if primary_suite() == "v6" else "v6"
    return _get_shared_engine(suite)
