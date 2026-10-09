# -*- coding: utf-8 -*-
"""武将名称证据解析与页面级消歧（审计 G3 切片 4.6a，自 GeneralRecognizer 出仓）。

识别策略中的纯决策层：按字数门禁建立候选闭包，多路证据在候选交集内确认；
等长仅错一字时用结构化字形评分决胜；全部证据族高置信一致读出词表外原文时
不强制纠错绑定（新武将保护）。零 numpy/零引擎依赖——只消费词表与
CharacterSimilarityService，可独立单测。预处理、批量画布与引擎生命周期
仍在 GeneralRecognizer。
"""

from __future__ import annotations

from src.ocr.character_similarity import CharacterSimilarityService, levenshtein_distance

_UNIQUE_PREFIX_MIN_LENGTH = 2
# 单槽回退触发线（B1 按 v6 置信分布定标：v6 0.7~0.99 中带占 17.9%、v4 仅 7.5%，
# 0.75 使触发率回到 v4 时代 0.8 的水平，避免切换后单槽回退量放大约一个数量级）
_NAME_RECHECK_CONFIDENCE = 0.75
_MULTI_CANDIDATE_MIN_CONFIDENCE = 0.7
_MULTI_CANDIDATE_MIN_SIMILARITY = 0.35
_MULTI_CANDIDATE_MIN_MARGIN = 0.15
_MULTI_CANDIDATE_MIN_EVIDENCE_FAMILIES = 2
# 词表外新武将保护：全部证据族以不低于此值的置信度一致读出同一词表外原文时，
# 判定为新武将而不强制纠错绑定（历史 _HIGH_CONFIDENCE 保护在证据体系迁移中遗失后重建）
_UNMATCHED_CONSENSUS_MIN_CONFIDENCE = 0.995
# 消解状态词表单源（此前 peak_select_watcher / recognizer / poll_coordinator /
# match_lineup_state / match_guide_panel / recommendation_panel / ocr_worker 各自
# 手抄副本，差异无注释易被当成漏同步"修正"）。已确认集与未定全集互补；
# 收窄集是未定全集的有意子集：unknown 拒识无读数无候选、没有可供下一步
# （复核注入 / 人工确认 / 待确认展示）消费的材料，各调用处另有守卫兜底
CONFIRMED_RESOLUTIONS = frozenset({
    "exact", "unique_prefix", "unique_similarity", "multi_similarity",
    "slot_unique", "manual",
})
UNRESOLVED_RESOLUTIONS = frozenset({"unresolved", "unknown", "conflict"})
ACTIONABLE_UNRESOLVED_RESOLUTIONS = frozenset({"unresolved", "conflict"})
_RESOLUTION_PRIORITY = {
    "manual": 5,
    "exact": 4,
    "unique_prefix": 3,
    "unique_similarity": 2,
    "multi_similarity": 2,
    "slot_unique": 1,
}


class NameResolver:
    """名称证据解析器：候选闭包确认、相似度决胜与页面唯一性消歧。"""

    def __init__(self, hero_names: list[str],
                 similarity_service: CharacterSimilarityService) -> None:
        self._hero_names = hero_names or []
        self._similarity_service = similarity_service

    # ── 槽位结果 ──────────────────────────────────────────────────────

    @staticmethod
    def empty_name_result(index: int) -> dict:
        return {
            "index": index,
            "raw_name": "",
            "name": "",
            "candidates": [],
            "resolution": "unknown",
            "length_mode": "unknown",
            "confidence": 0.0,
            "evidence": [],
        }

    @staticmethod
    def append_evidence(
        evidence: list[dict], source: str, text: str, confidence: float,
    ) -> None:
        normalized = text.strip()
        if normalized:
            evidence.append({
                "source": source,
                "text": normalized,
                "confidence": round(float(confidence), 4),
            })

    @staticmethod
    def requires_slot_recheck(result: dict, text: str, confidence: float) -> bool:
        return (
            not text
            or confidence < _NAME_RECHECK_CONFIDENCE
            or result["resolution"] in UNRESOLVED_RESOLUTIONS
        )

    # ── 证据确认 ──────────────────────────────────────────────────────

    def resolve_name_evidence(self, index: int, evidence: list[dict]) -> dict:
        """在各路证据候选闭包的交集内确认名称。"""
        result = self.empty_name_result(index)
        result["evidence"] = list(evidence)
        if not evidence:
            return result

        strongest = max(evidence, key=lambda item: float(item.get("confidence", 0.0)))
        result["raw_name"] = str(strongest.get("text", "")).strip()
        result["confidence"] = round(float(strongest.get("confidence", 0.0)), 4)
        parsed = [self.parse_name_evidence(item) for item in evidence]
        candidate_sets = [set(item["candidates"]) for item in parsed if item["candidates"]]
        candidate_union = set().union(*candidate_sets) if candidate_sets else set()
        length_modes = {
            item["length_mode"] for item in parsed if item["candidates"]
        }
        if len(length_modes) == 1:
            result["length_mode"] = next(iter(length_modes))
        elif length_modes:
            result["length_mode"] = "uncertain"

        exact_names = {item["name"] for item in parsed if item["resolution"] == "exact"}
        if len(exact_names) == 1:
            name = exact_names.pop()
            if any(name not in candidates for candidates in candidate_sets):
                result.update(
                    candidates=sorted(candidate_union | {name}),
                    resolution="conflict",
                )
                return result
            result.update(name=name, resolution="exact")
            result["candidates"] = [result["name"]]
            return result
        if len(exact_names) > 1:
            result["resolution"] = "conflict"
            result["candidates"] = sorted(candidate_union | exact_names)
            return result

        confirmed = {
            item["name"] for item in parsed
            if item["resolution"] in CONFIRMED_RESOLUTIONS and item["name"]
        }
        if len(confirmed) == 1:
            name = confirmed.pop()
            if any(name not in candidates for candidates in candidate_sets):
                result.update(
                    candidates=sorted(candidate_union | {name}),
                    resolution="conflict",
                )
                return result
            resolutions = [
                item["resolution"] for item in parsed if item["name"] == name
            ]
            result.update(
                name=name,
                candidates=[name],
                resolution=max(resolutions, key=_RESOLUTION_PRIORITY.get),
            )
            return result
        if len(confirmed) > 1:
            result["resolution"] = "conflict"
            result["candidates"] = sorted(candidate_union | confirmed)
            return result

        consensus = self.unmatched_consensus_name(evidence)
        if consensus:
            if not candidate_sets:
                result.update(candidates=[], resolution="unknown_new_hero")
            else:
                # 词表外但存在候选：可能是新武将（王导），也可能是生僻字被稳定
                # 误读或整字漏识（王濬→"王"），抑制评分决胜、保留候选走人工确认
                result.update(candidates=sorted(candidate_union), resolution="unresolved")
            return result

        if not candidate_sets:
            return result

        common = set.intersection(*candidate_sets)
        if not common:
            result.update(candidates=sorted(candidate_union), resolution="conflict")
            return result

        winner = self.resolve_multi_candidate_similarity(evidence, parsed, common)
        if winner:
            result.update(
                name=winner,
                candidates=[winner],
                resolution="multi_similarity",
            )
            return result

        result.update(candidates=sorted(common), resolution="unresolved")
        return result

    def parse_name_evidence(self, evidence: dict) -> dict:
        text = str(evidence.get("text", "")).strip()
        if not text:
            return {
                "name": "",
                "candidates": [],
                "resolution": "unknown",
                "length_mode": "unknown",
            }
        if text in self._hero_names:
            return {
                "name": text,
                "candidates": [text],
                "resolution": "exact",
                "length_mode": "complete",
            }
        prefix_candidates = [
            hero for hero in self._hero_names
            if len(hero) > len(text) and hero.startswith(text)
        ]
        same_length_candidates = [
            hero for hero in self._hero_names
            if len(hero) == len(text)
            if levenshtein_distance(text, hero)
            <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
        ]
        if prefix_candidates and same_length_candidates:
            return {
                "name": "",
                "candidates": sorted(set(prefix_candidates) | set(same_length_candidates)),
                "resolution": "unresolved",
                "length_mode": "uncertain",
            }
        if len(prefix_candidates) == 1 and len(text) >= _UNIQUE_PREFIX_MIN_LENGTH:
            return {
                "name": prefix_candidates[0],
                "candidates": prefix_candidates,
                "resolution": "unique_prefix",
                "length_mode": "missing",
            }
        if prefix_candidates:
            return {
                "name": "",
                "candidates": prefix_candidates,
                "resolution": "unresolved",
                "length_mode": "missing",
            }
        if len(same_length_candidates) == 1 and self._similarity_service.is_safe_single_substitution(
            text, same_length_candidates[0],
        ):
            return {
                "name": same_length_candidates[0],
                "candidates": same_length_candidates,
                "resolution": "unique_similarity",
                "length_mode": "complete",
            }
        if same_length_candidates:
            return {
                "name": "",
                "candidates": same_length_candidates,
                "resolution": "unresolved",
                "length_mode": "complete",
            }
        length_mismatch_candidates = [
            hero for hero in self._hero_names
            if levenshtein_distance(text, hero)
            <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
        ]
        return {
            "name": "",
            "candidates": length_mismatch_candidates,
            "resolution": "unresolved" if length_mismatch_candidates else "unknown",
            "length_mode": "uncertain" if length_mismatch_candidates else "unknown",
        }

    def resolve_multi_candidate_similarity(
        self,
        evidence: list[dict],
        parsed: list[dict],
        candidates: set[str],
    ) -> str:
        """完整等长名称仅在两个独立证据族均通过双门槛时决胜。"""
        if len(candidates) < 2:
            return ""

        by_family: dict[str, tuple[dict, dict]] = {}
        for raw, item in zip(evidence, parsed, strict=False):
            confidence = float(raw.get("confidence", 0.0))
            if (
                item.get("length_mode") != "complete"
                or confidence < _MULTI_CANDIDATE_MIN_CONFIDENCE
            ):
                continue
            family = self.evidence_family(str(raw.get("source", "")))
            current = by_family.get(family)
            if current is None or confidence > float(current[0].get("confidence", 0.0)):
                by_family[family] = (raw, item)

        supported: dict[str, str] = {}
        for family, (raw, _item) in by_family.items():
            text = str(raw.get("text", "")).strip()
            ranked = self._similarity_service.rank_single_substitution_candidates(
                text, candidates,
            )
            if len(ranked) < 2:
                continue
            best_name, best_score = ranked[0]
            margin = best_score - ranked[1][1]
            if (
                best_score >= _MULTI_CANDIDATE_MIN_SIMILARITY
                and margin >= _MULTI_CANDIDATE_MIN_MARGIN
            ):
                supported[family] = best_name

        winners = set(supported.values())
        if (
            len(supported) >= _MULTI_CANDIDATE_MIN_EVIDENCE_FAMILIES
            and len(winners) == 1
        ):
            return winners.pop()
        return ""

    def unmatched_consensus_name(self, evidence: list[dict]) -> str:
        """全部证据族以极高置信度一致读出同一词表外原文时返回该原文，否则空串。

        词表外但命中确定性混淆字对白名单的原文不视为新武将，
        交由既有评分决胜纠错（如"王翡"→王翦）。
        """
        by_family: dict[str, tuple[str, float]] = {}
        for raw in evidence:
            text = str(raw.get("text", "")).strip()
            confidence = float(raw.get("confidence", 0.0))
            if not text or confidence < _UNMATCHED_CONSENSUS_MIN_CONFIDENCE:
                return ""
            family = self.evidence_family(str(raw.get("source", "")))
            current = by_family.get(family)
            if current is None or confidence > current[1]:
                by_family[family] = (text, confidence)
        if len(by_family) < _MULTI_CANDIDATE_MIN_EVIDENCE_FAMILIES:
            return ""
        texts = {text for text, _ in by_family.values()}
        if len(texts) != 1:
            return ""
        text = next(iter(texts))
        if text in self._hero_names or self.has_whitelist_correction(text):
            return ""
        return text

    def has_whitelist_correction(self, text: str) -> bool:
        """等长替换一处即可命中白名单混淆字对的词表武将时返回 True。"""
        return any(
            self._similarity_service.single_substitution_similarity(text, hero) == 1.0
            for hero in self._hero_names
            if len(hero) == len(text)
        )

    @staticmethod
    def evidence_family(source: str) -> str:
        if "plain" in source:
            return "plain"
        if "enhanced" in source:
            return "enhanced"
        return source or "unknown"

    # ── 页面级消歧 ────────────────────────────────────────────────────

    def resolve_page_names(self, results: list[dict]) -> list[dict]:
        """按页面唯一性消歧，并将重复确认结果回退为冲突。"""
        occupied = {item["name"] for item in results if item["name"]}
        pending = [
            item for item in results
            if (
                item["resolution"] == "unresolved"
                and len(item["candidates"]) > 1
                and item.get("length_mode") in {"missing", "complete"}
            )
        ]
        remaining = {
            item["index"]: set(item["candidates"]) - occupied
            for item in pending
        }
        for item in pending:
            candidates = remaining[item["index"]]
            if not candidates and item["candidates"]:
                item["resolution"] = "conflict"
                continue
            if len(candidates) != 1:
                item["candidates"] = sorted(candidates)
                continue
            candidate = next(iter(candidates))
            if any(
                candidate in other_candidates
                for other_index, other_candidates in remaining.items()
                if other_index != item["index"]
            ):
                item["candidates"] = [candidate]
                continue
            item.update(name=candidate, candidates=[candidate], resolution="slot_unique")

        by_name: dict[str, list[dict]] = {}
        for item in results:
            if item["name"]:
                by_name.setdefault(item["name"], []).append(item)
        for name, duplicates in by_name.items():
            if len(duplicates) < 2:
                continue
            strongest = max(_RESOLUTION_PRIORITY[item["resolution"]] for item in duplicates)
            winners = [
                item for item in duplicates
                if _RESOLUTION_PRIORITY[item["resolution"]] == strongest
            ]
            for item in duplicates:
                if len(winners) == 1 and item is winners[0]:
                    continue
                item.update(name="", candidates=[name], resolution="conflict")
        return results
