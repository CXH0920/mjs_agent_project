# -*- coding: utf-8 -*-
"""ComboStrip ratings_computed 信号契约回归。"""

from src.ui.recommendation.combo_strip import ComboStrip


def test_ratings_computed_delivers_int_keyed_dict(qapp):
    """hero_id→评级 负载是 int 键 dict：Signal(dict) 会按 QVariantMap 编译，
    int 键转换失败后槽只收到空 dict，卡片"实战 ★评级"角标静默失效；
    信号必须声明为 Signal(object) 保负载原样送达。"""
    strip = ComboStrip(None, None)
    received: list = []
    strip.ratings_computed.connect(received.append)
    payload = {101: 4, 102: 3}
    strip.ratings_computed.emit(payload)
    assert received == [payload]
