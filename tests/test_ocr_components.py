"""GeneralRecognizer 识别器域测试（D5 锚点迁移后聚焦 recognizer；其余组件已归位各自同名文件）。"""

from __future__ import annotations

import json

import numpy as np
from src.ocr.character_similarity import CharacterSimilarityService
from src.ocr.recognizer import GeneralRecognizer
from src.ocr.roi_config import OcrRoiLayout, OcrRoiSlot

# 真实 match_guide 默认布局（config/ocr_rois.default.json）：名称批次按 960 上限
# 分 2 组（4+1 条），阵营批次分 2 组（3+2 条），一批识别共 4 次画布调用
_MATCH_GUIDE_LAYOUT = OcrRoiLayout(
    (2560, 1440),
    (
        OcrRoiSlot(name_roi=(780, 190, 55, 140), team_roi=(990, 120, 80, 120)),
        OcrRoiSlot(name_roi=(1515, 190, 55, 140), team_roi=(1725, 130, 65, 90)),
        OcrRoiSlot(name_roi=(31, 361, 61, 170), team_roi=(225, 310, 90, 115)),
        OcrRoiSlot(name_roi=(2256, 374, 50, 150), team_roi=(2442, 304, 87, 99)),
        OcrRoiSlot(name_roi=(2249, 971, 60, 165), team_roi=(2450, 930, 65, 90)),
    ),
)



def test_general_recognizer_maps_batch_boxes_by_slot_center() -> None:
    class FakeEngine:
        def ocr(self, _image, cls):
            assert cls is False
            return [[
                [[[43, 0], [53, 0], [53, 8], [43, 8]], ("第二", 0.9)],
                [[[2, 0], [12, 0], [12, 8], [2, 8]], ("第一", 0.8)],
            ]]

    recognizer = GeneralRecognizer()
    recognizer.adopt_engine(FakeEngine())

    assert recognizer._recognize_prepared_batch({1: np.zeros((10, 10), dtype=np.uint8), 2: np.zeros((10, 10), dtype=np.uint8)}, "name") == {
        1: ("第一", 0.8),
        2: ("第二", 0.9),
    }


def test_general_recognizer_joins_fragments_and_rejects_low_confidence_slot() -> None:
    class FakeEngine:
        def ocr(self, _image, cls):
            assert cls is False
            return [[
                [[[2, 0], [12, 0], [12, 8], [2, 8]], ("低", 0.4)],
                [[[43, 0], [53, 0], [53, 8], [43, 8]], ("重", 0.9)],
                [[[44, 0], [54, 0], [54, 8], [44, 8]], ("复", 0.9)],
            ]]

    recognizer = GeneralRecognizer()
    recognizer.adopt_engine(FakeEngine())

    assert recognizer._recognize_prepared_batch({1: np.zeros((10, 10), dtype=np.uint8), 2: np.zeros((10, 10), dtype=np.uint8)}, "name") == {
        2: ("重复", 0.9),
    }


def test_general_recognizer_does_not_join_team_kind_fragments() -> None:
    class FakeEngine:
        def ocr(self, _image, cls):
            assert cls is False
            return [[
                [[[2, 0], [12, 0], [12, 8], [2, 8]], ("楚", 0.9)],
                [[[14, 0], [24, 0], [24, 8], [14, 8]], ("军", 0.9)],
            ]]

    recognizer = GeneralRecognizer()
    recognizer.adopt_engine(FakeEngine())

    assert recognizer._recognize_prepared_batch({1: np.zeros((10, 30), dtype=np.uint8)}, "team") == {}




def test_general_recognizer_rejects_truncated_name_with_multiple_corrections() -> None:
    class FakeEngine:
        def ocr(self, _image, cls):
            assert cls is False
            return [[[[[2, 0], [12, 0], [12, 8], [2, 8]], ("王", 0.99)]]]

    recognizer = GeneralRecognizer(hero_names=["王异", "王翦"])
    recognizer.adopt_engine(FakeEngine())

    assert recognizer._recognize_prepared_batch({1: np.zeros((10, 10), dtype=np.uint8)}, "name") == {}


def test_name_resolver_nearby_hero_candidates_uses_edit_distance_threshold() -> None:
    resolver = GeneralRecognizer(hero_names=["张郃", "王翦", "夏侯惇"])._resolver

    assert resolver.nearby_hero_candidates("张邻") == ["张郃"]      # 等长差一字
    assert resolver.nearby_hero_candidates("王翡") == ["王翦"]
    assert resolver.nearby_hero_candidates("夏侯") == ["夏侯惇"]    # 不等长差一字
    assert resolver.nearby_hero_candidates("张郃") == ["张郃"]      # 词表内名字自身即唯一近邻
    assert resolver.nearby_hero_candidates("张飞飞") == []          # 差二字超出阈值


def test_batch_fallback_guard_matches_resolver_candidate_closure() -> None:
    """批次回退守卫必须与 NameResolver 候选闭包同口径（单源委托，防静默分叉）。"""
    recognizer = GeneralRecognizer(hero_names=["张郃", "王翦", "夏侯惇"])

    for text in ("张邻", "王翡", "夏侯", "夏侯惇惇", "张飞飞", "毫不相干"):
        expected = len(recognizer._resolver.nearby_hero_candidates(text)) != 1
        assert recognizer._requires_name_batch_fallback(text) is expected, text

    # 词表内名字与空词表恒放行（前置短路语义不经过候选筛选）
    assert recognizer._requires_name_batch_fallback("张郃") is False
    assert GeneralRecognizer(hero_names=None)._requires_name_batch_fallback("张郃") is False


def test_general_recognizer_keeps_ambiguous_prefix_unresolved() -> None:
    recognizer = GeneralRecognizer(hero_names=["夏侯惇", "夏侯渊", "夏侯婴", "夏侯霸"])

    result = recognizer._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "夏侯", "confidence": 0.98},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["candidates"] == ["夏侯婴", "夏侯惇", "夏侯渊", "夏侯霸"]


def test_general_recognizer_confirms_unique_prefix_but_not_unsafe_similarity() -> None:
    prefix = GeneralRecognizer(hero_names=["夏侯惇"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "夏侯", "confidence": 0.98},
    ])
    unsafe = GeneralRecognizer(hero_names=["周瑜"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "正瑜", "confidence": 0.99},
    ])

    assert (prefix["name"], prefix["resolution"]) == ("夏侯惇", "unique_prefix")
    assert unsafe["name"] == ""
    assert unsafe["resolution"] == "unresolved"
    assert unsafe["candidates"] == ["周瑜"]


def test_general_recognizer_keeps_single_character_prefix_unresolved() -> None:
    result = GeneralRecognizer(hero_names=["樊哙"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "樊", "confidence": 0.99},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["candidates"] == ["樊哙"]


def test_general_recognizer_keeps_mixed_prefix_and_equal_length_candidates_unresolved() -> None:
    result = GeneralRecognizer(hero_names=["赵姬", "赵婕妤"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "赵婕", "confidence": 0.99},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["length_mode"] == "uncertain"
    assert result["candidates"] == ["赵姬", "赵婕妤"]


def test_general_recognizer_scores_complete_multi_candidates_with_two_evidence_families() -> None:
    result = GeneralRecognizer(hero_names=["张郃", "张飞"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "张邻", "confidence": 0.9065},
        {"source": "single_enhanced", "text": "张邻", "confidence": 0.7623},
        {"source": "single_plain", "text": "张邻", "confidence": 0.7889},
    ])

    assert result["name"] == "张郃"
    assert result["resolution"] == "multi_similarity"
    assert result["candidates"] == ["张郃"]


def test_general_recognizer_accepts_revised_multi_candidate_score_threshold() -> None:
    class StubSimilarityService(CharacterSimilarityService):
        def rank_single_substitution_candidates(
            self, _text: str, _candidates: list[str] | set[str],
        ) -> list[tuple[str, float]]:
            return [("王乙", 0.35), ("王丙", 0.19)]

    recognizer = GeneralRecognizer(
        hero_names=["王乙", "王丙"],
        similarity_service=StubSimilarityService(),
    )

    result = recognizer._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "王甲", "confidence": 0.9},
        {"source": "single_plain", "text": "王甲", "confidence": 0.9},
    ])

    assert (result["name"], result["resolution"]) == ("王乙", "multi_similarity")


def test_general_recognizer_uses_filled_confusion_features_within_whitelist() -> None:
    recognizer = GeneralRecognizer(hero_names=["卫青", "卫玠", "周瑜"])

    result = recognizer._resolver.resolve_name_evidence(1, [
        {"source": "single_enhanced", "text": "卫珍", "confidence": 0.9},
        {"source": "single_plain", "text": "卫珍", "confidence": 0.9},
    ])

    assert (result["name"], result["resolution"]) == ("卫玠", "multi_similarity")
    assert result["candidates"] == ["卫玠"]


def test_general_recognizer_does_not_score_multi_candidates_from_one_evidence_family() -> None:
    result = GeneralRecognizer(hero_names=["王异", "王翦"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "王翡", "confidence": 0.91},
        {"source": "single_enhanced", "text": "王翡", "confidence": 0.88},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["candidates"] == ["王异", "王翦"]


def test_general_recognizer_enforces_all_multi_candidate_score_thresholds() -> None:
    class StubSimilarityService(CharacterSimilarityService):
        def __init__(self, rankings: dict[str, list[tuple[str, float]]]) -> None:
            self._rankings = rankings

        def rank_single_substitution_candidates(
            self, text: str, candidates: list[str] | set[str],
        ) -> list[tuple[str, float]]:
            assert set(candidates) == {"王丙", "王乙"}
            return self._rankings[text]

    cases = [
        (
            {"王甲": [("王乙", 0.8), ("王丙", 0.5)]},
            [
                {"source": "batch_enhanced", "text": "王甲", "confidence": 0.69},
                {"source": "single_plain", "text": "王甲", "confidence": 0.9},
            ],
        ),
        (
            {"王甲": [("王乙", 0.34), ("王丙", 0.1)]},
            [
                {"source": "batch_enhanced", "text": "王甲", "confidence": 0.9},
                {"source": "single_plain", "text": "王甲", "confidence": 0.9},
            ],
        ),
        (
            {"王甲": [("王乙", 0.6), ("王丙", 0.5)]},
            [
                {"source": "batch_enhanced", "text": "王甲", "confidence": 0.9},
                {"source": "single_plain", "text": "王甲", "confidence": 0.9},
            ],
        ),
        (
            {
                "王甲": [("王乙", 0.8), ("王丙", 0.5)],
                "王丁": [("王丙", 0.8), ("王乙", 0.5)],
            },
            [
                {"source": "batch_enhanced", "text": "王甲", "confidence": 0.9},
                {"source": "single_plain", "text": "王丁", "confidence": 0.9},
            ],
        ),
    ]

    for rankings, evidence in cases:
        recognizer = GeneralRecognizer(
            hero_names=["王乙", "王丙"],
            similarity_service=StubSimilarityService(rankings),
        )

        result = recognizer._resolver.resolve_name_evidence(1, evidence)

        assert result["name"] == ""
        assert result["resolution"] == "unresolved"
        assert result["candidates"] == ["王丙", "王乙"]


def test_general_recognizer_suppresses_binding_for_consistent_unknown_with_candidates() -> None:
    # 高置信度一致读出词表外原文时抑制评分决胜（王导不再误绑王异），
    # 但候选可能是生僻字被稳定误读，必须保留待人工确认
    result = GeneralRecognizer(hero_names=["王异", "王戎", "王濬", "王翦"])._resolver.resolve_name_evidence(7, [
        {"source": "batch_enhanced", "text": "王导", "confidence": 0.9997},
        {"source": "single_enhanced", "text": "王导", "confidence": 0.9995},
        {"source": "single_plain", "text": "王导", "confidence": 0.9998},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["candidates"] == ["王异", "王戎", "王濬", "王翦"]
    assert result["raw_name"] == "王导"


def test_general_recognizer_keeps_truncated_rare_char_read_pending() -> None:
    # 生僻字被整字漏识（王濬 只读出"王"）时保留全部前缀候选待人工确认
    result = GeneralRecognizer(
        hero_names=["王元姬", "王异", "王戎", "王濬", "王翦"],
    )._resolver.resolve_name_evidence(6, [
        {"source": "batch_plain", "text": "王", "confidence": 0.9998},
        {"source": "single_enhanced", "text": "王", "confidence": 0.9997},
        {"source": "single_plain", "text": "王", "confidence": 0.9997},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["candidates"] == ["王元姬", "王异", "王戎", "王濬", "王翦"]


def test_general_recognizer_prefers_whitelist_correction_over_new_hero_consensus() -> None:
    # 邻→郃 是确定性混淆字对，极高置信度读出"张邻"仍应纠正为张郃而非新武将
    result = GeneralRecognizer(hero_names=["张郃", "张飞"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "张邻", "confidence": 0.9997},
        {"source": "single_plain", "text": "张邻", "confidence": 0.9998},
    ])

    assert (result["name"], result["resolution"]) == ("张郃", "multi_similarity")


def test_general_recognizer_binds_medium_confidence_unknown_via_multi_similarity() -> None:
    # 低于共识门槛的一致读数仍按既有字形评分决胜
    result = GeneralRecognizer(hero_names=["王异", "王翦"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "王导", "confidence": 0.95},
        {"source": "single_enhanced", "text": "王导", "confidence": 0.93},
        {"source": "single_plain", "text": "王导", "confidence": 0.94},
    ])

    assert (result["name"], result["resolution"]) == ("王异", "multi_similarity")


def test_general_recognizer_keeps_split_evidence_out_of_new_hero_consensus() -> None:
    # 任一路证据读出不同原文即非共识，精确命中照常生效
    result = GeneralRecognizer(hero_names=["王异", "王翦"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "王导", "confidence": 0.9997},
        {"source": "single_enhanced", "text": "王导", "confidence": 0.9995},
        {"source": "single_plain", "text": "王异", "confidence": 0.9998},
    ])

    assert (result["name"], result["resolution"]) == ("王异", "exact")


def test_general_recognizer_does_not_fire_new_hero_consensus_from_single_family() -> None:
    # 仅批图单族证据不满足共识门槛，保持未确认等待逐槽复核
    result = GeneralRecognizer(hero_names=["王异", "王翦"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "王导", "confidence": 0.9997},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unresolved"
    assert result["candidates"] == ["王异", "王翦"]


def test_general_recognizer_marks_candidate_free_consensus_as_new_hero() -> None:
    # 词表外且无编辑距离候选的共识读数同样归入新武将而非 unknown
    result = GeneralRecognizer(hero_names=["张飞"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "王导", "confidence": 0.9997},
        {"source": "single_plain", "text": "王导", "confidence": 0.9998},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "unknown_new_hero"
    assert result["candidates"] == []


def test_general_recognize_keeps_consistent_unknown_name_unresolved() -> None:
    class FakeEngine:
        def ocr(self, _image, cls=False):
            return [[[[[0, 0], [5, 0], [5, 5], [0, 5]], ("王导", 0.9997)]]]

    recognizer = GeneralRecognizer(
        hero_names=["王异", "王翦"],
        rois=[[10 * index, 100, 20, 40] for index in range(8)],
        reference_size=(200, 200),
    )
    recognizer.adopt_engine(FakeEngine())

    results = recognizer.recognize(np.zeros((200, 200, 3), dtype=np.uint8))

    assert results[0]["name"] == ""
    assert results[0]["resolution"] == "unresolved"
    assert results[0]["raw_name"] == "王导"
    assert results[0]["candidates"] == ["王异", "王翦"]


def test_general_recognizer_rejects_evidence_outside_prefix_candidate_closure() -> None:
    result = GeneralRecognizer(hero_names=["卫青", "卫玠", "周瑜"])._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "卫", "confidence": 0.99},
        {"source": "single_plain", "text": "正瑜", "confidence": 0.71},
    ])

    assert result["name"] == ""
    assert result["resolution"] == "conflict"
    assert result["candidates"] == ["卫玠", "卫青", "周瑜"]


def test_general_recognizer_does_not_promote_unsafe_single_candidate_by_page_uniqueness() -> None:
    recognizer = GeneralRecognizer(hero_names=["周瑜"])
    results = [recognizer._resolver.resolve_name_evidence(1, [
        {"source": "batch_enhanced", "text": "正瑜", "confidence": 0.99},
    ])]

    recognizer._resolver.resolve_page_names(results)

    assert results[0]["name"] == ""
    assert results[0]["resolution"] == "unresolved"
    assert results[0]["candidates"] == ["周瑜"]


def test_general_recognizer_does_not_promote_uncertain_length_by_page_uniqueness() -> None:
    recognizer = GeneralRecognizer(hero_names=["甲乙", "丙乙"])
    results = [
        recognizer._resolver.resolve_name_evidence(1, [
            {"source": "batch_enhanced", "text": "甲乙", "confidence": 0.99},
        ]),
        recognizer._resolver.resolve_name_evidence(2, [
            {"source": "batch_enhanced", "text": "乙", "confidence": 0.99},
        ]),
    ]

    recognizer._resolver.resolve_page_names(results)

    assert results[1]["name"] == ""
    assert results[1]["resolution"] == "unresolved"
    assert results[1]["length_mode"] == "uncertain"
    assert results[1]["candidates"] == ["丙乙", "甲乙"]


def test_general_recognizer_resolves_slot_unique_candidate_without_competition() -> None:
    recognizer = GeneralRecognizer(hero_names=["卫子夫", "卫青", "卫玠"])
    results = [
        recognizer._resolver.resolve_name_evidence(1, [{"text": "卫子夫", "confidence": 0.9}]),
        recognizer._resolver.resolve_name_evidence(2, [{"text": "卫青", "confidence": 0.9}]),
        recognizer._resolver.resolve_name_evidence(3, [{"text": "卫", "confidence": 0.9}]),
    ]

    recognizer._resolver.resolve_page_names(results)

    assert (results[2]["name"], results[2]["resolution"]) == ("卫玠", "slot_unique")


def test_general_recognizer_does_not_resolve_competing_slot_candidates() -> None:
    recognizer = GeneralRecognizer(hero_names=["卫子夫", "卫青", "卫玠"])
    results = [
        recognizer._resolver.resolve_name_evidence(1, [{"text": "卫子夫", "confidence": 0.9}]),
        recognizer._resolver.resolve_name_evidence(2, [{"text": "卫青", "confidence": 0.9}]),
        recognizer._resolver.resolve_name_evidence(3, [{"text": "卫", "confidence": 0.9}]),
        recognizer._resolver.resolve_name_evidence(4, [{"text": "卫", "confidence": 0.9}]),
    ]

    recognizer._resolver.resolve_page_names(results)

    assert [item["name"] for item in results[2:]] == ["", ""]
    assert [item["candidates"] for item in results[2:]] == [["卫玠"], ["卫玠"]]


def test_general_recognizer_rolls_weaker_duplicate_back_to_conflict() -> None:
    recognizer = GeneralRecognizer()
    results = [
        {"index": 1, "name": "周瑜", "candidates": ["周瑜"], "resolution": "exact"},
        {
            "index": 2,
            "name": "周瑜",
            "candidates": ["周瑜"],
            "resolution": "unique_similarity",
        },
    ]

    recognizer._resolver.resolve_page_names(results)

    assert results[0]["name"] == "周瑜"
    assert results[1]["name"] == ""
    assert results[1]["resolution"] == "conflict"


def test_save_results_writes_given_page_type(tmp_path) -> None:
    json_path = tmp_path / "latest.json"
    GeneralRecognizer.save_results([], json_path, page_type="match_guide")

    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["page_type"] == "match_guide"


def test_match_guide_empty_detection_short_circuits_per_slot_evidence() -> None:
    """det 全空（错位页常态）时批次零产出即短路：仅剩 4 次批次调用，
    逐槽双证据与阵营重试链不再对无字画面空转。"""

    class _EmptyEverywhereEngine:
        def __init__(self) -> None:
            self.calls = 0

        def ocr(self, _image, cls=False):
            assert cls is False
            self.calls += 1
            return [None]

    recognizer = GeneralRecognizer(
        hero_names=["王濬", "王翦"], page_type="match_guide", layout=_MATCH_GUIDE_LAYOUT,
    )
    engine = _EmptyEverywhereEngine()
    recognizer.adopt_engine(engine)

    results = recognizer._recognize_match_guide(np.zeros((1440, 2560, 3), dtype=np.uint8))

    assert engine.calls == 4
    assert all(item["name"] == "" for item in results)


def test_match_guide_slot_recheck_still_runs_when_batch_has_text() -> None:
    """批次有产出而个别槽未决时，逐槽双证据照常触发——短路只针对批次整体零产出。"""

    class _PartialCanvasEngine:
        def __init__(self) -> None:
            self.widths: list[int] = []

        def ocr(self, image, cls=False):
            assert cls is False
            self.widths.append(image.shape[1])
            if image.shape[1] > 400:  # 双槽批次画布（2×240 + 30 间隙）：仅槽 1 直读
                line = [[[10, 10], [20, 10], [20, 30], [10, 30]], ("王濬", 0.9)]
            else:  # 单条回退
                line = [[[10, 10], [20, 10], [20, 30], [10, 30]], ("王", 0.9)]
            return [[line]]

    recognizer = GeneralRecognizer(
        hero_names=["王濬", "王翦"],
        page_type="match_guide",
        layout=OcrRoiLayout(
            (510, 120),
            (OcrRoiSlot(name_roi=(0, 0, 80, 40)), OcrRoiSlot(name_roi=(90, 0, 80, 40))),
        ),
    )
    engine = _PartialCanvasEngine()
    recognizer.adopt_engine(engine)

    results = recognizer._recognize_match_guide(np.zeros((120, 510, 3), dtype=np.uint8))

    assert results[0]["name"] == "王濬"
    # 1 次批次 + 槽 2 未决的 2 次逐槽（gamma 提亮 + plain 放大）
    assert engine.widths == [510, 240, 240]


def test_recognize_collects_det_stats_into_timing() -> None:
    """识别收尾把主引擎 det 调用/空检统计并入 timing_ms，供阶段耗时日志聚合输出。"""

    class _CountingEngine:
        def __init__(self) -> None:
            self.calls = 0

        def ocr(self, _image, cls=False):
            assert cls is False
            self.calls += 1
            return [None]

        def reset_call_stats(self) -> None:
            self.calls = 0

        def drain_call_stats(self) -> tuple[int, int]:
            stats = (self.calls, self.calls)
            self.calls = 0
            return stats

    engine = _CountingEngine()
    recognizer = GeneralRecognizer(
        hero_names=["王濬", "王翦"], page_type="match_guide", layout=_MATCH_GUIDE_LAYOUT,
    )
    recognizer.adopt_engine(engine)

    recognizer.recognize(np.zeros((1440, 2560, 3), dtype=np.uint8))

    assert recognizer.timing_ms["det_calls"] == 4
    assert recognizer.timing_ms["det_empty_calls"] == 4
