"""OCR 前的纯图像预处理。"""

from __future__ import annotations

import cv2
import numpy as np


class ImagePreprocessor:
    """将武将名称 ROI 转为适合 PaddleOCR 的灰度图。"""

    @staticmethod
    def preprocess_roi(roi: np.ndarray) -> np.ndarray:
        """放大、增强对比度、锐化并转为灰度。"""
        enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        lab = cv2.cvtColor(enlarged, cv2.COLOR_BGR2LAB)
        lightness, channel_a, channel_b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = cv2.merge([clahe.apply(lightness), channel_a, channel_b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)
        kernel = np.array([
            [-1, -1, -1],
            [-1, 9, -1],
            [-1, -1, -1],
        ], dtype=np.float32)
        sharpened = cv2.filter2D(enhanced, -1, kernel)
        return cv2.cvtColor(sharpened, cv2.COLOR_BGR2GRAY)
