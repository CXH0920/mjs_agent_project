# 调用链路：数据模型与数据管理

> 对应源码：`src/data/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 与巅峰赛/实战配队相关的调用链路见 [call_graph_peak_combos.md](./call_graph_peak_combos.md)。

---

## 当前实现基线（2026-08-01）

`DataFacade.load_all()` 现在返回并保存 `LoadReport`，加载阶段不会调用 `save()`，因此源 JSON 不会被自动改写。

```
MainWindow._load_data() -> DataFacade.load_all()
  -> HeroManager.load() / SynergyManager.load() / GuideManager.load()
    -> DataManager._load_unlocked()
      -> json.load(f)                                       [读取 JSON 文件]
      -> self._parse_items(data)                             [子类重写，构建 _items dict]
      -> model_class.model_validate(raw)                     [逐条 Pydantic 校验]
      -> 坏记录或重复键 -> DataIssue -> 跳过该记录
  -> DataFacade._validate_references(report)
    -> 仅追加 missing_reference 问题，不修改内存数据
  -> return LoadReport                                       [只读校验，不写盘]

[若 LoadReport 有问题]
  -> MainWindow 询问用户是否修复并保存
    -> DataMutationService.repair_missing_references()       [创建备份后写入]
```

| 对象 | 职责 |
|------|------|
| `DataIssue` | 记录严重级别、类别、文件、记录下标、实体键、字段和消息 |
| `LoadReport` | 汇总 `issues`，提供 `error_count`、`warning_count` |
| `last_load_report` | 保存最近一次 `load_all()` 的完整报告 |

示例：攻略关联英雄 ID 不存在时，正文、关联列表和原 `guides.json` 都保持不变，并生成 `missing_reference`；只有用户确认修复后才会清理并保存。

---

## 一、数据加载链路

### 1.1 完整数据加载

```
MainWindow._load_data()
  -> self._data.load_all()                                     [DataFacade.load_all]
    -> HeroManager.load()
      -> json.load(f)                                          [读取 heroes.json]
      -> self._parse_items(data)
        -> self._parse_models(data, lambda hero: hero.id)
          -> Hero.model_validate(h)                            [逐项 Pydantic 校验]
          -> self._heroes[hero.id] = hero                      [存入内存字典，以 id 为键]
    -> SynergyManager.load()
      -> json.load(f)                                          [读取 synergies.json]
      -> self._parse_items(data)
        -> self._synergy_key(a, b) = tuple(sorted((a, b)))    [生成双向归一 key]
        -> SynergyScore.model_validate(s)                      [逐项 Pydantic 校验]
        -> self._synergies[key] = score                        [存入内存字典，以双向归一 key 为键]
    -> GuideManager.load()
      -> json.load(f)                                          [读取 guides.json]
      -> self._parse_items(data)
        -> HeroGuide.model_validate(g)                         [逐项 Pydantic 校验]
        -> self._guides[guide.hero_id] = guide                 [存入内存字典，以 hero_id 为键]
```

| 调用方 | 被调用方 | 所在文件 | 说明 |
|--------|----------|----------|------|
| `MainWindow.__init__()` | `DataFacade.load_all()` | `manager.py` | 主窗口构造时加载全部数据 |
| `MainWindow._reload_data()` | `DataFacade.load_all()` | `manager.py` | 菜单"重新加载数据" |
| `DataFacade.load_all()` | `HeroManager.load()` | `hero_manager.py` | 加载武将 JSON→内存（以 id 为键） |
| `DataFacade.load_all()` | `SynergyManager.load()` | `synergy_manager.py` | 加载相性 JSON→内存（以双向归一 key 为键） |
| `DataFacade.load_all()` | `GuideManager.load()` | `guide_manager.py` | 加载攻略 JSON→内存（以 hero_id 为键） |

### 1.2 数据保存链路（通用）

```
[任意 Manager].save()
  -> _save_unlocked()
    -> [v.model_dump(mode="json") for v in self._items.values()]  [Pydantic→dict，dict 迭代顺序]
    -> atomic_write_json(self.file_path, data, indent=2)            [同目录 mkstemp + fsync + replace]
```

| 调用方 | 被调用方 | 说明 |
|--------|----------|------|
| `MainWindow._reload_data()` | `DataFacade.load_all()` | 重新读取三个文件并执行跨实体校验，不会先保存 |
| `HeroDetailPanel._on_info_edit()` | `DataMutationService.update_hero()` | 修改武将信息后备份并保存 |
| `HeroDetailPanel._on_info_delete()` | `DataMutationService.delete_hero_with_relations()` | 删除武将及关联数据，失败时从备份恢复 |
| `HeroDetailPanel._on_guide_edit()` | `DataMutationService.update_guide()` | 修改攻略后备份并保存 |
| `HeroDetailPanel._on_guide_delete()` | `DataMutationService.delete_guide()` | 删除攻略后备份并保存 |
| `HeroDetailPanel._on_synergy_edit()` / `_on_synergy_delete()` | `DataMutationService.update_synergy()` / `delete_synergy()` | 修改或删除相性后备份并保存 |
| `AiGenerationWorkflow._on_guide_completed()` | `GuideManager.load()` (仅重新加载) | 攻略生成成功后重载内存缓存 |
| `AiGenerationWorkflow._on_synergy_completed()` | `SynergyManager.load()` (仅重新加载) | 相性生成成功后重载内存缓存 |

### 1.3 实战配队保存（ComboManager 专用排序）

`ComboManager` 重写了 `_save_unlocked()`：落盘前按 `(-rating, hero1_id, hero2_id)` 稳定排序，物理行序与武将名解绑。

```
ComboManager.save()
  -> _save_unlocked()
    -> sorted(self._items.values(),
              key=lambda c: (-c.rating, c.hero1_id, c.hero2_id))    [评分降序、ID 升序]
    -> [v.model_dump(mode="json") for v in ordered]
    -> atomic_write_json(self.file_path, data, indent=2)
```

> **设计说明：** 新增武将（id 较大）自然落到各 rating 段末尾，避免按名排序时新名字插入中段、其后条目整体平移造成的 diff 噪音。

`ComboManager` 的 CRUD 方法：

| 方法 | 说明 |
|------|------|
| `get_combo(a_id, b_id)` | 以双向归一 key 查询一对武将的配队 |
| `list_combos_for_hero(hero_id)` | 列出某武将参与的全部配队 |
| `save_manual_combo(combo, previous)` | 新增/编辑一条手工配队；previous 变化时迁移存储 key；固定 `manual=True` |
| `delete_combo(combo)` | 删除一条配队并原子落盘 |

---

## 二、胜率仓库调用链

### 2.1 2v2 胜率（win_rate_repository.py）

2v2 胜率 CSV 不属于三个 JSON Manager，由独立仓库按名称读取并缓存：

```
RecommendationPanel._load_win_rate_by_name() / MatchGuidePanel._load_default_heroes()
  -> load_win_rates()
     -> [默认路径] data/2v2胜率排行.csv
     -> csv.DictReader()
     -> row["武将"] + float(row["胜率"].replace("%", ""))
     -> {武将名: 百分比}
     -> 首次访问默认 CSV 时填充 _win_rate_cache（模块级全局缓存）
```

`load_win_rates()` 传入自定义 `Path` 时不污染默认缓存，便于测试和离线数据校验。文件缺失、I/O 错误或单行百分比格式非法只记录 warning/跳过该行，不阻断页面加载。官方数据导入成功覆盖 2v2 CSV 后调用 `clear_win_rate_cache()`，使后续页面查询读取新数据。

```
OfficialDataImportService.import_file("2v2", image_path)
  -> _resolve_batch_names() / _validate_output_names()
  -> [名称校验失败] 只写对应 *_待复核.csv，保留正式 CSV
  -> [名称校验通过]
  -> _write_csv(data/2v2胜率排行.csv, ["排名", "武将", "胜率"], rows)
  -> _write_csv(data/2v2出场排行.csv, ["排名", "武将"], rows)
  -> _write_csv(对应 *_待复核.csv, review_rows)
  -> mark_recommendation_index_stale(True)
  -> clear_win_rate_cache()
  -> RecommendationPanel / MatchGuidePanel 下次 load_win_rates() 读取新胜率

OfficialDataImportService.import_file("exile", image_path)
  -> 合并左右表视觉行序
  -> 榜单内部唯一性补全 / 未知名与重复名校验
  -> _write_csv(data/武将放逐.csv, ["排名", "武将"], rows)
  -> _write_csv(data/武将放逐_待复核.csv, review_rows)
  -> mark_recommendation_index_stale(True)
```

`_write_csv()` 先在目标目录创建 UTF-8、LF 换行的临时文件，再以 `Path.replace()` 原子替换正式文件。名称完整性校验在任何正式文件写入前完成；失败时仅更新待复核 CSV 和截图，不标记推荐指数过期。待复核 CSV 不参与 `win_rate_repository` 缓存。

### 2.2 巅峰赛胜率（peak_win_rate_repository.py）

巅峰赛榜单与 2v2 榜单独立缓存，数据源落地前优雅空态（文件缺失返回空 dict）。

```
[巅峰赛相关面板]
  -> load_peak_win_rates()
     -> [默认路径] data/巅峰赛胜率排行.csv
     -> csv.DictReader()
     -> row["武将"] + float(row["胜率"].replace("%", ""))
     -> {武将名: 百分比}
     -> 填充 _peak_win_rate_cache（模块级全局缓存）

  -> load_peak_pick_ranks()
     -> [默认路径] data/巅峰赛出场排行.csv
     -> csv.DictReader()
     -> row["武将"] + int(row["排名"])
     -> {武将名: 出场排名}
     -> 填充 _peak_pick_rank_cache（模块级全局缓存）
```

`clear_peak_win_rate_cache()` 同时清除胜率与出场排行两个缓存。

### 2.3 2v2 出场排行（recommendation_index_repository.py 内）

2v2 出场排行由 `recommendation_index_repository._read_ranks()` 读取，不走 `win_rate_repository`：

```
refresh_recommendation_indexes()
  -> _read_ranks(PICK_RANK_CSV, "出场")
     -> PICK_RANK_CSV = data/2v2出场排行.csv
     -> csv.DictReader()
     -> row["武将"] -> row["排名"]
     -> {武将名: int}
```

---

## 三、武将查询链路

### 3.1 按名称查询（全模块最频繁的查询）

```
RecommendationPanel.update_recommendations()
  -> self._hero_mgr.get_hero_by_name(name)                    [O(N) 线性扫描]
    -> for hero in self._items.values():
    -> if hero.name == name: return hero                       [精确匹配]
    -> return None                                              [未找到]
```

| 调用方 | 调用位置 | 说明 |
|--------|----------|------|
| `RecommendationPanel.update_recommendations()` | `recommendation_panel.py` | OCR 结果导入、默认加载 |
| `RecommendationPanel._load_real_synergies()` | `recommendation_panel.py` | 相性伙伴名称→ID 解析 |
| `GuideEditDialog._open_relation_selector()` | `guide_edit_dialog.py` | 打开攻略关系武将选择器并回填 ID 列表 |
| `HeroRelationSelectDialog._accept_selection()` | `hero_relation_select_dialog.py` | 按英雄 ID 的稳定顺序提交已选择关系 |
| `RecommendationPanel._load_default_heroes()` | `recommendation_panel.py` | 启动时默认武将加载 |

> **性能标注：** `get_hero_by_name()` 内部是 O(N) 线性遍历（N=165）。在 OCR 矫正流程中，每帧可由 `CharacterSimilarityService.correct_hero_name()` 触发 8 次编辑距离遍历。如果修改为 `name -> id` 的 dict 索引可消除 O(N) 查找，但当前 165 规模下线性扫描的延迟可以忽略（< 0.01ms）。

### 3.2 按 ID 查询（O(1) 字典访问）

```
[任意] controller
  -> self._hero_mgr.get_hero(hero_id)                         [dict.get, O(1)]
  -> return self._items.get(hero_id)                           [Optional[Hero]]
```

| 调用方 | 说明 |
|--------|------|
| `RecommendationPanel._load_real_synergies()` | 相性伙伴 ID→Hero 对象 |
| `RecommendationPanel._show_guide_popup()` | 弹出攻略时获取 Hero |
| `HeroDetailPanel.show_hero()` | 点击列表项展示详情 |
| `HeroGuideSummaryView.show_guide()` | 渲染攻略中的搭配武将名 |
| `GuideDetailDialog.__init__()` | 弹出攻略详情时获取伙伴名 |

### 3.3 模糊搜索

```
HeroBrowser._setup_ui()                                       [搜索框 textChanged 信号]
  -> HeroListPanel._apply_filters()
    -> self._hero_mgr.search_heroes(keyword)                  [关键词匹配 id/name/title/faction]
  -> _refresh_list()                                           [刷新 QListWidget]
```

| 调用方 | 说明 |
|--------|------|
| `HeroListPanel._load_heroes()` | 初始化时加载全部 |
| `HeroListPanel.relad()` | 数据重载 |
| 搜索框 `textChanged` 信号 | 实时过滤列表 |

---

## 四、相性查询链路

### 4.1 武将相性列表查询（全表扫描）

```
RecommendationPanel._load_real_synergies(card_idx, hero_id)
  -> self._synergy_mgr.list_synergies_for_hero(hero_id)       [O(N) 全表扫描]
    -> for (a_id, b_id), score in self._items.items():
    -> if hero_id in (a_id, b_id): results.append(score)
    -> return results
  -> sorted(results, key=lambda s: s.score, reverse=True)      [按评分降序]
  -> [取 top 4]
    -> self._hero_mgr.get_hero(partner_id)                     [ID→名称]
  -> card.set_synergies(pairs)                                 [展示到卡片]
```

| 调用方 | 说明 |
|--------|------|
| `RecommendationPanel._load_default_heroes()` | 启动时加载前 8 武将相性 |
| `RecommendationPanel.update_recommendations()` | OCR 导入后刷新相性 |

> **性能标注：** `list_synergies_for_hero()` 全表扫描当前相性数据量。如果相性条目很多（C(165,2)=13,530 条满数据），8 张卡片就是 8 次全表扫描。当前实际数据量较小，不是性能瓶颈。

### 4.2 双向归一查询

```
[任意] controller
  -> self._synergy_mgr.get_synergy(hero_a_id, hero_b_id)
    -> self._synergy_key(a_id, b_id) = tuple(sorted((a_id, b_id)))
    -> return self._items.get(key)                             [O(1) dict lookup]
```

| 调用方 | 说明 |
|--------|------|
| 外部查询 | 查询特定两个武将的相性评分 |

---

## 五、实战配队查询链路

### 5.1 按配对查询

```
[任意] controller
  -> self._combo_mgr.get_combo(hero_a_id, hero_b_id)
    -> self._combo_key(a_id, b_id) = tuple(sorted((a_id, b_id)))   [双向归一 key]
    -> return self._items.get(key)                            [Optional[Combo]]
```

### 5.2 某武将的全部配队

```
HeroDetailView / RecommendationPanel
  -> self._combo_mgr.list_combos_for_hero(hero_id)
    -> for combo in self._items.values():
    -> if hero_id in (combo.hero1_id, combo.hero2_id): results.append(combo)
```

### 5.3 座次解析（combo_seats.py）

```
Combo.note 自由文本解析
  -> parse_seats(note, hero1, hero2)
    -> [优先级1] "武将名+数字" 或 "数字+武将名" 前置写法
       -> re.search(pattern, note) -> _seats_of(digits)
       -> ALIAS: {"吕布":["牢布"], "甄宓":["甄姬"], "夏侯惇":["夏侯停"]}  [手录别名映射]
    -> [优先级2] 剥离武将名后取开头的纯数字 token
       -> "0" -> 无座次要求（返回空列表）
       -> 两个 token -> 分别对应 hero1_seats / hero2_seats
    -> 返回 (status, hero1_seats, hero2_seats)
       status ∈ {parsed, partial, none, unparsed}
```

---

## 六、增量更新链路

### 6.1 增量更新处理

```
[外部] apply_incremental_update(hero_mgr, synergy_mgr, guide_mgr, update)
  -> [added_heroes] hero_mgr.add_hero(hero)
  -> [modified_heroes] hero_mgr.update_hero(hero)
  -> [removed_hero_ids]
    -> hero_mgr.delete_hero(hid)
    -> synergy_mgr.delete_synergies_for_hero(hid)              [级联删除相性]
    -> guide_mgr.delete_guide(hid)                             [级联删除攻略]
  -> [added_synergies] synergy_mgr.add_synergy(score)
  -> [modified_synergies] synergy_mgr.update_synergy(score)
  -> [removed_synergy_ids] synergy_mgr.delete_synergy(a, b)
  -> [added_guides] guide_mgr.add_guide(guide)
  -> [modified_guides] guide_mgr.update_guide(guide)
  -> [removed_guide_ids] guide_mgr.delete_guide(hero_id)
  -> return stats                                              [操作用统计]
```

| 调用方 | 说明 |
|--------|------|
| `src.scraper.incremental` | 增量采集 CLI 完成后调用 |
| 外部导入工具 | 批量更新数据时使用 |

---

## 七、外部调用关系总览

### 7.1 本模块被外部调用

```
┌─────────────────────────────────────────────────────────────────┐
│                         UI 层                                    │
│  MainWindow._load_data()       → DataFacade.load_all()          │
│  MainWindow._reload_data()    → DataFacade.load_all()           │
│  MainWindow._update_status()  → DataFacade.get_stats()          │
│  RecommendationPanel          → HeroManager.get_hero_by_name()  │
│  RecommendationPanel          → HeroManager.get_hero()          │
│  RecommendationPanel          → SynergyManager.list_synergies() │
│  HeroDetailPanel              → HeroManager/GuideManager CRUD   │
│  GuideDetailDialog            → HeroManager/GuideManager 查询   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     业务服务层                                   │
│  MainWindow._on_synergy_*     → SynergyManager.load()           │
│  MainWindow._on_guide_*       → GuideManager.load()             │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                     爬虫 / AI 层                                 │
│  official.py / incremental.py → models.py (Pydantic 校验)       │
│  api_generator.py              → HeroGuide / SynergyScore 校验    │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `pydantic.BaseModel` | Hero、SynergyScore、HeroGuide 等模型基类 |
| Python 标准库 `json` | JSON 序列化/反序列化 |
| Python 标准库 `pathlib` | 文件路径操作 |
| Python 标准库 `csv` | CSV 胜率/出场/推荐指数读取 |
| Python 标准库 `tempfile` / `os` | 原子写临时文件（mkstemp + fsync） |

### 7.3 调用频率最高的关键路径

```
RecommendationPanel.update_recommendations()    [OCR 每帧触发]
  -> HeroManager.get_hero_by_name() ×8           [O(N) × 8]
  -> SynergyManager.list_synergies_for_hero() ×8 [全表扫描 × 8]
  -> HeroManager.get_hero() ×32                  [top4 × 8 卡片]
```

---

## 八、函数清单总表

| 函数 | 文件 | 调用方（主要） | 被调用方（主要） |
|------|------|----------------|------------------|
| `DataFacade.load_all()` | `manager.py` | `MainWindow._load_data()` | 三个 Manager.load() + `_validate_references()` |
| `DataFacade.save_all()` | `manager.py` | 外部批量保存 | 三个 Manager.save() |
| `DataFacade.get_stats()` | `manager.py` | `MainWindow._update_status()` | 三个 Manager 的计数接口 |
| `HeroManager.load()` | `hero_manager.py` | `DataFacade.load_all()` | `json.load()`, `Hero.model_validate()` |
| `HeroManager.save()` | `hero_manager.py` | `DataMutationService.update_hero()` | `json.dump()`, 原子替换 |
| `HeroManager.get_hero()` | `hero_manager.py` | `RecommendationPanel` 等 | dict get O(1) |
| `HeroManager.get_hero_by_name()` | `hero_manager.py` | `RecommendationPanel` | 线性遍历 O(N) |
| `HeroManager.search_heroes()` | `hero_manager.py` | `HeroListPanel` | 模糊匹配 4 字段 |
| `SynergyManager.load()` | `synergy_manager.py` | `DataFacade.load_all()` | `json.load()`, `SynergyScore.model_validate()` |
| `SynergyManager.list_synergies_for_hero()` | `synergy_manager.py` | `RecommendationPanel` | 全表扫描 O(N) |
| `SynergyManager.get_synergy()` | `synergy_manager.py` | 外部查询 | `_synergy_key()` + dict get |
| `SynergyManager._synergy_key()` | `synergy_manager.py` | `SynergyManager` 内部 | `tuple(sorted())` |
| `GuideManager.load()` | `guide_manager.py` | `DataFacade.load_all()` | `json.load()`, `HeroGuide.validate()` |
| `GuideManager.get_guide()` | `guide_manager.py` | `HeroDetailPanel`, `GuideDetailDialog` | dict get |
| `ComboManager.save()` | `combo_manager.py` | 导入服务、手工维护 | 按 `(-rating, hero1_id, hero2_id)` 排序后原子写 |
| `ComboManager.get_combo()` | `combo_manager.py` | 面板查询 | `_combo_key()` + dict get |
| `ComboManager.list_combos_for_hero()` | `combo_manager.py` | `HeroDetailView`, `RecommendationPanel` | 线性遍历 O(N) |
| `ComboManager.save_manual_combo()` | `combo_manager.py` | 手工配队编辑 | 内存写入 + `_save_unlocked()` |
| `parse_seats()` | `combo_seats.py` | 配队导入/显示 | 正则匹配 + ALIAS 别名映射 |
| `apply_incremental_update()` | `manager.py` | 测试和外部导入工具 | 按 added/modified/removed 更新三个 Manager，并执行武将删除级联 |
| `load_win_rates()` | `win_rate_repository.py` | `RecommendationPanel`, `MatchGuidePanel` | CSV 解析、百分比转浮点、默认路径缓存 |
| `load_peak_win_rates()` | `peak_win_rate_repository.py` | 巅峰赛面板 | CSV 解析、巅峰赛专属缓存 |
| `load_peak_pick_ranks()` | `peak_win_rate_repository.py` | 巅峰赛面板 | CSV 解析、出场排名缓存 |
| `CardCatalogService.save_annotation_fields()` | `card_catalog.py` | `CardAnnotationEditDialog` | 校验字段值并将旧效果记录迁移为内部时间字段 |

---

## 九、公告记录与百科快照链路

```
AnnouncementService._do_check()
  -> AnnouncementManager.merge_new(items, baseline)
    -> Announcement.model_validate(raw)              [字段含 hero_related/matched_heroes/content_missing/status]
    -> Announcement.stable_key(url) 判重             [API 与回退模式统一按 url]
    -> status: baseline/非武将相关 -> applied；武将相关新公告 -> pending
    -> save()（原子写入 announcements.json）
  -> AnnouncementManager.mark_ready_if_updated(diff) [匹配名字 -> pending -> ready]
  -> AnnouncementManager.mark_applied()              [pending/ready -> applied]
  -> load_baike_snapshot() / save_baike_snapshot()   [覆盖式 baike_snapshot.json]

被外部调用：MainWindow（菜单/横幅/对话框刷新）、AnnouncementService（检查与采集完成联动）。
```

---

## 十、RAG 源数据维护仓储链路

### 10.1 加载与校验

```
CardPointsRepository.load()
  -> JsonRepository._read_root()   [RLock 加锁 + 文件缺失 warning / 解析失败 error（DataIssue）]
  -> 读取 data/card_points.json（{cards, judge_rules}）
  -> CardPointItem / JudgeRuleItem Pydantic 校验
     -> 花色/点数/数量合法性（name strip、花色点数非空 model_validator）、judge_rules 重复名 -> DataIssue
  -> list_cards() / list_rules() 供 UI 展示
EquipAttrsRepository.load()
  -> _read_root() -> 读取 data/equip_attrs.json（26 件）
  -> EquipAttrItem 校验（subtype ∈ 武器/防具/坐骑、攻击范围/距离修正数值）
SpecialCardRepository.load()
  -> _read_root() -> 读取 data/special_cards.json
  -> SpecialCardItem 校验（category ∈ 5 类、name 非空、stackable ∈ 是/否/—、同类别重名 -> DataIssue）
HeroClassificationRepository.load()
  -> _read_root() -> 读取 data/hero_classification.json（分类/克制链/hero_categories）
  -> ClassificationCategory 校验；unknown_category_ref / chain_list_legacy 兼容处理
```

### 10.2 保存与联动

```
CardPointsPanel / EquipAttrsPanel / SpecialCardsPanel / HeroClassificationPanel 编辑
  -> Repository.add_item/update_item/delete_item / save()
     -> _save_or_rollback()       [写前 _snapshot()，写盘失败 _restore() 回滚内存并重新抛出]
        -> save_payload() -> atomic_write_json()   [mkstemp + fsync + replace，UTF-8/LF]
  -> data_changed -> RagMaintenancePanel.refresh()
     -> 任务表标记待重建 -> 用户一键 maintain_rag.py
        -> build_cardpts.py     读 card_points.json -> 卡牌点数花色语料
        -> build_equip_attr.py  读 equip_attrs.json + cards.json -> 装备属性语料（并注入卡牌语料）
        -> build_special_corpus.py 读 special_cards.json -> 特殊机制语料
        -> build_combo_corpus.py 读 raw_guides/combos (csv+4md) -> 组合RAG语料（combo类，437块，不贴单值hero但贴heroes列表）
        -> build_guide_corpus.py 读 raw_guides/guides (45md) -> 武将攻略RAG语料（guide类，357块，贴hero）
```

### 10.3 从 xlsx 应急重导入

```
scripts/migrate_excel_to_json.py [--only points|equips|special]
  -> 读取 data/archive/mjs卡牌点数.xlsx（3 个 sheet）
  -> points: 162 张花色点数聚合（72 组合 × count）+ 12 条判定规则 -> card_points.json
  -> equips: 26 件装备属性 -> equip_attrs.json
  -> special: 比对【专属牌】sheet 与 special_cards.json，缺失项自动补入、已有条目不覆盖
```

### 10.4 函数清单（RAG 源数据仓储）

| 函数 | 文件 | 说明 |
|------|------|------|
| `atomic_write_json(path, data, indent)` | `json_repository.py` | 全库统一原子写（mkstemp + fsync + replace） |
| `JsonRepository._read_root()` / `save_payload()` | `json_repository.py` | 加锁读盘骨架 / 加锁原子写盘 |
| `JsonRepository._save_or_rollback()` | `json_repository.py` | 写盘失败恢复内存快照并重新抛出 |
| `CardPointsRepository.save()` | `card_points_repository.py` | 原子写 card_points.json（继承 JsonRepository） |
| `EquipAttrsRepository.save()` | `equip_attrs_repository.py` | 原子写 equip_attrs.json（继承 JsonRepository） |
| `SpecialCardRepository.save()` | `special_cards_repository.py` | 原子写 special_cards.json（继承 JsonRepository） |
| `HeroClassificationRepository.save()` | `hero_classification_repository.py` | 原子写 hero_classification.json（继承 JsonRepository） |
| `migrate_excel_to_json.main()` | `scripts/migrate_excel_to_json.py` | xlsx 应急重导入 |

---

## 十一、推荐指数状态自愈链路（2026-08 新增）

```
选将推荐页 / 启动刷新：is_recommendation_index_stale(index_path=武将推荐指数.csv)
  -> 状态文件 .recommendation_index_state.json 读取 stale 标记
     -> stale=false 或文件缺失       -> 返回 False（不弹「推荐指数待重建」）
     -> stale=true -> _has_newer_source_file(三份榜单 CSV, 推荐指数快照)
        -> 任一榜单 mtime > 快照 mtime -> True   [存在未反映的新榜单数据]
        -> 快照缺失                  -> True   [需要重建]
        -> 三份榜单均不新于快照       -> 误标记：日志告警并 mark_recommendation_index_stale(False)
                                       自愈写回 false -> 返回 False（不再误弹横幅）
```

- 目的：状态文件曾被 git 历史/命令行操作意外置为 `true` 的兜底；官方榜单未更新时不再反复提示"推荐指数待重建"。
- 测试：`tests/test_recommendation_index_repository.py` 覆盖自愈判定与 mtime 比较分支。

### 11.1 推荐指数重建流程

```
refresh_recommendation_indexes()
  -> _load_hero_ids(HEROES_JSON)                       [name -> hero_id 映射]
  -> _read_win_rates(WIN_RATE_CSV)                     [胜率 -> {name: float}, 含格式/范围校验]
  -> _read_ranks(PICK_RANK_CSV, "出场")                [出场排名 -> {name: int}]
  -> _read_ranks(BAN_RANK_CSV, "禁用")                 [禁用排名 -> {name: int}]
  -> _validate_rank_ranges()                           [排名越界/重复 -> DataIssue]
  -> [有效数据] _score_valid_results(valid, config)
     -> pick_score = _rank_to_score(rank, n)
     -> ban_score = _rank_to_score(rank, n)
     -> preference = (p_floor + (1-p_floor)*pick_score) * (1 + ban_weight*ban_score)
     -> sigmoid = 1/(1 + exp(-sigmoid_k * (win_rate - offset)))
     -> raw_index = win_rate * preference * sigmoid
     -> 百分位归一化（p5/p95）-> score -> rating（S/A/B/C/D）
     -> 排序：低胜率降级优先，再按 raw_index 降序，再按 hero_id 升序
  -> _write_snapshot()                                 [原子 CSV 写入，按 hero_id 升序]
  -> mark_recommendation_index_stale(False)            [快照生成后清除 stale 标记]
```
