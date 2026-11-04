"""巅峰赛牌面纯规则层测试（候选池快照 / 确认存续验证 / 签名判等）。"""

from __future__ import annotations

from src.business.recognition.peak_board_rules import (
    board_signature,
    board_signature_equal,
    parse_pool,
    refresh_resolutions,
)


def test_parse_pool_splits_confirmed_and_pending():
    """已确认槽位进候选名单，未确认槽位带候选进入待确认，禁选差集正确。"""
    results = [
        {"name": "荆轲", "resolution": "exact", "raw_name": "荆轲", "candidates": ["荆轲"]},
        {"name": "", "raw_name": "典韦", "candidates": ["典韦", "陆机"], "resolution": "unresolved"},
        {"name": "", "raw_name": "", "candidates": [], "resolution": "unknown"},
    ]

    snapshot = parse_pool(results, 9, ban_names=("陈阿娇", "荆轲"))

    assert snapshot.names == ("荆轲",)
    assert snapshot.pending == ({"slot": 1, "raw_name": "典韦", "candidates": ["典韦", "陆机"]},)
    assert snapshot.stage == "pick"
    assert snapshot.overlap == 1
    assert snapshot.banned == ("陈阿娇",)


def test_parse_pool_applies_manual_resolutions():
    """人工确认优先于一切自动结论：闭包缺名的抖动拍与猜测型决胜都按确认展示。"""
    jittered = [
        {"name": "", "raw_name": "文君", "candidates": ["卓瑀", "卓瑒"], "resolution": "unresolved"},
    ]

    resolved = parse_pool(jittered, 14, resolutions={0: "卓文君"})
    assert resolved.names == ("卓文君",)
    assert resolved.pending == ()

    guessed = [
        {"name": "君王后", "raw_name": "文君", "candidates": ["君王后"], "resolution": "multi_similarity"},
    ]

    overridden = parse_pool(guessed, 14, resolutions={0: "卓文君"})
    assert overridden.names == ("卓文君",)
    assert overridden.pending == ()


def test_parse_pool_ban_stage_keeps_full_board():
    """14 张牌判为禁选阶段，不计算撞车数。"""
    results = [{"name": f"武将{i}", "resolution": "exact"} for i in range(14)]

    snapshot = parse_pool(results, 14)

    assert snapshot.stage == "ban"
    assert snapshot.overlap == 0
    assert len(snapshot.names) == 14


def test_refresh_resolutions_keeps_migrates_and_marks_stale():
    """内容命中原地保留/唯一命中迁移；未命中的确认原槽保留进宽限，不立即丢弃。"""
    ocr = [
        {"resolution": "unresolved", "raw_name": "荀歇", "candidates": ["荀勖", "荀彧"]},
        {"resolution": "exact", "name": "袁术", "raw_name": "袁术", "candidates": ["袁术"]},
        {"resolution": "unresolved", "raw_name": "黄忠", "candidates": ["黄忠"]},
        {"resolution": "unresolved", "raw_name": "凌统", "candidates": ["凌统"]},
    ]

    carried, _, unverified = refresh_resolutions(
        {0: "荀勖", 2: "黄忠"}, {0: "荀歇", 2: "黄忠"}, ocr
    )
    assert carried == {0: "荀勖", 2: "黄忠"}
    assert unverified == set()

    ocr_moved = [
        {"resolution": "unresolved", "raw_name": "荀或", "candidates": ["荀彧", "荀灌"]},
        {"resolution": "exact", "name": "袁术", "raw_name": "袁术", "candidates": ["袁术"]},
        {"resolution": "unresolved", "raw_name": "凌统", "candidates": ["凌统"]},
        {"resolution": "unresolved", "raw_name": "黄忠", "candidates": ["黄忠", "黄盖"]},
    ]

    # 黄忠重排迁移到新槽位；荀勖内容消失但原槽宽限保留（读数也变了，验不到）
    carried, _, unverified = refresh_resolutions(
        {0: "荀勖", 2: "黄忠"}, {0: "荀歇", 2: "黄忠"}, ocr_moved
    )
    assert carried == {0: "荀勖", 3: "黄忠"}
    assert unverified == {0}


def test_refresh_resolutions_raw_fingerprint_keeps_stable_misread():
    """稳定错读的牌闭包永远缺确认名，读数原文复现即验证通过，不进宽限。"""
    ocr = [
        {"resolution": "unresolved", "raw_name": "荀歇", "candidates": ["荀彧", "荀蔼"]},
    ]

    carried, _, unverified = refresh_resolutions({0: "荀勖"}, {0: "荀歇"}, ocr)

    assert carried == {0: "荀勖"}
    assert unverified == set()


def test_refresh_resolutions_stale_slot_never_claims_ambiguous_hit():
    """确认名同时命中多个槽位闭包时不猜测归属，原槽保留进宽限。"""
    ocr = [{"resolution": "unresolved", "raw_name": "", "candidates": ["黄忠", "凌统"]}]

    carried, _, unverified = refresh_resolutions(
        {1: "黄忠", 2: "凌统"}, {1: "黄忠", 2: "凌统"}, ocr
    )

    assert carried == {1: "黄忠", 2: "凌统"}
    assert unverified == {1, 2}


def test_board_signature_ignores_animation_drift():
    """签名判等吸收候选阶段卡面浮动动画的实测漂移（y±3、剪影 h±5），真实位移仍判新板。

    漂移数据取自 2026-09-06 日志实测：卡2 三拍 bbox (904,297,71,124)/
    (904,294,71,122)/(904,296,71,124)。09-21 的 x=1179↔1180 振荡在旧
    round 量化下跨 147.5 边界逐拍翻转（同一牌面被全量重识别约 15 次），
    容差比较是连续判断，对任意坐标无此边界，本用例防其回归。
    """
    cards = [(200, 200, 238, 326), (520, 200, 238, 326)]
    drifted = [(197, 203, 237, 324), (523, 197, 239, 328)]
    moved = [(200, 600, 238, 326), (520, 200, 238, 326)]
    measured_0906 = [(904, 297, 71, 124), (904, 294,  71, 122), (904, 296, 71, 124)]
    measured_0921 = [(1179, 620, 71, 124), (1180, 621, 71, 122), (1179, 620, 72, 124)]

    assert board_signature_equal(board_signature(cards), board_signature(drifted))
    for measured in (measured_0906, measured_0921):
        for other in measured[1:]:
            assert board_signature_equal(
                board_signature(measured[:1]), board_signature([other])
            )
    assert not board_signature_equal(board_signature(cards), board_signature(moved))
    assert not board_signature_equal(None, board_signature(cards))


def test_board_signature_flips_on_card_count_change():
    """卡数变化（换人/禁选）必然判为新牌面，与容差无关。"""
    ten_cards = [(904, 297, 71, 124)] * 10
    nine_cards = [(904, 297, 71, 124)] * 9

    assert not board_signature_equal(board_signature(ten_cards), board_signature(nine_cards))
