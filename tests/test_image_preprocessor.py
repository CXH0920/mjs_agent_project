# -*- coding: utf-8 -*-
"""image_preprocessor 同名测试：ROI 三倍放大灰度化（D5 锚点迁移，自 test_ocr_components 归位）。"""

from __future__ import annotations

import numpy as np
from src.ocr.image_preprocessor import ImagePreprocessor


def test_image_preprocessor_outputs_tripled_grayscale_image() -> None:
    roi = np.full((4, 5, 3), 128, dtype=np.uint8)

    prepared = ImagePreprocessor.preprocess_roi(roi)

    assert prepared.shape == (12, 15)
    assert prepared.dtype == np.uint8
