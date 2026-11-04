"""巅峰赛牌面纯规则层（自 peak_select_watcher 拆出）。

候选池快照构造、人工确认存续验证与牌面签名判等三个纯规则子域：
零 Qt / 零线程 / 零锁依赖，只消费 OCR 槽位结果与既有确认映射，可独立
单测；watcher 的识别循环状态机（锁与世代守卫）仍在 peak_select_watcher。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ocr.name_resolution import UNRESOLVED_RESOLUTIONS
from src.ocr.roi_config import Roi

# 牌面签名容差：候选阶段卡面带 idle 浮动动画，实测剪影 bbox 逐拍漂移
# y ±3~4px、尺寸 ±5px（2026-09-06 日志实测），容差取 2 倍幅度。不用分桶
# 量化：基点贴近量化边界时签名逐拍翻转误判"新牌面"（2026-09-21 实测
# x=1179↔1180 跨 round 量化 147.5 边界，54 秒内同一牌面被全量重识别约
# 15 次），逐卡容差比较无边界；真实换牌表现为卡数变化或整排重排（位移
# ≥ 一个卡位宽），远超容差不会漏
SIGNATURE_POSITION_TOLERANCE_PX = 8
SIGNATURE_SIZE_TOLERANCE_PX = 16
# 14 张为禁选阶段（双方尚未提交禁选），8~11 张为候选阶段
_BAN_PHASE_MIN_CARDS = 12


@dataclass(frozen=True)
class PoolSnapshot:
    """一次牌面识别的结构化结果，供面板渲染。"""

    card_count: int
    names: tuple[str, ...]
    pending: tuple[dict, ...]
    stage: str  # "ban" 禁选阶段 / "pick" 候选阶段
    overlap: int  # 候选阶段双方禁选撞车数（池大小 - 8）
    banned: tuple[str, ...]  # 相对禁选期已确认名单的差集


def parse_pool(
    ocr_results: list[dict],
    card_count: int,
    ban_names: tuple[str, ...] = (),
    resolutions: dict[int, str] | None = None,
) -> PoolSnapshot:
    """把 OCR 槽位结果整理为候选池快照：已确认名单 + 待确认槽位。

    resolutions 为人工确认（槽位序号 → 武将名），优先于一切自动结论：
    确认落定后即使本拍闭包缺名（读数抖动）或自动决胜出别的猜测结论，
    也按人工结果展示；过期确认由 watcher 的逐拍内容验证剔除后才会消失。
    """
    names: list[str] = []
    pending: list[dict] = []
    for index, item in enumerate(ocr_results):
        manual = (resolutions or {}).get(index)
        if manual:
            names.append(manual)
            continue
        name = str(item.get("name") or "").strip()
        if name and item.get("resolution") not in UNRESOLVED_RESOLUTIONS:
            names.append(name)
            continue
        raw_name = str(item.get("raw_name") or "").strip()
        candidates = [str(c) for c in (item.get("candidates") or [])]
        if not (raw_name or candidates):
            continue
        pending.append({"slot": index, "raw_name": raw_name, "candidates": candidates})

    stage = "ban" if card_count >= _BAN_PHASE_MIN_CARDS else "pick"
    identified = set(names)
    banned = tuple(name for name in ban_names if name not in identified)
    overlap = card_count - 8 if stage == "pick" else 0
    return PoolSnapshot(
        card_count=card_count,
        names=tuple(names),
        pending=tuple(pending),
        stage=stage,
        overlap=overlap,
        banned=banned,
    )


def refresh_resolutions(
    resolutions: dict[int, str],
    raws: dict[int, str],
    ocr_results: list[dict],
) -> tuple[dict[int, str], dict[int, str], set[int]]:
    """逐拍验证人工确认的内容存续，返回 (沿用映射, 读数指纹, 未验证槽位)。

    验证判据（满足其一即视为仍是同一张牌）：确认名仍在本槽候选闭包；
    确认时的读数原文在本槽复现（稳定错读的确认名可能永远不在候选闭包，
    读数原文是其内容指纹）；确认名或读数在其它槽位唯一命中（牌面重排
    后迁移槽位）。未验证的确认原槽保留进宽限，由调用方按连续未验证拍
    数淘汰——浮动动画导致的单拍闭包缺名不应丢确认。
    """
    slots = [
        (
            str(item.get("raw_name") or "").strip(),
            {str(c) for c in (item.get("candidates") or [])},
        )
        for item in ocr_results
    ]
    carried: dict[int, str] = {}
    carried_raws: dict[int, str] = {}
    unverified: set[int] = set()
    claimed: set[int] = set()
    all_names = set(resolutions.values())

    def locate(old_slot: int, name: str, raw: str) -> int | None:
        # 单字读数信息量不足，不同牌极易读出同字，不作指纹使用
        raw_matchable = len(raw) >= 2
        if old_slot < len(slots) and old_slot not in claimed:
            slot_raw, candidates = slots[old_slot]
            if name in candidates or (raw_matchable and slot_raw == raw):
                return old_slot
        # 闭包同时命中多个已确认名的歧义槽不猜测归属（无法断定哪张牌在哪）
        other_names = all_names - {name}
        closure_hits = [
            slot
            for slot, (_, candidates) in enumerate(slots)
            if slot != old_slot
            and slot not in claimed
            and name in candidates
            and not other_names & candidates
        ]
        if len(closure_hits) == 1:
            return closure_hits[0]
        if not raw_matchable:
            return None
        raw_hits = [
            slot
            for slot, (slot_raw, _) in enumerate(slots)
            if slot != old_slot and slot not in claimed and slot_raw == raw
        ]
        return raw_hits[0] if len(raw_hits) == 1 else None

    for old_slot, name in resolutions.items():
        raw = raws.get(old_slot, "")
        target = locate(old_slot, name, raw)
        if target is None:
            carried[old_slot] = name
            carried_raws[old_slot] = raw
            unverified.add(old_slot)
            continue
        carried[target] = name
        carried_raws[target] = raw
        claimed.add(target)
    return carried, carried_raws, unverified


def verified_resolutions_for_import(
    resolutions: dict[int, str],
    raws: dict[int, str],
    ocr_results: list[dict],
) -> tuple[dict[int, str], dict[int, str], int]:
    """导入快照前校验人工确认，返回 (确认, 读数指纹, 丢弃数)。

    与实时循环的逐拍验证同一套定位语义（名在闭包 / 原文复现 / 唯一迁移），
    但导入是一次性快照、没有后续拍可宽限：无法定位的确认立即丢弃。
    槽位号是跨牌面不稳定键——选将板与禁将板同槽位并非同一张牌，不校验
    直接套用会把旧确认顶到别的武将头上（2026-09-30 卓文君事故）。
    """
    carried, carried_raws, unverified = refresh_resolutions(resolutions, raws, ocr_results)
    for slot in unverified:
        carried.pop(slot, None)
        carried_raws.pop(slot, None)
    return carried, carried_raws, len(resolutions) - len(carried)


def board_signature(cards: list[Roi]) -> tuple:
    """生成牌面布局签名（原始 bbox 元组）；判等必须用 board_signature_equal。"""
    return tuple(cards)


def board_signature_equal(left: tuple | None, right: tuple | None) -> bool:
    """逐卡容差比较：漂移在容差内判同板，卡数变化或真实位移判新板。"""
    if left is None or right is None or len(left) != len(right):
        return False
    return all(
        abs(a[0] - b[0]) <= SIGNATURE_POSITION_TOLERANCE_PX
        and abs(a[1] - b[1]) <= SIGNATURE_POSITION_TOLERANCE_PX
        and abs(a[2] - b[2]) <= SIGNATURE_SIZE_TOLERANCE_PX
        and abs(a[3] - b[3]) <= SIGNATURE_SIZE_TOLERANCE_PX
        for a, b in zip(left, right, strict=True)
    )


def is_subset_in_place(current: list[Roi], previous: list[Roi]) -> bool:
    """current 是否逐张能在 previous 里按顺序找到原位对应卡（位置容差同签名比对）。

    两张表都是行优先排序，"漏检"只是表里少了条目、其余条目的相对顺序不变，
    因此用子序列贪心匹配即可，不需要做全局最近邻。
    """
    remaining = iter(previous)
    for card in current:
        for candidate in remaining:
            if (
                abs(card[0] - candidate[0]) <= SIGNATURE_POSITION_TOLERANCE_PX
                and abs(card[1] - candidate[1]) <= SIGNATURE_POSITION_TOLERANCE_PX
            ):
                break
        else:
            return False
    return True
