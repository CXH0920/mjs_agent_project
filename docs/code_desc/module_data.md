# 模块：数据模型与数据管理

> 对应目录：`src/data/`
> 职责：定义项目核心数据模型，提供对 JSON 数据文件的增删改查和原子持久化操作

---

## 一、模块职责

本模块是项目的**数据基础层**，承担三个核心角色：

1. **模型定义**（`models.py`）— 通过 Pydantic v2 定义 `Hero`、`SynergyScore`、`HeroGuide`、`Combo`、`Card`、`Announcement`、`IncrementalUpdate` 等核心数据模型，作为项目唯一的 JSON 格式契约，确保官网爬虫与 AI 生成的输出格式一致
2. **数据管理**（`manager.py` + `*_manager.py`）— `DataManager[V_co]` 泛型基类提供通用 CRUD、加载、保存与内存快照回滚；五个子类 Manager 继承基类并添加各自的查询与领域方法；`DataFacade` 门面统一访问入口，并可通过 `from_managers()` 复用外部 Manager
3. **JSON 仓库基类**（`json_repository.py`）— `JsonRepository` 统一维护仓库的原子写盘、加锁读盘与写盘失败内存回滚；`atomic_write_json` 是全库唯一原子写入口（`DataManager` / `card_catalog` / 所有维护仓库均委托于此）

---

## 二、文件结构

```
src/data/
├── __init__.py
├── models.py                     # Pydantic 数据模型（Hero / Skill / Card / SynergyScore / Combo / HeroGuide / Announcement / HeroChange / BaikeSnapshot / IncrementalUpdate）
├── manager.py                    # DataIssue / LoadReport / DataManager[V_co] 泛型基类 / DataFacade 门面 / apply_incremental_update()
├── json_repository.py            # atomic_write_json / JsonRepository 基类（原子写 + 加锁读 + 写失败回滚）
├── hero_manager.py               # Hero CRUD + JSON 持久化（继承 DataManager[Hero]）
├── synergy_manager.py            # SynergyScore CRUD + JSON 持久化（继承 DataManager[SynergyScore]）
├── guide_manager.py              # HeroGuide CRUD + JSON 持久化（继承 DataManager[HeroGuide]）
├── combo_manager.py              # Combo CRUD + JSON 持久化 + 手工配队维护（继承 DataManager[Combo]）
├── combo_seats.py                # parse_seats() — 从 note 自由文本解析双方武将座次要求
├── announcement_manager.py       # Announcement CRUD / 状态机 / BaikeSnapshot 百科快照
├── card_catalog.py               # 官方卡牌只读仓储 + 字段定义仓储 + 追加内容仓储 + CardViewModel 合并
├── card_points_repository.py     # 卡牌点数花色维护（data/card_points.json，原 xlsx sheet1 迁移）
├── equip_attrs_repository.py     # 装备属性维护（data/equip_attrs.json，原 xlsx sheet2 迁移）
├── hero_classification_repository.py # 武将分类/克制链/归类维护（data/hero_classification.json）
├── special_cards_repository.py   # 专属牌/战法牌/特殊牌区/状态/概念维护（data/special_cards.json）
├── peak_win_rate_repository.py   # 巅峰赛单将胜率 + 出场排行 CSV 读取（独立于 2v2 胜率）
├── win_rate_repository.py        # 2v2 胜率 CSV 读取（打包基线 BUNDLE_ROOT/data）
└── recommendation_index_repository.py # 武将推荐指数计算、快照写入与读取 / stale 状态自愈校验
```

六个维护仓库（`card_points` / `equip_attrs` / `hero_classification` / `special_cards`）由 RAG 语料构建脚本（`build_cardpts.py` / `build_equip_attr.py` / `build_classification_corpus.py` / `build_special_corpus.py`）读取生成向量库语料，是**唯一的人工维护源**，不再从 xlsx 归档读取。

卡牌资料由 `CardRepository` 只读加载 `data/cards.json`；`CardFieldSchemaRepository` 与 `CardAnnotationRepository` 分别维护 `card_field_schema.json` 与 `card_annotations.json`。`CardViewModel` 将基础卡牌与追加字段合并为可展示视图，`CardFieldDefinition` 支持字段归档（`archived`）与旧记录迁移（`EffectEntry.migrate_legacy_fields` 将 `effective_from` 映射为 `created_at/updated_at`）。基础文件从不提供保存入口。

2v2 胜率数据由 `win_rate_repository.load_win_rates()` 从 `BUNDLE_ROOT/data/2v2胜率排行.csv` 读取（打包只读基线，结果默认缓存）；巅峰赛胜率/出场排行由 `peak_win_rate_repository` 读取 `data/巅峰赛胜率排行.csv` 与 `data/巅峰赛出场排行.csv`，数据源未落地时返回空 dict。`recommendation_index_repository` 基于 2v2 三份榜单（胜率/出场/放逐）及 `heroes.json` 计算推荐指数，输出 `武将推荐指数.csv`；官方榜单导入后写 `stale=true` 标记，用户确认后立即重建；`is_recommendation_index_stale()` 带**自愈校验**：即使状态文件被外部误置 `stale=true`，只要三份榜单 CSV 修改时间均不晚于快照，自动写回 `false` 避免误弹横幅。

---

## 三、核心逻辑

### 3.1 数据模型体系

所有核心模型定义在 `models.py` 中，继承 `pydantic.BaseModel`：

```python
class Hero(BaseModel):
    id: int                        # 武将 ID（validation_alias="角色ID"）
    name: str                      # validation_alias="名称"
    title: str                     # 称号
    faction: str                   # validation_alias="势力"
    position: str                  # validation_alias="定位"
    max_hp: int                    # validation_alias="体力上限"，范围 1-20
    max_hand: int                  # validation_alias="手牌上限"，范围 0-20
    gender: Gender                 # 枚举：MALE/女
    skills: list[Skill]            # 最多 20 项
    difficulty: Difficulty         # 1-5 枚举
    mode_viability: dict[str, ViabilityTier]  # 仅允许键 1v1/2v2/3v3/5v5/乱斗
    last_updated: str              # 默认 date.today()
    icon_url: str
    # 校验器：name 非空 / id 正整数 / mode_viability 键合法

class SynergyScore(BaseModel):
    hero_a_id: int                 # 正整数校验
    hero_b_id: int                 # 正整数校验
    score: int                     # 范围 -10~10
    synergy_rating: str            # S/A/B/C/D，由 synergy_rating_for_score() 自动推导
    combo_ceiling: int             # 1-10
    combo_stability: int           # 1-10
    adaptability: int              # 1-10
    description: str               # 最大 4000 字符
    last_updated: str
    # model_validator：双方不能是同 ID；rating 由 score 重新推导

class Combo(BaseModel):
    hero1_name: str                # 导入保留的原始名称
    hero2_name: str
    hero1_id: int                  # 正整数校验
    hero2_id: int                  # 正整数校验
    rating: int                    # 1-10
    position: str                  # 配对级座位摘要（如 both/14/23）
    note: str                      # 手录备注（座次顺序的权威来源）
    hero1_seats: list[int]         # 1-4，自动排序
    hero2_seats: list[int]         # 1-4，自动排序
    manual: bool                   # 手工录入标记，导入合并时优先保留
    # model_validator：双方不能是同 ID

class HeroGuide(BaseModel):
    hero_id: int
    key_points: list[str]          # 每项最大 1000 字符，最多 20 项
    weak_against_type: list[str]
    strong_against_type: list[str]
    synergizes_with: list[int]     # 武将 ID 列表
    counter_strategy: str          # 最大 1000 字符
    description: str               # 最大 20000 字符
    tips_for_beginners: str        # 最大 1000 字符
    last_updated: str
```

**官网字段映射**：Hero 模型使用 `validation_alias` 将官网中文字段名映射到英文属性名，AI 生成结果直接使用英文字段名，`model_config = {"populate_by_name": True}` 使两种数据源均可校验。

**`synergy_rating_for_score(score)`** 函数根据综合评分推定评级：`>=9 → S`、`>=6 → A`、`>=3 → B`、`>=0 → C`、其余 `D`，在 `SynergyScore` 的 `model_validator` 中自动赋值，外部赋值会被覆盖。

### 3.2 DataManager 泛型基类

`DataManager[V_co]` 定义了所有 Manager 共用的 CRUD 和能力接口：

```python
class DataManager(Generic[V_co]):
    def __init__(self, file_path: str | Path, model_class: type[V_co])
    def load(self) -> list[DataIssue]          # 加锁，委托 _load_unlocked()
    def _parse_items(self, data) -> dict        # 子类重写：从 JSON 构建 _items
    def _parse_models(self, data, key_of) -> dict  # 逐条 Pydantic 校验，跳过坏记录与重复键
    def save(self) -> None                      # 加锁，委托 _save_unlocked()
    def _save_unlocked(self) -> None            # 子类可重写（如 ComboManager 排序后写盘）
    def get(key) -> V_co | None                 # 加锁查询
    def list_all() -> list[V_co]                # 加锁返回全部
    def add(item, key) -> None                  # 已存在抛 ValueError
    def update(item, key) -> None               # 覆盖式 upsert
    def delete(key) -> None                     # 不存在静默
    def clear_all() -> int                      # 清空并返回条数
    def snapshot_items() / restore_items()      # 跨文件回滚用的内存快照
```

`_parse_models()` 对 JSON 数组逐条执行 `model_class.model_validate()`，校验失败记为 `invalid_record`，重复键记为 `duplicate_key`，均不影响其他合法记录的加载。

> **设计思路：** 子类仅需实现 `_parse_items()`（通过 `key_of` lambda 确定字典键）与领域查询方法，完全共享 CRUD 骨架。`Generic[V_co]` 协变使 `DataManager[Hero]` 可安全赋值给 `DataManager[BaseModel]` 类型变量。

### 3.3 DataFacade 门面

`DataFacade` 聚合三个 Manager，提供统一的数据访问入口：

```python
facade = DataFacade()                          # 默认 heroes/synergies/guides 文件
facade = DataFacade.from_managers(h, s, g)    # 复用已有 Manager（避免循环依赖）
report = facade.load_all()                     # 三个 load() + 跨实体校验 → LoadReport
stats  = facade.get_stats()                    # {"heroes": N, "synergies": N, "guides": N}
facade.save_all()                              # 原子保存三个文件
```

`load_all()` 内部对每个 Manager 执行 `load()` 后将 `DataIssue` 汇总到 `LoadReport`，随后 `_validate_references()` 校验跨实体引用：相性双方 ID、攻略归属 ID、攻略 `synergizes_with` 列表中的武将 ID 都必须存在于英雄库。失效引用仅记入报告（`kind="missing_reference"`），不在加载时删除内存数据；报告持久化到 `facade.last_load_report`。

`from_managers()` 使用 `__new__` 跳过 `__init__` 的默认构造，直接注入已有 Manager，供测试与业务层注入 mock。

### 3.4 相性与配队的双向归一

相性是双向无向关系 — `(A=114, B=115)` 和 `(B=115, A=114)` 被视为同一条数据：

```python
# synergy_manager.py
def _synergy_key(a_id, b_id) -> tuple[int, int]:
    return tuple(sorted((a_id, b_id)))

# combo_manager.py
def _combo_key(a_id, b_id) -> tuple[int, int]:
    return tuple(sorted((a_id, b_id)))
```

`SynergyScore` 模型层的 `model_validator` 额外保证 `hero_a_id != hero_b_id`；`Combo` 同样保证 `hero1_id != hero2_id`。

### 3.5 ComboManager 落盘排序（消除 diffs 噪音）

`ComboManager._save_unlocked()` 覆写基类的默认写法，落盘前按 **`-rating`、`hero1_id`、`hero2_id`** 升序稳定排序，再写入 `combos.json`：

```python
def _save_unlocked(self) -> None:
    ordered = sorted(
        self._items.values(),
        key=lambda c: (-c.rating, c.hero1_id, c.hero2_id),
    )
    data = [v.model_dump(mode="json") for v in ordered]
    from src.data.json_repository import atomic_write_json
    atomic_write_json(self.file_path, data, indent=2)
```

> **设计思路：** 物理行序与武将名解绑——新增武将（id 较大）自然落到各 rating 段末尾，避免按名排序时新名字插入中段、其后条目整体平移造成的 git diff 噪音。

### 3.6 公告记录与百科快照

`AnnouncementManager(DataManager[Announcement])` 管理 `data/announcements.json`：

- `Announcement` 模型字段：`id/title/content/url/publishdate/hero_related/matched_heroes/content_missing/status/first_seen_at`；
- `AnnouncementStatus` 枚举：`PENDING(待生效) → READY(可更新) → APPLIED(已处理)`；
- 以 `url` 为主键去重（`stable_key()` 回退到 `f"id:{id}"`）；`merge_new(items, baseline=True)` 用于首次运行基线：只落盘、不返回、不提醒，非武将相关公告直接置 `APPLIED`；
- 状态推进：`mark_ready_if_updated(diff, current_names)`（公告提及的武将名与百科 diff 变更集匹配 → READY，或新增武将已全部落地百科 → READY）；`mark_applied()`（仅将 READY 推进到 APPLIED，PENDING 因可能仍在百科滞后窗口内保留不动）。

`BaikeSnapshot` / `load_baike_snapshot()` / `save_baike_snapshot()` 管理 `data/baike_snapshot.json`（覆盖式，结构 `{checked_at, heroes:{id:{name, hash}}}`）。快照解析失败时返回空快照（由调用方重建基线）；`save_baike_snapshot()` 用 `mkstemp` 生成唯一中转名（固定 `.tmp` 名在并发时会互相覆盖）。

### 3.7 实战配队座次解析

`combo_seats.py` 从 note 自由文本解析双方武将座次：

| 规则 | 说明 |
|------|------|
| 优先级 1 | 匹配"武将名+数字"或"数字+武将名"（含 ALIAS 别名：牢布→吕布、甄姬→甄宓、夏侯停→夏侯惇） |
| 优先级 2 | 剥离武将名后取开头纯数字 token，按顺序对应 hero1/hero2 |
| "0" | 无座次要求，返回空列表 |
| 两位数字 | 可选区间（如 "34"=3 或 4 号） |

状态：`STATUS_PARSED` / `STATUS_PARTIAL` / `STATUS_NONE` / `STATUS_UNPARSED`。`parse_seats(note, hero1, hero2) -> (status, hero1_seats, hero2_seats)`。`format_seats(seats)` 将列表转为展示文本（如 "1/3/4" 或 "任意"）。

`Combo.position` 字段是配对级无序摘要（如 `both`/`14`/`23`），不含顺序信息；`note` 才是座次顺序的权威来源。

### 3.8 巅峰赛胜率仓库

巅峰赛使用独立的单将胜率 + 出场排行 CSV（`data/巅峰赛胜率排行.csv` / `data/巅峰赛出场排行.csv`），与 2v2 胜率仓库互相独立。数据源尚未落地时返回空 dict。

```
load_peak_win_rates(path)   → {武将名: 百分比}     # 默认缓存
load_peak_pick_ranks(path)  → {武将名: 出场排名}    # 默认缓存
clear_peak_win_rate_cache()  # 清空胜率与出场排行
```

### 3.9 武将推荐指数

`recommendation_index_repository` 基于 2v2 三份榜单（`2v2胜率排行.csv` / `2v2出场排行.csv` / `武将放逐.csv`）及 `heroes.json` 计算武将推荐指数，输出 `武将推荐指数.csv` 快照。

核心流程：`refresh_recommendation_indexes(config) -> dict[str, RecommendationIndex]`：
1. 读取三份 CSV 与 `heroes.json` 武将 ID 映射；
2. 每武将校验胜率/出场排名/禁用排名/唯一 ID 是否齐全，缺失标记"数据不足"；
3. 有效武将按 `win_rate * preference * sigmoid` 计算 `raw_index`，再映射为 `[0, 100]` 的 `score` 与 S/A/B/C/D 评级；
4. 低胜率武将（低于中位数 - `low_win_rate_gap`）降序优先；
5. 写 CSV 快照后调 `mark_recommendation_index_stale(False)`。

`is_recommendation_index_stale()` 自愈校验：状态文件 `stale=true` 但三份榜单均未更新时，自动写回 `false`，避免状态文件被 git 历史或命令行操作意外置 true 后误弹横幅。

### 3.10 JsonRepository 基类

`src/data/json_repository.py` 提供维护仓库公共基建：

- **`atomic_write_json(path, data, indent=2)`** — UTF-8/LF 原子写：`mkstemp` 生成同目录唯一临时文件 → 写入后 `flush + fsync` → `replace`；任一异常清理临时文件并重新抛出（原文件保持不变）。`path.parent.mkdir(parents=True, exist_ok=True)` 保证目标目录存在。
- **`JsonRepository` 基类** — 子类职责：`__init__` 先 `super().__init__(file_path)`；`load()` 用 `_read_root()` 取 `(root, ok)` 后做根结构校验与逐条解析；`save()` 构造 payload 后调 `save_payload(payload)`；CRUD 用 `_snapshot()/_restore()/_save_or_rollback()` 实现"先改内存、写盘失败回滚"。
  - `_read_root()` 加 `RLock`（防止与写盘并发），文件缺失记 warning、解析失败记 error（`DataIssue`）；
  - 写盘失败时 `_save_or_rollback()` 恢复内存快照并重新抛出，避免"看似失败、实际已变"的脏状态。

六个维护仓库已继承本基类（`CardPointsRepository` / `EquipAttrsRepository` / `HeroClassificationRepository` / `SpecialCardRepository`）；`card_catalog.py` 的 `_JsonRepository` 与 `manager.py` 的 `DataManager._save_unlocked` 也改为委托 `atomic_write_json`（全库原子写收敛）。

另有社区侧 combo/guide 语料由 `src/scripts/build_combo_corpus.py`（组合 RAG 语料，437 块，combo 类，不贴单值 hero 但贴 heroes 列表）与 `build_guide_corpus.py`（武将攻略 RAG 语料，357 块，guide 类，贴 hero）从 `data/raw_guides/` 生成，进向量库供 RAG 检索，非 Pydantic 模型不入维护仓库。

---

## 四、关键代码片段

### 4.1 JSON 原子写入

```python
# src/data/json_repository.py（全库统一入口）
def atomic_write_json(path: Path | str, data: Any, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=indent)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except Exception:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise
```

> **设计思路：** 所有写盘统一走此入口：`mkstemp` 保证临时文件唯一、`fsync` 保证断电/崩溃不留下空或半截文件、`replace` 原子替换；任何异常清理临时文件，原文件保持不变。2026-08 起 `DataManager`、`card_catalog` 与四个维护仓库统一委托于此。

### 4.2 增量更新级联删除

```python
# src/data/manager.py
def apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update) -> dict[str, int]:
    # 新增武将
    for hero in update.added_heroes:
        try: hero_mgr.add_hero(hero); stats["added_heroes"] += 1
        except ValueError: logger.warning("武将已存在，跳过: %s", hero.id)

    # 删除武将（同时清理关联的相性和攻略）
    for hid in update.removed_hero_ids:
        hero_mgr.delete_hero(hid)
        synergy_mgr.delete_synergies_for_hero(hid)
        guide_mgr.delete_guide(hid)
        stats["removed_heroes"] += 1
    # 相性/攻略的新增/修改/删除依次类推
```

> **设计思路：** 删除武将时必须同时清理关联的相性和攻略，否则 UI 中会出现"幽灵 ID"导致显示 `#114` 而非武将名。返回的 `stats` dict 供调用方记录变更明细。

### 4.3 写盘失败内存回滚（以 SpecialCardRepository 为例）

```python
# src/data/special_cards_repository.py
class SpecialCardRepository(JsonRepository):
    def _snapshot(self) -> list[SpecialCardItem]:
        return list(self._items)

    def _restore(self, snapshot: list[SpecialCardItem]) -> None:
        self._items = snapshot

    def add_item(self, item: SpecialCardItem) -> None:
        if self.get_item(item.category, item.name) is not None:
            raise ValueError(f"同类别已存在同名条目: ...")
        snapshot = self._snapshot()
        self._items.append(item)
        self._save_or_rollback(snapshot)     # 内部：try save(); except: _restore(snapshot); raise
```

---

## 五、接口说明

### HeroManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `load()` | 无 | `list[DataIssue]` | 从 JSON 加载 |
| `save()` | 无 | `None` | 原子写入 JSON |
| `get_hero(id)` | int | `Hero \| None` | 精确 ID 查找 |
| `get_hero_by_name(name)` | str | `Hero \| None` | 精确名称查找 |
| `search_heroes(keyword)` | str | `list[Hero]` | 模糊搜索 id/name/title/faction |
| `list_heroes()` | — | `list[Hero]` | 全部武将 |
| `list_heroes_by_faction(faction)` | str | `list[Hero]` | 按势力筛选 |
| `list_factions()` | — | `list[str]` | 所有势力（排序去重） |
| `add_hero(hero)` | Hero | `None` | 已存在抛 ValueError |
| `update_hero(hero)` | Hero | `None` | 覆盖式 upsert |
| `delete_hero(id)` | int | `None` | 不存在静默 |

### SynergyManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_synergy(a, b)` | int, int | `SynergyScore \| None` | 双向归一查找 |
| `list_synergies_for_hero(id)` | int | `list[SynergyScore]` | 武将涉及的所有相性 |
| `list_synergies()` | — | `list[SynergyScore]` | 全部相性 |
| `add_synergy(score)` | SynergyScore | `None` | 重复抛 ValueError |
| `update_synergy(score)` | SynergyScore | `None` | 覆盖 |
| `delete_synergy(a, b)` | int, int | `None` | 双向归一删除 |
| `delete_synergies_for_hero(id)` | int | `int` | 删除某武将关联所有相性，返回条数 |
| `replace_loaded_data(synergies, issues)` | list, list | `None` | 主线程原子替换后台校验结果 |

### GuideManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_guide(hero_id)` | int | `HeroGuide \| None` | 按武将 ID 查询 |
| `list_guides()` | — | `list[HeroGuide]` | 全部攻略 |
| `add_guide(guide)` | HeroGuide | `None` | 重复抛 ValueError |
| `update_guide(guide)` | HeroGuide | `None` | 覆盖 |
| `delete_guide(hero_id)` | int | `None` | 不存在静默 |

### ComboManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_combo(a_id, b_id)` | int, int | `Combo \| None` | 双向归一查找 |
| `list_combos_for_hero(hero_id)` | int | `list[Combo]` | 某武将参与的所有配队 |
| `list_combos()` | — | `list[Combo]` | 全部配队 |
| `save_manual_combo(combo, previous=None)` | Combo, Combo\|None | `None` | 新增/编辑手工配队，原子落盘 |
| `delete_combo(combo)` | Combo | `None` | 删除并落盘 |

### AnnouncementManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `list_announcements()` | — | `list[Announcement]` | 按发布时间倒序 |
| `pending_count()` | — | `int` | 待生效公告数 |
| `ready_count()` | — | `int` | 可更新公告数 |
| `merge_new(items, baseline=False)` | list[dict], bool | `list[Announcement]` | 合并并返回新增列表 |
| `mark_ready_if_updated(diff, current_names=None)` | dict | `bool` | pending→ready 状态推进 |
| `mark_applied()` | — | `None` | ready→applied 状态推进 |

### CardRepository / CardFieldSchemaRepository / CardAnnotationRepository

| 仓储 | 方法 | 说明 |
|------|------|------|
| `CardRepository` | `load()` / `list_cards()` / `get_card(id)` | 只读，无保存入口 |
| `CardFieldSchemaRepository` | `load()` / `list_fields()` / `get_field(key)` / `add_field()` / `update_field()` / `save()` | 字段定义维护，key 不可修改 |
| `CardAnnotationRepository` | `load()` / `list_annotations()` / `get_annotation(id)` / `update_annotation()` / `save()` | 追加内容维护 |

### 维护仓库（继承 JsonRepository）

| 仓库 | 数据文件 | 关键 CRUD |
|------|----------|-----------|
| `CardPointsRepository` | `data/card_points.json` | `add_card/update_card/replace_card/delete_card` + `add_rule/update_rule/delete_rule` |
| `EquipAttrsRepository` | `data/equip_attrs.json` | `add_equip/update_equip/delete_equip` |
| `HeroClassificationRepository` | `data/hero_classification.json` | `add_category/update_category/delete_category` + `set_counter_chain` + `set_hero_categories` + `list_unclassified` |
| `SpecialCardRepository` | `data/special_cards.json` | `add_item/update_item/delete_item`（同类别同名不可重复） |

### 巅峰赛 / 2v2 胜率 / 推荐指数（模块级函数）

| 函数 | 说明 |
|------|------|
| `load_peak_win_rates(path)` | 巅峰赛胜率 `{武将名: 百分比}`，默认缓存 |
| `load_peak_pick_ranks(path)` | 巅峰赛出场排行 `{武将名: 排名}`，默认缓存 |
| `clear_peak_win_rate_cache()` | 清空巅峰赛缓存 |
| `load_win_rates(path)` | 2v2 胜率 `{武将名: 百分比}`，默认缓存 |
| `clear_win_rate_cache()` | 清空 2v2 胜率缓存 |
| `refresh_recommendation_indexes(config)` | 基于三份榜单重建推荐指数 CSV |
| `load_recommendation_indexes(path)` | 读取已有推荐指数快照 |
| `is_recommendation_index_stale(path)` | 快照是否过期（带自愈校验） |
| `mark_recommendation_index_stale(stale, path)` | 原子写入 stale 状态 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `pydantic` / Python 标准库 | 模型校验、JSON/CSV/tempfile/csv/math 等 |
| 被调用方 | `src/scraper/` | 爬虫采集写入数据文件后通知 Manager 重新加载 |
| 被调用方 | `src/business/` | 业务服务在子进程结束后调用 `manager.load()` 刷新缓存；索引精化通过 `DataFacade` 读取 heroes/synergies/guides |
| 被调用方 | `src/ui/` | UI 层通过 `DataFacade` / 各 Manager / 各 Repository 读取与写入数据 |
| 被调用方 | `src/scripts/build_*_corpus.py` | RAG 语料构建脚本读取四个维护仓库（card_points/equip_attrs/hero_classification/special_cards）JSON 源生成向量库 |
| 内部依赖 | `src/data/json_repository.atomic_write_json` | `DataManager` / `card_catalog` / 六个维护仓库统一委托此函数原子写盘 |
| 内部依赖 | `src/data/manager.DataIssue` | `json_repository` 的 `_issue()` 统一使用 `DataIssue` 结构收集加载问题 |
| 被调用方 | `src/data/combo_manager` | `src/scripts/import_combos.py` 调用 `save_manual_combo()` 持久化手工配队 |
