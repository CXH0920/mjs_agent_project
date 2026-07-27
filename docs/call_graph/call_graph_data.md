# 调用链路：数据模型与数据管理

> 对应源码：`src/data/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

## 当前实现基线（2026-07-22）

`DataFacade.load_all()` 现在返回并保存 `LoadReport`，加载阶段不会调用 `save()`，因此源 JSON 不会被自动改写。

```
MainWindow._load_data() -> DataFacade.load_all()
  -> HeroManager.load() / SynergyManager.load() / GuideManager.load()
    -> DataManager._parse_models(data, key_of)
      -> model_class.model_validate(raw)                         [逐条校验]
      -> 坏记录或重复键 -> DataIssue -> 跳过该记录
  -> DataFacade._validate_references(report)
    -> 仅追加 missing_reference 问题，不修改内存数据
  -> MainWindow 询问用户是否修复并保存
    -> DataMutationService.repair_missing_references()           [创建备份后写入]
  -> return LoadReport                                           [只读校验]
```

| 对象 | 职责 |
|------|------|
| `DataIssue` | 记录严重级别、类别、文件、记录下标、实体键、字段和消息 |
| `LoadReport` | 汇总 `issues`，提供 `error_count`、`warning_count` |
| `last_load_report` | 保存最近一次 `load_all()` 的完整报告 |

示例：攻略关联英雄 ID 不存在时，正文、关联列表和原 `guides.json` 都保持不变，并生成 `missing_reference`；只有用户确认修复后才会清理并保存。

## 胜率仓库调用链

胜率 CSV 不属于三个 JSON Manager，由独立仓库按名称读取并缓存：

```
RecommendationPanel._load_win_rate_by_name() / MatchGuidePanel._load_default_heroes()
  -> load_win_rates()
     -> [默认路径] data/2v2胜率排行.csv
     -> csv.DictReader()
     -> row["武将"] + float(row["胜率"].replace("%", ""))
     -> {武将名: 百分比}
```

`load_win_rates()` 首次访问默认 CSV 时填充模块缓存；文件缺失、I/O 错误或单行百分比格式非法只记录 warning/跳过该行，不阻断页面加载。传入自定义 `Path` 时不污染默认缓存，便于测试和离线数据校验。官方数据导入成功覆盖 2v2 CSV 后调用 `clear_win_rate_cache()`，使后续页面查询读取新数据。

```
OfficialDataImportService.import_file("2v2", image_path)
  -> _write_csv(data/2v2胜率排行.csv, ["排名", "武将", "胜率"], rows)
  -> _write_csv(data/2v2出场排行.csv, ["排名", "武将"], rows)
  -> _write_csv(对应 *_待复核.csv, review_rows)
  -> mark_recommendation_index_stale(True)
  -> clear_win_rate_cache()
  -> RecommendationPanel / MatchGuidePanel 下次 load_win_rates() 读取新胜率

OfficialDataImportService.import_file("exile", image_path)
  -> 合并左右表视觉行序
  -> _write_csv(data/武将放逐.csv, ["排名", "武将"], rows)
  -> _write_csv(data/武将放逐_待复核.csv, review_rows)
  -> mark_recommendation_index_stale(True)
```

`_write_csv()` 先在目标目录创建 UTF-8、LF 换行的临时文件，再以 `Path.replace()` 原子替换正式文件。待复核 CSV 不参与 `win_rate_repository` 缓存；它与 `screenshot_data/official_import/` 的行截图共同构成导入质量追踪记录。

## 一、数据加载链路

### 1.1 完整数据加载

```
MainWindow._load_data()
  -> self._data.load_all()                                     [DataFacade.load_all]
    -> HeroManager.load()
      -> json.load(f)                                          [读取 heroes.json]
      -> Hero.model_validate(h)                                [逐项 Pydantic 校验]
      -> self._heroes[id] = hero                               [存入内存字典]
    -> SynergyManager.load()
      -> json.load(f)                                          [读取 synergies.json]
      -> self._synergy_key(a, b) = tuple(sorted((a, b)))       [生成双向归一 key]
      -> SynergyScore.model_validate(s)                        [逐项 Pydantic 校验]
      -> self._synergies[key] = score                          [存入内存字典]
    -> GuideManager.load()
      -> json.load(f)                                          [读取 guides.json]
      -> HeroGuide.model_validate(g)                           [逐项 Pydantic 校验]
      -> self._guides[hero_id] = guide                         [存入内存字典]
```

| 调用方 | 被调用方 | 所在文件 | 说明 |
|--------|----------|----------|------|
| `MainWindow.__init__()` | `DataFacade.load_all()` | `manager.py` | 主窗口构造时加载全部数据 |
| `MainWindow._reload_data()` | `DataFacade.load_all()` | `manager.py` | 菜单"重新加载数据" |
| `DataFacade.load_all()` | `HeroManager.load()` | `hero_manager.py` | 加载武将 JSON→内存 |
| `DataFacade.load_all()` | `SynergyManager.load()` | `synergy_manager.py` | 加载相性 JSON→内存 |
| `DataFacade.load_all()` | `GuideManager.load()` | `guide_manager.py` | 加载攻略 JSON→内存 |

### 1.2 数据保存链路

```
[任意] Manager.save()
  -> [mgr].model_dump(mode="json")                             [Pydantic→dict]
  -> json.dump(data, tmp_file)                                 [写入 .tmp 临时文件]
  -> tmp_path.replace(mgr.synergies_file)                      [原子替换原文件]
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

---

## 二、武将查询链路

### 2.1 按名称查询（全模块最频繁的查询）

```
RecommendationPanel.update_recommendations()
  -> self._hero_mgr.get_hero_by_name(name)                    [O(N) 线性扫描]
    -> for hero in self._heroes.values():
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

> **性能标注：** `get_hero_by_name()` 内部是 O(N) 线性遍历（N=165）。在 OCR 矫正流程中，每帧会被 `_correct_with_hero_list()` 调用 8 次，每次又通过编辑距离遍历全部 165 个名称。如果修改为 `name -> id` 的 dict 索引可消除 O(N) 查找，但当前 165 规模下线性扫描的延迟可以忽略（< 0.01ms）。

### 2.2 按 ID 查询（O(1) 字典访问）

```
[任意] controller
  -> self._hero_mgr.get_hero(hero_id)                         [dict.get, O(1)]
  -> return self._heroes.get(hero_id)                          [Optional[Hero]]
```

| 调用方 | 说明 |
|--------|------|
| `RecommendationPanel._load_real_synergies()` | 相性伙伴 ID→Hero 对象 |
| `RecommendationPanel._show_guide_popup()` | 弹出攻略时获取 Hero |
| `HeroDetailPanel.show_hero()` | 点击列表项展示详情 |
| `HeroDetailPanel._update_guide_tab()` | 渲染攻略中的克制/搭配名 |
| `GuideDetailDialog.__init__()` | 弹出攻略详情时获取伙伴名 |

### 2.3 模糊搜索

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

## 三、相性查询链路

### 3.1 武将相性列表查询（全表扫描）

```
RecommendationPanel._load_real_synergies(card_idx, hero_id)
  -> self._synergy_mgr.list_synergies_for_hero(hero_id)       [O(N) 全表扫描]
    -> for (a_id, b_id), score in self._synergies.items():
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

### 3.2 双向归一查询

```
[任意] controller
  -> self._synergy_mgr.get_synergy(hero_a_id, hero_b_id)
    -> self._synergy_key(a_id, b_id) = tuple(sorted((a_id, b_id)))
    -> return self._synergies.get(key)                         [O(1) dict lookup]
```

| 调用方 | 说明 |
|--------|------|
| 外部查询 | 查询特定两个武将的相性评分 |

---

## 四、增量更新链路

### 4.1 增量更新处理

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

## 五、外部调用关系总览

### 5.1 本模块被外部调用

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
│  ai_generator.py              → HeroGuide / SynergyScore 校验    │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `pydantic.BaseModel` | Hero、SynergyScore、HeroGuide 等模型基类 |
| Python 标准库 `json` | JSON 序列化/反序列化 |
| Python 标准库 `pathlib` | 文件路径操作 |

### 5.3 调用频率最高的关键路径

```
RecommendationPanel.update_recommendations()    [OCR 每帧触发]
  -> HeroManager.get_hero_by_name() ×8           [O(N) × 8]
  -> SynergyManager.list_synergies_for_hero() ×8 [全表扫描 × 8]
  -> HeroManager.get_hero() ×32                  [top4 × 8 卡片]
```

---

## 六、函数清单总表

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
| `apply_incremental_update()` | `manager.py` | 测试和外部导入工具 | 按 added/modified/removed 更新三个 Manager，并执行武将删除级联 |
| `load_win_rates()` | `win_rate_repository.py` | `RecommendationPanel`, `MatchGuidePanel` | CSV 解析、百分比转浮点、默认路径缓存 |
