# 模块：数据模型与数据管理

> 对应目录：`src/data/`
> 职责：定义项目核心数据模型，提供对 JSON 数据文件的增删改查和持久化操作

---

## 一、模块职责

本模块是项目的**数据基础层**，承担两个核心角色：

1. **模型定义**（`models.py`）— 通过 Pydantic v2 定义 Hero、SynergyScore、HeroGuide 等核心数据模型，作为项目唯一的 JSON 格式契约，确保所有数据来源（官网爬虫、AI 生成）的输出格式一致
2. **数据管理**（`*_manager.py`）— 三个 Manager 类分别管理武将、相性、攻略的 CRUD 和 JSON 文件持久化，`DataFacade` 门面统一访问入口

---

## 二、文件结构

```
src/data/
├── __init__.py
├── models.py              # Pydantic 数据模型（Hero / Skill / SynergyScore / HeroGuide / Card / IncrementalUpdate）
├── manager.py             # DataFacade 门面 + 增量更新函数 + 重新导出三个 Manager
├── hero_manager.py        # 武将 CRUD + JSON 持久化
├── synergy_manager.py     # 相性评分 CRUD + JSON 持久化
└── guide_manager.py       # 攻略 CRUD + JSON 持久化
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

### 3.2 Manager CRUD 模式

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

### 3.3 相性双向归一

相性是双向无向关系 — `(A=114, B=115)` 和 `(B=115, A=114)` 应被视为同一条数据：

```python
class SynergyManager:
    def _make_key(self, a_id, b_id) -> tuple[int, int]:
        return tuple(sorted([a_id, b_id]))
    # 存储: self._synergies[key] = SynergyScore
```

排序保证了无论调用方以什么顺序传入，都映射到同一个存储 key。

### 3.4 DataFacade 门面

`DataFacade` 聚合三个 Manager，提供统一的数据访问入口：

```python
facade = DataFacade()
facade.load_all()           # 三个 load() 依次调用
stats = facade.get_stats()  # {heroes: N, synergies: N, guides: N}
facade.heroes.get_hero(114) # 直接访问各 Manager
```

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
