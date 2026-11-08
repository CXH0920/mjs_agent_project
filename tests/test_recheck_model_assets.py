# -*- coding: utf-8 -*-
"""recheck_model_assets 同名测试：v4 复核模型资产清单的纯函数（D5 零覆盖冒烟补测）。"""

from __future__ import annotations

import hashlib

import pytest
from src.ocr.recheck_model_assets import models_dir, sha256_stream


def test_sha256_stream_matches_hashlib(tmp_path) -> None:
    """分块流式哈希与一次性 hashlib 结果一致（跨块边界样本）。"""
    payload = (b"model-bytes-chunk." * 700) + b"tail"  # 跨默认分块边界
    path = tmp_path / "model.onnx"
    path.write_bytes(payload)

    assert sha256_stream(path) == hashlib.sha256(payload).hexdigest()


def test_models_dir_points_into_rapidocr_package() -> None:
    """models_dir 指向 rapidocr 包内 models 目录（v4 预取目标，v6 同址）。"""
    pytest.importorskip("rapidocr")

    directory = models_dir()

    assert directory.name == "models"
    assert directory.parent.name == "rapidocr"
