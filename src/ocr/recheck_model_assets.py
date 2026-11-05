# -*- coding: utf-8 -*-
"""B1 复核引擎（PP-OCRv4）模型资产清单与就位校验。

v4 三件套（det/rec/字典）由 src/scripts/fetch_recheck_models.py 按本清单
预取到 rapidocr 包 models/ 目录；运行期 engine_loader 显式指向这些文件，
缺件熔断、绝不在线下载（离线纪律见设计文档 §十四）。校验逻辑住能力域，
预取 CLI 与 doctor 检查共用同一份清单口径（清单与哈希是唯一事实源）。
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

# det/rec 条目与 rapidocr 3.9.2 default_models.yaml 登记的 URL+SHA256 逐一一致；
# 字典 URL 取 rapidocr/ch_ppocr_rec/main.py 的 DEFAULT_DICT_URL（yaml 未登记
# 哈希，此处钉 .tmp_test 语料验证资产的实测值），来源登记见 data/SOURCES.md
V4_MODEL_FILES = [
    (
        "ch_PP-OCRv4_det_mobile.onnx",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/det/ch_PP-OCRv4_det_mobile.onnx",
        "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9",
    ),
    (
        "ch_PP-OCRv4_rec_mobile.onnx",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.9.2/onnx/PP-OCRv4/rec/ch_PP-OCRv4_rec_mobile.onnx",
        "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b",
    ),
    (
        "ppocr_keys_v1.txt",
        "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v2.0.7/paddle/PP-OCRv4/rec/ch_PP-OCRv4_rec_infer/ppocr_keys_v1.txt",
        # 与 .tmp_test 语料验证资产的唯一差异是末行无换行符（6623 行内容逐行一致），行为等价
        "28b2362ad4ab2dc38769aa72feb535e3a9ddb3fd2a7585a05920e6393b1dc7f7",
    ),
]

_CHUNK = 1024 * 1024


def models_dir() -> Path:
    """rapidocr 包内置模型目录（v4 预取目标，与 v6 同址）。"""
    if importlib.util.find_spec("rapidocr") is None:
        raise SystemExit("rapidocr 未安装：请先按 environment.yml 安装 rapidocr==3.9.2")
    import rapidocr

    return Path(rapidocr.__file__).resolve().parent / "models"


def sha256_stream(path: Path) -> str:
    """大文件友好：分块流式 sha256（模型文件数十 MB，避免一次性读入）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_v4_models() -> list[str]:
    """返回未就位/哈希不符的问题清单；空列表即三件套全部就位。"""
    problems = []
    try:
        target_dir = models_dir()
    except SystemExit as exc:
        return [str(exc)]
    for name, _url, expected in V4_MODEL_FILES:
        path = target_dir / name
        if not path.is_file():
            problems.append(f"缺失 {name}")
        elif sha256_stream(path) != expected:
            problems.append(f"哈希不符 {name}（预期 {expected[:8]}…）")
    return problems
