"""基于字符特征的武将名称纠错。"""

from __future__ import annotations

import difflib
import logging

from src.ocr.character_feature_repository import CharacterFeatureRepository

logger = logging.getLogger(__name__)


class CharacterSimilarityService:
    """按编辑距离筛选，并以汉字视觉特征决胜名称候选。"""

    EDIT_DISTANCE_THRESHOLD = 1
    SAFE_CHARACTER_SIMILARITY = 0.55

    def __init__(self, repository: CharacterFeatureRepository | None = None) -> None:
        self._repository = repository or CharacterFeatureRepository()

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
            if self._levenshtein_distance(text, hero) <= self.EDIT_DISTANCE_THRESHOLD
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
        if len(text) != len(candidate):
            return False
        mismatches = [
            (source, target)
            for source, target in zip(text, candidate)
            if source != target
        ]
        return len(mismatches) == 1 and (
            self._multi_dim_similarity(*mismatches[0]) >= self.SAFE_CHARACTER_SIMILARITY
        )

    @staticmethod
    def _levenshtein_distance(first: str, second: str) -> int:
        if len(first) < len(second):
            return CharacterSimilarityService._levenshtein_distance(second, first)
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
        for text_char, candidate_char in zip(text, candidate):
            score += 1.0 if text_char == candidate_char else self._multi_dim_similarity(text_char, candidate_char)
        extra = abs(len(candidate) - len(text))
        return score - extra

    def _tie_break_key(self, text: str, candidate: str) -> tuple[float, int]:
        pinyin_score = 0.0
        stroke_difference = 0
        for text_char, candidate_char in zip(text, candidate):
            if text_char == candidate_char:
                pinyin_score += 1.0
                continue
            pinyin_score += self._pinyin_similarity(text_char, candidate_char)
            stroke_difference += self._stroke_difference(text_char, candidate_char)
        return -pinyin_score, stroke_difference

    def _multi_dim_similarity(self, first: str, second: str) -> float:
        return (
            self._four_corner_score(first, second) * 0.4
            + self._cangjie_score(first, second) * 0.4
            + self._radical_score(first, second) * 0.2
        )

    def _four_corner_score(self, first: str, second: str) -> float:
        first_code = "".join(char for char in self._value(first, "four_corner") if char.isdigit())
        second_code = "".join(char for char in self._value(second, "four_corner") if char.isdigit())
        first_code = (first_code + "00000")[:5]
        second_code = (second_code + "00000")[:5]
        return sum(left == right for left, right in zip(first_code, second_code)) / 5.0

    def _cangjie_score(self, first: str, second: str) -> float:
        first_code = self._value(first, "cangjie")
        second_code = self._value(second, "cangjie")
        if not first_code or not second_code:
            return 0.0
        return difflib.SequenceMatcher(None, first_code, second_code).ratio()

    def _radical_score(self, first: str, second: str) -> float:
        first_radical = self._value(first, "radical")
        second_radical = self._value(second, "radical")
        return 1.0 if first_radical and first_radical == second_radical else 0.0

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
