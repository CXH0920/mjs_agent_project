# -*- coding: utf-8 -*-
"""轮询闲置检测的整帧指纹。

与 ocr_worker 的页面指纹（ROI 级、服务于 OCR 结果复用）职责不同：
本模块对整帧降采样，回答"画面自上一拍以来是否发生过任何可见变化"，
供轮询闲置自动暂停判定使用。判定采用相邻帧比较。

阈值依据 src/scripts/calibrate_idle_threshold.py 对真实截图序列的
MAD（平均绝对差）分布标定：需远低于真实画面变化（出牌、结算动画）
的下界，同时容纳环境噪声（小面积低幅度扰动）。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

FINGERPRINT_SIZE = (32, 18)  # (宽, 高) 降采样尺寸，576 字节
MAD_THRESHOLD = 3.0          # 平均绝对差低于该值视为同一画面（0-255 灰度）


def compute_fingerprint(image) -> bytes | None:
    """将 PIL 图像降采样为灰度字节指纹；图像不可用时返回 None。

    返回 None 会被 frames_match 判为"有变化"（宁漏暂停不误暂停）。
    """
    try:
        return image.convert("L").resize(FINGERPRINT_SIZE).tobytes()
    except Exception:
        logger.warning("帧指纹计算失败", exc_info=True)
        return None


def frames_match(left: bytes | None, right: bytes | None) -> bool:
    """比较两份指纹，平均绝对差低于阈值视为同一画面。"""
    if left is None or right is None or len(left) != len(right):
        return False
    total = sum(abs(a - b) for a, b in zip(left, right))
    return total / len(left) < MAD_THRESHOLD
