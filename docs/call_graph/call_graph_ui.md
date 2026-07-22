# 调用链路：UI 界面层

> 对应源码：`src/ui/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 信号连接以 `[signal] → slot` 标注，QProcess 子进程以虚线分隔。

---

## 当前实现基线（2026-07-22）

```
MainWindow.__init__()
  -> DataFacade(...) -> _load_data() -> load_all()
  -> 创建 Fetch / Capture / OCR 服务并连接信号
  -> get_mumu_config() -> update_config()
  -> _setup_ui()
    -> HeroBrowser(hero_mgr, guide_mgr, synergy_mgr)
    -> RecommendationPanel(hero_mgr, synergy_mgr, guide_mgr, capture_svc, ocr_svc)
    -> MatchGuidePanel(hero_mgr, capture_svc)
```

| 用户动作 | UI 入口 | 核心调用 | 刷新 |
|----------|---------|----------|------|
| 武将采集 | `_request_fetch_*()` | `HeroFetchService.fetch_*()` | `_reload_data()` |
| 攻略生成 | `_request_guide_*()` | `AiGenerationWorkflow.request_guide_*()` -> `GuideFetchService.fetch_*()` | `GuideManager.load()` + 状态栏统计刷新 |
| 相性生成 | `_request_synergy_*()` | `AiGenerationWorkflow.request_synergy_*()` -> `SynergyFetchService.fetch_pair/single()` | `SynergyManager.load()` + 浏览器、推荐页刷新 |
| 官方数据导入 | `_open_official_data_import()` | `OfficialDataImportDialog` -> `OfficialDataImportWorker` -> `OfficialDataImportService` | 覆盖 2v2/放逐 CSV，完成后显示统计 |
| 截图、图片导入、轮询 | 推荐页或 `poll_tick` | `CaptureService` -> `OcrWorker` | 推荐卡或对局攻略页 |

数据完整性问题存于 `self._data.last_load_report`；当前 UI 使用已恢复的内存数据，尚未提供报告查看或写回修复界面。服务完成状态以 CLI 退出码为准，不解析 `RESULT: FAIL=`。

武将浏览页已将可独立维护的编辑单元从 `hero_browser.py` 拆出：

| 模块 | 公开组件 | 职责 | 由谁调用 |
|------|----------|------|----------|
| `hero_edit_dialog.py` | `HeroEditDialog` | 编辑单个 `Hero` 的基础字段 | `HeroDetailPanel._on_info_edit()` |
| `guide_edit_dialog.py` | `GuideEditDialog` | 编辑 `HeroGuide` 的正文、要点和关系集合 | `HeroDetailPanel._on_guide_edit()` |
| `hero_relation_select_dialog.py` | `HeroRelationSelectDialog` | 搜索、势力筛选和多选攻略关系武将 | `GuideEditDialog._open_relation_selector()` |
| `synergy_edit_dialog.py` | `SynergyEditDialog` | 编辑一对武将的评分、维度和说明 | `HeroDetailPanel._on_synergy_edit()` |

`hero_browser.py` 仍负责列表、详情、信号协调及持久化调用；它导入上述公开对话框以保持原有外部导入兼容，但不再持有它们的表单构建逻辑。

## 共享 UI 与数据访问接口

跨页面复用的实现已集中到公开模块，调用方向如下：

```
RecommendationPanel / MatchGuidePanel
  -> src.ui.shared.widgets.DoubleClickLabel
     -> double_clicked [左键双击信号]
  -> src.ui.shared.hero_dialogs.HeroSkillDialog(hero).exec()
  -> src.ui.shared.faction_colors.get_faction_colors()
     -> load_faction_colors() [首次访问时读取并校验 JSON]
     -> DEFAULT_FACTION_COLORS [文件不可用时兜底]
  -> src.data.win_rate_repository.load_win_rates()
     -> csv.DictReader(data/2v2胜率排行.csv)
     -> {武将名: 百分比} [默认路径结果缓存]

MainWindow._open_faction_colors()
  -> save_faction_colors()
  -> reload_faction_colors()
  -> RecommendationPanel.refresh_faction_colors()
  -> MatchGuidePanel.refresh_faction_colors()
```

这些页面只导入公开名称，不再从 `recommendation_panel.py`、`hero_browser.py` 或其他页面模块跨模块导入 `_xxx` 私有实现。

## 一、MainWindow 信号拓扑

### 1.1 初始化流程

```
MainWindow.__init__()
  -> DataFacade.__init__(heroes_file, synergies_file, guides_file)   [数据门面]
  -> HeroFetchService(self)                                          [武将采集服务]
  -> GuideFetchService(self._data.guides, self)                      [攻略生成服务 + 注入 guide_mgr]
  -> SynergyFetchService(self)                                       [相性获取服务]
  -> AiGenerationWorkflow(hero_mgr, guide_mgr, synergy_mgr,
                          guide_service, synergy_service, self)     [AI 任务工作流]
  -> CaptureService(self)                                            [截图服务]
  -> OcrService(self)                                                [OCR 控制服务]
  -> get_mumu_config()                                              [读取模拟器配置]
  -> CaptureService.update_config(config)
  -> OcrService.update_config(config)
  -> OcrService.set_hero_names(names)                               [设置武将名列表]
  -> _connect_fetch_signals()
  -> _connect_capture_signals()
  -> AiGenerationWorkflow.status_changed -> _on_fetch_status()
  -> AiGenerationWorkflow.guides_changed -> _on_guides_generated()
  -> AiGenerationWorkflow.synergies_changed -> _on_synergies_generated()
  -> setWindowTitle(), resize()
  -> _setup_menu()
  -> _load_data()
     -> self._data.load_all()
  -> _setup_ui()
     -> QTabWidget()
        -> HeroBrowser(self._data.heroes, self._data.guides,
                        self._data.synergies)                      [Tab 0: 武将浏览]
        -> RecommendationPanel(self._data.heroes, self._data.synergies,
                               guide_mgr, capture_svc, ocr_svc)      [Tab 1: 选将推荐]
        -> MatchGuidePanel(self._data.heroes, capture_svc)           [Tab 2: 对局攻略]
  -> _setup_status_bar()
  -> _update_status()
```

### 1.2 各服务信号连接

```
_connect_fetch_signals():
  HeroFetchService.status_changed   → _on_fetch_status       → status_label.setText()
  HeroFetchService.fetch_completed  → _on_fetch_completed    → QMessageBox
  HeroFetchService.error_occurred   → _on_fetch_error        → QMessageBox.warning()

AiGenerationWorkflow._connect_services():
  GuideFetchService.status_changed    → workflow.status_changed → MainWindow._on_fetch_status()
  GuideFetchService.fetch_completed   → workflow._on_guide_completed()
    → GuideProgressDialog.on_process_finished()
    → [success] GuideManager.load() → workflow.guides_changed → MainWindow._on_guides_generated()
  GuideFetchService.error_occurred    → workflow._on_guide_error() → 详细错误弹窗
  GuideFetchService.progress_output/value → workflow._on_guide_progress*() → GuideProgressDialog

  SynergyFetchService.status_changed  → workflow.status_changed → MainWindow._on_fetch_status()
  SynergyFetchService.fetch_completed → workflow._on_synergy_completed()
    → GuideProgressDialog.on_process_finished()
    → [success] SynergyManager.load() → workflow.synergies_changed
      → MainWindow._on_synergies_generated() → 浏览器/推荐页刷新
  SynergyFetchService.error_occurred  → workflow._on_synergy_error() → 警告弹窗
  SynergyFetchService.progress_output/value → workflow._on_synergy_progress*() → GuideProgressDialog

_connect_capture_signals():
  OcrService.poll_tick                → _on_poll_capture             [轮询截图触发]
  self._poll_result_ready (自定义 signal) → _on_poll_result          [后台线程结果回传]
```

| 函数 | 所在行 | 说明 |
|------|--------|------|
| `_connect_fetch_signals()` | 主窗口 | 连接 HeroFetchService 三个信号 |
| `AiGenerationWorkflow._connect_services()` | AI 工作流 | 连接 GuideFetchService、SynergyFetchService 的状态、完成、错误和进度信号 |
| `_connect_capture_signals()` | 主窗口 | 连接 OcrService.poll_tick + 自定义信号 |

---

## 二、菜单操作触发链路

### 官方数据导入

```
菜单「数据 → 官方数据导入」clicked
  -> MainWindow._open_official_data_import()
    -> OfficialDataImportDialog(self).exec()
      -> 用户选择 2v2 和/或武将放逐图片
      -> _start_import()
        -> 禁用导入/取消按钮，显示“正在准备导入”不定进度条
        -> OfficialDataImportWorker(paths, self).start()
          -> [signal] progress_changed(status, 0, 0)
             -> _on_progress_changed() -> QProgressBar.setRange(0, 0)
          -> [signal] progress_changed(status, current, total)
             -> _on_progress_changed() -> 显示 current / total
          -> [signal] completed(summaries)
             -> _on_completed() -> QMessageBox -> accept()
          -> [signal] failed(message)
             -> _on_failed() -> 恢复按钮、隐藏进度条、显示错误
```

对话框只负责文件选择、按钮状态和进度显示；表格裁剪、名称候选决策、待复核和 CSV 写入均位于业务服务层。任务运行时 `reject()` 不关闭对话框，避免销毁仍在运行的线程。

### 2.1 武将采集菜单

```
菜单「数据 → 武将获取 → 全量获取」clicked
  -> MainWindow._request_fetch_all()
    -> QMessageBox.question("确认全量获取？")
    -> [Yes] HeroFetchService.fetch_all()
      -> _start_process(["-m", "src.scraper.official"])
    -> [子进程结束]
      -> [signal] fetch_completed → MainWindow._on_fetch_completed()
        -> QMessageBox.information()

菜单「数据 → 武将获取 → 增量获取」
  -> MainWindow._request_fetch_incremental()
    -> HeroFetchService.fetch_incremental()

菜单「数据 → 武将获取 → 指定获取」
  -> MainWindow._request_fetch_specific()
    -> HeroFetchDialog(self._data.heroes, parent)                [武将选择对话框]
      -> BaseHeroSelectDialog(MULTI mode → 多选 checkbox)
    -> [accepted] HeroFetchService.fetch_specific(dialog.selected_ids)
```

| 函数 | 菜单路径 | 调用链 |
|------|----------|--------|
| `_request_fetch_all()` | 数据→武将获取→全量获取 | `QAction` → `fetch_all()` → `_start_process()` |
| `_request_fetch_incremental()` | 数据→武将获取→增量获取 | `QAction` → `fetch_incremental()` → `_start_process()` |
| `_request_fetch_specific()` | 数据→武将获取→指定获取 | `QAction` → `HeroFetchDialog` → `fetch_specific(ids)` |

### 2.2 攻略生成菜单

```
菜单「数据 → 攻略获取 → 全量获取」
  -> MainWindow._request_guide_all()
    -> AiGenerationWorkflow.request_guide_all()
      -> _get_heroes_as_dicts()                                  [HeroManager.list_heroes() → dict]
      -> _start_guide_generation(heroes, "all", ...)
        -> estimate_cost(len(heroes), "guide")                   [AI 成本估算]
        -> BackendChooseDialog(estimation, title, parent)        [选择 API/浏览器]
          -> [accepted] backend = dialog.get_selected_backend()
            -> GuideProgressDialog(hero_count, parent)           [创建进度对话框]
            -> GuideFetchService.fetch_all(heroes, backend)
            -> GuideProgressDialog.exec()                        [模态等待]

菜单「数据 → 攻略获取 → 增量获取」
  -> MainWindow._request_guide_incremental()
    -> AiGenerationWorkflow.request_guide_incremental()
      -> HeroManager.list_heroes() + GuideManager.list_guides() [对比已有攻略]
      -> 筛选: 已有攻略 → 跳过; 无攻略 → missing
      -> [无缺失] status_changed("所有武将已有攻略，无需生成")
      -> [有缺失] _start_guide_generation(missing, "incremental", ...)
        -> GuideFetchService.fetch_incremental(missing, backend)
        -> GuideProgressDialog(len(missing))                     [总数与实际任务一致]

菜单「数据 → 攻略获取 → 指定获取」
  -> MainWindow._request_guide_specific()
    -> AiGenerationWorkflow.request_guide_specific()
      -> GuideFetchDialog(hero_manager, parent)                  [选择指定武将]
      -> _start_guide_generation(selected, "specific", ...)
```

GuideFetchService [signal] fetch_completed
  -> AiGenerationWorkflow._on_guide_completed(success, message)
    -> GuideProgressDialog.on_process_finished(success, message)
    -> [success] GuideManager.load() -> guides_changed
      -> MainWindow._on_guides_generated() -> _update_status()
    -> [failure] QMessageBox.warning()
```

| 函数 | 菜单路径 | 调用链 |
|------|----------|--------|
| `MainWindow._request_guide_*()` | 数据→攻略获取 | 仅委托 `AiGenerationWorkflow.request_guide_*()` |
| `request_guide_all()` | 全量获取 | `_get_heroes_as_dicts()` → `_start_guide_generation()` → `GuideFetchService.fetch_all()` |
| `request_guide_incremental()` | 增量获取 | `GuideManager.list_guides()` → 缺失筛选 → `fetch_incremental(missing)` |
| `request_guide_specific()` | 指定获取 | `GuideFetchDialog` → `_start_guide_generation()` → `fetch_specific()` |

### 2.3 相性评分菜单

```
菜单「数据 → 武将相性 → 指定获取」
  -> MainWindow._request_synergy_pair()
    -> AiGenerationWorkflow.request_synergy_pair()
      -> _require_heroes()                                       [无英雄数据则提示]
      -> SynergyPairDialog(hero_manager, parent)                 [选 2-8 武将]
       -> BaseHeroSelectDialog(MULTI_LIMIT, max_selection=8)
       -> 覆盖 _on_accept(): 允许 2-8 个（不要求正好 8 个）
      -> [accepted] 计算组合数 C(n,2)
      -> _choose_backend(title) -> BackendChooseDialog
      -> _start_synergy_generation(pair_count, title, ...)
        -> GuideProgressDialog(pair_count, title, parent)
        -> SynergyFetchService.fetch_pair(selected, backend)
      -> 写入 temp JSON
      -> _start_process(["-m", "src.scraper.ai_batch", "--synergy-pair", tmp])
        -> GuideProgressDialog.exec()

菜单「数据 → 武将相性 → 选定武将」
  -> MainWindow._request_synergy_single()
    -> AiGenerationWorkflow.request_synergy_single()
      -> SynergySingleDialog(hero_manager, parent)               [选 1 武将]
       -> BaseHeroSelectDialog(SINGLE mode → 单选)
      -> _choose_backend(title) -> BackendChooseDialog
      -> SynergyFetchService.fetch_single(hero, all_heroes, backend)
```

SynergyFetchService [signal] fetch_completed
  -> AiGenerationWorkflow._on_synergy_completed(success, message)
    -> [success] SynergyManager.load() -> synergies_changed
      -> MainWindow._on_synergies_generated()
        -> HeroBrowser.refresh_synergies()
        -> RecommendationPanel.refresh_synergies()
        -> _update_status()
```

| 函数 | 菜单路径 | 调用链 |
|------|----------|--------|
| `MainWindow._request_synergy_*()` | 数据→武将相性 | 仅委托 `AiGenerationWorkflow.request_synergy_*()` |
| `request_synergy_pair()` | 指定获取 | `SynergyPairDialog` → 后端选择 → `SynergyFetchService.fetch_pair()` |
| `request_synergy_single()` | 选定武将 | `SynergySingleDialog` → 后端选择 → `SynergyFetchService.fetch_single()` |

---

## 三、截图与 OCR 轮询链路

### 3.1 手动触发截图

```
RecommendationPanel._on_import_from_screenshot()               [「截图」按钮]
  -> [无 capture service] _open_mumu_config() -> return        [先配置模拟器]
  -> CaptureService.do_capture(perform_ocr=False)
    -> QTimer.singleShot(0, self._execute_capture)              [延后回调；ADB 截图仍在 GUI 线程]

CaptureService._execute_capture(hero_names)                    [异步回调]
  -> self._capture.connect()                                   [连接 ADB]
  -> self._capture.screencap_full()                            [ADB 截图]
  -> save_image(image, save_path)                              [保存截图]
  -> emit capture_completed({ocr_results, image, ...})

RecommendationPanel._on_capture_result(result)                 [信号接收]
  -> [ADB 截图来源] 复位按钮；不导入 OCR 结果
```

### 3.2 从文件导入

```
RecommendationPanel._on_import_from_file()                     [「从图片导入」按钮]
  -> QFileDialog.getOpenFileName(...)                           [选择图片文件]
  -> CaptureService.capture_completed.connect(...)
  -> CaptureService.do_capture_from_file(file_path, hero_names)
    -> QTimer.singleShot(0, _execute_file_ocr)
       -> PIL.Image.open(file_path)
       -> _queue_capture_ocr()
          -> submit_ocr_task() -> OcrWorker.submit(OcrTask)
             -> OcrWorker._execute() -> 模板匹配 -> OCR
          -> _on_ocr_task_completed() -> capture_completed
  -> [信号] → load_from_ocr()
```

### 3.3 对局攻略卡片与导入链路

```
MatchGuidePanel.__init__(hero_mgr, capture_service)
  -> _load_default_heroes()
     -> HeroManager.list_heroes()                                [按 ID 升序取前四名]
     -> MatchHeroCard.set_hero(hero)
        -> get_faction_colors()                                  [头像左上势力标签]
        -> _load_portrait()                                      [头像 120×160，区域 135×162]
     -> load_win_rates()                                         [加载胜率仓库]

MatchHeroCard._portrait [左键双击]
  -> MatchHeroCard._on_hero_double_clicked()
  -> MatchGuidePanel._show_skill_popup(hero_id)
  -> src.ui.shared.hero_dialogs.HeroSkillDialog(hero).exec()

MatchGuidePanel._on_import_from_screenshot()
  -> [未配置 ADB] request_mumu_config → MainWindow._open_mumu_config()
  -> CaptureService.do_capture(perform_ocr=False)
     -> _execute_capture() → 保存截图
  -> MatchGuidePanel._on_capture_result(result)
     -> [ADB 截图来源] 仅复位导入状态

MatchGuidePanel._on_import_from_file()
  -> QFileDialog.getOpenFileName()
  -> CaptureService.do_capture_from_file(
       file_path, template_name="match_guide", force_ocr=True)
  -> _execute_file_ocr() → _queue_capture_ocr() → OcrWorker → _on_capture_result()
```

对局攻略导入复用 `CaptureService` 的异步采集接口，但通过 `template_name` 使用独立模板；未识别到武将时保留默认四张卡片。

### 3.4 轮询截图链路（关键：跨线程）

轮询匹配成功后的页面跳转采用边沿触发，武将选择与对局攻略任务分别维护冷却：

```text
_on_poll_result()
  -> task_results.hero_selection == matched
     -> RecommendationPanel.load_from_ocr()
     -> _tabs.setCurrentWidget(_recommendation)（仅首次）
     -> set_task_cooldown("hero_selection", 180)
     -> activate_task("match_guide")
  -> task_results.match_guide == matched
     -> MatchGuidePanel.update_block()
     -> _tabs.setCurrentWidget(_match_guide)（仅首次）
     -> set_task_cooldown("match_guide", 5)
```

轮询冷却期间的重复匹配不会重复抢占用户当前页面；截图为空、图像截断等可重试结果也不会重置选将页面状态。

```
                                                     [主线程]
OcrService.poll_tick  [signal, QTimer 驱动]
  → MainWindow._on_poll_capture()
    -> [冷却期内] return
    -> [未配置] return
    -> threading.Lock.acquire(blocking=False)
    -> [已有一轮运行中] return
    -> [后台线程] threading.Thread(target=_do_poll_work())
                         ↓
                  [后台线程]
                  -> CaptureService.is_connected
                  -> [未连接] CaptureService.connect_emulator()
                  -> AdbCapture.screencap_full()                 [截图]
                  -> due_poll_tasks()                             [任务独立冷却]
                  -> 每个到期任务：CaptureService.submit_ocr_task()
                     -> OcrWorker.submit(OcrTask)
                     -> task.completed.wait()
                  -> self._poll_result_ready.emit({generation, task_results}) [跨线程信号]
                  -> Lock.release()
                         ↓
                  [主线程接收]
MainWindow._on_poll_result(result)                              [主线程槽函数]
  -> [hero_selection 命中] RecommendationPanel.load_from_ocr()
  -> [match_guide 命中] MatchGuidePanel.update_block()
  -> [任务级] OcrService.set_task_cooldown(task_name, seconds)
```

| 函数 | 所在类 | 说明 |
|------|--------|------|
| `_on_poll_capture()` | `MainWindow` | QTimer 触发，启动后台线程 |
| `_do_poll_work()` | `MainWindow` (内联) | 后台线程：截图 + 模板匹配 + OCR |
| `_on_poll_result()` | `MainWindow` | 主线程：接收结果 → 更新推荐面板 |

> **关键架构决策：** 手动 ADB 截图（`do_capture`）在 `QTimer.singleShot(0)` 回调中运行，仍在主线程；文件导入和轮询的模板匹配/OCR 均提交到唯一 `OcrWorker`。轮询的 ADB 截图通过 `threading.Thread` 运行在后台线程，并以 PySide6 信号把结构化结果传回主线程。

---

## 四、推荐面板链路

### 4.1 OCR 结果导入推荐面板

```
RecommendationPanel.load_from_ocr(ocr_results)                  [OCR 结果 list[dict]]
  -> self._ocr_mode = True
  -> self._current_hero_ids.clear()
  -> [遍历 8 个槽位]
     -> idx = item["index"] - 1
     -> name = item["name"]
     -> confidence = item["confidence"]
     -> hero = self._hero_mgr.get_hero_by_name(name)            [名称→Hero 对象]
     -> [找到] card.set_hero(hero)
               card.set_confidence(0.5)                         [固定 0.5: OCR 非游戏内推荐]
               self._current_hero_ids.add(hero.id)
     -> [未找到] card.set_hero(None)                            [清空卡片]
                 直接设置 card._name_label.setText(name)         [显示原始名称]
     -> self._load_real_synergies(idx, hero.id)                 [加载相性]
     -> self._load_win_rate_by_name(idx, name)                  [加载胜率]
  -> self._apply_medal_rankings()                               [Top 3 奖牌]
```

### 4.2 相性加载与展示

```
RecommendationPanel._load_real_synergies(card_idx, hero_id)
  -> self._synergy_mgr.list_synergies_for_hero(hero_id)         [全表扫描]
  -> [OCR 模式] 过滤: partner_id in _current_hero_ids           [仅显示当前 8 人相性]
  -> 按 score 降序排序
  -> 取 top 4
  -> [遍历每个伙伴]
     -> self._hero_mgr.get_hero(partner_id) -> partner.name     [ID→名称解析]
     -> (partner.name, "评分" 或 "S/A/B/C/D")
  -> card.set_synergies(pairs)                                  [展示到 QGridLayout]
```

### 4.3 默认加载（启动/重新加载）

```
RecommendationPanel._load_default_heroes()
  -> self._ocr_mode = False
  -> heroes = self._hero_mgr.list_heroes()                      [全部武将]
  -> top8 = sorted(heroes, key=lambda h: h.id)[:8]              [按 ID 取前 8]
  -> [遍历 8 个]
     -> card.set_hero(hero)
     -> self._load_real_synergies(i, hero.id)
     -> self._load_win_rate_by_name(i, hero.name)
  -> self._apply_medal_rankings()
```

### 4.4 奖牌计算

```
RecommendationPanel._apply_medal_rankings()
  -> [遍历 8 张卡片]
     -> 解析 _win_rate_label.text() → float                     ["胜率: 52.3%" → 52.3]
  -> 按胜率降序排序
  -> rank 1 → card.set_medal(1) → "🥇"
  -> rank 2 → card.set_medal(2) → "🥈"
  -> rank 3 → card.set_medal(3) → "🥉"
```

| 函数 | 所在类 | 调用方 | 被调用方 |
|------|--------|--------|----------|
| `load_from_ocr(results)` | `RecommendationPanel` | `_on_capture_result()`, `_on_poll_result()` | `get_hero_by_name()`, `set_hero()`, `_load_real_synergies()` |
| `_load_default_heroes()` | `RecommendationPanel` | `__init__()` | `list_heroes()`, `_load_real_synergies()`, `_load_win_rate_by_name()` |
| `_load_real_synergies(idx, id)` | `RecommendationPanel` | `load_from_ocr()`, `_load_default_heroes()` | `list_synergies_for_hero()`, `get_hero()`, `set_synergies()` |
| `_load_win_rate_by_name(idx, name)` | `RecommendationPanel` | `load_from_ocr()`, `_load_default_heroes()` | `load_win_rates()`, `set_win_rate()` |
| `_apply_medal_rankings()` | `RecommendationPanel` | `load_from_ocr()`, `_load_default_heroes()` | 解析胜率文本, `set_medal()` |
| `set_hero(hero)` | `HeroCardWidget` | 外部 | `_update_display()`, `_load_portrait()`, `_update_confidence_display()` |
| `set_confidence(conf)` | `HeroCardWidget` | 外部 | `_update_confidence_display()` |
| `set_synergies(pairs)` | `HeroCardWidget` | `_load_real_synergies()` | 4 列 QGridLayout 动态添加 QLabel |
| `set_win_rate(rate)` | `HeroCardWidget` | `_load_win_rate_by_name()` | 设置胜率 QLabel 文本 |
| `set_medal(rank)` | `HeroCardWidget` | `_apply_medal_rankings()` | 设置奖牌 QLabel 文本 |
| `_load_portrait(name)` | `HeroCardWidget` (static) | `_update_display()` | `QPixmap(str(IMAGES_DIR/name.ext))` |

### 4.5 攻略弹出

```
HeroCardWidget.guide_clicked [signal] → RecommendationPanel._show_guide_popup(hero_id)
  -> self._hero_mgr.get_hero(hero_id)                           [查询 Hero]
  -> self._guide_mgr.get_guide(hero_id)                         [查询 HeroGuide]
  -> GuideDetailDialog(hero_name, guide, hero_mgr, parent)      [创建攻略对话框]
     -> [无 guide] 显示 "暂无攻略数据"
     -> [有 guide] 渲染:
        -> 核心要点 (key_points)
        -> 新手提示 (tips_for_beginners)
        -> 被克制 (counters) → get_hero(counter_id) → name     [ID→名称]
        -> 搭配推荐 (synergizes_with) → 同上
        -> 攻略正文 (description) → _markdown_to_html()         [mistune Markdown 渲染]
  -> GuideDetailDialog.exec()                                   [模态展示]
```

`RecommendationPanel` 仅协调数据和信号：它创建 `HeroCardWidget`、连接卡片信号并打开 `GuideDetailDialog`。卡片绘制/奖牌样式位于 `hero_card_widget.py`，攻略摘要和 Markdown 渲染位于 `guide_detail_dialog.py`；原模块继续导入这两个名称，保持既有 `from recommendation_panel import ...` 调用兼容。

### 4.6 函数清单总表（推荐面板）

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `__init__()` | `recommendation_panel.py` | `MainWindow._setup_ui()` | `_setup_ui()`, `_load_default_heroes()` |
| `load_from_ocr(results)` | `recommendation_panel.py` | `_on_capture_result()` | `get_hero_by_name()`, `set_hero()`, `_load_real_synergies()` |
| `_load_default_heroes()` | `recommendation_panel.py` | `__init__()` | `list_heroes()`, `_load_real_synergies()`, `_load_win_rate_by_name()` |
| `_load_real_synergies(idx, id)` | `recommendation_panel.py` | 内部 | `list_synergies_for_hero()`, `get_hero()`, `set_synergies()` |
| `_load_win_rate_by_name(idx, name)` | `recommendation_panel.py` | 内部 | `load_win_rates()`, `set_win_rate()` |
| `_apply_medal_rankings()` | `recommendation_panel.py` | 内部 | 排序 + `set_medal()` |
| `_show_guide_popup(hero_id)` | `recommendation_panel.py` | `card.guide_clicked` | `get_hero()`, `get_guide()`, `GuideDetailDialog` |
| `HeroCardWidget` | `hero_card_widget.py` | `RecommendationPanel._setup_ui()` | 卡片展示与 `guide_clicked` / `hero_double_clicked` 信号 |
| `GuideDetailDialog` | `guide_detail_dialog.py` | `_show_guide_popup()` | 攻略摘要、关系跳转与 Markdown 正文 |

---

## 五、武将浏览器链路

### 5.1 初始化与布局

```
HeroBrowser.__init__(hero_manager, guide_manager, synergy_manager)
  -> _setup_ui()
    -> QSplitter(Horizontal)
       -> HeroListPanel(self._hero_mgr)                         [左侧: 列表面板]
          -> _setup_ui()
             -> QLineEdit (搜索框).textChanged → _apply_filters
             -> QComboBox (势力筛选).currentTextChanged → _apply_filters
             -> QListWidget.currentRowChanged → _on_selection_changed
          -> _load_heroes()
             -> self._hero_mgr.list_heroes()
             -> self._hero_mgr.list_factions()                  [填充势力下拉框]
             -> self._apply_filters()
       -> HeroDetailPanel(self._hero_mgr, self._guide_mgr, self._synergy_mgr) [右侧: 详情面板]
          -> _setup_ui()
             -> QTabWidget
                -> Tab 0: 武将信息 (_setup_info_tab)
                   -> QLabel(HTML rich text) for basic info
                   -> QScrollArea for skills
                -> Tab 1: 攻略指南 (_setup_guide_tab)
                   -> placeholder QLabel("暂无攻略数据")
             -> _setup_corner_buttons()
                -> [信息 Tab] "修改" + "删除" 按钮
                -> [攻略 Tab] "修改" + "删除" 按钮
                -> currentChanged → _on_tab_changed [切换按钮可见性]
    -> HeroListPanel.hero_selected → HeroDetailPanel.show_hero    [信号连接]
    -> HeroDetailPanel.data_changed → HeroBrowser.reload_data()   [信号连接]
```

### 5.2 武将选择和详情展示

```
HeroListPanel._on_selection_changed(row)                       [列表选择变化]
  -> self.hero_selected.emit(filtered_heroes[row].id)           [信号: 发射 hero_id]

HeroDetailPanel.show_hero(hero_id)                              [接收信号]
  -> self._current_hero = self._hero_mgr.get_hero(hero_id)      [查询 Hero]
  -> self._current_guide = self._guide_mgr.get_guide(hero_id)   [查询 HeroGuide]
  -> self._update_info_tab(self._current_hero)
     -> 设置 QLabel HTML: name, title, position, difficulty, faction, gender, HP, hand
     -> self._update_skills(hero)
        -> 清理旧技能布局
        -> [无技能] 显示 "无技能"
        -> [有技能] for each Skill:
           -> 展开面板: name + description
           -> [有 settlement] 可折叠"结算详情"面板
  -> self._update_guide_tab(self._current_guide)
     -> [无 guide] 显示 "暂无攻略数据"
     -> [有 guide] 渲染:
        -> 核心要点 (list[str] → 逐项 QLabel)
        -> 新手提示 (tips_for_beginners)
        -> 被克制 (counters) → get_hero(id) → name             [克制武将链接]
        -> 搭配推荐 (synergizes_with) → 同上
        -> 攻略正文 (description) → _markdown_to_html()
```

### 5.3 武将编辑链路

```
HeroDetailPanel._on_info_edit()                                ["修改"按钮 - 武将信息]
  -> HeroEditDialog(self._current_hero, parent)                 [编辑对话框]
     -> QFormLayout: name/title/faction/position/max_hp/max_hand/gender/difficulty
  -> [accepted] updated = dialog.get_hero()
  -> self._hero_mgr.update_hero(updated)
  -> self._hero_mgr.save()
  -> self._update_info_tab(updated)
  -> self.data_changed.emit()                                   [通知列表刷新]

HeroDetailPanel._on_info_delete()                              ["删除"按钮 - 武将信息]
  -> QMessageBox.question("确认删除 ...？")
  -> [Yes]
     -> self._hero_mgr.delete_hero(self._current_hero.id)
     -> self._guide_mgr.delete_guide(self._current_hero.id)     [级联删除攻略]
     -> self._hero_mgr.save()
     -> self._guide_mgr.save()
     -> self._clear_skills()
     -> self._update_guide_tab(None)
     -> self.data_changed.emit()
```

### 5.4 攻略编辑链路

```
HeroDetailPanel._on_guide_edit()                               ["修改"按钮 - 攻略指南]
  -> GuideEditDialog(self._current_guide, self._hero_mgr, parent)
     -> _setup_ui():
        -> 核心要点: QTextEdit (多行, 每行一条)
        -> 新手提示: QTextEdit
        -> _create_relation_selector("被克制", counters)
           -> "选择武将…" -> _open_relation_selector()
              -> HeroRelationSelectDialog(...).exec()
                 -> 搜索 + 势力多选筛选 + 勾选列表
                 -> _accept_selection() -> selected_ids
           -> _update_relation_summary()                       [显示已选名称]
        -> _create_relation_selector("搭配推荐", synergizes_with)
           -> 同上
        -> 攻略正文: QTextEdit (Markdown)
     -> [accepted] updated = dialog.get_guide()
        -> 读取文本字段 + counters/synergizes_with 已选 ID 列表
  -> self._guide_mgr.update_guide(updated)
  -> self._guide_mgr.save()
  -> self._update_guide_tab(updated)
  -> self.data_changed.emit()

HeroDetailPanel._on_guide_delete()                             ["删除"按钮 - 攻略指南]
  -> QMessageBox.question("确认删除攻略？")
  -> [Yes]
     -> self._guide_mgr.delete_guide(self._current_guide.hero_id)
     -> self._guide_mgr.save()
     -> self._update_guide_tab(None)
     -> self.data_changed.emit()
```

### 5.5 相性浏览与编辑链路

```
HeroDetailPanel.show_hero(hero_id)
  -> _refresh_synergy_table()
     -> SynergyManager.list_synergies_for_hero(hero_id)
     -> 按搭档名称、评分和评级筛选并按评分降序显示

相性表格双击（非说明列）/「修改」按钮
  -> _selected_synergy()
  -> SynergyEditDialog(hero_mgr, synergy).exec()
     -> 评分变化 -> synergy_rating_for_score()                 [实时更新评级]
  -> [accepted] SynergyManager.update_synergy(dialog.get_synergy())
  -> SynergyManager.save() -> _refresh_synergy_table()
  -> synergies_changed.emit()                                  [通知推荐页刷新]

相性说明列双击
  -> _show_synergy_description()
  -> _markdown_to_html() -> QTextBrowser 预览
```

### 5.6 函数清单总表（武将浏览器）

| 函数 | 所在类 | 调用方 | 被调用方 |
|------|--------|--------|----------|
| `reload_data()` | `HeroBrowser` | 外部 UI | `HeroListPanel.reload()` |
| `_load_heroes()` | `HeroListPanel` | `__init__()`, `reload()` | `list_heroes()`, `list_factions()`, `_apply_filters()` |
| `_apply_filters()` | `HeroListPanel` | 搜索/textChanged | 过滤 + `_refresh_list()` |
| `_on_selection_changed(row)` | `HeroListPanel` | QListWidget 信号 | `hero_selected.emit(id)` |
| `show_hero(hero_id)` | `HeroDetailPanel` | `hero_selected` 信号 | `get_hero()`, `get_guide()`, `_update_info_tab()`, `_update_guide_tab()` |
| `_update_info_tab(hero)` | `HeroDetailPanel` | `show_hero()`, `_on_info_edit()` | 设置 HTML, `_update_skills()` |
| `_update_skills(hero)` | `HeroDetailPanel` | `_update_info_tab()` | 动态创建 Skill 展开面板 |
| `_update_guide_tab(guide)` | `HeroDetailPanel` | `show_hero()`, `_on_guide_edit()` | `get_hero()` ×N, `_markdown_to_html()` |
| `_on_info_edit()` | `HeroDetailPanel` | "修改"按钮 | `HeroEditDialog`, `update_hero()`, `save()` |
| `_on_info_delete()` | `HeroDetailPanel` | "删除"按钮 | `delete_hero()`, `delete_guide()`, `save()` |
| `_on_guide_edit()` | `HeroDetailPanel` | "修改"按钮 | `GuideEditDialog`, `update_guide()`, `save()` |
| `_on_guide_delete()` | `HeroDetailPanel` | "删除"按钮 | `delete_guide()`, `save()` |
| `_refresh_synergy_table()` | `HeroDetailPanel` | `show_hero()`、筛选控件、保存后 | `list_synergies_for_hero()`、表格排序和按钮状态 |
| `_on_synergy_edit()` | `HeroDetailPanel` | 双击相性行或"修改" | `SynergyEditDialog`、`update_synergy()`、`save()` |
| `HeroEditDialog.get_hero()` | `HeroEditDialog` | `_on_info_edit()` accepted | 读取控件值 → `Hero` |
| `GuideEditDialog.get_guide()` | `GuideEditDialog` | `_on_guide_edit()` accepted | 读取文本字段 + 已选择的关系 ID → `HeroGuide` |
| `GuideEditDialog._open_relation_selector()` | `GuideEditDialog` | 关系选择按钮 | `HeroRelationSelectDialog.exec()` → 回填 ID 列表 |
| `HeroRelationSelectDialog._accept_selection()` | `HeroRelationSelectDialog` | "确定"按钮 | 按稳定的英雄 ID 顺序输出 `selected_ids` |
| `SynergyEditDialog.get_synergy()` | `SynergyEditDialog` | `_on_synergy_edit()` accepted | 表单值 → 校验后的 `SynergyScore` |

---

## 六、对话框体系链路

### 6.1 武将选择对话框基类

```
BaseHeroSelectDialog.__init__(hero_manager, title, tip, mode, format, max, parent)
  -> self._setup_ui(tip)
    -> self._hero_mgr.list_heroes()                             [加载全部武将]
    -> self._hero_mgr.list_factions()                           [加载势力列表]
    -> [UI 布局]:
       -> QLabel(tip) [可选]
       -> QLineEdit(搜索) → textChanged → _apply_filter
       -> CheckableComboBox(彩色势力标签 + 多选下拉) → checked_values_changed → _apply_filter
          -> Popup: QLineEdit(搜索势力) + 浅蓝色 QListWidget + 全选/反选/确定
       -> QLabel(计数: "已选 N / 共 M")
       -> [MULTI_LIMIT] QLabel(上限提示)
       -> QPushButton("全选") / "取消全选"
       -> QListWidget (武将列表)
       -> QPushButton("确定") / "取消"

  -> _on_accept(list_widget, all_heroes)
    -> [SINGLE] selectedItems()[0] → hero_id
    -> [MULTI/MULTI_LIMIT] 收集所有 checked item → IDs
    -> self._set_result_by_ids(ids, all_heroes)
      -> self.selected_ids = ids
      -> self.selected_heroes = [match.get_hero(id) → dict, ...]
      -> self.selected_hero = hero_dicts[0] if 精确 1
    -> self.accept()
```

### 6.2 子类配置

| 对话框 | 继承自 | SelectionMode | max_selection | 返回格式 | 特殊覆盖 |
|--------|--------|---------------|---------------|----------|----------|
| `HeroFetchDialog` | `BaseHeroSelectDialog` | `MULTI` | 无限制 | `IDS` | 无 |
| `GuideFetchDialog` | `BaseHeroSelectDialog` | `MULTI` | 无限制 | `HEROES_DICT` | 无 |
| `SynergyPairDialog` | `BaseHeroSelectDialog` | `MULTI_LIMIT` | 8 | `HEROES_DICT` | `_on_accept`: 允许 2~8 个 |
| `SynergySingleDialog` | `BaseHeroSelectDialog` | `SINGLE` | 1 | `HEROES_DICT` | 无 |

### 6.3 模拟器配置对话框

```
MainWindow._open_faction_colors()
  -> FactionColorDialog(parent=self).exec()
  -> ColorPicker._open_picker()
     -> QColorDialog(DontUseNativeDialog)
     -> HSB 调整 / 屏幕取色
  -> save_faction_colors(colors, data/faction_colors.json)
  -> reload_faction_colors()
  -> RecommendationPanel.refresh_faction_colors()

MumuConfigDialog.__init__(config, capture_service, ocr_service, parent)
  -> _setup_ui()
  -> _load_config()
    -> CaptureService.update_config()                           [唯一 ADB 会话]
    -> _on_refresh_devices()
       -> EmulatorOperationService.refresh_devices() [后台]
       -> [signal] devices_refreshed -> _on_devices_refreshed() -> 填充设备下拉列表
       -> [signal] device_refresh_failed -> _on_device_refresh_failed() -> 保留当前选择
    -> _refresh_template_status()
       -> OcrService.is_template_loaded()

  → 模板制作:
  _on_make_template()
    -> EmulatorOperationService.capture_template_screenshot() [后台]
      -> CaptureService.capture_screenshot()                    [共享 ADB 会话]
      -> [signal] screenshot_ready -> _on_template_screenshot_ready()
    -> pil_to_qpixmap(image)                                    [UI 线程 PIL→QPixmap]
    -> RoiSelectorDialog(pixmap, title, parent)                 [UI 框选 ROI]
       -> 鼠标拖拽: mousePress → mouseMove → mouseRelease       [绘制矩形框]
       -> 确认: _on_confirm → ROI = (x, y, w, h)
    -> [accepted] OcrService.create_template(image, roi, template_name)
    -> _refresh_template_status()

  [任意连接/刷新状态变化]
    -> _update_ui()
    -> 模板截图进行中？保持“正在截图...”与禁用状态

  → 保存配置:
  _on_save()
    -> 收集控件值到 self._config
    -> self.accept()
  [MainWindow._open_mumu_config() 中]
    -> dialog.get_config()
    -> save_env_file(DEFAULT_ENV_FILE, config)
    -> CaptureService.update_config(config)
    -> OcrService.update_config(config)
    -> MainWindow._sync_poll_with_connection()
    -> OcrService.start_poll(interval * 1000)（仅 connected）/ stop_poll()
```

| 函数 | 所在类 | 调用方 | 被调用方 |
|------|--------|--------|----------|
| `RoiSelectorDialog` 鼠标事件 | `RoiSelectorDialog` | Qt 事件 | `_on_mouse_press/move/release`, `_update_info()`, `_on_paint()` |
| `RoiSelectorDialog._on_confirm()` | `RoiSelectorDialog` | "确认"按钮 | 计算 ROI → `self.accept()` |
| `MumuConfigDialog._on_auto_detect()` | `MumuConfigDialog` | "自动探测"按钮 | `EmulatorOperationService.detect_adb()` |
| `MumuConfigDialog._on_make_template()` | `MumuConfigDialog` | "制作模板"按钮 | `capture_template_screenshot()` → `RoiSelectorDialog` → `OcrService.create_template()` |
| `MumuConfigDialog._on_save()` | `MumuConfigDialog` | "保存"按钮 | 收集配置 → `accept()` |

### 6.4 进度对话框

```
GuideProgressDialog.__init__(hero_count, title, parent)
  -> _setup_ui(hero_count)
    -> QLabel(status: "正在准备...")
    -> QProgressBar(0 -> hero_count)                            [固定总量]
    -> QLabel(detail: 当前处理项)
    -> QLabel(error: 隐藏, 红色)                                 [仅失败时显示]
    -> QPushButton("关闭", 初始禁用)

  → 进度更新:
  update_status(text):
    -> 正则 r"\[(\d+)/(\d+)\]\s*(.+?)\s+OK"
    -> [匹配 OK] _status_label.setText("已生成 XXX..."), update_progress(current, total)
    -> 正则 r"\[(\d+)/(\d+)\]\s*(.+?)\s+FAIL"
    -> [匹配 FAIL] _status_label.setText("生成失败: XXX..."), 不更新进度条位置
    -> [均不匹配] _detail_label.setText(text)

  → 完成:
  on_process_finished(success, message):
    -> 启用"关闭"按钮
    -> [成功] _status_label = "生成完成 ✓", progress = max
    -> [失败] _status_label = "生成失败 ✗", set_error(message)
      -> 失败信息由 BaseFetchService 根据非零退出码生成

> **注意**: 进度对话框仍可显示 CLI 输出中的 `OK`/`FAIL` 行，但 FetchService 的完成状态只依赖子进程退出码；`RESULT: FAIL=` 不参与状态判定。
```

---

## 七、外部调用关系总览

### 7.1 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.hero_manager.HeroManager` | 武将 CRUD 和查询 |
| `src.data.synergy_manager.SynergyManager` | 相性 CRUD 和查询 |
| `src.data.guide_manager.GuideManager` | 攻略 CRUD 和查询 |
| `src.business.fetch_service.HeroFetchService` | 武将采集 QProcess 管理 |
| `src.business.guide_fetch_service.GuideFetchService` | 攻略生成 QProcess 管理 |
| `src.business.synergy_fetch_service.SynergyFetchService` | 相性获取 QProcess 管理 |
| `src.business.capture_service.CaptureService` | 截图业务编排 |
| `src.business.emulator_operation_service.EmulatorOperationService` | 配置页后台 ADB 操作 |
| `src.business.ocr_service.OcrService` | OCR 控制、模板管理、轮询 |
| `src.business.ocr_worker.OcrWorker` | 由 CaptureService 持有的唯一后台识别队列 |
| `src.config.env.get_mumu_config()` | 读取模拟器配置 |
| `src.config.env.save_env_file()` | 保存模拟器配置 |
| `src.capture.prober.*` | ADB/模拟器探测 |
| `src.capture.adb_screen.AdbCapture` | CaptureService 持有的唯一 ADB 会话 |
| `src.ocr.ocr_loader.get_template_manager()` | 仅由 OcrService 管理模板 |
| `src.ocr.recognizer.GeneralRecognizer` | OCR 预热 |
| `src.scraper.ai_utils.estimate_cost()` | AI 成本估算 |

---

## 八、函数清单总表

### MainWindow

| 函数 | 调用方（触发方式） | 被调用方 |
|------|-------------------|----------|
| `__init__()` | `main.py:main()` | 创建所有服务、`_setup_ui()`、`_load_data()` |
| `_load_data()` | `__init__()`, `_reload_data()` | `DataFacade.load_all()` |
| `_reload_data()` | 菜单"重新加载数据" | `_load_data()`, `_update_status()`, `HeroBrowser.reload_data()` |
| `_update_status()` | 各 fetch 完成回调 | `DataFacade.get_stats()`, `status_label.setText()` |
| `_request_fetch_all()` | 菜单 | `HeroFetchService.fetch_all()` |
| `_request_fetch_incremental()` | 菜单 | `HeroFetchService.fetch_incremental()` |
| `_request_fetch_specific()` | 菜单 | `HeroFetchDialog`, `fetch_specific(ids)` |
| `_request_guide_*()` | 菜单 | `AiGenerationWorkflow.request_guide_*()` |
| `_request_synergy_*()` | 菜单 | `AiGenerationWorkflow.request_synergy_*()` |
| `_open_settings()` | 菜单"配置→API 配置" | `SettingsDialog` |
| `_open_mumu_config()` | 菜单"配置→模拟器配置" | `MumuConfigDialog`, `save_env_file()`, `OcrService.start_poll()` |
| `_on_poll_capture()` | `OcrService.poll_tick` 信号 | 后台线程截图+OCR |
| `_on_poll_result()` | 自定义信号 | `RecommendationPanel.load_from_ocr()` |
| `_on_guides_generated()` | `AiGenerationWorkflow.guides_changed` | `_update_status()` |
| `_on_synergies_generated()` | `AiGenerationWorkflow.synergies_changed` | `HeroBrowser.refresh_synergies()`, `RecommendationPanel.refresh_synergies()`, `_update_status()` |

### AiGenerationWorkflow

| 函数 | 调用方（触发方式） | 被调用方 |
|------|-------------------|----------|
| `request_guide_all()` | 主窗口“全量获取”菜单 | `_get_heroes_as_dicts()`、`_start_guide_generation()`、`GuideFetchService.fetch_all()` |
| `request_guide_incremental()` | 主窗口“增量获取”菜单 | `GuideManager.list_guides()`、缺失筛选、`fetch_incremental(missing)` |
| `request_guide_specific()` | 主窗口“指定获取”菜单 | `GuideFetchDialog`、`_start_guide_generation()`、`fetch_specific()` |
| `request_synergy_pair()` | 主窗口“指定获取”菜单 | `SynergyPairDialog`、后端选择、`SynergyFetchService.fetch_pair()` |
| `request_synergy_single()` | 主窗口“选定武将”菜单 | `SynergySingleDialog`、后端选择、`fetch_single()` |
| `_on_guide_completed()` | `GuideFetchService.fetch_completed` | 关闭进度、`GuideManager.load()`、`guides_changed` |
| `_on_synergy_completed()` | `SynergyFetchService.fetch_completed` | 关闭进度、`SynergyManager.load()`、`synergies_changed` |

### HeroCardWidget

| 函数 | 说明 |
|------|------|
| `set_hero(hero or None)` | 设置 Hero → `_update_display()` |
| `set_confidence(conf)` | 设置推荐指数 0.0~1.0 |
| `set_synergies(pairs)` | 设置高相性组合展示（4 列 GridLayout） |
| `set_win_rate(rate)` | 设置胜率百分比 |
| `set_medal(rank 1/2/3)` | 设置 🥇🥈🥉 奖牌 |

### 对话框

| 对话框类 | 输入 | 输出 |
|----------|------|------|
| `BaseHeroSelectDialog` | 搜索文本、势力筛选、多/单选 | `selected_ids`, `selected_heroes`, `selected_hero` |
| `SettingsDialog` | API Key/URL/Model/限速/超时/重试 | 保存到 config.env |
| `MumuConfigDialog` | ADB 路径/端口/模板/OCR 配置 | config dict + 服务状态更新 |
| `BackendChooseDialog` | Token/费用估算 | `"api"` 或 `"browser"` |
| `CostConfirmDialog` | 遗留组件，当前 AI 流程未调用 | - |
| `GuideProgressDialog` | 总数量、子进程进度信号 | 实时进度条 + 完成/失败提示 |
| `RoiSelectorDialog` | 截图 QPixmap | ROI (x, y, w, h) |
| `HeroEditDialog` | Hero 对象 | 修改后的 Hero 对象 |
| `GuideEditDialog` | HeroGuide + HeroManager | 修改后的 HeroGuide 对象 |
| `HeroRelationSelectDialog` | HeroManager + 预选 ID | 按英雄 ID 稳定排序的 `selected_ids` |
| `SynergyEditDialog` | HeroManager + SynergyScore | 修改后的 SynergyScore 对象 |
