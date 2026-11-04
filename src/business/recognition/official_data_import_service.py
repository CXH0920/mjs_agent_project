"""官方榜单图片导入服务。"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
from PIL import Image
from src.business.recognition.name_resolution import HeroNameResolver
from src.business.recognition.official_cell_recognition import OfficialCellRecognizer
from src.business.recognition.official_ocr_engines import OfficialOcrEngines
from src.config.atomic_write import atomic_write_bytes, atomic_write_json, atomic_write_text
from src.config.env import PROJECT_ROOT
from src.data.recommendation_index_repository import notify_official_outputs_written
from src.ocr import official_board_parser
from src.ocr.official_board_parser import LAYOUTS, OfficialBoardLayout

logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data"
REVIEW_DIR = PROJECT_ROOT / "screenshot_data" / "official_import"

# 胜率复核线：数字模板胜率匹配置信度低于该值且与 OCR 胜率不一致时，标记"胜率OCR与数字模板不一致"。
# 与 NAME_CONFIDENCE_REVIEW_THRESHOLD 语义不同（模板匹配置信度 vs 名称 OCR 置信度），勿合并。
TEMPLATE_RATE_REVIEW_THRESHOLD = 0.90
# 名称复核线：武将名称 OCR 置信度低于该值时标记"武将名称置信度低"。
NAME_CONFIDENCE_REVIEW_THRESHOLD = 0.75


class _PanelTask(NamedTuple):
    """单个榜单面板的识别任务参数（import_pages 页解析产出、行循环消费）。"""

    page_index: int
    image_path: Path
    layout: OfficialBoardLayout
    panel_index: int
    panel_x: int
    panel_y: int
    panel: np.ndarray
    columns: tuple[str, ...]
    boundaries: list[int]
    repaired_ranks: set[int]


class OfficialDataImportService:
    """按表格线切分官方榜单，并将识别结果写入 CSV。"""

    def __init__(
        self,
        hero_names: list[str] | None = None,
        ocr_engine=None,
        rare_char_ocr_engine=None,
        name_resolver: HeroNameResolver | None = None,
    ) -> None:
        self._names = name_resolver or HeroNameResolver(hero_names)
        self._engines = OfficialOcrEngines(ocr_engine, rare_char_ocr_engine)
        self._cells = OfficialCellRecognizer(self._engines, self._names)

    @property
    def ocr_engine(self):
        """已加载/注入的简体 OCR 引擎，供 ocr_worker 回收复用。"""
        return self._engines.ocr

    @property
    def rare_char_ocr_engine(self):
        """已加载/注入的罕见字 OCR 引擎，供 ocr_worker 回收复用。"""
        return self._engines.rare_char_ocr

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
        panel_tasks: list[_PanelTask] = []
        total_steps = 0
        variants = set()
        page_units = self._build_page_units(image_paths, key, status_callback)
        page_count = len(page_units)

        for page_index, (image_path, image, segment_offset) in enumerate(page_units, start=1):
            if status_callback:
                status_callback(f"正在分析第 {page_index}/{page_count} 张图片")
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
                page_tasks.append(_PanelTask(
                    page_index, image_path, layout, panel_index, panel_x,
                    panel_y + segment_offset,
                    panel, columns, boundaries, repaired_ranks,
                ))
                row_count = len(boundaries) - 1
                total_steps += row_count * (2 if "胜率" in columns else 1)
            row_counts = [len(task.boundaries) - 1 for task in page_tasks]
            self._validate_page_panels(key, page_index, row_counts)
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
                    panel, boundaries, columns, column_breaks, self._cells.recognize_cell,
                    advance_progress, panel_expected_start,
                )
                if "胜率" in columns else ({}, None)
            )
            for local_rank, (top, bottom) in enumerate(
                zip(boundaries, boundaries[1:], strict=False), start=1,
            ):
                row = panel[top + 3:bottom - 3]
                if row.size == 0:
                    advance_progress()
                    continue
                expected_rank = len(batch["records"]) + 1
                cells = official_board_parser.split_row_cells(row, columns, column_breaks)
                fields = self._cells.recognize_row(
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
                name, confidence = self._names.normalize_name(fields["武将"])
                record = {"排名": expected_rank, "武将": name}
                template_rate, template_score = "", 0.0
                if "胜率" in columns:
                    template_rate, template_score = official_board_parser.recognize_rate_with_templates(
                        cells["胜率"], digit_templates or {},
                    )
                    record["胜率"] = template_rate or self._normalize_rate(fields["胜率"][0])
                batch["records"].append(record)

                reasons = self._review_reasons(expected_rank, fields, name, record)
                unresolved_name_reason = self._names.unresolved_name_reason(name)
                if unresolved_name_reason:
                    reasons.append(unresolved_name_reason)
                if local_rank in repaired_ranks:
                    reasons.append("检测到缺失表格横线，已按行高补全")
                ocr_rate = self._normalize_rate(fields.get("胜率", ("", 0.0))[0])
                if template_rate and ocr_rate and template_rate != ocr_rate and template_score < TEMPLATE_RATE_REVIEW_THRESHOLD:
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
                        "_name_cell": cells["武将"].copy(),
                    })
                advance_progress()
            self._validate_panel_rank_sequence(
                image_path, panel_expected_start, rank_offsets,
            )

        if not outputs:
            raise ValueError("未检测到任何数据行")
        for batch in outputs.values():
            self._names.resolve_batch_names(batch)
        self._names.resolve_names_across_outputs(outputs)
        validation_errors = self._names.validate_output_names(outputs)
        for output_name, batch in outputs.items():
            records = batch["records"]
            if not records:
                raise ValueError(f"{output_name} 未识别到任何数据行")
            for review in batch["reviews"]:
                name_cell = review.pop("_name_cell")
                crop_path = self._save_review_crop(
                    Path(output_name).stem, int(review["期望排名"]), name_cell,
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
        notify_official_outputs_written(outputs)
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

    def _build_page_units(
        self,
        image_paths: list[Path],
        key: str,
        status_callback: Callable[[str], None] | None,
    ) -> list[tuple[Path, np.ndarray, int]]:
        """读图并把拼接长图切分成虚拟分页，返回 (图片, 图像, 段 y 偏移) 页单元。

        20261022 拼接长图先切成虚拟分页再进入统一解析；旧版长图与分页图
        切不出多个有效段，仍按整图走原路径。
        """
        page_units: list[tuple[Path, np.ndarray, int]] = []
        for image_path in image_paths:
            image = official_board_parser.read_image(image_path)
            segments = (
                official_board_parser.split_stitched_image(image, key)
                if image.shape[0] / image.shape[1] >= 4 else []
            )
            if segments:
                logger.info(
                    "官方%s榜单拼接长图切分出 %d 段: %s", key, len(segments), image_path,
                )
                if status_callback:
                    status_callback(
                        f"{image_path.name} 为拼接长图，切分出 {len(segments)} 段",
                    )
                page_units.extend(
                    (image_path, segment, offset) for segment, offset in segments
                )
            else:
                page_units.append((image_path, image, 0))
        return page_units

    @staticmethod
    def _validate_page_panels(key: str, page_index: int, row_counts: list[int]) -> None:
        """逐版式行数校验：面板数量、左右等行与放逐榜行数约束。"""
        if not row_counts:
            raise ValueError(
                f"第 {page_index} 张图片未检出任何榜单面板，请确认图片清晰完整"
            )
        if key in ("2v2", "peak"):
            label = "巅峰赛" if key == "peak" else "2v2"
            if len(row_counts) != 2:
                raise ValueError(
                    f"第 {page_index} 张 {label} 图片未检出左右两个榜单"
                    f"（实际 {len(row_counts)} 个），请确认图片完整"
                )
            if row_counts[0] != row_counts[1]:
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
            if self._names.corrected_via_confusion_swap(ocr_name, name):
                reason += "（混淆字对）"
            reasons.append(reason)
        if len(name) < 2:
            reasons.append("武将名称疑似缺字")
        elif not re.fullmatch(r"[\u4e00-\u9fff]{1,8}", name):
            reasons.append("武将名称为空或包含异常字符")
        elif fields["武将"][1] < NAME_CONFIDENCE_REVIEW_THRESHOLD:
            reasons.append("武将名称置信度低")
        if "胜率" in record and not record["胜率"]:
            reasons.append("胜率识别失败")
        return reasons

    @staticmethod
    def _save_review_crop(kind: str, rank: int, row: np.ndarray) -> Path:
        directory = REVIEW_DIR / kind
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{rank:03d}.png"
        # 行截图是复核界面的正式展示内容，与数据写盘同纪律：先序列化再原子写，
        # 覆盖中断不留半截图片
        buffer = io.BytesIO()
        Image.fromarray(cv2.cvtColor(row, cv2.COLOR_BGR2RGB)).save(buffer, format="PNG")
        atomic_write_bytes(path, buffer.getvalue())
        return path

    @staticmethod
    def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
        # 先整体序列化再走原子写：fsync 与失败清理临时文件由工具层统一保证
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        atomic_write_text(path, buffer.getvalue())
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
        if session_path.exists():
            # 旧会话还没复核完又来一批失败导入：静默覆盖等于无声丢掉用户的
            # 复核现场，留日志供事后追溯（图片仍在用户手里，可重新导入恢复）
            logger.warning(
                "存在未复核完成的官方榜单待复核会话，本次导入失败将覆盖: %s"
                "（旧复核数据丢失，可用原图片重新导入恢复）",
                session_path,
            )
        atomic_write_json(session_path, payload, indent=2)
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
        validation_errors = self._names.validate_output_names(outputs)
        if validation_errors:
            raise ValueError("官方榜单名称校验失败：" + "；".join(validation_errors))
        for output_name, batch in outputs.items():
            records = batch["records"]
            if not records:
                raise ValueError(f"{output_name} 未识别到任何数据行")
            self._write_csv(DATA_DIR / output_name, list(records[0]), records)
        notify_official_outputs_written(outputs)
        clear_pending_session(session_path)
        record_count = sum(len(batch["records"]) for batch in outputs.values())
        logger.info("官方榜单复核修正写入完成: %d 条", record_count)
        return {
            "records": record_count,
            "outputs": [DATA_DIR / name for name in outputs],
        }

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
