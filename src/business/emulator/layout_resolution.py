"""
模拟器画面分辨率与 ROI 参考尺寸的体检判定（首启向导段 2）。

纯函数、无 Qt 依赖：判定规则写死可测（spec_onboarding §3.4 判定表）。
等比缩放属识别链路的设计内行为（recognizer 按参考尺寸换算 ROI），故
1920×1080 等等比分辨率必须判 WARN 而非 FAIL，仅竖屏 / 非等比 / 过低才 FAIL。
"""

from __future__ import annotations

_RATIO_TOLERANCE = 0.01  # 高宽比一致判定误差（1%）
_MIN_WIDTH_RATIO = 0.5   # 宽低于参考宽的该比例即判 FAIL（文字过糊）


def compare_resolution(actual: tuple[int, int], reference: tuple[int, int]) -> dict:
    """比较实际画面与 ROI 参考尺寸。

    返回 {'status': 'pass'|'warn'|'fail', 'detail': str, 'hint': str}；
    hint 为建议动作方向（如「去重新框选」「打开模拟器配置」），由 UI 渲染成按钮。
    """
    aw, ah = actual
    rw, rh = reference
    if (aw, ah) == (rw, rh):
        return {
            "status": "pass",
            "detail": f"{aw}×{ah}，画面比例与识别区域一致",
            "hint": "",
        }
    if ah > aw:
        return {
            "status": "fail",
            "detail": f"实际 {aw}×{ah} 为竖屏，识别区域按横屏 {rw}×{rh} 框选",
            "hint": "打开模拟器配置",
        }
    if aw < rw * _MIN_WIDTH_RATIO:
        return {
            "status": "fail",
            "detail": f"实际 {aw}×{ah}，分辨率过低（低于参考 {rw}×{rh} 的一半），名称文字会糊",
            "hint": "打开模拟器配置",
        }
    actual_ratio, reference_ratio = aw / ah, rw / rh
    if abs(actual_ratio - reference_ratio) / reference_ratio <= _RATIO_TOLERANCE:
        return {
            "status": "warn",
            "detail": f"实际 {aw}×{ah}，将按参考 {rw}×{rh} 等比缩放，名称框位置可能偏移（可用，识别率或下降）",
            "hint": "去重新框选",
        }
    return {
        "status": "fail",
        "detail": f"实际 {aw}×{ah} 与参考 {rw}×{rh} 比例不一致（非等比），名称框位置会错乱",
        "hint": "打开模拟器配置",
    }
