"""官方榜单图片导入服务。"""

from __future__ import annotations

import csv
import json
import logging
import re
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from src.config.env import PROJECT_ROOT
from src.data.recommendation_index_repository import mark_recommendation_index_stale
from src.ocr import official_board_parser
from src.ocr.character_similarity import CharacterSimilarityService
from src.ocr.official_board_parser import LAYOUTS
from src.ocr.paddle_loader import create_paddle_ocr

logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data"
REVIEW_DIR = PROJECT_ROOT / "screenshot_data" / "official_import"
OCR_NAME_CONFUSION_PAIRS: tuple[tuple[str, str], ...] = (
    ("候", "侯"),
    ("侯", "候"),
    ("怀", "惇"),
    ("惇", "怀"),
)


class OfficialDataImportService:
    """按表格线切分官方榜单，并将识别结果写入 CSV。"""

    def __init__(
        self,
        hero_names: list[str] | None = None,
        ocr_engine=None,
        rare_char_ocr_engine=None,
    ) -> None:
        self._hero_names = hero_names or self._load_hero_names()
        self._name_corrector = CharacterSimilarityService()
        self._ocr = ocr_engine
        self._rare_char_ocr = rare_char_ocr_engine
        self._rare_char_engine_failed = False

    @staticmethod
    def _load_hero_names() -> list[str]:
        heroes_path = DATA_DIR / "heroes.json"
        try:
            import json
            with heroes_path.open("r", encoding="utf-8") as file:
                return [item["name"] for item in json.load(file) if item.get("name")]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("无法加载武将词表: %s", exc)
            return []

    @property
    def _engine(self):
        if self._ocr is None:
            logger.info("正在加载官方榜单 OCR 模型")
            self._ocr = create_paddle_ocr(
                use_angle_cls=False,
                lang="ch",
                show_log=False,
            )
        return self._ocr

    @property
    def _rare_char_engine(self):
        """按需加载繁体模型，以覆盖简体模型字典外的罕见字。"""
        if self._rare_char_engine_failed:
            return None
        if self._rare_char_ocr is None:
            try:
                logger.info("正在加载官方榜单罕见字 OCR 模型")
                self._rare_char_ocr = create_paddle_ocr(
                    use_angle_cls=False, lang="chinese_cht", show_log=False,
                )
            except Exception as exc:
                logger.warning("罕见字 OCR 模型不可用，将保留原结果待复核: %s", exc)
                self._rare_char_engine_failed = True
                return None
        return self._rare_char_ocr

    def import_selected(self, paths: dict[str, str | list[str]]) -> list[dict]:
        """执行所有已选择的导入流程。"""
        summaries = []
        for key, selected in paths.items():
            page_paths = [selected] if isinstance(selected, str) else selected
            if page_paths:
                summaries.append(self.import_pages(key, [Path(path) for path in page_paths]))
        return summaries

    def import_file(
        self,
        key: str,
        image_path: Path,
        progress_callback: Callable[[int, int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict:
        """从一张官方榜单图片识别并覆盖对应数据文件。"""
        return self.import_pages(
            key, [image_path], progress_callback, status_callback,
        )

    def import_pages(
        self,
        key: str,
        image_paths: list[Path],
        progress_callback: Callable[[int, int], None] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict:
        """按给定顺序合并同类榜单页面，校验通过后一次写入。"""
        if key not in LAYOUTS:
            raise ValueError(f"不支持的官方榜单类型: {key}")
        if not image_paths:
            raise ValueError("未选择官方榜单图片")
        if len({str(path.resolve()) for path in image_paths}) != len(image_paths):
            raise ValueError("同一张官方榜单图片被重复选择")

        outputs: dict[str, dict] = {}
        panel_tasks = []
        total_steps = 0
        variants = set()
        page_count = len(image_paths)

        for page_index, image_path in enumerate(image_paths, start=1):
            if status_callback:
                status_callback(f"正在分析第 {page_index}/{page_count} 张图片")
            image = official_board_parser.read_image(image_path)
            layout = official_board_parser.detect_layout(image, key)
            variants.add(layout.variant)
            page_tasks = []
            for panel_index, panel_data in enumerate(official_board_parser.extract_panels(image, layout)):
                panel_x, panel_y, panel = panel_data
                columns = layout.columns[panel_index]
                boundaries = official_board_parser.find_data_boundaries(
                    panel, image.shape[0], layout, panel_index,
                )
                boundaries, repaired_ranks = official_board_parser.restore_missing_boundaries(boundaries)
                page_tasks.append((
                    page_index, image_path, layout, panel_index, panel_x, panel_y,
                    panel, columns, boundaries, repaired_ranks,
                ))
                row_count = len(boundaries) - 1
                total_steps += row_count * (2 if "胜率" in columns else 1)
            row_counts = [len(task[8]) - 1 for task in page_tasks]
            if key in ("2v2", "peak") and row_counts[0] != row_counts[1]:
                label = "巅峰赛" if key == "peak" else "2v2"
                raise ValueError(
                    f"第 {page_index} 张 {label} 图片左右榜单行数不一致: {row_counts}"
                )
            if key == "exile":
                try:
                    official_board_parser.validate_exile_row_counts(row_counts)
                except ValueError as exc:
                    raise ValueError(
                        f"第 {page_index} 张武将放逐图片{exc}"
                    ) from exc
            panel_tasks.extend(page_tasks)

        if len(variants) > 1:
            raise ValueError("同一次导入不能混用旧版长图和新版分页图片")

        completed_steps = 0

        def advance_progress() -> None:
            nonlocal completed_steps
            completed_steps += 1
            if progress_callback:
                progress_callback(completed_steps, total_steps)

        if progress_callback:
            progress_callback(0, total_steps)

        current_page = 0
        for (
            page_index, image_path, layout, panel_index, panel_x, panel_y,
            panel, columns, boundaries, repaired_ranks,
        ) in panel_tasks:
            if page_index != current_page:
                current_page = page_index
                if status_callback:
                    status_callback(f"正在识别第 {page_index}/{page_count} 张图片")
            output_name = layout.output_names[panel_index]
            review_name = layout.review_names[panel_index]
            column_breaks = layout.column_breaks[panel_index]
            batch = outputs.setdefault(output_name, {
                "columns": columns,
                "review_name": review_name,
                "records": [],
                "reviews": [],
                "seen_names": set(),
            })
            panel_expected_start = len(batch["records"]) + 1
            rank_offsets = []
            rate_ocr_results, digit_templates = (
                official_board_parser.prepare_rate_templates(
                    panel, boundaries, columns, column_breaks, self._recognize_cell,
                    advance_progress, panel_expected_start,
                )
                if "胜率" in columns else ({}, None)
            )
            for local_rank, (top, bottom) in enumerate(
                zip(boundaries, boundaries[1:]), start=1,
            ):
                row = panel[top + 3:bottom - 3]
                if row.size == 0:
                    advance_progress()
                    continue
                expected_rank = len(batch["records"]) + 1
                cells = official_board_parser.split_row_cells(row, columns, column_breaks)
                fields = self._recognize_row(
                    row, columns, column_breaks,
                    {"胜率": rate_ocr_results[local_rank]} if "胜率" in columns else None,
                    (
                        lambda text, page=page_index: status_callback(
                            f"第 {page}/{page_count} 张图片：{text}"
                        )
                    ) if status_callback else None,
                )
                rank_match = re.search(r"\d+", fields["排名"][0])
                if rank_match:
                    rank_offsets.append(int(rank_match.group()) - local_rank)
                name, confidence = self._normalize_name(fields["武将"])
                record = {"排名": expected_rank, "武将": name}
                template_rate, template_score = "", 0.0
                if "胜率" in columns:
                    template_rate, template_score = official_board_parser.recognize_rate_with_templates(
                        cells["胜率"], digit_templates or {},
                    )
                    record["胜率"] = template_rate or self._normalize_rate(fields["胜率"][0])
                batch["records"].append(record)

                reasons = self._review_reasons(expected_rank, fields, name, record)
                unresolved_name_reason = self._unresolved_name_reason(name)
                if unresolved_name_reason:
                    reasons.append(unresolved_name_reason)
                if local_rank in repaired_ranks:
                    reasons.append("检测到缺失表格横线，已按行高补全")
                ocr_rate = self._normalize_rate(fields.get("胜率", ("", 0.0))[0])
                if template_rate and ocr_rate and template_rate != ocr_rate and template_score < 0.90:
                    reasons.append("胜率OCR与数字模板不一致")
                elif "胜率" in columns and not template_rate:
                    reasons.append("胜率数字模板识别失败")
                if name and name in batch["seen_names"]:
                    reasons.append("武将名称重复")
                batch["seen_names"].add(name)
                if reasons:
                    batch["reviews"].append({
                        "期望排名": expected_rank,
                        "OCR排名": fields["排名"][0],
                        "OCR名称": fields["武将"][0],
                        "OCR胜率": fields.get("胜率", ("", 0.0))[0],
                        "数字模板胜率": template_rate,
                        "数字模板置信度": f"{template_score:.4f}",
                        "置信度": f"{confidence:.4f}",
                        "异常原因": "；".join(reasons),
                        "来源图片": str(image_path),
                        "页序号": page_index,
                        "原图坐标": f"{panel_x},{panel_y + top},{panel_x + panel.shape[1]},{panel_y + bottom}",
                        "行截图路径": "",
                        "_row": row.copy(),
                    })
                advance_progress()
            self._validate_panel_rank_sequence(
                image_path, panel_expected_start, rank_offsets,
            )

        if not outputs:
            raise ValueError("未检测到任何数据行")
        for batch in outputs.values():
            self._resolve_batch_names(batch)
        self._resolve_names_across_outputs(outputs)
        validation_errors = self._validate_output_names(outputs)
        for output_name, batch in outputs.items():
            records = batch["records"]
            if not records:
                raise ValueError(f"{output_name} 未识别到任何数据行")
            for review in batch["reviews"]:
                row = review.pop("_row")
                crop_path = self._save_review_crop(
                    Path(output_name).stem, int(review["期望排名"]), row,
                )
                review["行截图路径"] = str(crop_path)
            self._write_csv(
                DATA_DIR / batch["review_name"],
                ["期望排名", "OCR排名", "OCR名称", "OCR胜率", "数字模板胜率", "数字模板置信度", "置信度", "异常原因", "来源图片", "页序号", "原图坐标", "行截图路径"],
                batch["reviews"],
            )
        if validation_errors:
            self._save_pending_session(
                key,
                [str(path) for path in image_paths],
                page_count,
                sorted(variants),
                validation_errors,
                outputs,
            )
            raise ValueError("官方榜单名称校验失败：" + "；".join(validation_errors))
        for output_name, batch in outputs.items():
            records = batch["records"]
            self._write_csv(DATA_DIR / output_name, list(records[0]), records)
        if "巅峰赛胜率排行.csv" not in outputs:
            mark_recommendation_index_stale(True)
        if "2v2胜率排行.csv" in outputs:
            from src.data.win_rate_repository import clear_win_rate_cache
            clear_win_rate_cache()
        if "巅峰赛胜率排行.csv" in outputs:
            from src.data.peak_win_rate_repository import clear_peak_win_rate_cache
            clear_peak_win_rate_cache()
        record_count = sum(len(batch["records"]) for batch in outputs.values())
        review_count = sum(len(batch["reviews"]) for batch in outputs.values())
        logger.info("官方%s榜单导入完成: %d 条，待复核 %d 条", layout.key, record_count, review_count)
        return {
            "name": key,
            "pages": page_count,
            "variant": next(iter(variants)),
            "records": record_count,
            "reviews": review_count,
            "outputs": [DATA_DIR / name for name in outputs],
        }

    @staticmethod
    def _validate_panel_rank_sequence(
        image_path: Path,
        expected_start: int,
        rank_offsets: list[int],
    ) -> None:
        """在排名 OCR 提供足够一致证据时阻止错序页面覆盖数据。"""
        if len(rank_offsets) < 3:
            return
        observed_offset, count = Counter(rank_offsets).most_common(1)[0]
        required = max(3, round(len(rank_offsets) * 0.6))
        expected_offset = expected_start - 1
        if count >= required and observed_offset != expected_offset:
            observed_start = observed_offset + 1
            raise ValueError(
                f"图片 {image_path.name} 排名顺序异常："
                f"期望从 {expected_start} 开始，识别为从 {observed_start} 开始"
            )

    def _recognize_row(
        self,
        row: np.ndarray,
        columns: tuple[str, ...],
        column_breaks: tuple[float, ...],
        precomputed: dict[str, tuple[str, float]] | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> dict[str, tuple[str, float]]:
        return {
            column: precomputed[column] if precomputed and column in precomputed else (
                self._recognize_name_cell(cell, status_callback) if column == "武将" else self._recognize_cell(cell)
            )
            for column, cell in official_board_parser.split_row_cells(
                row, columns, column_breaks,
            ).items()
        }

    def _recognize_cell_candidates(self, cell: np.ndarray, engine=None) -> list[tuple[str, float]]:
        if cell.size == 0:
            return []
        enlarged = cv2.resize(cell, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC)
        candidates = [enlarged]
        lab = cv2.cvtColor(enlarged, cv2.COLOR_BGR2LAB)
        lightness, a_channel, b_channel = cv2.split(lab)
        lightness = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lightness)
        enhanced = cv2.cvtColor(cv2.merge([lightness, a_channel, b_channel]), cv2.COLOR_LAB2BGR)
        candidates.append(cv2.filter2D(enhanced, -1, np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]])))
        results: list[tuple[str, float]] = []
        for candidate in candidates:
            # 恢复完整检测+识别流程：det 网络先框出文字区域，避免整格输入产生的边缘幻觉
            ocr_result = (engine if engine is not None else self._engine).ocr(
                candidate, cls=False,
            )
            for line in ocr_result[0] if ocr_result and ocr_result[0] else []:
                # 完整流程 line 为 [box, (text, confidence)]
                if isinstance(line[0], (list, tuple)):
                    text, confidence = line[1]
                else:
                    text, confidence = line
                if text:
                    results.append((text.replace(" ", ""), float(confidence)))
        return results

    def _recognize_cell(self, cell: np.ndarray, engine=None) -> tuple[str, float]:
        candidates = (
            self._recognize_cell_candidates(cell, engine)
            if engine is not None else self._recognize_cell_candidates(cell)
        )
        return max(candidates, key=lambda item: item[1], default=("", 0.0))

    @staticmethod
    def _chinese_text(text: str) -> str:
        return "".join(re.findall(r"[\u4e00-\u9fff]", text))

    def _exact_hero_matches(
        self, candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        return [
            (self._chinese_text(text), confidence)
            for text, confidence in candidates
            if self._chinese_text(text) in self._hero_names
        ]

    @staticmethod
    def _select_unique_name_match(
        matches: list[tuple[str, float]],
    ) -> tuple[tuple[str, float] | None, tuple[str, ...]]:
        names = tuple(dict.fromkeys(name for name, _confidence in matches))
        if len(names) == 1:
            return max(matches, key=lambda item: item[1]), ()
        return None, names

    @staticmethod
    def _common_prefix(names: tuple[str, ...] | list[str]) -> str:
        if not names:
            return ""
        prefix = names[0]
        for name in names[1:]:
            while prefix and not name.startswith(prefix):
                prefix = prefix[:-1]
        return prefix

    def _strict_prefix_matches(self, name: str) -> list[str]:
        return [hero for hero in self._hero_names if len(hero) > len(name) and hero.startswith(name)]

    def _nearby_hero_names(self, name: str) -> list[str]:
        return [
            hero for hero in self._hero_names
            if CharacterSimilarityService._levenshtein_distance(name, hero)
            <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
        ]

    def _ambiguous_name_candidates(self, name: str) -> list[str]:
        if not name:
            return []
        prefix_matches = self._strict_prefix_matches(name)
        if len(prefix_matches) > 1:
            return prefix_matches
        nearby_names = self._nearby_hero_names(name)
        if len(nearby_names) > 1 and len(self._common_prefix(nearby_names)) >= 2:
            return nearby_names
        return []

    def _correct_official_name(self, name: str) -> str:
        """保留复姓公共前缀歧义，避免词表扩充后静默改绑。"""
        return self._correct_official_name_with_path(name)[0]

    def _correct_official_name_with_path(self, name: str) -> tuple[str, bool]:
        """返回 (校正名, 是否经 OCR 混淆字对变体唯一命中)。"""
        if not name or name in self._hero_names or self._ambiguous_name_candidates(name):
            return name, False
        corrected = self._name_corrector.correct_hero_name(name, self._hero_names)
        if corrected in self._hero_names:
            return corrected, False
        reachable: set[str] = set()
        for variant in self._confusion_variants(name):
            reachable.update(self._ambiguous_name_candidates(variant))
            variant_corrected = self._name_corrector.correct_hero_name(variant, self._hero_names)
            if variant_corrected in self._hero_names:
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

    def _corrected_via_confusion_swap(self, original: str, final: str) -> bool:
        """仅当校正路径经过混淆字对变体时返回 True（用于复核原因标注）。"""
        if not original or original == final:
            return False
        corrected, used_swap = self._correct_official_name_with_path(original)
        return used_swap and corrected == final

    def _unresolved_name_reason(self, name: str) -> str:
        if not name or name in self._hero_names:
            return ""
        ambiguous_candidates = self._ambiguous_name_candidates(name)
        if ambiguous_candidates:
            return f"武将名称候选不唯一：{'/'.join(ambiguous_candidates)}"
        return "武将名称未命中词表"

    def _resolve_batch_names(self, batch: dict) -> None:
        """仅在榜单内部唯一性能够证明时补全未决武将名称。"""
        records = batch["records"]
        reviews = {
            int(review["期望排名"]): review for review in batch["reviews"]
        }
        confirmed = {
            record["武将"] for record in records
            if record["武将"] in self._hero_names
        }
        pending = {
            index: tuple(self._ambiguous_name_candidates(record["武将"]))
            for index, record in enumerate(records)
            if record["武将"] not in self._hero_names
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

    def _resolve_names_across_outputs(self, outputs: dict[str, dict]) -> None:
        """跨榜单未确认名称在候选集交集唯一时统一补全，避免集合不一致误报。"""
        pending = [
            (output_name, index, record)
            for output_name, batch in outputs.items()
            for index, record in enumerate(batch["records"])
            if record["武将"] not in self._hero_names
        ]
        if not pending:
            return
        candidate_sets: list[set[str]] = []
        for _output_name, _index, record in pending:
            name = record["武将"]
            candidates: set[str] = set()
            for hero in self._hero_names:
                if CharacterSimilarityService._levenshtein_distance(name, hero) <= 2:
                    candidates.add(hero)
            candidates.update(self._ambiguous_name_candidates(name))
            corrected = self._correct_official_name(name)
            if corrected in self._hero_names:
                candidates.add(corrected)
            for variant in self._confusion_variants(name):
                candidates.update(self._ambiguous_name_candidates(variant))
                variant_corrected = self._correct_official_name(variant)
                if variant_corrected in self._hero_names:
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

    def _validate_output_names(self, outputs: dict[str, dict]) -> list[str]:
        """返回阻止正式 CSV 覆盖的名称完整性错误。"""
        errors = []
        name_sets: list[tuple[str, int, set[str]]] = []
        hero_names = set(self._hero_names)
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

    def _recognize_name_with_engine(
        self,
        cell: np.ndarray,
        engine,
        allowed_names: tuple[str, ...] | list[str],
    ) -> tuple[str, float]:
        """使用指定引擎识别名称，仅接受词表中可确认的完整结果。"""
        allowed_names = tuple(dict.fromkeys(allowed_names))
        allowed_set = set(allowed_names)
        candidates = self._recognize_cell_candidates(cell, engine)
        exact_matches = [
            match for match in self._exact_hero_matches(candidates)
            if match[0] in allowed_set
        ]
        exact_match, exact_conflicts = self._select_unique_name_match(exact_matches)
        if exact_match:
            return exact_match
        if exact_conflicts:
            logger.warning("官方榜单武将精确候选冲突: %s", exact_conflicts)
            return self._common_prefix(exact_conflicts), max(confidence for _text, confidence in candidates)
        corrected_matches = []
        for text, confidence in candidates:
            candidate_name = self._chinese_text(text)
            if len(candidate_name) < 2:
                continue
            nearby_names = [
                hero for hero in allowed_names
                if CharacterSimilarityService._levenshtein_distance(candidate_name, hero)
                <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
            ]
            corrected = nearby_names[0] if len(nearby_names) == 1 else candidate_name
            if corrected in allowed_set:
                corrected_matches.append((corrected, confidence))
        corrected_match, corrected_conflicts = self._select_unique_name_match(corrected_matches)
        if corrected_match:
            return corrected_match
        if corrected_conflicts:
            logger.warning("官方榜单武将校正候选冲突: %s", corrected_conflicts)
            return self._common_prefix(corrected_conflicts), max(confidence for _text, confidence in candidates)
        glyph_name, glyph_confidence = self._recognize_name_glyphs(cell, engine)
        if glyph_name:
            nearby_names = [
                hero for hero in allowed_names
                if CharacterSimilarityService._levenshtein_distance(glyph_name, hero)
                <= CharacterSimilarityService.EDIT_DISTANCE_THRESHOLD
            ]
            corrected = nearby_names[0] if len(nearby_names) == 1 else glyph_name
            if corrected in allowed_set:
                return corrected, glyph_confidence
        return "", 0.0

    def _recognize_name_cell(
        self,
        cell: np.ndarray,
        status_callback: Callable[[str], None] | None = None,
    ) -> tuple[str, float]:
        """优先选用词表中的完整 OCR 候选，单字结果再逐字补识别。"""
        candidates = self._recognize_cell_candidates(cell)
        if not candidates:
            return "", 0.0
        exact_match, exact_conflicts = self._select_unique_name_match(self._exact_hero_matches(candidates))
        if exact_match:
            return exact_match
        if exact_conflicts:
            logger.warning("官方榜单武将精确候选冲突: %s", exact_conflicts)
            text = self._common_prefix(exact_conflicts)
            confidence = max(confidence for _text, confidence in candidates)
        else:
            text, confidence = max(candidates, key=lambda item: item[1])
        name = self._chinese_text(text)
        if name in self._hero_names:
            return text, confidence
        ambiguous_candidates = self._ambiguous_name_candidates(name)

        glyph_name, glyph_confidence = self._recognize_name_glyphs(cell)
        if glyph_name:
            corrected = self._correct_official_name(glyph_name)
            if corrected in self._hero_names:
                return corrected, glyph_confidence
        prefix_matches = [hero for hero in self._hero_names if hero.startswith(name)]
        if len(prefix_matches) == 1:
            return prefix_matches[0], confidence
        allowed_names = tuple(ambiguous_candidates or prefix_matches)
        if not allowed_names:
            return text, confidence
        if status_callback:
            status_callback("正在执行罕见字兜底识别")
        rare_char_engine = self._rare_char_engine
        if rare_char_engine is not None:
            try:
                rare_name, rare_confidence = self._recognize_name_with_engine(
                    cell, rare_char_engine, allowed_names,
                )
                if rare_name:
                    return rare_name, rare_confidence
            except Exception as exc:
                logger.warning("罕见字 OCR 识别失败，将保留原结果待复核: %s", exc)
                self._rare_char_engine_failed = True
        return text, confidence

    def _recognize_name_glyphs(self, cell: np.ndarray, engine=None) -> tuple[str, float]:
        """按亮色字形切分名称格，并在保留背景留白后逐字识别。"""
        gray = cv2.cvtColor(cell, cv2.COLOR_BGR2GRAY)
        columns = (gray > 180).any(axis=0)
        groups: list[tuple[int, int]] = []
        start = None
        for x, has_foreground in enumerate(columns):
            if has_foreground and start is None:
                start = x
            elif not has_foreground and start is not None:
                groups.append((start, x - 1))
                start = None
        if start is not None:
            groups.append((start, len(columns) - 1))
        if not 2 <= len(groups) <= 4:
            return "", 0.0

        background = tuple(int(value) for value in np.median(cell.reshape(-1, 3), axis=0))
        characters: list[str] = []
        confidences: list[float] = []
        for left, right in groups:
            glyph = cell[:, max(0, left - 5):min(cell.shape[1], right + 6)]
            glyph = cv2.copyMakeBorder(glyph, 8, 8, 8, 8, cv2.BORDER_CONSTANT, value=background)
            text, confidence = (
                self._recognize_cell(glyph, engine)
                if engine is not None else self._recognize_cell(glyph)
            )
            character = self._chinese_text(text)
            if len(character) != 1:
                return "", 0.0
            characters.append(character)
            confidences.append(confidence)
        return "".join(characters), min(confidences)

    def _normalize_name(self, value: tuple[str, float]) -> tuple[str, float]:
        text, confidence = value
        name = "".join(re.findall(r"[\u4e00-\u9fff]", text))
        if not name or not self._hero_names:
            return name, confidence
        if len(name) == 1:
            return name, confidence
        return self._correct_official_name(name), confidence

    @staticmethod
    def _normalize_rate(text: str) -> str:
        match = re.search(r"\d{1,3}(?:\.\d+)?", text)
        return f"{match.group(0)}%" if match else ""

    def _review_reasons(self, expected_rank: int, fields: dict[str, tuple[str, float]], name: str, record: dict) -> list[str]:
        reasons = []
        rank_match = re.search(r"\d+", fields["排名"][0])
        if rank_match and int(rank_match.group()) != expected_rank:
            reasons.append("排名OCR与行序不一致")
        ocr_name = "".join(re.findall(r"[\u4e00-\u9fff]", fields["武将"][0]))
        if ocr_name and ocr_name != name:
            reason = "武将名称已由词表校正"
            if self._corrected_via_confusion_swap(ocr_name, name):
                reason += "（混淆字对）"
            reasons.append(reason)
        if len(name) < 2:
            reasons.append("武将名称疑似缺字")
        elif not re.fullmatch(r"[\u4e00-\u9fff]{1,8}", name):
            reasons.append("武将名称为空或包含异常字符")
        elif fields["武将"][1] < 0.75:
            reasons.append("武将名称置信度低")
        if "胜率" in record and not record["胜率"]:
            reasons.append("胜率识别失败")
        return reasons

    @staticmethod
    def _save_review_crop(kind: str, rank: int, row: np.ndarray) -> Path:
        directory = REVIEW_DIR / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{rank:03d}.png"
        Image.fromarray(cv2.cvtColor(row, cv2.COLOR_BGR2RGB)).save(path)
        return path

    @staticmethod
    def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as file:
            temp_path = Path(file.name)
            writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        temp_path.replace(path)
    def _save_pending_session(
        self,
        key: str,
        image_paths: list[str],
        page_count: int,
        variants: list[str],
        validation_errors: list[str],
        outputs: dict[str, dict],
        path: Path | None = None,
    ) -> Path:
        """将校验失败批次持久化，供复核界面修正后复用（不重新 OCR）。"""
        session_path = path or (DATA_DIR / "official_import_pending.json")
        payload = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "key": key,
            "image_paths": image_paths,
            "page_count": page_count,
            "variant": variants,
            "validation_errors": validation_errors,
            "outputs": {
                name: {
                    "review_name": batch["review_name"],
                    "columns": list(batch["columns"]),
                    "records": batch["records"],
                    "reviews": batch["reviews"],
                }
                for name, batch in outputs.items()
            },
        }
        session_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="", dir=session_path.parent,
            prefix=f".{session_path.name}.", suffix=".tmp", delete=False,
        ) as file:
            temp_path = Path(file.name)
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temp_path.replace(session_path)
        return session_path

    def apply_reviewed_records(
        self,
        pending: dict,
        corrections: dict[tuple[str, int], str],
        session_path: Path | None = None,
    ) -> dict:
        """应用人工复核修正后写入正式 CSV；校验失败时抛错且不写文件。"""
        outputs = pending["outputs"]
        for (output_name, rank), hero_name in corrections.items():
            batch = outputs[output_name]
            record = next(
                (
                    record for record in batch["records"]
                    if int(record["排名"]) == int(rank)
                ),
                None,
            )
            if record is None:
                raise ValueError(f"{output_name} 排名 {rank} 不存在待修正记录")
            record["武将"] = hero_name
        validation_errors = self._validate_output_names(outputs)
        if validation_errors:
            raise ValueError("官方榜单名称校验失败：" + "；".join(validation_errors))
        for output_name, batch in outputs.items():
            records = batch["records"]
            if not records:
                raise ValueError(f"{output_name} 未识别到任何数据行")
            self._write_csv(DATA_DIR / output_name, list(records[0]), records)
        if "巅峰赛胜率排行.csv" not in outputs:
            mark_recommendation_index_stale(True)
        if "2v2胜率排行.csv" in outputs:
            from src.data.win_rate_repository import clear_win_rate_cache
            clear_win_rate_cache()
        if "巅峰赛胜率排行.csv" in outputs:
            from src.data.peak_win_rate_repository import clear_peak_win_rate_cache
            clear_peak_win_rate_cache()
        clear_pending_session(session_path)
        record_count = sum(len(batch["records"]) for batch in outputs.values())
        logger.info("官方榜单复核修正写入完成: %d 条", record_count)
        return {
            "records": record_count,
            "outputs": [DATA_DIR / name for name in outputs],
        }

    def review_candidates(self, ocr_name: str, current: str | None = None) -> list[str]:
        """为复核界面提供候选武将名：当前值 ∪ 距离≤2/歧义候选，空则全表按距离排序。"""
        candidates: list[str] = []
        seen: set[str] = set()

        def add(name: str) -> None:
            if name and name not in seen:
                seen.add(name)
                candidates.append(name)

        if current and current in self._hero_names:
            add(current)
        if ocr_name in self._hero_names:
            add(ocr_name)
        for hero in self._hero_names:
            if (
                CharacterSimilarityService._levenshtein_distance(ocr_name, hero) <= 2
                and (any(char in hero for char in ocr_name) or len(ocr_name) != len(hero))
            ):
                add(hero)
        for hero in self._ambiguous_name_candidates(ocr_name):
            add(hero)
        if not candidates:
            candidates.extend(sorted(
                self._hero_names,
                key=lambda hero: (
                    CharacterSimilarityService._levenshtein_distance(ocr_name, hero),
                    hero,
                ),
            ))
        return candidates

    def is_known_hero_name(self, name: str) -> bool:
        """判断名称是否在词表中（复核界面统计用）。"""
        return name in self._hero_names

def load_pending_session(path: Path | None = None) -> dict | None:
    """读取最近一次校验失败保存的官方榜单会话；损坏时返回 None。"""
    session_path = Path(path) if path is not None else DATA_DIR / "official_import_pending.json"
    try:
        with session_path.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict) or not payload.get("outputs"):
            logger.warning("官方榜单待复核会话格式无效: %s", session_path)
            return None
        return payload
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("无法读取官方榜单待复核会话: %s", exc)
        return None


def clear_pending_session(path: Path | None = None) -> None:
    """删除待复核会话文件（存在时才删除）。"""
    session_path = Path(path) if path is not None else DATA_DIR / "official_import_pending.json"
    try:
        session_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("无法删除官方榜单待复核会话: %s", exc)
