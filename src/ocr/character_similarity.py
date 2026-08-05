"""基于字符特征的武将名称纠错。"""

from __future__ import annotations

import logging

from src.ocr.character_feature_repository import CharacterFeatureRepository

logger = logging.getLogger(__name__)


class CharacterSimilarityService:
    """按编辑距离筛选，并以汉字视觉特征决胜名称候选。"""

    EDIT_DISTANCE_THRESHOLD = 1
    SAFE_CHARACTER_SIMILARITY = 0.55
    # 字形相似度维度权重：四角 30% + 仓颉 30% + 五笔 40%（选型依据见 docs/design/character_similarity_design.md）
    FOUR_CORNER_WEIGHT = 0.3
    CANGJIE_WEIGHT = 0.3
    WUBI_WEIGHT = 0.4
    # 确定性纠错映射：OCR 高频且多维相似度不足的「错字 → 正字」，命中即视为安全。
    SAFE_SUBSTITUTION_WHITELIST: dict[str, str] = {
        "昧": "眜", "敦": "惇", "邵": "绍", "雨": "羽", "半": "芈", "易": "勖",
        "赞": "瓒", "桥": "乔", "正": "政", "旦": "且", "菲": "非", "睢": "雎",
        "表": "袁", "央": "英", "合": "郃", "神": "禅", "菜": "蔡", "种": "钟",
        "翡": "翦",
    }

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
        similarity = self.single_substitution_similarity(text, candidate)
        return similarity is not None and similarity >= self.SAFE_CHARACTER_SIMILARITY

    def single_substitution_similarity(self, text: str, candidate: str) -> float | None:
        """返回等长名称唯一错字的字形相似度；其他编辑类型不参与评分。"""
        if len(text) != len(candidate):
            return None
        mismatches = [
            (source, target)
            for source, target in zip(text, candidate)
            if source != target
        ]
        if len(mismatches) != 1:
            return None
        source, target = mismatches[0]
        if self.SAFE_SUBSTITUTION_WHITELIST.get(source) == target:
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
        return sum(left == right for left, right in zip(first_code, second_code)) / 4.0

    def _cangjie_score(self, first: str, second: str) -> float:
        first_code = self._value(first, "cangjie").strip().upper()
        second_code = self._value(second, "cangjie").strip().upper()
        if not first_code or not second_code:
            return 0.0
        distance = self._levenshtein_distance(first_code, second_code)
        return max(0.0, 1.0 - distance / max(len(first_code), len(second_code)))

    def _wubi_score(self, first: str, second: str) -> float:
        first_code = self._value(first, "wubi").strip().upper()
        second_code = self._value(second, "wubi").strip().upper()
        if not first_code or not second_code:
            return 0.0
        distance = self._levenshtein_distance(first_code, second_code)
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
