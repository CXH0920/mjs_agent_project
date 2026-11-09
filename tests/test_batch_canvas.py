# -*- coding: utf-8 -*-
"""batch_canvas 同名测试：名称批次画布分组与碎片拼接（D5 锚点迁移，自 test_ocr_components 归位）。"""

from __future__ import annotations

import numpy as np
from src.ocr.batch_canvas import _BATCH_CANVAS_MAX_WIDTH, _BATCH_SLOT_GAP, join_name_fragments, split_canvas_groups


def test_split_canvas_groups_caps_group_width_and_keeps_slot_order() -> None:
    strips = {i: np.zeros((372, 213), dtype=np.uint8) for i in range(1, 15)}

    groups = split_canvas_groups(strips)

    assert [slot for group in groups for slot in group] == list(range(1, 15))
    for group in groups:
        width = sum(strips[slot].shape[1] for slot in group) + _BATCH_SLOT_GAP * (len(group) - 1)
        assert width <= _BATCH_CANVAS_MAX_WIDTH


def test_split_canvas_groups_passes_oversized_single_strip_alone() -> None:
    strips = {1: np.zeros((10, 1200), dtype=np.uint8), 2: np.zeros((10, 100), dtype=np.uint8)}

    assert split_canvas_groups(strips) == [[1], [2]]


def test_join_name_fragments_sorts_by_y_and_uses_min_confidence() -> None:
    fragments = [("信", 0.99, 150.0), ("韩", 1.0, 40.0)]

    assert join_name_fragments(2, fragments) == [("韩信", 0.99, 150.0)]
