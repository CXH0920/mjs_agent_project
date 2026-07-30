"""对局攻略阵容的纯状态与确认规则。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from src.data.models import Hero

SIDE_ALLY = "ally"
SIDE_ENEMY = "enemy"
_TEAMS = frozenset({"楚军", "汉军"})


@dataclass(frozen=True)
class LineupSlot:
    """一个对局攻略槽位的识别和确认状态。"""

    hero: Hero | None = None
    recognized_name: str = ""
    raw_name: str = ""
    candidates: tuple[str, ...] = ()
    resolution: str = "unknown"
    evidence: tuple[dict, ...] = ()
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
    PLAYER_SLOT_INDEX = 5
    ENEMY_SLOT_INDICES = frozenset({1, 2})
    TEAMMATE_SLOT_INDICES = frozenset({3, 4})

    def __init__(self) -> None:
        self._slots = [LineupSlot() for _ in range(self.SLOT_COUNT)]
        self._ally_leader_slot: int | None = None
        self._analysis_confirmed = False
        self._recognized_at = ""
        self._team_labels_match_positions: bool | None = None

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
    def team_labels_match_positions(self) -> bool | None:
        """返回阵营标签与固定席位规则是否一致；标签缺失时返回 None。"""
        return self._team_labels_match_positions

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
        recognized_items = [
            item for item in sorted(ocr_results, key=self._ocr_sort_key)
            if self._has_name_identity(item)
        ]
        player_item = next(
            (item for item in recognized_items if self._ocr_sort_key(item) == self.PLAYER_SLOT_INDEX),
            None,
        )
        if player_item is None:
            selected_items = recognized_items[:self.SLOT_COUNT]
        else:
            selected_items = [
                item for item in recognized_items if item is not player_item
            ][:self.SLOT_COUNT - 1]
            selected_items.append(player_item)

        teammate_items = [
            item for item in selected_items
            if self._ocr_sort_key(item) in self.TEAMMATE_SLOT_INDICES
        ]
        has_unique_teammate = len(teammate_items) == 1
        confirmed_names = [
            str(item.get("name", "")).strip()
            for item in selected_items if self._is_confirmed_item(item)
        ]
        has_unique_names = (
            len(confirmed_names) == len(selected_items)
            and len(set(confirmed_names)) == len(confirmed_names)
        )

        slots: list[LineupSlot] = []
        for item in selected_items:
            name = str(item.get("name", "")).strip()
            raw_name = str(item.get("raw_name", "")).strip()
            resolution = str(item.get("resolution", "exact" if name else "unknown"))
            team = str(item.get("team", "")).strip()
            source_index = self._ocr_sort_key(item)
            slots.append(LineupSlot(
                hero=hero_by_name(name) if self._is_confirmed_item(item) else None,
                recognized_name=name or raw_name,
                raw_name=raw_name,
                candidates=tuple(item.get("candidates") or ()),
                resolution=resolution,
                evidence=tuple(item.get("evidence") or ()),
                confidence=self._read_confidence(item.get("confidence", 0.0)),
                team=team,
                side=self._side_from_position(
                    source_index,
                    player_item is not None and has_unique_names,
                    has_unique_teammate,
                ),
            ))

        self._slots = slots + [LineupSlot() for _ in range(self.SLOT_COUNT - len(slots))]
        self._ally_leader_slot = next(
            (
                index for index, item in enumerate(selected_items)
                if self._ocr_sort_key(item) == self.PLAYER_SLOT_INDEX
                and self._slots[index].side == SIDE_ALLY
            ),
            None,
        )
        self._team_labels_match_positions = self._check_team_labels(selected_items)
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
        self._slots[index] = LineupSlot(
            hero=hero,
            recognized_name=hero.name,
            raw_name=hero.name,
            candidates=(hero.name,),
            resolution="manual",
        )
        self._slots = [replace(slot, team="", side="") for slot in self._slots]
        self._ally_leader_slot = None
        self._analysis_confirmed = False
        self._team_labels_match_positions = None

    def validate(self) -> LineupValidationResult:
        """返回阵容是否可用于分析，以及当前最直接的失败原因。"""
        pending_names = sum(
            slot.hero is None
            and slot.resolution in {"unresolved", "conflict"}
            and bool(slot.recognized_name or slot.candidates)
            for slot in self._slots
        )
        if pending_names:
            return LineupValidationResult(
                False,
                "unresolved_name",
                f"还有 {pending_names} 名武将名称待确认。",
            )
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
        self._team_labels_match_positions = None

    def _check_index(self, index: int) -> None:
        if not 0 <= index < self.SLOT_COUNT:
            raise IndexError(f"槽位索引超出范围: {index}")

    @classmethod
    def _side_from_position(
        cls,
        source_index: int,
        has_player: bool,
        has_unique_teammate: bool,
    ) -> str:
        if not has_player:
            return ""
        if source_index in cls.ENEMY_SLOT_INDICES:
            return SIDE_ENEMY
        if source_index == cls.PLAYER_SLOT_INDEX:
            return SIDE_ALLY
        if has_unique_teammate and source_index in cls.TEAMMATE_SLOT_INDICES:
            return SIDE_ALLY
        return ""

    @classmethod
    def _check_team_labels(cls, items: list[dict]) -> bool | None:
        teams = {
            cls._ocr_sort_key(item): str(item.get("team", "")).strip()
            for item in items
        }
        player_team = teams.get(cls.PLAYER_SLOT_INDEX, "")
        teammate_teams = [
            teams[index] for index in cls.TEAMMATE_SLOT_INDICES if index in teams
        ]
        enemy_teams = [
            teams[index] for index in cls.ENEMY_SLOT_INDICES if index in teams
        ]
        if (
            player_team not in _TEAMS
            or len(teammate_teams) != 1
            or teammate_teams[0] not in _TEAMS
            or len(enemy_teams) != len(cls.ENEMY_SLOT_INDICES)
            or any(team not in _TEAMS for team in enemy_teams)
        ):
            return None
        return teammate_teams[0] == player_team and all(team != player_team for team in enemy_teams)

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

    @staticmethod
    def _has_name_identity(item: dict) -> bool:
        return bool(
            str(item.get("name", "")).strip()
            or str(item.get("raw_name", "")).strip()
            or item.get("candidates")
        )

    @staticmethod
    def _is_confirmed_item(item: dict) -> bool:
        name = str(item.get("name", "")).strip()
        return bool(name) and item.get("resolution") not in {
            "unresolved", "unknown", "conflict",
        }
