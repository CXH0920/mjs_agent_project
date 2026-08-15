# -*- coding: utf-8 -*-
"""武将分类维护仓储（data/hero_classification.json）。

该文件是 RAG「武将分类语料」的唯一人工维护源：
- build_classification_corpus.py 读取本文件生成武将分类语料；
- 本仓储提供分类/克制链/武将归类的增删改与校验，不直接写语料/索引。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.data.manager import DataIssue

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_HERO_CLASSIFICATION_FILE = DEFAULT_DATA_DIR / "hero_classification.json"


class ClassificationCategory(BaseModel):
    """机制分类：核心特征 / 典型武将 / 占比仅供参考展示。"""

    name: str = Field(..., min_length=1)
    core_features: str = ""
    typical_heroes: list[str] = Field(default_factory=list)
    ratio: str = ""

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("分类名称不能为空")
        return value


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """以 UTF-8、LF 和同目录临时文件原子保存 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        Path(temporary).replace(path)
    except Exception:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise


class HeroClassificationRepository:
    """data/hero_classification.json 的可写仓储（分类/克制链/武将归类）。

    所有 CRUD 仅修改内存，需显式调用 save() 才写盘（便于批量编辑后一次保存）。
    """

    def __init__(self, file_path: str | Path = DEFAULT_HERO_CLASSIFICATION_FILE,
                 hero_names: set[str] | None = None):
        self.file_path = Path(file_path)
        self.hero_names = set(hero_names or ())
        self.load_issues: list[DataIssue] = []
        self.available = False
        self._categories: list[ClassificationCategory] = []
        self._counter_chain: dict[str, str] = {}
        self._hero_categories: dict[str, list[str]] = {}
        self._version = "1.0"
        self._source = ""
        self._updated_at = ""

    def _issue(self, severity: str, kind: str, message: str, index: int | None = None,
               key: object | None = None) -> None:
        self.load_issues.append(DataIssue(severity, kind, self.file_path, message, index, key))
        (logger.warning if severity == "warning" else logger.error)(
            "武将分类数据问题 [%s] %s", kind, message)

    def load(self) -> list[DataIssue]:
        self.load_issues = []
        self._categories = []
        self._counter_chain = {}
        self._hero_categories = {}
        self.available = False
        try:
            with self.file_path.open("r", encoding="utf-8") as stream:
                root = json.load(stream)
        except FileNotFoundError:
            self._issue("warning", "file_missing", "文件不存在")
            return self.load_issues
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self._issue("error", "file_read_error", str(error))
            return self.load_issues
        if not isinstance(root, dict):
            self._issue("error", "invalid_root", "武将分类文件必须是 JSON 对象")
            return self.load_issues
        self.available = True
        self._version = str(root.get("version", "1.0"))
        self._source = str(root.get("source", "") or "")
        self._updated_at = str(root.get("updated_at", "") or "")

        cat_names: set[str] = set()
        raw_categories = root.get("categories", [])
        if not isinstance(raw_categories, list):
            self._issue("error", "invalid_categories", "categories 必须是数组")
            return self.load_issues
        for index, raw in enumerate(raw_categories):
            try:
                cat = ClassificationCategory.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_category", str(error), index)
                continue
            if cat.name in cat_names:
                self._issue("error", "duplicate_key", f"重复分类名: {cat.name}", index, cat.name)
                continue
            cat_names.add(cat.name)
            self._categories.append(cat)

        raw_hero_cats = root.get("hero_categories", {})
        if isinstance(raw_hero_cats, dict):
            for hero, cats in raw_hero_cats.items():
                if not isinstance(cats, list):
                    self._issue("warning", "invalid_hero_categories", f"武将 {hero} 归类格式错误", key=hero)
                    continue
                valid = [c for c in cats if c in cat_names]
                if len(valid) != len(cats):
                    self._issue("warning", "unknown_category_ref",
                                f"武将 {hero} 引用了不存在的分类", key=hero)
                self._hero_categories[hero] = valid
        raw_chain = root.get("counter_chain", {})
        if isinstance(raw_chain, dict):
            for cat, desc in raw_chain.items():
                if cat not in cat_names:
                    self._issue("warning", "unknown_category_ref", f"克制链键 {cat} 不在分类中", key=cat)
                if isinstance(desc, str):
                    self._counter_chain[cat] = desc
                elif isinstance(desc, list):
                    # 兼容历史坏数据：字符列表还原为文本
                    self._counter_chain[cat] = "".join(str(c) for c in desc)
                    self._issue("warning", "chain_list_legacy",
                                f"克制链 {cat} 的字符列表已自动还原为文本", key=cat)
        return self.load_issues

    # ---------------------------------------------------------------
    # 分类 CRUD
    # ---------------------------------------------------------------
    def list_categories(self) -> list[ClassificationCategory]:
        return list(self._categories)

    def get_category(self, name: str) -> ClassificationCategory | None:
        for cat in self._categories:
            if cat.name == name:
                return cat
        return None

    def add_category(self, category: ClassificationCategory) -> None:
        if self.get_category(category.name) is not None:
            raise ValueError(f"分类已存在: {category.name}")
        self._categories.append(category)

    def update_category(self, category: ClassificationCategory) -> None:
        for index, existing in enumerate(self._categories):
            if existing.name == category.name:
                self._categories[index] = category
                return
        raise ValueError(f"分类不存在: {category.name}")

    def delete_category(self, name: str) -> None:
        if self.get_category(name) is None:
            raise ValueError(f"分类不存在: {name}")
        self._categories = [c for c in self._categories if c.name != name]
        # 克制链描述为自然语言文本，不做子串清理；仅移除键并丢弃历史坏数据（list 形态）
        self._counter_chain.pop(name, None)
        for key in list(self._counter_chain):
            if not isinstance(self._counter_chain[key], str):
                del self._counter_chain[key]
        for hero in list(self._hero_categories):
            self._hero_categories[hero] = [c for c in self._hero_categories[hero] if c != name]
            if not self._hero_categories[hero]:
                del self._hero_categories[hero]

    # ---------------------------------------------------------------
    # 克制链
    # ---------------------------------------------------------------
    def counter_chain(self) -> dict[str, str]:
        return dict(self._counter_chain)

    def get_chain_description(self, category: str) -> str:
        return self._counter_chain.get(category, "")

    def set_counter_chain(self, category: str, description: str) -> None:
        if self.get_category(category) is None:
            raise ValueError(f"分类不存在: {category}")
        self._counter_chain[category] = description.strip()

    # ---------------------------------------------------------------
    # 武将归类
    # ---------------------------------------------------------------
    def hero_categories(self) -> dict[str, list[str]]:
        return {k: list(v) for k, v in self._hero_categories.items()}

    def get_hero_categories(self, hero: str) -> list[str]:
        return list(self._hero_categories.get(hero, []))

    def set_hero_categories(self, hero: str, categories: list[str]) -> None:
        unknown = [c for c in categories if self.get_category(c) is None]
        if unknown:
            raise ValueError(f"分类不存在: {'、'.join(unknown)}")
        if self.hero_names and hero not in self.hero_names:
            raise ValueError(f"武将不在武将库中: {hero}")
        self._hero_categories[hero] = list(dict.fromkeys(categories))

    def clear_hero(self, hero: str) -> None:
        self._hero_categories.pop(hero, None)

    def list_unclassified(self) -> list[str]:
        if not self.hero_names:
            return []
        return sorted(self.hero_names - set(self._hero_categories))

    def list_classified(self) -> list[str]:
        return sorted(self._hero_categories)

    # ---------------------------------------------------------------
    # 持久化
    # ---------------------------------------------------------------
    def dirty(self) -> bool:
        return True

    def save(self) -> None:
        payload = {
            "version": self._version,
            "updated_at": date.today().isoformat(),
            "source": self._source,
            "categories": [cat.model_dump(mode="json", exclude_defaults=True)
                           for cat in self._categories],
            "hero_categories": {k: v for k, v in sorted(self._hero_categories.items())},
            "counter_chain": {k: v for k, v in sorted(self._counter_chain.items())},
        }
        _atomic_json_write(self.file_path, payload)
        self._updated_at = payload["updated_at"]