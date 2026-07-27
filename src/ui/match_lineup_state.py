"""对局攻略阵容的纯状态与确认规则。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from src.data.models import Hero

SIDE_ALLY = "ally"
SIDE_ENEMY = "enemy"
TEAM_TO_SIDE = {"楚军": SIDE_ALLY, "汉军": SIDE_ENEMY}


@dataclass(frozen=True)
class LineupSlot:
    """一个对局攻略槽位的识别和确认状态。"""

    hero: Hero | None = None
    recognized_name: str = ""
    confidence: float = 0.0
    team: str = ""
    side: str = ""


@dataclass(frozen=True)
class LineupMutationResult:
    """一次阵容编辑的结果，供 UI 决定提示方式。"""

    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class LineupValidationResult:
    """阵容能否生成攻略及其失败原因。"""

    is_valid: bool
    reason: str = ""
    message: str = ""


class LineupState:
    """保存四名武将的阵容状态、敌我确认和主将选择。"""

    SLOT_COUNT = 4

    def __init__(self) -> None:
        self._slots = [LineupSlot() for _ in range(self.SLOT_COUNT)]
        self._ally_leader_slot: int | None = None
        self._analysis_confirmed = False
        self._recognized_at = ""

    @property
    def slots(self) -> tuple[LineupSlot, ...]:
        return tuple(self._slots)

    @property
    def heroes(self) -> list[Hero | None]:
        return [slot.hero for slot in self._slots]

    @property
    def sides(self) -> list[str]:
        return [slot.side for slot in self._slots]

    @property
    def ally_leader_slot(self) -> int | None:
        return self._ally_leader_slot

    @property
    def analysis_confirmed(self) -> bool:
        return self._analysis_confirmed

    @property
    def recognized_at(self) -> str:
        return self._recognized_at

    @property
    def valid_count(self) -> int:
        return sum(slot.hero is not None for slot in self._slots)

    @property
    def unresolved_count(self) -> int:
        return sum(slot.hero is not None and not slot.side for slot in self._slots)

    @property
    def allies(self) -> list[Hero]:
        return [slot.hero for slot in self._slots if slot.hero and slot.side == SIDE_ALLY]

    @property
    def enemies(self) -> list[Hero]:
        return [slot.hero for slot in self._slots if slot.hero and slot.side == SIDE_ENEMY]

    def load_from_ocr(
        self,
        ocr_results: list[dict],
        hero_by_name: Callable[[str], Hero | None],
        recognized_at: str,
    ) -> bool:
        """按 OCR 槽位导入新阵容，并清除旧的确认状态。"""
        slots: list[LineupSlot] = []
        for item in sorted(ocr_results, key=self._ocr_sort_key):
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            team = str(item.get("team", "")).strip()
            slots.append(LineupSlot(
                hero=hero_by_name(name),
                recognized_name=name,
                confidence=self._read_confidence(item.get("confidence", 0.0)),
                team=team,
                side=TEAM_TO_SIDE.get(team, ""),
            ))
            if len(slots) == self.SLOT_COUNT:
                break

        self._slots = slots + [LineupSlot() for _ in range(self.SLOT_COUNT - len(slots))]
        self._ally_leader_slot = next(
            (index for index, slot in enumerate(self._slots) if slot.side == SIDE_ALLY),
            None,
        )
        self._analysis_confirmed = False
        self._recognized_at = recognized_at if slots else ""
        return bool(slots)

    def set_side(self, index: int, side: str) -> LineupMutationResult:
        """设置敌我阵营，并限制每方最多两名武将。"""
        self._check_index(index)
        if side not in ("", SIDE_ALLY, SIDE_ENEMY):
            raise ValueError(f"未知阵营: {side}")
        slot = self._slots[index]
        if slot.hero is None:
            return LineupMutationResult(False, "missing_hero")
        if side and side != slot.side and self.sides.count(side) >= 2:
            return LineupMutationResult(False, "side_full")

        self._slots[index] = replace(slot, side=side)
        self._analysis_confirmed = False
        if side == SIDE_ALLY and self._ally_leader_slot is None:
            self._ally_leader_slot = index
        elif index == self._ally_leader_slot and side != SIDE_ALLY:
            self._ally_leader_slot = next(
                (slot_index for slot_index, item in enumerate(self._slots) if item.side == SIDE_ALLY),
                None,
            )
        return LineupMutationResult(True)

    def set_ally_leader(self, index: int) -> bool:
        """将已确认的我方武将设为主将。"""
        self._check_index(index)
        if self._slots[index].side != SIDE_ALLY:
            return False
        self._ally_leader_slot = index
        return True

    def replace_hero(self, index: int, hero: Hero) -> None:
        """替换一个槽位，并要求重新确认全部敌我阵营。"""
        self._check_index(index)
        self._slots[index] = LineupSlot(hero=hero, recognized_name=hero.name)
        self._slots = [replace(slot, team="", side="") for slot in self._slots]
        self._ally_leader_slot = None
        self._analysis_confirmed = False

    def validate(self) -> LineupValidationResult:
        """返回阵容是否可用于分析，以及当前最直接的失败原因。"""
        heroes = [slot.hero for slot in self._slots if slot.hero]
        if len(heroes) != self.SLOT_COUNT:
            return LineupValidationResult(
                False,
                "missing_hero",
                "需要识别或手动补全四名本地武将。",
            )
        if len({hero.id for hero in heroes}) != self.SLOT_COUNT:
            return LineupValidationResult(
                False,
                "duplicate_hero",
                "阵容中存在重复武将，请手动替换后再确认。",
            )
        if self.unresolved_count:
            return LineupValidationResult(
                False,
                "side_unconfirmed",
                f"还有 {self.unresolved_count} 名武将未确认敌我。",
            )
        if self.sides.count(SIDE_ALLY) != 2 or self.sides.count(SIDE_ENEMY) != 2:
            return LineupValidationResult(
                False,
                "invalid_side_count",
                "我方和敌方各需要两名武将。",
            )
        return LineupValidationResult(True)

    def can_confirm(self) -> bool:
        """四名不同武将且敌我各两名时，阵容可由用户确认。"""
        return self.validate().is_valid

    def is_confirmed(self) -> bool:
        """兼容确认规则的语义化名称。"""
        return self.can_confirm()

    def confirm(self) -> bool:
        """确认当前可用阵容，允许生成攻略。"""
        if not self.can_confirm():
            return False
        self._analysis_confirmed = True
        return True

    def clear(self) -> None:
        """清空全部识别结果和确认状态。"""
        self._slots = [LineupSlot() for _ in range(self.SLOT_COUNT)]
        self._ally_leader_slot = None
        self._analysis_confirmed = False
        self._recognized_at = ""

    def _check_index(self, index: int) -> None:
        if not 0 <= index < self.SLOT_COUNT:
            raise IndexError(f"槽位索引超出范围: {index}")

    @staticmethod
    def _ocr_sort_key(item: dict) -> int:
        try:
            return int(item.get("index", 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _read_confidence(value: object) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
