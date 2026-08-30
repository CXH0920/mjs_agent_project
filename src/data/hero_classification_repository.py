# -*- coding: utf-8 -*-
"""武将分类维护仓储（data/hero_classification.json）。

该文件是 RAG「武将分类语料」的唯一人工维护源：
- build_classification_corpus.py 读取本文件生成武将分类语料；
- 本仓储提供分类/克制链/武将归类的增删改与校验，不直接写语料/索引。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.config.env import PROJECT_ROOT
from src.data.json_repository import JsonRepository
from src.data.manager import DataIssue

logger = logging.getLogger(__name__)

# 打包态 __file__ 落在只读 _internal，须写 exe 级可写运行时根（见 src/config/env.py）
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
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


class HeroClassificationRepository(JsonRepository):
    """data/hero_classification.json 的可写仓储（分类/克制链/武将归类）。

    所有 CRUD 仅修改内存，需显式调用 save() 才写盘（便于批量编辑后一次保存）。
    """

    def __init__(self, file_path: str | Path = DEFAULT_HERO_CLASSIFICATION_FILE,
                 hero_names: set[str] | None = None):
        super().__init__(file_path)
        self.hero_names = set(hero_names or ())
        self._categories: list[ClassificationCategory] = []
        self._counter_chain: dict[str, str] = {}
        self._hero_categories: dict[str, list[str]] = {}
        self._version = "1.0"
        self._source = ""
        self._updated_at = ""
        self._note = ""
        # 上次成功持久化/加载的内存快照：保存失败时回滚到该状态（显式保存模式的 #11）
        self._saved_snapshot: tuple[list[ClassificationCategory], dict[str, str], dict[str, list[str]]] | None = None

    # 写盘失败回滚：三类数据字段快照
    def _snapshot(self) -> tuple[list[ClassificationCategory], dict[str, str], dict[str, list[str]]]:
        return list(self._categories), dict(self._counter_chain), {k: list(v) for k, v in self._hero_categories.items()}

    def _restore(self, snapshot: tuple[list[ClassificationCategory], dict[str, str], dict[str, list[str]]]) -> None:
        self._categories, self._counter_chain, self._hero_categories = snapshot

    def load(self) -> list[DataIssue]:
        root, ok = self._read_root()
        if not ok:
            return self.load_issues
        if not isinstance(root, dict):
            self._issue("error", "invalid_root", "武将分类文件必须是 JSON 对象")
            return self.load_issues
        self.available = True
        self._categories = []
        self._counter_chain = {}
        self._hero_categories = {}
        self._version = str(root.get("version", "1.0"))
        self._source = str(root.get("source", "") or "")
        self._updated_at = str(root.get("updated_at", "") or "")
        self._note = str(root.get("note", "") or "")

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
        self._saved_snapshot = self._snapshot()
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
            "note": self._note,
            "categories": [cat.model_dump(mode="json", exclude_defaults=True)
                           for cat in self._categories],
            "hero_categories": dict(self._hero_categories),
            "counter_chain": dict(self._counter_chain),
        }
        try:
            self.save_payload(payload)
        except Exception:
            # 回滚到上次成功持久化的状态（避免"看似失败、实际已变"的脏状态）
            if self._saved_snapshot is not None:
                self._restore(self._saved_snapshot)
            raise
        self._updated_at = payload["updated_at"]
        self._saved_snapshot = self._snapshot()