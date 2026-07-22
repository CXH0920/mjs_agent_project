# 模块：数据模型与数据管理

> 对应目录：`src/data/`
> 职责：定义项目核心数据模型，提供对 JSON 数据文件的增删改查和持久化操作

---

## 一、模块职责

本模块是项目的**数据基础层**，承担两个核心角色：

1. **模型定义**（`models.py`）— 通过 Pydantic v2 定义 Hero、SynergyScore、HeroGuide 等核心数据模型，作为项目唯一的 JSON 格式契约，确保所有数据来源（官网爬虫、AI 生成）的输出格式一致
2. **数据管理**（`manager.py` + `*_manager.py`）— `DataManager[V_co]` 泛型基类提供通用的 CRUD、加载、保存方法，三个子类 Manager 继承基类并添加各自的查询方法；`DataFacade` 门面统一访问入口

---

## 二、文件结构

```
src/data/
├── __init__.py
├── models.py              # Pydantic 数据模型（Hero / Skill / SynergyScore / HeroGuide / Card / IncrementalUpdate）
├── manager.py             # DataManager[V_co] 泛型基类 + DataFacade 门面 + 增量更新函数
├── hero_manager.py        # 武将 CRUD + JSON 持久化（继承 DataManager[Hero]）
├── synergy_manager.py     # 相性评分 CRUD + JSON 持久化（继承 DataManager[SynergyScore]）
└── guide_manager.py       # 攻略 CRUD + JSON 持久化（继承 DataManager[HeroGuide]）
```

---

## 三、核心逻辑

### 3.1 数据模型体系

所有核心模型定义在 `models.py` 中，继承 `pydantic.BaseModel`：

```python
class Hero(BaseModel):
    id: int              # 武将 ID（游戏内编号）
    name: str            # 武将名
    title: str           # 称号
    faction: str         # 势力
    position: str        # 定位（输出/辅助/控制/防御）
    max_hp: int          # 体力上限
    max_hand: int        # 手牌上限
    gender: Gender       # 性别枚举
    skills: list[Skill]  # 技能列表
    difficulty: Difficulty # 难度评级 1-5
    mode_viability: dict # 各模式强度梯队
    icon_url: str        # 头像 URL

class SynergyScore(BaseModel):
    hero_a_id: int       # 武将 A ID
    hero_b_id: int       # 武将 B ID
    score: int           # 综合相性评分 (-10 ~ 10)
    synergy_rating: str  # S/A/B/C/D 总评
    combo_ceiling: int   # 配合上限 1-10
    combo_stability: int # 配合稳定性 1-10
    adaptability: int    # 环境适应力 1-10
    description: str     # 相性定性描述

class HeroGuide(BaseModel):
    hero_id: int                 # 武将 ID
    key_points: list[str]        # 操作要点
    counters: list[int]          # 被谁克制（武将 ID 列表）
    synergizes_with: list[int]   # 与谁搭配好（武将 ID 列表）
    description: str             # 攻略正文（Markdown）
    tips_for_beginners: str      # 新手提示
```

**特色：** Hero 模型使用 `validation_alias` 映射官网中文字段名：

```python
id: int = Field(..., validation_alias="角色ID")
name: str = Field(..., validation_alias="名称")
faction: str = Field(..., validation_alias="势力")
```

AI 生成的结果使用英文字段名（`hero_id` / `name` / `faction`），同一模型兼容两种数据源。

### 3.2 DataManager 泛型基类

`DataManager[V_co]` 定义了所有 Manager 共用的 CRUD 和能力接口：

```python
class DataManager(Generic[V_co], ABC):
    def __init__(self, file_path: Path)      # 绑定 JSON 文件路径
    def load(self) -> list[DataIssue]          # 逐条读入内存，返回加载问题
    def save(self) -> None                    # 原子写入 JSON（tmp → rename）
    def get(self, key) -> V_co | None         # 抽象：键查询
    def list_all(self) -> list[V_co]          # 全部数据
    def add(self, item: V_co) -> None         # 新增（重复抛 ValueError）
    def update(self, item: V_co) -> None      # 覆盖式 upsert
    def delete(self, key) -> None             # 删除（不存在静默）
```

三个子类继承 `DataManager` 后仅需实现 `get()` 抽象方法，以及各自的领域查询方法（如 `get_hero_by_name`、`search_heroes` 等）。

> **设计思路：** 将三个 Manager 中完全重复的 CRUD 骨架抽取为泛型基类，子类只保留与数据类型相关的特有查询。`Generic[V_co]` 的协变设计使得 `DataManager[Hero]` 可以安全地赋值给 `DataManager[BaseModel]` 类型的变量。

### 3.3 Manager CRUD 模式

三个 Manager 遵循相同设计模式：

| 操作 | 方法 | 异常 |
|------|------|------|
| 查询 | `get_*(key)` | 返回 `Optional`，不抛异常 |
| 列表 | `list_*()` | 返回全部（已排序） |
| 新增 | `add_*(obj)` | 重复时抛 `ValueError` |
| 更新 | `update_*(obj)` | 覆盖式 upsert，不抛异常 |
| 删除 | `delete_*(key)` | 不存在静默退出 |
| 保存 | `save()` | 原子写入 JSON |
| 加载 | `load()` | 全量读入内存 |

> **设计思路：** `add_*` 抛异常而 `update_*` 不抛，让调用方能区分「第一次写入」和「覆盖」。`delete_*` 静默处理不存在的 key，因为删除不存在的对象不是错误。

### 3.4 相性双向归一

相性是双向无向关系 — `(A=114, B=115)` 和 `(B=115, A=114)` 应被视为同一条数据：

```python
class SynergyManager:
    def _make_key(self, a_id, b_id) -> tuple[int, int]:
        return tuple(sorted([a_id, b_id]))
    # 存储: self._synergies[key] = SynergyScore
```

排序保证了无论调用方以什么顺序传入，都映射到同一个存储 key。

### 3.5 容错加载与数据报告

`DataManager.load()` 会逐条执行 Pydantic 校验。格式错误的记录和重复键会被跳过，其他合法记录继续加载；源 JSON 不会在加载过程中被改写。每项问题以 `DataIssue` 返回，包含文件、记录下标、实体键和字段信息。

`LoadReport` 汇总一次完整加载的问题，并提供 `error_count` 与 `warning_count`。这使 UI 可以提示“部分数据已恢复”，同时保留日志和后续修复入口所需的精确上下文。

### 3.6 DataFacade 门面

`DataFacade` 聚合三个 Manager，提供统一的数据访问入口：

```python
facade = DataFacade()
report = facade.load_all()  # 三个 load() 后校验跨实体引用
stats = facade.get_stats()  # {heroes: N, synergies: N, guides: N}
facade.heroes.get_hero(114) # 直接访问各 Manager
```

相性双方、攻略归属和攻略中的克制/搭配 ID 都必须存在于英雄库。失效的相性或攻略归属仅从内存结果移除；攻略正文仍保留，但其中失效的关联 ID 会被剔除。该只读恢复过程会记录到 `facade.last_load_report`，不会自动覆写源文件。

---

## 四、关键代码片段

### 4.1 JSON 原子写入

```python
def save(self) -> None:
    data = [g.model_dump(mode="json") for g in self._guides.values()]
    tmp_path = self.guides_file.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(self.guides_file)  # 原子替换
```

> **设计思路：** 模型输出永远走 `model_dump(mode="json")` 确保格式受 Pydantic 约束。临时文件再 rename 避免写入中途崩溃破坏数据文件。

### 4.2 增量更新级联删除

```python
def apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update):
    for hid in update.removed_hero_ids:
        hero_mgr.delete_hero(hid)
        synergy_mgr.delete_synergies_for_hero(hid)
        guide_mgr.delete_guide(hid)
```

> **设计思路：** 删除武将时必须同时清理关联的相性和攻略，否则 UI 中会出现"幽灵 ID"导致显示 `#114` 而非武将名。

---

## 五、接口说明

### HeroManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `load()` | 无 | `None` | 从 JSON 加载 |
| `save()` | 无 | `None` | 原子写入 JSON |
| `get_hero(id)` | int | `Hero \| None` | 精确 ID 查找 |
| `get_hero_by_name(name)` | str | `Hero \| None` | 精确名称查找 |
| `search_heroes(keyword)` | str | `list[Hero]` | 模糊搜索 id/name/title/faction |
| `add_hero(hero)` | Hero | `None` | 已存在抛 ValueError |
| `update_hero(hero)` | Hero | `None` | 覆盖式 upsert |
| `delete_hero(id)` | int | `None` | 不存在静默 |
| `list_heroes()` | — | `list[Hero]` | 全部武将 |
| `list_factions()` | — | `list[str]` | 所有势力 |

### SynergyManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_synergy(a, b)` | int, int | `SynergyScore \| None` | 双向归一查找 |
| `list_synergies_for_hero(id)` | int | `list[SynergyScore]` | 武将涉及的所有相性 |
| `add_synergy(score)` | SynergyScore | `None` | 重复抛 ValueError |
| `delete_synergy(a, b)` | int, int | `None` | 双向归一删除 |

### GuideManager

| 方法 | 参数 | 返回 | 说明 |
|------|------|------|------|
| `get_guide(hero_id)` | int | `HeroGuide \| None` | 按武将 ID 查询 |
| `add_guide(guide)` | HeroGuide | `None` | 重复抛 ValueError |
| `update_guide(guide)` | HeroGuide | `None` | 覆盖 |
| `delete_guide(hero_id)` | int | `None` | 不存在静默 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | 无外部依赖 | 仅依赖 `pydantic` 和 Python 标准库 |
| 被调用方 | `src/scraper/` | 爬虫和 AI 生成写入数据文件后通知 Manager 重新加载 |
| 被调用方 | `src/business/` | 业务服务在子进程结束后调用 `manager.load()` 刷新缓存 |
| 被调用方 | `src/ui/` | UI 层通过 DataFacade 读取/写入数据 |
