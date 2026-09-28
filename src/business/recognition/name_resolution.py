"""武将名纠错与词表消解：官方榜单 OCR 候选到词表名的纯规则判定。

从 OfficialDataImportService 拆出的纠错域：零 cv2/numpy/OCR 引擎依赖，
导入服务（识别编排）与复核对话框（候选建议）共用同一词表实例。
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter

from src.config.env import PROJECT_ROOT
from src.ocr.character_similarity import CharacterSimilarityService, levenshtein_distance

logger = logging.getLogger(__name__)

HEROES_PATH = PROJECT_ROOT / "data" / "heroes.json"

OCR_NAME_CONFUSION_PAIRS: tuple[tuple[str, str], ...] = (
    ("候", "侯"),
    ("侯", "候"),
    ("怀", "惇"),
    ("惇", "怀"),
)

# 候选池扩展距离：按全表构建纠错候选时的编辑距离上限（宽松于名称纠错的
# EDIT_DISTANCE_THRESHOLD=1——此处是"扩大候选集供后续复核"，非直接纠错）。
CANDIDATE_EXPANSION_EDIT_DISTANCE = 2

# 安全替换基线表的转发引用。CharacterSimilarityService 的该类属性自定义起
# 全仓只读（实例侧均为 dict 拷贝，无 reassign），模块级绑定不会陈旧；
# 若类属性改为实例可变，此转发失效，需改为 getter。
SAFE_SUBSTITUTION_WHITELIST = CharacterSimilarityService.SAFE_SUBSTITUTION_WHITELIST


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


def _load_hero_names() -> list[str]:
    try:
        with HEROES_PATH.open("r", encoding="utf-8") as file:
            return [item["name"] for item in json.load(file) if item.get("name")]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("无法加载武将词表: %s", exc)
        return []


class HeroNameResolver:
    """按词表对 OCR 名称做匹配、纠错与唯一性消解（纯规则，无 OCR/图像依赖）。"""

    def __init__(self, hero_names: list[str] | None = None) -> None:
        self.hero_names = hero_names or _load_hero_names()
        self._corrector = CharacterSimilarityService()

    @staticmethod
    def chinese_text(text: str) -> str:
        return "".join(re.findall(r"[\u4e00-\u9fff]", text))

    def exact_hero_matches(
        self, candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        return [
            (self.chinese_text(text), confidence)
            for text, confidence in candidates
            if self.chinese_text(text) in self.hero_names
        ]

    @staticmethod
    def select_unique_name_match(
        matches: list[tuple[str, float]],
    ) -> tuple[tuple[str, float] | None, tuple[str, ...]]:
        names = tuple(dict.fromkeys(name for name, _confidence in matches))
        if len(names) == 1:
            return max(matches, key=lambda item: item[1]), ()
        return None, names

    @staticmethod
    def common_prefix(names: tuple[str, ...] | list[str]) -> str:
        if not names:
            return ""
        prefix = names[0]
        for name in names[1:]:
            while prefix and not name.startswith(prefix):
                prefix = prefix[:-1]
        return prefix

    def _strict_prefix_matches(self, name: str) -> list[str]:
        return [hero for hero in self.hero_names if len(hero) > len(name) and hero.startswith(name)]

    def _nearby_hero_names(self, name: str) -> list[str]:
        return [
            hero for hero in self.hero_names
            if levenshtein_distance(name, hero)
            <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
        ]

    def ambiguous_name_candidates(self, name: str) -> list[str]:
        if not name:
            return []
        prefix_matches = self._strict_prefix_matches(name)
        if len(prefix_matches) > 1:
            return prefix_matches
        nearby_names = self._nearby_hero_names(name)
        if len(nearby_names) > 1 and len(self.common_prefix(nearby_names)) >= 2:
            return nearby_names
        return []

    def correct_official_name(self, name: str) -> str:
        """保留复姓公共前缀歧义，避免词表扩充后静默改绑。"""
        return self.correct_official_name_with_path(name)[0]

    def correct_official_name_with_path(self, name: str) -> tuple[str, bool]:
        """返回 (校正名, 是否经 OCR 混淆字对变体唯一命中)。"""
        if not name or name in self.hero_names or self.ambiguous_name_candidates(name):
            return name, False
        corrected = self._corrector.correct_hero_name(name, self.hero_names)
        if corrected in self.hero_names:
            return corrected, False
        reachable: set[str] = set()
        for variant in self._confusion_variants(name):
            reachable.update(self.ambiguous_name_candidates(variant))
            variant_corrected = self._corrector.correct_hero_name(variant, self.hero_names)
            if variant_corrected in self.hero_names:
                reachable.add(variant_corrected)
        if len(reachable) != 1:
            return name, False
        return next(iter(reachable)), True

    def _confusion_variants(self, name: str) -> list[str]:
        """生成仅替换一个 OCR 混淆字的变体（单字互换，保序去重）。"""
        if not name:
            return []
        variants: list[str] = []
        seen: set[str] = set()
        for index, char in enumerate(name):
            for source, target in OCR_NAME_CONFUSION_PAIRS:
                if char == source:
                    variant = name[:index] + target + name[index + 1:]
                    if variant not in seen:
                        seen.add(variant)
                        variants.append(variant)
        return variants

    def corrected_via_confusion_swap(self, original: str, final: str) -> bool:
        """仅当校正路径经过混淆字对变体时返回 True（用于复核原因标注）。"""
        if not original or original == final:
            return False
        corrected, used_swap = self.correct_official_name_with_path(original)
        return used_swap and corrected == final

    def unresolved_name_reason(self, name: str) -> str:
        if not name or name in self.hero_names:
            return ""
        ambiguous_candidates = self.ambiguous_name_candidates(name)
        if ambiguous_candidates:
            return f"武将名称候选不唯一：{'/'.join(ambiguous_candidates)}"
        return "武将名称未命中词表"

    def resolve_batch_names(self, batch: dict) -> None:
        """仅在榜单内部唯一性能够证明时补全未决武将名称。"""
        records = batch["records"]
        reviews = {
            int(review["期望排名"]): review for review in batch["reviews"]
        }
        confirmed = {
            record["武将"] for record in records
            if record["武将"] in self.hero_names
        }
        pending = {
            index: tuple(self.ambiguous_name_candidates(record["武将"]))
            for index, record in enumerate(records)
            if record["武将"] not in self.hero_names
        }

        while pending:
            proposals: dict[str, list[int]] = {}
            for index, candidates in pending.items():
                available = [name for name in candidates if name not in confirmed]
                if len(available) == 1:
                    proposals.setdefault(available[0], []).append(index)
            unique_proposals = {
                name: indexes[0] for name, indexes in proposals.items()
                if len(indexes) == 1
            }
            if not unique_proposals:
                break
            for name, index in unique_proposals.items():
                record = records[index]
                original = record["武将"]
                record["武将"] = name
                confirmed.add(name)
                pending.pop(index)
                review = reviews.get(int(record["排名"]))
                if review is not None:
                    review["异常原因"] += (
                        f"；武将名称已按榜单唯一性由{original or '空值'}补全为{name}"
                    )

    def resolve_names_across_outputs(self, outputs: dict[str, dict]) -> None:
        """跨榜单未确认名称在候选集交集唯一时统一补全，避免集合不一致误报。"""
        pending = [
            (output_name, index, record)
            for output_name, batch in outputs.items()
            for index, record in enumerate(batch["records"])
            if record["武将"] not in self.hero_names
        ]
        if not pending:
            return
        candidate_sets: list[set[str]] = []
        for _output_name, _index, record in pending:
            name = record["武将"]
            candidates: set[str] = set()
            for hero in self.hero_names:
                if levenshtein_distance(name, hero) <= CANDIDATE_EXPANSION_EDIT_DISTANCE:
                    candidates.add(hero)
            candidates.update(self.ambiguous_name_candidates(name))
            corrected = self.correct_official_name(name)
            if corrected in self.hero_names:
                candidates.add(corrected)
            for variant in self._confusion_variants(name):
                candidates.update(self.ambiguous_name_candidates(variant))
                variant_corrected = self.correct_official_name(variant)
                if variant_corrected in self.hero_names:
                    candidates.add(variant_corrected)
            candidates.discard(name)
            candidate_sets.append(candidates)
        if any(not candidates for candidates in candidate_sets):
            return
        common = set.intersection(*candidate_sets)
        if len(common) != 1:
            return
        target = next(iter(common))
        for output_name, index, record in pending:
            original = record["武将"]
            record["武将"] = target
            review = next(
                (
                    review for review in outputs[output_name]["reviews"]
                    if int(review["期望排名"]) == int(record["排名"])
                ),
                None,
            )
            if review is not None:
                review["异常原因"] += (
                    "；武将名称已按跨榜单一致性由"
                    + (original or "空值")
                    + "补全为" + target
                )

    def validate_output_names(self, outputs: dict[str, dict]) -> list[str]:
        """返回阻止正式 CSV 覆盖的名称完整性错误。"""
        errors = []
        name_sets: list[tuple[str, int, set[str]]] = []
        hero_names = set(self.hero_names)
        if not hero_names:
            return ["武将词表为空"]
        for output_name, batch in outputs.items():
            records = batch["records"]
            unknown = [
                f"{record['排名']}:{record['武将'] or '空值'}"
                for record in records if record["武将"] not in hero_names
            ]
            counts = Counter(record["武将"] for record in records if record["武将"])
            duplicates = [
                f"{name}({','.join(str(record['排名']) for record in records if record['武将'] == name)})"
                for name, count in counts.items() if count > 1
            ]
            if unknown:
                errors.append(f"{output_name} 存在未确认武将：{','.join(unknown)}")
            if duplicates:
                errors.append(f"{output_name} 存在重复武将：{','.join(duplicates)}")
            name_sets.append((output_name, len(records), set(counts)))

        for index, (left_name, left_count, left_names) in enumerate(name_sets):
            for right_name, right_count, right_names in name_sets[index + 1:]:
                if left_count == right_count and left_names != right_names:
                    errors.append(f"{left_name} 与 {right_name} 的武将集合不一致")
        return errors

    def normalize_name(self, value: tuple[str, float]) -> tuple[str, float]:
        text, confidence = value
        name = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        if not name or not self.hero_names:
            return name, confidence
        if len(name) == 1:
            return name, confidence
        return self.correct_official_name(name), confidence

    def review_candidates(self, ocr_name: str, current: str | None = None) -> list[str]:
        """为复核界面提供候选武将名：当前值 ∪ 距离≤2/歧义候选，空则全表按距离排序。"""
        candidates: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            if name and name not in seen:
                seen.add(name)
                candidates.append(name)

        if current and current in self.hero_names:
            add(current)
        if ocr_name in self.hero_names:
            add(ocr_name)
        for hero in self.hero_names:
            if (
                levenshtein_distance(ocr_name, hero) <= CANDIDATE_EXPANSION_EDIT_DISTANCE
                and (any(char in hero for char in ocr_name) or len(ocr_name) != len(hero))
            ):
                add(hero)
        for hero in self.ambiguous_name_candidates(ocr_name):
            add(hero)
        if not candidates:
            candidates.extend(sorted(
                self.hero_names,
                key=lambda hero: (
                    levenshtein_distance(ocr_name, hero),
                    hero,
                ),
            ))
        return candidates

    def is_known_hero_name(self, name: str) -> bool:
        """判断名称是否在词表中（复核界面统计用）。"""
        return name in self.hero_names
