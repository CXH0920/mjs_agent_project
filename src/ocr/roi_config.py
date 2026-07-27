"""OCR 识别区域配置的加载、校验与本地覆盖。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_ROI_CONFIG_PATH = PROJECT_ROOT / "config" / "ocr_rois.default.json"
USER_ROI_CONFIG_PATH = PROJECT_ROOT / "config" / "ocr_rois.json"
SCHEMA_VERSION = 1
_PAGE_REQUIREMENTS = {
    "hero_selection": (8, False),
    "match_guide": (5, True),
}

Roi = tuple[int, int, int, int]


class OcrRoiConfigError(ValueError):
    """ROI 配置内容不符合当前模式时抛出。"""


@dataclass(frozen=True)
class OcrRoiSlot:
    """一个识别席位的名称区域和可选阵营区域。"""

    name_roi: Roi
    team_roi: Roi | None = None


@dataclass(frozen=True)
class OcrRoiLayout:
    """单个 OCR 页面在一张参考截图上的识别区域。"""

    reference_size: tuple[int, int]
    slots: tuple[OcrRoiSlot, ...]

    def to_dict(self) -> dict:
        return {
            "reference_size": list(self.reference_size),
            "slots": [
                {
                    "name_roi": list(slot.name_roi),
                    **({"team_roi": list(slot.team_roi)} if slot.team_roi else {}),
                }
                for slot in self.slots
            ],
        }

class OcrRoiConfig:
    """加载默认布局，并以本地覆盖文件保存用户调整。"""

    def __init__(
        self,
        default_path: Path = DEFAULT_ROI_CONFIG_PATH,
        user_path: Path = USER_ROI_CONFIG_PATH,
    ) -> None:
        self._default_path = Path(default_path)
        self._user_path = Path(user_path)
        self._defaults: dict[str, OcrRoiLayout] = {}
        self._overrides: dict[str, OcrRoiLayout] = {}
        self._layouts: dict[str, OcrRoiLayout] = {}
        self.reload()

    @property
    def user_path(self) -> Path:
        return self._user_path

    def reload(self) -> None:
        """重新读取磁盘配置；本地文件无效时只使用默认布局。"""
        self._defaults = self._load_document(self._default_path, require_all_pages=True)
        self._overrides = {}
        if self._user_path.exists():
            try:
                self._overrides = self._load_document(self._user_path, require_all_pages=False)
            except OcrRoiConfigError as exc:
                logger.warning("本地 OCR ROI 配置无效，已回退默认布局: %s", exc)
        self._layouts = {**self._defaults, **self._overrides}

    def layout_for(self, page_type: str) -> OcrRoiLayout:
        self._validate_page_type(page_type)
        return self._layouts[page_type]

    def save_layout(self, page_type: str, layout: OcrRoiLayout) -> None:
        """保存一个页面的本地覆盖布局，并立即更新运行时读取结果。"""
        self._validate_layout(page_type, layout)
        previous_override = self._overrides.get(page_type)
        self._overrides[page_type] = layout
        try:
            self._write_overrides()
        except OcrRoiConfigError:
            if previous_override is None:
                self._overrides.pop(page_type, None)
            else:
                self._overrides[page_type] = previous_override
            raise
        self._layouts[page_type] = layout

    def reset_layout(self, page_type: str) -> None:
        """删除一个页面的本地覆盖，恢复仓库随附的默认布局。"""
        self._validate_page_type(page_type)
        previous_override = self._overrides.pop(page_type, None)
        try:
            self._write_overrides()
        except OcrRoiConfigError:
            if previous_override is not None:
                self._overrides[page_type] = previous_override
            raise
        self._layouts[page_type] = self._defaults[page_type]

    @staticmethod
    def _validate_page_type(page_type: str) -> None:
        if page_type not in _PAGE_REQUIREMENTS:
            raise OcrRoiConfigError(f"不支持的 OCR 页面类型: {page_type}")

    @classmethod
    def _load_document(cls, path: Path, *, require_all_pages: bool) -> dict[str, OcrRoiLayout]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OcrRoiConfigError(f"ROI 配置文件不存在: {path}") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OcrRoiConfigError(f"ROI 配置文件无法读取: {path}: {exc}") from exc

        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise OcrRoiConfigError("schema_version 不受支持")
        raw_layouts = data.get("layouts")
        if not isinstance(raw_layouts, dict):
            raise OcrRoiConfigError("layouts 必须是对象")
        unknown_pages = set(raw_layouts) - set(_PAGE_REQUIREMENTS)
        if unknown_pages:
            raise OcrRoiConfigError(f"包含不支持的页面: {', '.join(sorted(unknown_pages))}")
        if require_all_pages and set(raw_layouts) != set(_PAGE_REQUIREMENTS):
            raise OcrRoiConfigError("默认配置必须包含全部 OCR 页面")

        layouts: dict[str, OcrRoiLayout] = {}
        for page_type, raw_layout in raw_layouts.items():
            layout = cls._parse_layout(raw_layout)
            cls._validate_layout(page_type, layout)
            layouts[page_type] = layout
        return layouts

    @staticmethod
    def _parse_layout(raw_layout: object) -> OcrRoiLayout:
        if not isinstance(raw_layout, dict):
            raise OcrRoiConfigError("页面布局必须是对象")
        reference_size = OcrRoiConfig._parse_size(raw_layout.get("reference_size"))
        raw_slots = raw_layout.get("slots")
        if not isinstance(raw_slots, list):
            raise OcrRoiConfigError("slots 必须是数组")
        slots = []
        for raw_slot in raw_slots:
            if not isinstance(raw_slot, dict):
                raise OcrRoiConfigError("每个 slot 必须是对象")
            name_roi = OcrRoiConfig._parse_roi(raw_slot.get("name_roi"), "name_roi")
            raw_team_roi = raw_slot.get("team_roi")
            team_roi = (
                OcrRoiConfig._parse_roi(raw_team_roi, "team_roi")
                if raw_team_roi is not None else None
            )
            slots.append(OcrRoiSlot(name_roi=name_roi, team_roi=team_roi))
        return OcrRoiLayout(reference_size=reference_size, slots=tuple(slots))

    @staticmethod
    def _parse_size(raw_size: object) -> tuple[int, int]:
        if not isinstance(raw_size, list) or len(raw_size) != 2:
            raise OcrRoiConfigError("reference_size 必须是 [宽, 高]")
        width, height = raw_size
        if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0
                   for value in (width, height)):
            raise OcrRoiConfigError("reference_size 必须是正整数")
        return width, height

    @staticmethod
    def _parse_roi(raw_roi: object, field_name: str) -> Roi:
        if not isinstance(raw_roi, list) or len(raw_roi) != 4:
            raise OcrRoiConfigError(f"{field_name} 必须是 [x, y, 宽, 高]")
        x, y, width, height = raw_roi
        if not all(isinstance(value, int) and not isinstance(value, bool)
                   for value in (x, y, width, height)):
            raise OcrRoiConfigError(f"{field_name} 必须使用整数")
        return x, y, width, height

    @classmethod
    def _validate_layout(cls, page_type: str, layout: OcrRoiLayout) -> None:
        cls._validate_page_type(page_type)
        expected_slots, requires_team_roi = _PAGE_REQUIREMENTS[page_type]
        if len(layout.slots) != expected_slots:
            raise OcrRoiConfigError(f"{page_type} 应包含 {expected_slots} 个识别席位")
        reference_width, reference_height = layout.reference_size
        for slot_index, slot in enumerate(layout.slots, 1):
            cls._validate_roi(slot.name_roi, reference_width, reference_height, f"第 {slot_index} 个名称 ROI")
            if requires_team_roi and slot.team_roi is None:
                raise OcrRoiConfigError(f"{page_type} 的第 {slot_index} 个席位缺少阵营 ROI")
            if slot.team_roi is not None:
                cls._validate_roi(slot.team_roi, reference_width, reference_height, f"第 {slot_index} 个阵营 ROI")

    @staticmethod
    def _validate_roi(roi: Roi, image_width: int, image_height: int, label: str) -> None:
        x, y, width, height = roi
        if x < 0 or y < 0 or width <= 0 or height <= 0:
            raise OcrRoiConfigError(f"{label} 坐标或尺寸无效")
        if x + width > image_width or y + height > image_height:
            raise OcrRoiConfigError(f"{label} 超出参考尺寸 {image_width}x{image_height}")

    def _write_overrides(self) -> None:
        data = {
            "schema_version": SCHEMA_VERSION,
            "layouts": {
                page_type: layout.to_dict()
                for page_type, layout in sorted(self._overrides.items())
            },
        }
        self._user_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._user_path.with_suffix(self._user_path.suffix + ".tmp")
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        try:
            tmp_path.write_text(content, encoding="utf-8", newline="\n")
            tmp_path.replace(self._user_path)
        except OSError as exc:
            logger.error("保存 OCR ROI 配置失败: %s", exc)
            raise OcrRoiConfigError(f"保存 OCR ROI 配置失败: {exc}") from exc
