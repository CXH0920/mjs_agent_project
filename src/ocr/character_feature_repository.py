"""汉字视觉特征缓存的加载、补齐与保存。"""

from __future__ import annotations

import csv
import json
import logging
import threading
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CHARACTER_FEATURE_CACHE = PROJECT_ROOT / "src" / "data" / "char_info_cache.json"
USER_CHARACTER_FEATURE_CACHE = PROJECT_ROOT / "data" / "char_info_cache.json"
DEFAULT_WUBI_TABLE = PROJECT_ROOT / "src" / "data" / "wubi86.txt"
_EMPTY_FEATURE = {
    "radical": "",
    "cangjie": "",
    "four_corner": "",
    "pinyin": "",
    "total_strokes": "",
    "wubi": "",
}


class CharacterFeatureRepository:
    """管理用于汉字相似度比较的特征缓存。"""

    def __init__(
        self,
        cache_path: str | Path | None = None,
        user_cache_path: str | Path | None = None,
    ) -> None:
        self._cache_path = Path(cache_path or DEFAULT_CHARACTER_FEATURE_CACHE)
        if user_cache_path is None:
            user_cache_path = USER_CHARACTER_FEATURE_CACHE
        self._user_cache_path = Path(user_cache_path) if user_cache_path else None
        self._persist_lock = threading.Lock()
        self._entries: dict[str, dict[str, str]] | None = None
        self._radical_client = None
        self._unihan_cache: dict[str, dict[str, str]] | None = None
        self._strokes_cache: dict[str, int] | None = None
        self._strokes_path: Path | None = None
        self._wubi_cache: dict[str, str] | None = None
        self._wubi_path: Path | None = None
        self._pinyin_available: bool | None = None

    @property
    def cache_path(self) -> Path:
        return self._cache_path

    def load(self) -> dict[str, dict[str, str]]:
        """延迟加载基线 + 用户层缓存，格式异常时以空缓存继续运行。"""
        if self._entries is not None:
            return self._entries
        baseline = self._read_entries(self._cache_path, "汉字特征缓存")
        user = self._read_entries(self._user_cache_path, "汉字特征用户层缓存")
        if user:
            entries = {**baseline, **user}
            logger.debug(
                "汉字特征缓存已合并用户层: 基线 %d 字 + 用户 %d 字",
                len(baseline),
                len(user),
            )
        else:
            entries = baseline
        self._entries = entries
        return self._entries

    def _read_entries(self, path: Path | None, label: str) -> dict[str, dict[str, str]]:
        """读取单层特征缓存 JSON；缺失或格式异常时返回空字典。"""
        if path is None or not path.exists():
            logger.debug("%s不存在: %s", label, path)
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            if not isinstance(loaded, dict):
                raise ValueError("缓存根节点必须是对象")
            return {
                str(char): {str(key): str(value) for key, value in entry.items()}
                for char, entry in loaded.items()
                if isinstance(entry, dict)
            }
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("%s加载失败: %s", label, exc)
            return {}

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
            self._persist_user_entries([char])
        return entry

    def get_value(self, char: str, key: str, default: str = "") -> str:
        return self.get_feature(char).get(key, default)

    def warmup(self) -> None:
        """加载缓存及拼音库，避免首次纠错时发生额外初始化。"""
        self.load()
        if self._pinyin_available is False:
            return
        try:
            from pypinyin import Style, pinyin

            pinyin("一", style=Style.NORMAL)
            self._pinyin_available = True
        except Exception as exc:
            self._pinyin_available = False
            logger.warning("pypinyin 预热失败，拼音特征将降级: %s", exc)

    def warmup_characters(self, characters: Iterable[str]) -> int:
        """将词表字符补齐到进程内缓存，避免首次纠错按需初始化。"""
        entries = self.load()
        missing = sorted({char for char in characters if char and char not in entries})
        for char in missing:
            entries[char] = self._build_feature(char)
        if missing:
            logger.info("汉字特征预热补齐 %d 个词表字符", len(missing))
            self._persist_user_entries(missing)
        return len(missing)

    def _persist_user_entries(self, chars: Iterable[str]) -> None:
        """将动态构建的字符特征写入用户层缓存；失败仅降级，不影响识别。"""
        if self._user_cache_path is None:
            return
        chars = [char for char in chars if char]
        if not chars:
            return
        with self._persist_lock:
            entries = self.load()
            user_entries = self._read_entries(self._user_cache_path, "汉字特征用户层缓存")
            user_entries.update({char: entries[char] for char in chars if char in entries})
            try:
                self._user_cache_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = self._user_cache_path.with_suffix(
                    self._user_cache_path.suffix + ".tmp"
                )
                with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
                    json.dump(user_entries, file, ensure_ascii=False, indent=2, sort_keys=True)
                    file.write("\n")
                temporary_path.replace(self._user_cache_path)
                logger.debug(
                    "汉字特征用户层缓存已更新: %s (%d 字)",
                    self._user_cache_path,
                    len(user_entries),
                )
            except OSError as exc:
                logger.warning("汉字特征用户层缓存写入失败（忽略）: %s", exc)

    def _build_feature(self, char: str) -> dict[str, str]:
        entry = dict(_EMPTY_FEATURE)
        radical_client = self._get_radical_client()
        if radical_client:
            try:
                entry["radical"] = radical_client.trans_ch(char) or ""
            except Exception as exc:
                logger.warning("cnradical 查询失败 %s: %s", char, exc)
        unihan_feature = self._query_unihan(char)
        entry["cangjie"] = unihan_feature.get("cangjie", "")
        entry["four_corner"] = unihan_feature.get("four_corner", "")
        entry["pinyin"] = self._get_pinyin(char)
        entry["total_strokes"] = str(self._get_stroke(char))
        entry["wubi"] = self._get_wubi(char)
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
                csv_path = Path(options.destination)
                if not csv_path.exists():
                    Packager(options).export()
                if csv_path.exists():
                    with csv_path.open("r", encoding="utf-8") as file:
                        for row in csv.DictReader(file):
                            item = row.get("char", "")
                            if item:
                                four_corner = row.get("kFourCornerCode", "") or ""
                                self._unihan_cache[item] = {
                                    "cangjie": (row.get("kCangjie", "") or "").strip(),
                                    "four_corner": "".join(
                                        value for value in four_corner if value.isdigit()
                                    )[:4],
                                }
            except Exception as exc:
                logger.warning("UNIHAN 查询失败: %s", exc)
        return self._unihan_cache.get(char, {})

    def _get_wubi(self, char: str) -> str:
        return self._load_wubi().get(char, "")

    def _load_wubi(self) -> dict[str, str]:
        """懒加载五笔 86 全码表（UTF-8/LF，字<TAB>码），缺失返回空映射。"""
        if self._wubi_cache is not None:
            return self._wubi_cache
        self._wubi_cache = {}
        path = self._get_wubi_path()
        if path is None:
            return self._wubi_cache
        try:
            with path.open("r", encoding="utf-8") as file:
                for line in file:
                    if not line.strip() or line.startswith("#"):
                        continue
                    parts = line.strip().split("\t")
                    if len(parts) == 2 and len(parts[0]) == 1:
                        self._wubi_cache[parts[0]] = parts[1]
            logger.debug("五笔86全码已加载: %d 字", len(self._wubi_cache))
        except OSError as exc:
            logger.warning("五笔86全码表加载失败: %s", exc)
            self._wubi_cache = {}
        return self._wubi_cache

    def _get_wubi_path(self) -> Path | None:
        if self._wubi_path is not None:
            return self._wubi_path
        self._wubi_path = DEFAULT_WUBI_TABLE if DEFAULT_WUBI_TABLE.exists() else None
        if self._wubi_path is None:
            logger.warning("五笔86全码表不存在: %s", DEFAULT_WUBI_TABLE)
        return self._wubi_path

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

    def _get_pinyin(self, char: str) -> str:
        if self._pinyin_available is False:
            return ""
        try:
            from pypinyin import Style, pinyin

            values = pinyin(char, style=Style.NORMAL)
            self._pinyin_available = True
            return values[0][0] if values else ""
        except Exception as exc:
            self._pinyin_available = False
            logger.warning("pypinyin 查询失败 %s，拼音特征将降级: %s", char, exc)
            return ""
