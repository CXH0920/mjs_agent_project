"""基于字符特征的武将名称纠错。"""

from __future__ import annotations

import json
import logging

from src.config.env import PROJECT_ROOT
from src.ocr.character_feature_repository import CharacterFeatureRepository

logger = logging.getLogger(__name__)

# 用户层白名单文件：基线表之外的人工补充对（经"白名单配置"界面写入），
# 加载时与基线合并为生效表；文件缺失或损坏仅降级基线，不影响识别。
OVERRIDES_PATH = PROJECT_ROOT / "data" / "ocr_confusion_overrides.json"


def find_whitelist_conflicts(
    hero_names: list[str], whitelist_pairs: dict[str, str],
) -> list[tuple[str, str, str]]:
    """枚举词表中「等长仅差一字、且该差异对在白名单内」的高危武将名对。

    这类名对意味着白名单会在两个真实名字之间单方面拉边（误绑风险），
    供新增白名单对或新武将入库时做常驻检查。
    """
    conflicts: list[tuple[str, str, str]] = []
    for index, first in enumerate(hero_names):
        for second in hero_names[index + 1:]:
            if len(first) != len(second):
                continue
            diffs = [
                (a, b) for a, b in zip(first, second, strict=True) if a != b
            ]
            if len(diffs) != 1:
                continue
            source, target = diffs[0]
            if whitelist_pairs.get(source) == target:
                conflicts.append((first, second, f"{source}→{target}"))
            elif whitelist_pairs.get(target) == source:
                conflicts.append((first, second, f"{target}→{source}"))
    return conflicts


def levenshtein_distance(first: str, second: str) -> int:
    """两串的最小编辑距离；名称纠错与官方导入的候选筛选共用。"""
    if len(first) < len(second):
        return levenshtein_distance(second, first)
    if not second:
        return len(first)
    previous_row = range(len(second) + 1)
    for index, first_char in enumerate(first):
        current_row = [index + 1]
        for second_index, second_char in enumerate(second):
            cost = 0 if first_char == second_char else 1
            current_row.append(min(
                current_row[second_index] + 1,
                previous_row[second_index + 1] + 1,
                previous_row[second_index] + cost,
            ))
        previous_row = current_row
    return previous_row[-1]


class CharacterSimilarityService:
    """按编辑距离筛选，并以汉字视觉特征决胜名称候选。"""

    EDIT_DISTANCE_THRESHOLD = 1
    SAFE_CHARACTER_SIMILARITY = 0.55
    # 字形相似度维度权重：四角 30% + 仓颉 30% + 五笔 40%（选型依据见 docs/design/character_similarity_design.md）
    FOUR_CORNER_WEIGHT = 0.3
    CANGJIE_WEIGHT = 0.3
    WUBI_WEIGHT = 0.4
    # 确定性纠错映射：OCR 高频且多维相似度不足的「错字 → 正字」，命中即视为安全。
    # 仅保留新预处理（纯放大、无增强）时代实测仍活跃的对；2026-09-17 移除的
    # 16 对增强时代旧错法（敦惇/邵绍/雨羽/赞瓒/桥乔/正政/旦且/菲非/睢雎/
    # 表袁/央英/合郃/神禅/种钟/易勖/菜蔡）经出场统计与移除重放证实已不再发生，
    # 如复发会由错法频次记录重新捕获，经"白名单配置"界面补回。
    SAFE_SUBSTITUTION_WHITELIST: dict[str, str] = {
        "昧": "眜", "半": "芈", "翡": "翦", "会": "哙",
        "助": "勖", "歇": "勖", "怀": "惇",
    }

    def __init__(self, repository: CharacterFeatureRepository | None = None) -> None:
        self._repository = repository or CharacterFeatureRepository()
        self._effective_whitelist = dict(self.SAFE_SUBSTITUTION_WHITELIST)
        self._load_overrides()

    def _load_overrides(self) -> None:
        """合并用户层白名单文件；缺失仅用基线，损坏/非法条目降级并告警。"""
        if not OVERRIDES_PATH.exists():
            return
        try:
            document = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8"))
            pairs = document["pairs"]
            if not isinstance(document, dict) or not isinstance(pairs, dict):
                raise ValueError("根节点必须为含 pairs 对象的对象")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("用户层白名单加载失败，仅使用基线表: %s", exc)
            return
        merged = dict(self.SAFE_SUBSTITUTION_WHITELIST)
        for source, target in pairs.items():
            if (
                not isinstance(source, str) or not isinstance(target, str)
                or len(source) != 1 or len(target) != 1 or source == target
            ):
                logger.warning("用户层白名单条目非法，已跳过: %r→%r", source, target)
                continue
            merged[source] = target
        self._effective_whitelist = merged
        logger.debug(
            "用户层白名单已合并: 基线 %d 对 + 用户层 %d 对",
            len(self.SAFE_SUBSTITUTION_WHITELIST), len(pairs),
        )

    def reload_whitelist(self) -> None:
        """重读用户层白名单并重建生效表，供界面写入后调用。"""
        self._effective_whitelist = dict(self.SAFE_SUBSTITUTION_WHITELIST)
        self._load_overrides()

    def warmup(self) -> None:
        self._repository.warmup()

    def warmup_hero_names(self, hero_names: list[str]) -> int:
        """提前补齐词表字符，避免首次候选决胜触发动态特征查询。"""
        return self._repository.warmup_characters(
            char for hero_name in hero_names for char in hero_name
        )

    def correct_hero_name(self, text: str, hero_names: list[str]) -> str:
        """将 OCR 文本矫正为最接近的武将名称。"""
        if not text:
            return text
        text = text.strip()
        candidates = [
            hero for hero in hero_names
            if levenshtein_distance(text, hero) <= self.EDIT_DISTANCE_THRESHOLD
        ]
        if not candidates:
            return text
        if len(candidates) == 1:
            if candidates[0] != text:
                logger.debug("矫正: %s → %s", text, candidates[0])
            return candidates[0]
        best_match = self._pick_visually_similar(text, candidates)
        if best_match != text:
            logger.debug("矫正: %s → %s (候选=%s)", text, best_match, candidates)
        return best_match

    def is_safe_single_substitution(self, text: str, candidate: str) -> bool:
        """仅在等长名称恰有一个错字且字形足够接近时允许自动纠正。"""
        similarity = self.single_substitution_similarity(text, candidate)
        return similarity is not None and similarity >= self.SAFE_CHARACTER_SIMILARITY

    def single_substitution_similarity(self, text: str, candidate: str) -> float | None:
        """返回等长名称唯一错字的字形相似度；其他编辑类型不参与评分。"""
        if len(text) != len(candidate):
            return None
        mismatches = [
            (source, target)
            for source, target in zip(text, candidate, strict=False)
            if source != target
        ]
        if len(mismatches) != 1:
            return None
        source, target = mismatches[0]
        if self._effective_whitelist.get(source) == target:
            return 1.0
        return self._multi_dim_similarity(source, target)

    def rank_single_substitution_candidates(
        self, text: str, candidates: list[str] | set[str],
    ) -> list[tuple[str, float]]:
        """仅在给定候选闭包内按唯一错字字形相似度降序排列。"""
        scored = []
        for candidate in candidates:
            similarity = self.single_substitution_similarity(text, candidate)
            if similarity is not None:
                scored.append((candidate, similarity))
        return sorted(scored, key=lambda item: (-item[1], item[0]))

    def _pick_visually_similar(self, text: str, candidates: list[str]) -> str:
        scored = [(self._visual_score(text, candidate), candidate) for candidate in candidates]
        scored.sort(key=lambda item: (-item[0], item[1]))
        index = 0
        while index < len(scored):
            end = index
            while end + 1 < len(scored) and abs(scored[end][0] - scored[end + 1][0]) < 1e-9:
                end += 1
            if end > index:
                scored[index:end + 1] = sorted(
                    scored[index:end + 1],
                    key=lambda item: self._tie_break_key(text, item[1]),
                )
            index = end + 1
        best_match = scored[0][1]
        if best_match != text:
            logger.debug(
                "多维相似度: %s → %s (scores=%s)",
                text,
                best_match,
                [f"{candidate}={score:.2f}" for score, candidate in scored],
            )
        return best_match

    def _visual_score(self, text: str, candidate: str) -> float:
        score = 0.0
        for text_char, candidate_char in zip(text, candidate, strict=False):
            score += 1.0 if text_char == candidate_char else self._multi_dim_similarity(text_char, candidate_char)
        extra = abs(len(candidate) - len(text))
        return score - extra

    def _tie_break_key(self, text: str, candidate: str) -> tuple[float, int]:
        pinyin_score = 0.0
        stroke_difference = 0
        for text_char, candidate_char in zip(text, candidate, strict=False):
            if text_char == candidate_char:
                pinyin_score += 1.0
                continue
            pinyin_score += self._pinyin_similarity(text_char, candidate_char)
            stroke_difference += self._stroke_difference(text_char, candidate_char)
        return -pinyin_score, stroke_difference

    def _multi_dim_similarity(self, first: str, second: str) -> float:
        return (
            self._four_corner_score(first, second) * self.FOUR_CORNER_WEIGHT
            + self._cangjie_score(first, second) * self.CANGJIE_WEIGHT
            + self._wubi_score(first, second) * self.WUBI_WEIGHT
        )

    def _four_corner_score(self, first: str, second: str) -> float:
        first_code = "".join(
            char for char in self._value(first, "four_corner") if char.isdigit()
        )[:4]
        second_code = "".join(
            char for char in self._value(second, "four_corner") if char.isdigit()
        )[:4]
        if len(first_code) != 4 or len(second_code) != 4:
            return 0.0
        return sum(left == right for left, right in zip(first_code, second_code, strict=False)) / 4.0

    def _cangjie_score(self, first: str, second: str) -> float:
        first_code = self._value(first, "cangjie").strip().upper()
        second_code = self._value(second, "cangjie").strip().upper()
        if not first_code or not second_code:
            return 0.0
        distance = levenshtein_distance(first_code, second_code)
        return max(0.0, 1.0 - distance / max(len(first_code), len(second_code)))

    def _wubi_score(self, first: str, second: str) -> float:
        first_code = self._value(first, "wubi").strip().upper()
        second_code = self._value(second, "wubi").strip().upper()
        if not first_code or not second_code:
            return 0.0
        distance = levenshtein_distance(first_code, second_code)
        return max(0.0, 1.0 - distance / max(len(first_code), len(second_code)))

    def _pinyin_similarity(self, first: str, second: str) -> float:
        first_pinyin = self._value(first, "pinyin")
        second_pinyin = self._value(second, "pinyin")
        return 1.0 if first_pinyin and first_pinyin == second_pinyin else 0.0

    def _stroke_difference(self, first: str, second: str) -> int:
        return abs(self._stroke_value(first) - self._stroke_value(second))

    def _stroke_value(self, char: str) -> int:
        value = self._value(char, "total_strokes")
        try:
            return int(value)
        except ValueError:
            return 0

    def _value(self, char: str, key: str) -> str:
        return self._repository.get_value(char, key)
