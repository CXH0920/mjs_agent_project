"""汉字视觉特征缓存的加载、补齐与保存。"""

from __future__ import annotations

import csv
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CHARACTER_FEATURE_CACHE = PROJECT_ROOT / "src" / "data" / "char_info_cache.json"
_EMPTY_FEATURE = {
    "radical": "",
    "cangjie": "",
    "four_corner": "",
    "pinyin": "",
    "total_strokes": "",
}


class CharacterFeatureRepository:
    """管理用于汉字相似度比较的特征缓存。"""

    def __init__(self, cache_path: str | Path | None = None) -> None:
        self._cache_path = Path(cache_path or DEFAULT_CHARACTER_FEATURE_CACHE)
        self._entries: dict[str, dict[str, str]] | None = None
        self._radical_client = None
        self._unihan_cache: dict[str, dict[str, str]] | None = None
        self._strokes_cache: dict[str, int] | None = None
        self._strokes_path: Path | None = None

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def load(self) -> dict[str, dict[str, str]]:
        """延迟加载缓存，格式异常时以空缓存继续运行。"""
        if self._entries is not None:
            return self._entries
        if not self._cache_path.exists():
            logger.debug("汉字特征缓存不存在: %s", self._cache_path)
            self._entries = {}
            return self._entries
        try:
            with self._cache_path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            if not isinstance(loaded, dict):
                raise ValueError("缓存根节点必须是对象")
            self._entries = {
                str(char): {str(key): str(value) for key, value in entry.items()}
                for char, entry in loaded.items()
                if isinstance(entry, dict)
            }
            logger.debug("汉字特征缓存已加载: %s (%d 字)", self._cache_path, len(self._entries))
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("汉字特征缓存加载失败: %s", exc)
            self._entries = {}
        return self._entries

    def save(self) -> None:
        """将当前缓存以 UTF-8/LF 原子写入指定路径。"""
        entries = self.load()
        temporary_path = self._cache_path.with_suffix(self._cache_path.suffix + ".tmp")
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
                json.dump(entries, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
            temporary_path.replace(self._cache_path)
        except OSError as exc:
            logger.error("汉字特征缓存保存失败 %s: %s", self._cache_path, exc)
            raise

    def get_feature(self, char: str) -> dict[str, str]:
        """获取单字特征；缓存缺失时按需补齐。"""
        entries = self.load()
        entry = entries.get(char)
        if entry is None:
            entry = self._build_feature(char)
            entries[char] = entry
            logger.debug("汉字特征动态补齐: %s (U+%04X)", char, ord(char))
        return entry

    def get_value(self, char: str, key: str, default: str = "") -> str:
        return self.get_feature(char).get(key, default)

    def warmup(self) -> None:
        """加载缓存及拼音库，避免首次纠错时发生额外初始化。"""
        self.load()
        try:
            from pypinyin import Style, pinyin

            pinyin("一", style=Style.NORMAL)
        except Exception:
            pass

    def _build_feature(self, char: str) -> dict[str, str]:
        entry = dict(_EMPTY_FEATURE)
        radical_client = self._get_radical_client()
        if radical_client:
            try:
                entry["radical"] = radical_client.trans_ch(char) or ""
            except Exception:
                pass
        unihan_feature = self._query_unihan(char)
        entry["cangjie"] = unihan_feature.get("cangjie", "")
        entry["four_corner"] = unihan_feature.get("four_corner", "")
        entry["pinyin"] = self._get_pinyin(char)
        entry["total_strokes"] = str(self._get_stroke(char))
        return entry

    def _get_radical_client(self):
        if self._radical_client is None:
            try:
                from cnradical import Radical, RunOption

                self._radical_client = Radical(RunOption.Radical)
            except Exception as exc:
                logger.warning("cnradical 初始化失败: %s", exc)
                self._radical_client = False
        return self._radical_client if self._radical_client is not False else None

    def _query_unihan(self, char: str) -> dict[str, str]:
        if self._unihan_cache is None:
            self._unihan_cache = {}
            try:
                from unihan_etl.core import Options, Packager

                options = Options(
                    format="csv",
                    fields=("kCangjie", "kFourCornerCode"),
                    download=False,
                    cache=True,
                    log_level="WARNING",
                )
                Packager(options).export()
                csv_path = Path(options.destination)
                if csv_path.exists():
                    with csv_path.open("r", encoding="utf-8") as file:
                        for row in csv.DictReader(file):
                            item = row.get("char", "")
                            if item:
                                self._unihan_cache[item] = {
                                    "cangjie": (row.get("kCangjie", "") or "").strip(),
                                    "four_corner": (row.get("kFourCornerCode", "") or "").strip()[:5],
                                }
            except Exception as exc:
                logger.warning("UNIHAN 查询失败: %s", exc)
        return self._unihan_cache.get(char, {})

    def _get_stroke(self, char: str) -> int:
        return self._load_strokes().get(char, 0)

    def _load_strokes(self) -> dict[str, int]:
        if self._strokes_cache is not None:
            return self._strokes_cache
        self._strokes_cache = {}
        path = self._get_strokes_path()
        if path is None:
            return self._strokes_cache
        try:
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) < 3 or parts[1] != "kTotalStrokes":
                        continue
                    value = parts[2].strip().split("-", 1)[0]
                    self._strokes_cache[chr(int(parts[0][2:], 16))] = int(value)
            logger.debug("笔画数已加载: %d 字", len(self._strokes_cache))
        except (OSError, ValueError) as exc:
            logger.warning("UNIHAN IRGSources 笔画数加载失败: %s", exc)
            self._strokes_cache = {}
        return self._strokes_cache

    def _get_strokes_path(self) -> Path | None:
        if self._strokes_path is not None:
            return self._strokes_path
        try:
            from unihan_etl.core import Options

            path = Path(Options().work_dir) / "Unihan_IRGSources.txt"
            self._strokes_path = path if path.exists() else None
        except Exception as exc:
            logger.warning("UNIHAN IRGSources 路径获取失败: %s", exc)
        return self._strokes_path

    @staticmethod
    def _get_pinyin(char: str) -> str:
        try:
            from pypinyin import Style, pinyin

            values = pinyin(char, style=Style.NORMAL)
            return values[0][0] if values else ""
        except Exception:
            return ""
