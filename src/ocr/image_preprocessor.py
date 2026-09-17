"""OCR 前的纯图像预处理。

预处理原则：只做"恢复/归一化"——放大到模型适配尺度、统一色彩空间，把输入
拉近训练分布；不做对比度增强与锐化。局部对比度增强会加深字间灰度谷，与拼图
画布的检测缩放叠加会切断文本行（2026-09 选将页"司马相如"被拆成两框的事故
根因），且深度 OCR 模型对光照自带鲁棒性，增强只会把输入推离训练分布。
"""

from __future__ import annotations

import cv2
import numpy as np


class ImagePreprocessor:
    """将武将名称 ROI 转为适合 PaddleOCR 的灰度图。"""

    @staticmethod
    def preprocess_roi(roi: np.ndarray) -> np.ndarray:
        """放大并转为灰度，供批量拼图画布与常规识别使用。"""
        enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        return cv2.cvtColor(enlarged, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def preprocess_roi_enhanced(roi: np.ndarray) -> np.ndarray:
        """gamma 提亮的差异视图，仅供逐槽回退的第二证据票使用。

        与 plain 的差异集中在暗区（拉开暗横幅上被压扁的细节），属全局色调
        映射，不碰空间结构。只允许作用于单条竖条（长边 435 < 检测器工作尺度
        960，永不触发降采样），不要喂给批量拼图画布。
        """
        enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
        lut = ((np.arange(256) / 255.0) ** 0.7 * 255).astype(np.uint8)
        return cv2.cvtColor(cv2.LUT(enlarged, lut), cv2.COLOR_BGR2GRAY)
