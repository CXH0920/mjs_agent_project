# 调用链路：UI 界面层

> 对应源码：`src/ui/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 信号连接以 `[signal] → slot` 标注，QProcess 子进程以虚线分隔。
> 与巅峰赛/实战配队相关的调用链路见 [call_graph_peak_combos.md](./call_graph_peak_combos.md)。

---

## 当前实现基线（6cbe8b6 / 2026-09-07）

```
MainWindow.__init__()
  -> AppServices(hero_manager, synergy_manager, guide_manager)  [组合根：可无头构造]
     -> DataFacade.from_managers() / DataFacade(...)
     -> HeroFetchService()
     -> GuideFetchService(self.data.guides)
     -> SynergyFetchService(self.data.synergies)
     -> ComboManager()
     -> AiGenerationWorkflow(...)
     -> CaptureService() / OcrService()
     -> PollCoordinator(capture, ocr, hero_names_provider)
     -> AnnouncementManager() / AnnouncementService(...)
  -> AppServices.attach(self)                       [统一挂载 QObject 父子 + AiGenerationWorkflow.set_window]
  -> 解包 _fetch_service / _capture_service / ... 到惯用属性
  -> _load_data() -> load_all()
  -> _setup_ui()
    -> HeroBrowser(hero_mgr, guide_mgr, synergy_mgr, combo_manager=...)
    -> RecommendationPanel(hero_mgr, synergy_mgr, guide_mgr, capture_svc, ocr_svc, combo_manager=...)
    -> PeakSelectPanel(capture_svc, ocr_svc, hero_names_provider, hero_mgr, win_rates_provider, pick_ranks_provider, combo_manager)
    -> MatchGuidePanel(hero_mgr, guide_mgr, capture_svc)
  -> _setup_status_bar()                            [StatusChips 常驻胶囊]
```

| 用户动作 | UI 入口 | 核心调用 | 刷新 |
|----------|---------|----------|------|
| 武将采集 | `_request_fetch_*()` | `HeroFetchService.fetch_*()` | `_reload_data()` |
| 攻略生成 | `_request_guide_*()` | `AiGenerationWorkflow.request_guide_*()` -> `GuideFetchService.fetch_*()` | `GuideManager.load()` + 状态栏统计刷新 |
| 相性生成 | `_request_synergy_pair/single/combos()` | `AiGenerationWorkflow.request_synergy_*()` -> `SynergyFetchService.fetch_pair/single/pairs_list()` | `SynergyManager.load()` + 浏览器、推荐页刷新 |
| 官方数据导入 | `_open_official_data_import()` | `OfficialDataImportDialog` -> `CaptureService` -> `OcrWorker(OfficialImportTask)` -> `OfficialDataImportService` | 暂停轮询，覆盖 2v2/放逐 CSV，弹窗退出后恢复轮询 |
| 实战配队维护 | 选将推荐“实战配队”横条 [管理] | `ComboManagementDialog` -> `ComboService.save_manual_combo()/delete_combo()` | `_refresh_combo_strip()` + 卡片角标 + 相性表 |
| 实战配队批量生成 | 菜单“数据 → 武将相性 → 实战配队生成” | `SynergyCombosDialog` -> `fetch_pairs_list(selected_pairs)` | 相性 JSON + 浏览器/推荐页刷新 |
| 截图、图片导入、轮询 | 推荐页或 `poll_tick` | `CaptureRequestLock` -> `CaptureService` -> `OcrWorker` | 推荐卡或对局攻略页 |
| 公告更新 | 菜单“数据 > 检查公告更新 / 更新武将数据” | `AnnouncementService.prepare_update_candidates()` -> `HeroUpdateConfirmDialog` -> 两阶段 `fetch_specific/incremental` | `_pending_update_phases` 队列串接 |

数据完整性问题存于 `self._data.last_load_report`；当前 UI 使用已恢复的内存数据，尚未提供报告查看或写回修复界面。服务完成状态以 CLI 退出码为准，不解析 `RESULT: FAIL=`。

武将浏览页已将可独立维护的编辑单元从 `hero_browser.py` 拆出：

| 模块 | 公开组件 | 职责 | 由谁调用 |
|------|----------|------|----------|
| `hero_edit_dialog.py` | `HeroEditDialog` | 编辑单个 `Hero` 的基础字段 | `HeroDetailPanel._on_info_edit()` |
| `guide_edit_dialog.py` | `GuideEditDialog` | 编辑 `HeroGuide` 的正文、要点和关系集合 | `HeroDetailPanel._on_guide_edit()` |
| `hero_relation_select_dialog.py` | `HeroRelationSelectDialog` | 搜索、势力筛选和多选攻略关系武将 | `GuideEditDialog._open_relation_selector()` |
| `synergy_edit_dialog.py` | `SynergyEditDialog` | 编辑一对武将的评分、维度和说明 | `HeroDetailPanel._on_synergy_edit()` |
| `combo_edit_dialog.py` | `ComboEditDialog` | 实战配队新增/编辑（双人选择 + 评级 + 座次 + note） | `ComboManagementDialog._on_add/_on_edit_selected` |

`hero_browser.py` 仍负责列表、详情状态和信号协调；持久化调用统一委托给 `DataMutationService` 与 `run_edit_dialog()`（`src.ui.shared.persist`）收敛“模态编辑 → 保存 → 失败重试”循环。三个详情 Tab 的构造与渲染委托 `hero_detail_views.py` 中的 `HeroInfoView`、`HeroGuideSummaryView` 和 `HeroSynergyView`。`HeroSynergyView` 数据源为 AI 相性 ∪ 实战配队的并集：有实战配队但未生成 AI 评分的配对也显示（综合评分列标“未生成”），同一配对多座次变体逐条成行。它导入上述公开对话框以保持原有外部导入兼容，但不再持有它们的表单构建逻辑。

跨页面共享骨架集中在 `src.ui.shared`：

| 模块 | 公开组件 | 用途 |
|------|----------|------|
| `master_detail.py` | `MasterDetailPane` | 主从列表骨架：QSplitter + 可选计数标签的左列表窗格（默认 220–360px、`sizes=(280, 720)`）+ 右侧 `widgetResizable` 无边框滚动详情区；`list_pane` / `count_label` / `list` / `detail_scroll` / `detail` / `detail_layout` 六个属性解包子控件供调用方接线 |
| `capture_lock.py` | `CaptureRequestLock` / `CaptureSource` | 截图/文件导入的单飞锁。`begin(source)` 抢占来源，`finish()` 释放并回传刚完成的来源；无锁回调（另一请求先释放或重复触发）返回 None |
| `persist.py` | `run_edit_dialog(dialog, persist, ...)` | 模态编辑 → 保存 → 失败重试循环；业务性失败（`OSError`/`ValueError`/`ValidationError`）可重试，非预期异常留堆栈后退出 |
| `combo_detail.py` | `show_combo_detail(parent, combo)` | 实战配队详情：2×2 号位示意 + 座次要求 + note 原文，选将推荐与巅峰赛共用 |
| `status_chips.py` | `StatusChips` | 底部状态栏的常驻胶囊（模拟器 ADB / OCR 轮询）；`mumu_config_requested` 信号回主窗口 |

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

Dialog / configuration / import / progress
  -> PageHeader(title, subtitle)
  -> DialogFooter(cancel, accept)
     -> set_busy(True, text) [禁用底栏重复确认与取消]
  -> show_toast(parent, message)
     -> ToastOverlay.show_message() [非模态、复用实例并重置计时器]

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
MainWindow.__init__(hero_manager, synergy_manager, guide_manager)
  -> AppServices(hero_manager, synergy_manager, guide_manager)  [组合根，可无头构造]
     -> DataFacade.from_managers() / DataFacade(heroes_file, ...)
     -> HeroFetchService()                                       [武将采集服务]
     -> GuideFetchService(self.data.guides)                       [攻略生成服务 + 注入 guide_mgr]
     -> SynergyFetchService(self.data.synergies)                  [相性获取服务]
     -> ComboManager()                                            [实战配队共享实例]
     -> AiGenerationWorkflow(hero_mgr, guide_mgr, synergy_mgr,
                             guide_fetch, synergy_fetch,
                             combo_manager=ComboManager())        [AI 任务工作流]
     -> CaptureService()                                          [截图服务]
     -> OcrService() + set_ocr_task_submitter(capture.submit_ocr_task)
     -> get_mumu_config() -> capture.update_config() / ocr.update_config()
     -> ocr.set_hero_names([...])                                 [设置武将名列表]
     -> PollCoordinator(capture, ocr, hero_names_provider)        [轮询编排]
     -> AnnouncementManager() / AnnouncementService(mgr, heroes)  [公告检查]
  -> AppServices.attach(self)                                     [统一 setParent + ai_workflow.set_window]
  -> 解包到 _fetch_service / _guide_service / _capture_service / _ocr_service / _poll_coordinator / ...
  -> AnnouncementService.check_started/connect
  -> AnnouncementService.check_finished/connect
  -> AnnouncementService.update_candidates_prepared -> _on_hero_update_prepared
  -> _connect_fetch_signals()
  -> _connect_capture_signals()
  -> AiGenerationWorkflow.status_changed -> _on_fetch_status()
  -> AiGenerationWorkflow.guides_changed -> _on_guides_generated()
  -> AiGenerationWorkflow.synergies_changed -> _on_synergies_generated()
  -> setWindowTitle(), setMinimumSize(960, 640), resize(1100, 760)
  -> load_app_icon() -> setWindowIcon()
  -> _setup_actions() + _setup_menu()                             [共享 QAction]
  -> _load_data()
     -> self._data.load_all() -> report
     -> [missing_reference 存在] QMessageBox.question("是否修复并保存")
        -> Yes -> DataMutationService.repair_missing_references() -> load_all()
  -> _setup_ui()
     -> NavigationRail(icons, central)                             [左侧导航]
     -> workspace = QWidget("workspaceShell")
        -> NoticeBanner("公告更新", tone=INFO)                      [工作区顶部横幅]
        -> QTabWidget("workspaceTabs").tabBar().hide()              [主导航，隐藏 TabBar]
           -> QWidget("libraryPage")                                [Tab 0: 资料库]
              -> QTabWidget("librarySectionTabs")                  [浅色二级切换器]
                 -> HeroBrowser(heroes, guides, synergies,
                                combo_manager=_combo_manager)       [武将资料]
                 -> CardManagementPanel(CardCatalogService())       [卡牌图鉴]
           -> RecommendationPanel(heroes, synergies, guides,
                                  capture_svc, ocr_svc,
                                  combo_manager=_combo_manager)    [Tab 1: 选将推荐]
           -> PeakSelectPanel(capture_svc, ocr_svc, hero_names_provider,
                              hero_mgr, win_rates_provider,
                              pick_ranks_provider,
                              combo_manager=_combo_manager)         [Tab 2: 巅峰赛选将]
           -> MatchGuidePanel(heroes, guides, capture_svc)         [Tab 3: 对局攻略]
           -> [is_full_build] RagMaintenancePanel(PROJECT_ROOT, hero_names) [Tab 4: 知识库维护]
  -> _setup_status_bar()
     -> QLabel + QProgressBar + StatusChips()
     -> StatusChips.mumu_config_requested -> _open_mumu_config
  -> _update_status()
  -> PollCoordinator.sync_with_connection()
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
  GuideFetchService.error_occurred    → workflow._on_guide_error() → QMessageBox（详情列出 failed_items 失败武将清单 + 详情按钮翻译过滤器）
  GuideFetchService.progress_output/value → workflow._on_guide_progress*() → GuideProgressDialog

  SynergyFetchService.status_changed  → workflow.status_changed → MainWindow._on_fetch_status()
  SynergyFetchService.fetch_completed → workflow._on_synergy_completed()
    → GuideProgressDialog.on_process_finished()
    → [success] SynergyManager.load() → workflow.synergies_changed
      → MainWindow._on_synergies_generated() → 浏览器/推荐页刷新
  SynergyFetchService.error_occurred  → workflow._on_synergy_error() → QMessageBox（详情列出 failed_items 失败相性对清单 + 详情按钮翻译过滤器）
  SynergyFetchService.progress_output/value → workflow._on_synergy_progress*() → GuideProgressDialog

_connect_capture_signals():
  CaptureService.status_changed         → _on_fetch_status            [截图服务状态文字]
  CaptureService.capture_failed         → _on_capture_failed          [截图失败原因]
  CaptureService.connection_changed     → _on_capture_connection_changed
    → _update_emulator_status(state, detail)  [StatusChips.set_emulator_state]
    → [state == "connected"] CaptureService.warmup_ocr_model()
    → PollCoordinator.sync_with_connection()
  CaptureService.ocr_warmup_state_changed → _on_ocr_warmup_state_changed
  PollCoordinator.poll_state_changed  → _update_poll_status          [StatusChips.set_poll_state]
  PollCoordinator.poll_result_ready   → _on_poll_result              [已提交状态的结果]

PollCoordinator._consume_poll_result(result):
  → PollResult.from_raw(result)                          [兼容旧版 dict]
  → [generation != ocr.poll_generation] return           [丢弃过期结果]
  → [capture is not None and capture is not current] return  [会话已切换]
  → [RETRYABLE_CONNECTION/CAPTURE and capture] CaptureService.sync_poll_connection_state()
  → OcrService.complete_poll(generation, outcome, detail) [完成轮询状态迁移]
  → poll_result_ready.emit(poll_result)                  [通知界面]
```

| 函数 | 所在类 | 说明 |
|------|--------|------|
| `_connect_fetch_signals()` | `MainWindow` | 连接 HeroFetchService 三个信号（status / completed / progress / error） |
| `AiGenerationWorkflow._connect_services()` | `AiGenerationWorkflow` | 连接 GuideFetchService、SynergyFetchService 的状态、完成、取消、错误和进度信号 |
| `_connect_capture_signals()` | `MainWindow` | 连接 CaptureService 状态/失败/连接变化、OcrService 预热、PollCoordinator 状态与结果 |
| `StatusChips.set_emulator_state` / `set_poll_state` | `StatusChips` | 按内部 `_EMULATOR_STYLES` / `_POLL_STYLES` 常量表渲染胶囊，点击发 `mumu_config_requested` |

---

## 二、菜单操作触发链路

### 官方数据导入

```
菜单「数据 → 官方数据导入」clicked
  -> MainWindow._open_official_data_import()
    -> [轮询活跃] OcrService.stop_poll()
    -> OfficialDataImportDialog(capture_service, self).exec()
      -> 用户选择 2v2 和/或武将放逐图片
      -> _start_import()
        -> 禁用导入/取消按钮，显示“正在准备导入”不定进度条
        -> CaptureService.submit_official_import(paths)
          -> OcrWorker.submit(OfficialImportTask)
          -> [signal] official_import_progress(status, 0, 0)
             -> _on_progress_changed() -> QProgressBar.setRange(0, 0)
          -> [signal] official_import_progress(status, current, total)
             -> _on_progress_changed() -> 显示 current / total
          -> [signal] official_import_completed(summaries)
             -> _on_completed() -> show_toast(summary) -> accept()
          -> [signal] official_import_failed(message)
             -> _on_failed() -> 恢复按钮、隐藏进度条、显示错误
    -> [finally 且原轮询活跃] PollCoordinator.sync_with_connection()
```

对话框只负责文件选择、按钮状态和进度显示；表格裁剪、名称候选决策、待复核和 CSV 写入均位于业务服务层。任务运行时 `reject()` 不关闭对话框。官方任务整批占用唯一 OCR worker，普通任务留在 FIFO 队列等待。

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
        -> BackendChooseDialog(estimation, title, parent)        [选择 API/浏览器 + 语料增强]
          -> [accepted] (backend, use_rag) = dialog.get_selected_backend(), dialog.get_selected_rag()
            -> GuideProgressDialog(hero_count, parent)           [创建进度对话框]
            -> GuideFetchService.fetch_all(heroes, backend, use_rag)
            -> GuideProgressDialog.exec()                        [模态等待]

菜单「数据 → 攻略获取 → 增量获取」
  -> MainWindow._request_guide_incremental()
    -> AiGenerationWorkflow.request_guide_incremental()
      -> HeroManager.list_heroes() + GuideManager.list_guides() [对比已有攻略]
      -> 筛选: 已有攻略 → 跳过; 无攻略 → missing
      -> [无缺失] status_changed("所有武将已有攻略，无需生成")
      -> [有缺失] _start_guide_generation(missing, "incremental", ...)
        -> GuideFetchService.fetch_incremental(missing, backend, use_rag)
        -> GuideProgressDialog(len(missing))                     [总数与实际任务一致]

菜单「数据 → 攻略获取 → 指定获取」
  -> MainWindow._request_guide_specific()
    -> AiGenerationWorkflow.request_guide_specific()
      -> GuideFetchDialog(hero_manager, guide_manager, parent)   [默认筛选未生成]
        -> HeroManager.list_heroes() + GuideManager.get_guide()
        -> 状态：未生成 / 待更新 / 已有攻略
      -> _start_guide_generation(selected, "specific", ...)
```

GuideFetchService [signal] fetch_completed
  -> AiGenerationWorkflow._on_guide_completed(success, message)
    -> GuideProgressDialog.on_process_finished(success, message)
    -> [success] GuideManager.load() -> guides_changed
      -> MainWindow._on_guides_generated() -> _update_status()
    -> [failure] QMessageBox(Critical)：详情列出 GuideFetchService.failed_items 失败武将清单
```

| 函数 | 菜单路径 | 调用链 |
|------|----------|--------|
| `MainWindow._request_guide_*()` | 数据→攻略获取 | 仅委托 `AiGenerationWorkflow.request_guide_*()` |
| `request_guide_all()` | 全量获取 | `_get_heroes_as_dicts()` → `_start_guide_generation()` → `GuideFetchService.fetch_all()` |
| `request_guide_incremental()` | 增量获取 | `GuideManager.list_guides()` → 缺失筛选 → `fetch_incremental(missing)` |
| `request_guide_specific()` | 指定获取 | `GuideFetchDialog(HeroManager, GuideManager)` → 状态筛选 → `_start_guide_generation()` → `fetch_specific()` |

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
      -> estimate_item_cost(pair_count, "synergy")              [AI 成本估算]
      -> _choose_backend(title, estimation) -> BackendChooseDialog
        -> 返回 (backend, use_rag)
      -> _start_synergy_generation(pair_count, title, ...)
        -> GuideProgressDialog(pair_count, title, parent)
        -> SynergyFetchService.fetch_pair(selected, backend, use_rag=use_rag)
      -> 写入 temp JSON
      -> _start_process(["-m", "src.scraper.ai_batch", "--synergy-pair", tmp])
        -> GuideProgressDialog.exec()

菜单「数据 → 武将相性 → 选定武将」
  -> MainWindow._request_synergy_single()
    -> AiGenerationWorkflow.request_synergy_single()
      -> SynergySingleDialog(hero_manager, parent)               [选 1 武将]
       -> BaseHeroSelectDialog(SINGLE mode → 单选)
      -> estimate_item_cost(pair_count, "synergy")              [AI 成本估算]
      -> _choose_backend(title, estimation) -> BackendChooseDialog
      -> SynergyFetchService.fetch_single(hero, all_heroes, backend, use_rag=use_rag)
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

### 3.1 手动识别与仅保存截图

```
RecommendationPanel._on_recognize_current()                    [「识别当前阵容」]
  -> [capture_lock.current is not None] return                    [另一请求在途]
  -> [未配置 ADB] request_mumu_config.emit() -> _open_mumu_config()
  -> _begin_capture_request("adb_recognize")
     -> CaptureRequestLock.begin(CaptureSource.ADB_RECOGNIZE)   [抢占来源]
     -> _set_capture_controls_enabled(False)                       [禁用识别/导入/重建]
     -> _set_page_status("正在识别当前阵容...", TONE_INFO)
  -> CaptureService.do_capture(hero_names, force_ocr=True)
  -> capture_completed -> _on_capture_result(result)
     -> source = _capture_lock.finish()                            [释放锁并回传来源]
     -> [source is None] return                                     [过期回调]
     -> [source == "adb_save"] return                               [仅复位控件]
     -> ocr_results = result.get("ocr_results")
     -> [ocr_results] load_from_ocr(ocr_results)
     -> [未匹配] _show_error_notice("未识别到选将页面", source)

RecommendationPanel._on_save_screenshot()                     [「更多 > 保存截图」]
  -> [capture_lock.current is not None] return
  -> [未配置 ADB] request_mumu_config.emit()
  -> _begin_capture_request("adb_save")
  -> CaptureService.do_capture(perform_ocr=False)
  -> capture_completed -> 仅复位控件，不更新推荐结果
```

### 3.2 从文件导入

```
RecommendationPanel._on_import_from_file()                     [「从图片导入」按钮]
  -> [capture_lock.current is not None] return
  -> [无 capture_service] _show_error_notice("图片导入不可用")
  -> SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
  -> QFileDialog.getOpenFileName(...)                           [选择图片文件]
  -> hero_names = hero_mgr.list_heroes()
  -> _begin_capture_request("file")                             [CaptureRequestLock.begin(FILE)]
  -> CaptureService.do_capture_from_file(file_path, hero_names)
    -> QTimer.singleShot(0, _execute_file_ocr)
       -> PIL.Image.open(file_path)
       -> _queue_capture_ocr()
          -> submit_ocr_task() -> OcrWorker.submit(OcrTask)
             -> OcrWorker._execute() -> 模板匹配 -> OCR
          -> _on_ocr_task_completed() -> capture_completed
  -> [信号] → _on_capture_result() → load_from_ocr()

RecommendationPanel._on_capture_failed(message)
  -> source = _capture_lock.finish()                            [释放锁]
  -> _show_error_notice(titles[source], message, source)         [按来源分派按钮]
  -> _retry_last_action()                                       [按 _last_failed_source 重放]
```

### 3.3 对局攻略卡片与导入链路

```
MatchGuidePanel.__init__(hero_mgr, guide_mgr, capture_service, parent)
  -> LineupState()                                               [四个空槽位与确认规则]
  -> MatchAnalysisView(hero_mgr, guide_mgr)                      [总览/我方/敌方/详情页]
  -> MatchHeroCard × 4
  -> QSplitter(420/580)                                          [两侧不可折叠且禁用横向滚动]
  -> self._capture_lock = CaptureRequestLock()                   [单飞锁]

MatchHeroCard._portrait [左键双击]
  -> MatchHeroCard._on_hero_double_clicked()
  -> MatchGuidePanel._show_skill_popup(hero_id)
  -> src.ui.shared.hero_dialogs.HeroSkillDialog(hero).exec()

MatchGuidePanel._on_recognize_current()
  -> [capture_lock.current is not None] return                    [另一请求在途]
  -> [未配置 ADB] request_mumu_config → MainWindow._open_mumu_config()
  -> [不 begin_capture_request("adb_recognize")] return
  -> CaptureService.do_capture(template_name="match_guide", force_ocr=True)
  -> CaptureService.capture_completed → MatchGuidePanel._on_capture_result(result)
     -> _capture_lock.finish() → source or None
     -> [source == "adb_save"] return
     -> load_from_ocr(result["ocr_results"])

MatchGuidePanel._on_import_from_file()
  -> QFileDialog.getOpenFileName()
  -> _capture_lock.begin(CaptureSource.FILE)                      [抢占来源]
  -> CaptureService.do_capture_from_file(
       file_path, template_name="match_guide", force_ocr=True)
  -> _execute_file_ocr() → _queue_capture_ocr() → OcrWorker → _on_capture_result()
  -> load_from_ocr(ocr_results)

MatchGuidePanel.load_from_ocr()
  -> LineupState.load_from_ocr()                                 [确认名称与待定候选、队伍标签和主将初值]
  -> MatchHeroCard.set_hero()/set_side()                          [卡片重绘]
  -> MatchAnalysisView.render_unconfirmed()

MatchGuidePanel.update_block(index, task_result)                  [轮询入口，来自 MainWindow]
  -> task_result.ocr_results → load_from_ocr()

MatchGuidePanel._set_side() / _set_ally_leader()
  -> LineupState.set_side() / set_ally_leader()                  [两名我方、两名敌方限制]
  -> 取消已确认状态 → 卡片与确认提示重绘

MatchGuidePanel._confirm_lineup()
  -> LineupState.confirm()                                       [四个名称均确认、武将不同且敌我各两名]
  -> MatchAnalysisService.analyze(allies, enemies)
  -> MatchAnalysisView.render_analysis()
```

对局攻略导入复用 `CaptureService` 的异步采集接口，但通过 `template_name` 使用独立模板；新结果先替换 `LineupState`、清除旧分析并回到总览。确认区固定在左栏顶部，卡片重排只影响独立滚动区。阵容状态不依赖 Qt，分析视图不修改阵容，两者可分别测试和维护。

### 3.4 轮询截图链路（关键：跨线程）

轮询匹配成功后的页面跳转采用边沿触发，武将选择与对局攻略任务分别维护冷却；`PollCoordinator` 在后台线程执行截图 + OCR 与结果过滤，通过跨线程信号送回 GUI 线程，`MainWindow._on_poll_result()` 只消费已完成状态迁移的 `PollResult`。

```text
_on_poll_result(result)
  -> PollResult.from_raw(result)                                  [兼容旧版 dict]
  -> task_results: dict[str, PollTaskResult]
  -> task_results["hero_selection"].outcome == MATCHED
     -> OcrService.set_task_cooldown("hero_selection", mumu_hero_selection_cooldown)
     -> OcrService.clear_task_cooldown("match_guide")
     -> OcrService.activate_task("match_guide")                    [开启新一轮对局攻略自动跳转]
     -> _match_guide_activated_at = time.monotonic()
     -> _match_guide_page_active = False
     -> [首次] _selection_page_active = True
        -> [mumu_ocr_auto_switch_tab] _tabs.setCurrentWidget(_recommendation)
     -> [ocr_results] RecommendationPanel.load_from_ocr()
  -> task_results["hero_selection"].outcome == HEALTHY_NO_MATCH
     -> _selection_page_active = False
  -> task_results["hero_selection"].outcome == TEMPLATE_MISSING
     -> OcrService.deactivate_task("hero_selection")
  -> task_results["match_guide"].outcome == MATCHED
     -> OcrService.deactivate_task("match_guide")                  [命中即释放，避免同轮重复抢页]
     -> _match_guide_activated_at = None
     -> [首次] _match_guide_page_active = True
        -> [mumu_ocr_auto_switch_tab] _tabs.setCurrentWidget(_match_guide)
     -> MatchGuidePanel.update_block(0, task_result)
  -> task_results["match_guide"].outcome == HEALTHY_NO_MATCH
     -> _deactivate_match_guide_if_idle()                          [激活超 90s 仍无命中即失活]
  -> task_results["match_guide"].outcome == TEMPLATE_MISSING
     -> OcrService.deactivate_task("match_guide")
     -> _match_guide_activated_at = None
```

轮询冷却期间的重复匹配不会重复抢占用户当前页面；截图为空、图像截断等可重试结果也不会重置选将页面状态。`MATCH_GUIDE_IDLE_TIMEOUT_SECONDS=90` 覆盖进局加载动画窗口，超过该阈值仍无命中即认为用户未进对局，停止非对局页面（如巅峰赛后回大厅）上的空转 OCR。对局攻略的匹配结果在后台线程经 `PollCoordinator._validate_match_guide_result()` 校验：只有 `MATCHED` 且已确认角色数 `>= MATCH_GUIDE_MIN_CONFIRMED_NAMES=3` 才视作命中，不足时降级为 `HEALTHY_NO_MATCH`。

```
                                                     [主线程]
OcrService.poll_tick  [signal, QTimer 驱动]
  → PollCoordinator._on_poll_tick()
    -> CaptureService.start_ocr_worker()                          [确保 OcrWorker 已启动]
    -> generation = OcrService.begin_poll()                       [轮询代数递增]
    -> [generation is None] return
    -> task_names = OcrService.due_poll_tasks()                   [任务独立冷却]
    -> [task_names 空] OcrService.complete_poll(generation, HEALTHY_NO_MATCH)
    -> [capture 未配置] _poll_result_received.emit(PollResult(PREREQUISITE_UNCONFIGURED))
    -> threading.Lock.acquire(blocking=False)
    -> [已有一轮运行中] complete_poll(generation, RETRYABLE_CAPTURE)
    -> threading.Thread(target=do_poll_work, daemon=True).start()
                         ↓
                  [后台线程]
                  -> CaptureService.capture_for_poll(capture)      [截图]
                  -> [失败] PollResult(RETRYABLE_CONNECTION/CAPTURE)
                  -> [成功] for each task_name in task_names:
                     -> CaptureService.submit_ocr_task(
                          image, hero_names, template_name=task_name,
                          recognize=True,
                          fallback_on_template_miss=(task_name=="match_guide"),
                          allow_result_reuse=True)
                     -> wait_for_ocr_task(ocr_task, cancel_event)  [POLL_OCR_WAIT_TIMEOUT=10s]
                     -> [task_name == "match_guide"] _validate_match_guide_result()
                     -> task_results[task_name] = PollTaskResult
                  -> outcome = RETRYABLE_OCR if has_retryable_error
                             else MATCHED if has_match
                             else HEALTHY_NO_MATCH
                  -> _poll_result_received.emit(PollResult(generation, outcome, task_results))
                  -> Lock.release()
                         ↓
                  [主线程接收]
PollCoordinator._consume_poll_result() → complete_poll() → poll_result_ready
MainWindow._on_poll_result(result)                              [仅界面更新]
  -> [hero_selection 命中] RecommendationPanel.load_from_ocr() + activate_task("match_guide")
  -> [match_guide 命中] MatchGuidePanel.update_block() + deactivate_task("match_guide")
  -> [任务级] 按 outcome 分派状态迁移
```

| 函数 | 所在类 | 说明 |
|------|--------|------|
| `_on_poll_tick()` | `PollCoordinator` | QTimer 触发，启动后台线程 |
| `do_poll_work()` | `PollCoordinator` (闭包) | 后台线程：截图 + 各任务 OCR + 结果过滤 |
| `_validate_match_guide_result()` | `PollCoordinator` | 确认角色数 >= 3 才视作命中，不足降级 HEALTHY_NO_MATCH |
| `_consume_poll_result()` | `PollCoordinator` | 丢弃过期 generation，完成轮询状态迁移后通知界面 |
| `_on_poll_result()` | `MainWindow` | 主线程：按 task_results 消费结果，更新页面与任务级状态 |
| `_deactivate_match_guide_if_idle()` | `MainWindow` | 激活后超过 90s 空闲即失活对局攻略任务 |
| `_on_peak_exited_to_match()` | `MainWindow` | 巅峰赛牌面自动退出：激活对局攻略任务，等待用户进入对局页 |

> **关键架构决策：** 手动 ADB 截图（`do_capture`）在 `QTimer.singleShot(0)` 回调中运行，仍在主线程；文件导入和轮询的模板匹配/OCR 均提交到唯一 `OcrWorker`。轮询的 ADB 截图与 OCR 全部在 `PollCoordinator` 内部由 `threading.Thread(target=do_poll_work, daemon=True)` 运行在后台线程，并以 PySide6 信号把结构化 `PollResult` 传回主线程；`_consume_poll_result()` 负责丢弃过期 `generation`、完成轮询状态迁移后再发 `poll_result_ready` 通知界面。

---

## 四、推荐面板链路

### 4.1 OCR 结果导入推荐面板

```
RecommendationPanel.load_from_ocr(ocr_results)                  [OCR 结果 list[dict]]
  -> [ocr_results 空] _show_error_notice("未识别到选将阵容")
  -> [index 越界过滤后为空] _show_error_notice("未识别到有效武将")
  -> self._ocr_results_by_slot = {item["index"]: item for item in data}
  -> update_recommendations(data)
       -> self._ocr_mode = True
       -> self._current_hero_ids.clear()
       -> _show_cards()
       -> RecommendationService.load()                          [一次性读取胜率与推荐指数快照]
       -> 第一遍：遍历所有项，收集已确认武将的 hero.id 到 _current_hero_ids
              [确保相性过滤时 8 个 ID 齐全]
       -> 第二遍：按槽位填充卡片
          -> name 为空 → card.set_pending_name(raw_name, candidates)   [不加载任何武将数据]
          -> hero = self._hero_mgr.get_hero_by_name(name)
          -> [找到] card.set_hero(hero)
                  self._load_card_stats(idx-1, name, snapshot)         [胜率与推荐指数]
                  self._load_card_guide_state(idx-1, hero.id)          [攻略可用性]
          -> [未找到] card.set_unrecognized_name(name)
       -> 第三遍：所有 ID 齐全后统一 _load_real_synergies()
       -> _refresh_combo_strip()                                  [实战配队横条 + 卡片角标]
       -> _apply_medal_rankings()                                 [Top 3 奖牌]
  -> _update_recognition_status(confirmed_count)
```

待确认卡片的“确认”按钮打开候选白名单模式的 `BaseHeroSelectDialog(allowed_names=candidates)`；人工选择后 `resolution="manual"` 写回 `_ocr_results_by_slot`，再重跑 `update_recommendations()`。对局攻略的“替换”入口使用相同白名单能力，候选为空时仍允许从完整本地词表选择。

### 4.2 截图单飞锁与错误反馈

```
RecommendationPanel._on_recognize_current()
  -> [capture_lock.current is not None] return                    [另一请求在途，忽略本次触发]
  -> [未配置 ADB] request_mumu_config.emit() → MainWindow._open_mumu_config()
  -> _capture_lock.begin(CaptureSource.ADB_RECOGNIZE)             [抢占来源]
  -> _set_capture_controls_enabled(False)                          [禁用识别/导入/重建控件]
  -> _set_page_status("正在识别当前阵容...", TONE_INFO)
  -> CaptureService.do_capture(hero_names, force_ocr=True)

RecommendationPanel._on_capture_result(result)
  -> source = _capture_lock.finish()                              [释放锁并回传来源]
  -> [source is None] return                                      [过期回调，直接忽略]
  -> [source == "adb_save"] return                                [仅复位控件，不更新推荐结果]
  -> ocr_results = result.get("ocr_results")
  -> [ocr_results] load_from_ocr(ocr_results)
  -> [未匹配到选将页] _show_error_notice("未识别到选将页面", source)

RecommendationPanel._on_capture_failed(message)
  -> source = _capture_lock.finish()                              [同上]
  -> _show_error_notice(titles[source], message, source)          [按来源分派按钮]

RecommendationPanel._retry_last_action()
  -> source = _last_failed_source
  -> _clear_error_notice()
  -> [source == "adb_recognize"] _on_recognize_current()
  -> [source == "adb_save"] _on_save_screenshot()
  -> [source == "file"] _on_import_from_file()
  -> [source == "rebuild"] _rebuild_recommendation_indexes()
```

`CaptureRequestLock.begin()` 已有在途时返回 False（调用方直接忽略本次触发），`finish()` 空闲时返回 None（回调来自过期请求）。选将推荐与对局攻略各自持一个锁，共享同一 `CaptureService`。

### 4.3 实战配队横条与卡片角标

```
RecommendationPanel.update_recommendations(data)
  -> ...
  -> _refresh_combo_strip()                                       [当前 8 人匹配实战配队]
     -> self._matched_combos = []
     -> [len(_current_hero_ids) >= 2] for combo in _combo_mgr.list_combos():
        -> combo.hero1_id in _current_hero_ids AND combo.hero2_id in _current_hero_ids
        -> self._matched_combos.append(combo)
     -> sort by (-rating, hero1_name, hero2_name)
     -> _update_combo_badges()                                     [头像右上角「实战 ★N」金色徽章]
        -> [遍历 combo.hero1_id / combo.hero2_id] best_rating[hero_id] = max(...)
        -> [遍历 card] card.set_combo_badge("实战 ★N" if rating else None)
     -> _render_combo_chips()                                      [FlowLayout 芯片]
        -> [清空] _combo_chip_flow.takeAt + deleteLater
        -> [遍历 combo] QPushButton("★N hero1[seats] + hero2[seats]")
           -> setObjectName("recommendationComboChip")
           -> tooltip = combo_tooltip(combo)
           -> clicked.connect(_show_combo_detail(combo))
        -> _combo_strip.setVisible(bool(_matched_combos))

HeroCardWidget.set_combo_badge(text)
  -> _combo_badge.setText(text or "")
  -> _combo_badge.setVisible(bool(text))

show_combo_detail(parent, combo)                                  [shared/combo_detail.py]
  -> PageHeader(f"实战 ★{rating}", f"{hero1} + {hero2}")
  -> QGridLayout 2×2: 1/2/3/4 号位 → 武将名（或 --）
  -> 座次要求: hero1[seats] · hero2[seats]
  -> note 原文（可选）
  -> DialogFooter("关闭")
```

### 4.4 相性加载与展示

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
  -> [加载异常] logger.warning + card.set_synergies([("加载失败", "--"), ...])
  -> [无数据] card.set_synergies([("等待数据", "--"), ...])
```

### 4.5 默认加载（启动/重新加载）

```
RecommendationPanel._load_default_heroes()
  -> self._ocr_mode = False
  -> self._current_hero_ids.clear()
  -> [遍历 8 个] card.set_hero(None)
  -> _refresh_combo_strip()                                     [空匹配，隐藏横条]
  -> _show_empty_state()                                        [不预填业务数据]
```

### 4.6 奖牌计算

```
RecommendationPanel._apply_medal_rankings()
  -> RecommendationData.rank_win_rates(card.hero_name × 8)     [直接使用数值快照]
  -> rank 1/2/3 → card.set_medal(rank)                          [固定“胜率 TOP N”徽章]
```

| 函数 | 所在类 | 调用方 | 被调用方 |
|------|--------|--------|----------|
| `load_from_ocr(results)` | `RecommendationPanel` | `_on_capture_result()`, `_on_poll_result()` | `get_hero_by_name()`, `set_hero()`, `_load_real_synergies()` |
| `_load_default_heroes()` | `RecommendationPanel` | 页面清空 | `set_hero(None)`, `_refresh_combo_strip()`, `_show_empty_state()` |
| `_load_real_synergies(idx, id)` | `RecommendationPanel` | `update_recommendations()`, `refresh_synergies()` | `list_synergies_for_hero()`, `get_hero()`, `set_synergies()` |
| `_load_card_stats(idx, name, data)` | `RecommendationPanel` | `update_recommendations()` | `set_win_rate()`, `set_recommendation_index()` |
| `_apply_medal_rankings()` | `RecommendationPanel` | `update_recommendations()`, `_rebuild_recommendation_indexes()` | `rank_win_rates()`, `set_medal()` |
| `_refresh_combo_strip()` | `RecommendationPanel` | `update_recommendations()`, `_on_open_combo_management` | `_update_combo_badges()`, `_render_combo_chips()` |
| `_begin_capture_request(source)` | `RecommendationPanel` | 识别/保存/导入/重建按钮 | `_capture_lock.begin()`, `_set_capture_controls_enabled()`, `_set_page_status()` |
| `_finish_capture_request()` | `RecommendationPanel` | `_on_capture_result()`, `_on_capture_failed()` | `_capture_lock.finish()`, `_set_capture_controls_enabled()` |
| `_show_error_notice(title, msg, source)` | `RecommendationPanel` | `_on_capture_failed()`, `load_from_ocr()` 空结果, `_rebuild_recommendation_indexes()` 失败 | 显示 NoticeBanner + [重试/重新选择] + [打开模拟器配置] |
| `set_hero(hero)` | `HeroCardWidget` | 外部 | `_update_display()`, `_load_portrait()`, `_update_confidence_display()` |
| `set_pending_name(raw_name, candidates)` | `HeroCardWidget` | `update_recommendations()` 未确认槽位 | `set_hero(None)`, 显示候选数量与候选名单 Tooltip |
| `set_unrecognized_name(name)` | `HeroCardWidget` | `update_recommendations()` | `set_hero(None)`, 显示原始名称与 danger 徽章 |
| `set_combo_badge(text)` | `HeroCardWidget` | `_update_combo_badges()` | 头像右上角「实战 ★N」金色徽章 |
| `set_recommendation_stale(stale)` | `HeroCardWidget` | `_set_index_stale_notice()`, 官方导入后 | 卡片状态 indexStale |
| `refresh_faction_color()` | `HeroCardWidget` | `refresh_faction_colors()` | `_update_display()` |
| `hero_id` / `hero_name` | `HeroCardWidget` | `RecommendationPanel` | 只读卡片身份状态 |
| `set_synergies(pairs)` | `HeroCardWidget` | `_load_real_synergies()` | 更新预创建的相性标签，不重建布局 |
| `set_win_rate(rate)` | `HeroCardWidget` | `_load_card_stats()`、指数重建 | 设置历史单将胜率文本 |
| `set_medal(rank)` | `HeroCardWidget` | `_apply_medal_rankings()` | 设置 `rank` 属性与固定 TOP 徽章 |
| `_load_portrait(name)` | `HeroCardWidget` (static) | `_update_display()` | `QPixmap(str(IMAGES_DIR/name.ext))` KeepAspectRatio |

### 4.7 攻略与技能弹出

```
HeroCardWidget.guide_clicked [signal] → RecommendationPanel._show_guide_popup(hero_id)
  -> self._hero_mgr.get_hero(hero_id)                           [查询 Hero]
  -> self._guide_mgr.get_guide(hero_id)                         [查询 HeroGuide]
  -> GuideDetailDialog(hero_name, guide, hero_mgr, parent)      [创建攻略对话框]
     -> [无 guide] 显示 "暂无攻略数据"
     -> [有 guide] 渲染:
        -> 外层 QScrollArea（摘要、关系标签与正文预览）
        -> 摘要区 + 单个 QTextBrowser 正文阅读区
        -> 核心要点 (key_points)
        -> 新手提示 (tips_for_beginners)
        -> 劣势对局 (weak_against_type) → 流式标签
        -> 优势对局 (strong_against_type) → 流式标签
        -> 对抗建议 (counter_strategy) → 文本
        -> 搭配推荐 (synergizes_with) → 可点击流式标签
        -> 攻略正文 (description) → render_markdown() → DoubleClickTextBrowser    [双击打开 GuideMarkdownDialog]
  -> dialog.hero_requested.connect(open_related)                 [切换关联武将]
  -> GuideDetailDialog.exec()                                   [模态展示]

HeroCardWidget.hero_double_clicked [signal] → RecommendationPanel._show_skill_popup(hero_id)
  -> self._hero_mgr.get_hero(hero_id)
  -> HeroSkillDialog(hero, parent=self.window())
  -> HeroSkillDialog.exec()                                     [模态展示]

HeroCardWidget.candidate_confirm_requested [signal] → RecommendationPanel._confirm_candidate(slot)
  -> item = self._ocr_results_by_slot[slot]
  -> dialog = BaseHeroSelectDialog(allowed_names=candidates, SINGLE)
  -> [accepted] item.update(name=hero.name, candidates=[hero.name], resolution="manual")
  -> update_recommendations(list(_ocr_results_by_slot.values()))
```

`RecommendationPanel` 仅协调数据和信号：它创建 `HeroCardWidget`、连接卡片信号并打开 `GuideDetailDialog` / `HeroSkillDialog`。卡片绘制/奖牌样式位于 `hero_card_widget.py`，攻略摘要位于 `guide_detail_dialog.py`，安全 Markdown 渲染集中在 `markdown_renderer.py`；卡片状态通过 `cardState` 动态属性（empty / pending / unknown / ready / indexStale / insufficientData / missingGuide / missingPortrait）驱动 QSS 匹配。

### 4.8 函数清单总表（推荐面板）

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `__init__()` | `recommendation_panel.py` | `MainWindow._setup_ui()` | `_setup_ui()`, `_connect_capture_signals()`, `_show_empty_state()` |
| `update_recommendations(data)` | `recommendation_panel.py` | `load_from_ocr()`, `_confirm_candidate()` | 三遍扫描 + `_refresh_combo_strip()` + `_apply_medal_rankings()` |
| `load_from_ocr(results)` | `recommendation_panel.py` | `_on_capture_result()`, `_on_poll_result()` | `update_recommendations()`, `_update_recognition_status()` |
| `_on_recognize_current()` | `recommendation_panel.py` | 识别按钮 | `_capture_lock.begin(ADB_RECOGNIZE)`, `CaptureService.do_capture(force_ocr=True)` |
| `_on_save_screenshot()` | `recommendation_panel.py` | 更多菜单 | `_capture_lock.begin(ADB_SAVE)`, `CaptureService.do_capture(perform_ocr=False)` |
| `_on_import_from_file()` | `recommendation_panel.py` | 更多菜单 | `_capture_lock.begin(FILE)`, `CaptureService.do_capture_from_file()` |
| `_on_capture_result(result)` | `recommendation_panel.py` | `CaptureService.capture_completed` | `_capture_lock.finish()`, `load_from_ocr()` |
| `_on_capture_failed(msg)` | `recommendation_panel.py` | `CaptureService.capture_failed` | `_capture_lock.finish()`, `_show_error_notice()` |
| `_confirm_candidate(slot)` | `recommendation_panel.py` | `card.candidate_confirm_requested` | `BaseHeroSelectDialog(SINGLE, allowed_names=...)`, `update_recommendations()` |
| `_rebuild_recommendation_indexes()` | `recommendation_panel.py` | 更多菜单/横幅 | `RecommendationService.rebuild_indexes()`, 同步刷新卡片指数/胜率/奖牌 |
| `_refresh_combo_strip()` | `recommendation_panel.py` | `update_recommendations()`, `_open_combo_management` 返回 | `_update_combo_badges()`, `_render_combo_chips()` |
| `_open_combo_management()` | `recommendation_panel.py` | 实战配队横条 [管理] | `ComboManagementDialog`, `combos_changed` 连接回刷新 |
| `_show_combo_detail(combo)` | `recommendation_panel.py` | chip 点击 | `shared.combo_detail.show_combo_detail()` |
| `_show_guide_popup(hero_id)` | `recommendation_panel.py` | `card.guide_clicked` | `get_hero()`, `get_guide()`, `GuideDetailDialog` |
| `_show_skill_popup(hero_id)` | `recommendation_panel.py` | `card.hero_double_clicked` | `get_hero()`, `HeroSkillDialog` |
| `HeroCardWidget` | `hero_card_widget.py` | `RecommendationPanel._setup_ui()` | 卡片展示与 `guide_clicked` / `hero_double_clicked` / `candidate_confirm_requested` 信号 |
| `GuideDetailDialog` | `guide_detail_dialog.py` | `_show_guide_popup()` | 攻略摘要、关系跳转与 Markdown 正文 |
| `HeroSkillDialog` | `hero_dialogs.py` | `_show_skill_popup()` | 技能分 Tab 展示与结算详情 |

---

## 五、武将浏览器链路

### 5.1 初始化与布局

```
HeroBrowser.__init__(hero_manager, guide_manager, synergy_manager, parent, combo_manager)
  -> _setup_ui(combo_manager)
    -> QSplitter(Horizontal)                                    [240–360px 左栏，不可折叠]
       -> HeroListPanel(self._hero_mgr)                         [左侧: 列表面板]
          -> _setup_ui()
             -> QLineEdit (搜索框).textChanged → _apply_filters
             -> QComboBox (势力筛选).currentTextChanged → _apply_filters
             -> QLabel (显示 N / 共 M 名武将)
             -> QListWidget.currentRowChanged → _on_selection_changed
          -> _load_heroes()
             -> self._hero_mgr.list_heroes()
             -> self._hero_mgr.list_factions()                  [填充势力下拉框]
             -> self._apply_filters()
       -> HeroDetailPanel(hero_mgr, guide_mgr, synergy_mgr, combo_manager=combo_manager) [右侧: 详情面板]
          -> _setup_ui(combo_manager)
             -> QFrame#heroIdentityBar                          [当前武将身份头部]
                -> QPushButton("编辑…") → _on_context_edit()
                -> QToolButton("更多")
                   -> QAction("删除…") → _on_context_delete()
             -> QTabWidget
                -> Tab 0: HeroInfoView                          [基础资料与技能]
                -> Tab 1: HeroGuideSummaryView                  [攻略摘要]
                -> Tab 2: HeroSynergyView(hero_mgr, synergy_mgr, combo_manager)
                   [AI 相性 ∪ 实战配队 并集，含"来源"筛选"有实战配队"/"未生成 AI 评分"]
                -> currentChanged → _update_context_actions()   [映射文案与启用状态]
    -> HeroListPanel.hero_selected → HeroDetailPanel.show_hero    [信号连接]
    -> HeroDetailPanel.hero_requested → HeroListPanel.select_hero [攻略关系跳转]
    -> HeroDetailPanel.data_changed → HeroBrowser.reload_data()   [信号连接]
    -> HeroDetailPanel.synergies_changed → HeroBrowser.synergies_changed [相性变更通知主窗口]
    -> _list_panel.selected_hero_id()                            [主动展示初始选中项]
```

### 5.2 武将选择和详情展示

```
HeroListPanel._on_selection_changed(row)                       [列表选择变化]
  -> self.hero_selected.emit(filtered_heroes[row].id)           [信号: 发射 hero_id]

HeroDetailPanel.show_hero(hero_id)                              [接收信号]
  -> self._current_hero = self._hero_mgr.get_hero(hero_id)      [查询 Hero]
  -> self._current_guide = self._guide_mgr.get_guide(hero_id)   [查询 HeroGuide]
  -> self._info_tab.show_hero(self._current_hero)
     -> 设置 QLabel HTML: 基础信息与资料更新时间
     -> HeroInfoView._update_skills(hero)
        -> 清理旧技能布局
        -> [无技能] 显示 "无技能"
        -> [有技能] for each Skill:
           -> 展开面板: name + description
           -> [有 settlement] 可折叠"结算详情"面板
  -> self._guide_tab.show_guide(self._current_guide)
     -> [无 guide] 显示 "暂无攻略数据"
     -> [有 guide] 渲染:
        -> 核心要点 (list[str] → 逐项 QLabel)
        -> 新手提示 (tips_for_beginners)
        -> 劣势对局 (weak_against_type) → 文本列表
        -> 优势对局 (strong_against_type) → 文本列表
        -> 对抗建议 (counter_strategy) → 文本
        -> 搭配推荐 (synergizes_with) → 流式关系标签
  -> self._synergy_tab.show_hero(self._current_hero)
     -> HeroSynergyView.refresh() → 相性筛选与表格
```

### 5.3 武将编辑链路

```
HeroDetailPanel._on_info_edit()                                ["编辑武将"按钮]
  -> HeroEditDialog(self._current_hero, parent)                 [编辑对话框]
     -> QFormLayout: name/title/faction/position/max_hp/max_hand/gender/difficulty
  -> run_edit_dialog(dialog, persist=lambda: DataMutationService.update_hero(dialog.get_hero()),
                    success_message="武将资料已保存", failure_hint="编辑内容已保留。",
                    attempts=None)
     -> while dialog.exec() == Accepted:
        -> persist()                                            [业务写回]
        -> [OSError/ValueError/ValidationError] logger.warning + QMessageBox.critical + 保留输入
        -> [其他异常] logger.exception + QMessageBox.critical + 退出循环
        -> [成功] show_toast(success_message) + 返回 True
  -> [saved] self._info_tab.show_hero(updated) + self.data_changed.emit()

HeroDetailPanel._on_info_delete()                              ["删除武将"菜单]
  -> QMessageBox.question("确认删除 ...？")
  -> [Yes]
     -> DataMutationService.delete_hero_with_relations(hero_id) [级联删除攻略与相性]
     -> self._info_tab.show_deleted()
     -> self._guide_tab.show_guide(None)
     -> self._synergy_tab.show_hero(None)
     -> self.data_changed.emit()
     -> self.synergies_changed.emit()
     -> QMessageBox.information("删除完成")
```

### 5.4 攻略编辑链路

```
HeroDetailPanel._on_guide_edit()                               ["编辑攻略"按钮]
  -> GuideEditDialog(self._current_guide, self._hero_mgr, parent)
     -> _setup_ui():
        -> 核心要点: QTextEdit (多行, 每行一条)
        -> 新手提示: QTextEdit
        -> 劣势/优势对局类型: QTextEdit（每行一条）
        -> 对抗建议: QTextEdit
        -> _create_relation_selector("搭配推荐", synergizes_with)
           -> "选择武将…" -> _open_relation_selector()
              -> HeroRelationSelectDialog(...).exec()
                 -> 搜索 + 势力多选筛选 + 勾选列表
                 -> _accept_selection() -> selected_ids
           -> _update_relation_summary()                       [显示已选名称]
        -> 攻略正文: QTextEdit (Markdown)
  -> run_edit_dialog(dialog, persist=lambda: DataMutationService.update_guide(dialog.get_guide()),
                    success_message="攻略修改已保存", failure_hint="编辑内容已保留。",
                    attempts=None)
  -> [saved] self._guide_tab.show_guide(updated) + self.data_changed.emit()

HeroDetailPanel._on_guide_delete()                             ["删除攻略"菜单]
  -> QMessageBox.question("确认删除攻略？")
  -> [Yes]
     -> DataMutationService.delete_guide(self._current_guide.hero_id)
     -> self._guide_tab.show_guide(None)
     -> self.data_changed.emit()
     -> QMessageBox.information("删除完成")
```

### 5.5 相性浏览与编辑链路

```
HeroDetailPanel.show_hero(hero_id)
  -> HeroSynergyView.show_hero(hero)
  -> HeroSynergyView.refresh()
     -> SynergyManager.list_synergies_for_hero(hero_id)         [AI 相性]
     -> [combo_manager 非空] list_combos_for_hero(hero.id)      [实战配队]
     -> 合并：AI 相性 ∪ 实战配队（同配对合并为一行，多座次变体逐条）
     -> 按搭档名称 / 总评 / 来源筛选（"有实战配队"/"未生成 AI 评分"）
     -> 排序：先 AI 相性按评分降序，后实战配队按 rating 降序
     -> 六列表格：搭配武将 / 综合评分 / 总评 / 实战评级 / 实战座次 / 相性说明

相性表格双击（非说明列）/「修改相性」按钮
  -> HeroSynergyView.edit_requested 信号 → HeroDetailPanel._on_synergy_edit()
  -> SynergyEditDialog(hero_mgr, synergy).exec()
     -> 评分变化 -> synergy_rating_for_score()                 [实时更新评级]
  -> run_edit_dialog(dialog, persist=lambda: DataMutationService.update_synergy(dialog.get_synergy()),
                    success_message="相性修改已保存", failure_hint="编辑内容已保留。",
                    attempts=None)
  -> [saved] HeroSynergyView.refresh() + synergies_changed.emit()

相性说明列双击
  -> HeroSynergyView._show_description(row)
  -> PageHeader + render_markdown(synergy.description) -> QTextBrowser + DialogFooter("关闭")
```

### 5.6 函数清单总表（武将浏览器）

| 函数 | 所在类 | 调用方 | 被调用方 |
|------|--------|--------|----------|
| `reload_data()` | `HeroBrowser` | 外部 UI | `HeroListPanel.reload()` |
| `_load_heroes()` | `HeroListPanel` | `__init__()`, `reload()` | `list_heroes()`, `list_factions()`, `_apply_filters()` |
| `_apply_filters()` | `HeroListPanel` | 搜索/textChanged | 过滤 + `_refresh_list()` |
| `_on_selection_changed(row)` | `HeroListPanel` | QListWidget 信号 | `hero_selected.emit(id)` |
| `show_hero(hero_id)` | `HeroDetailPanel` | `hero_selected` 信号 | `get_hero()`, `get_guide()`, 三个详情视图的显示/刷新方法 |
| `show_hero(hero)` | `HeroInfoView` | `HeroDetailPanel.show_hero()`、武将编辑后 | 设置基本信息 HTML、重建技能卡片 |
| `show_guide(guide)` | `HeroGuideSummaryView` | `HeroDetailPanel.show_hero()`、攻略编辑后 | 渲染摘要、对局类型和关系标签 |
| `refresh()` | `HeroSynergyView` | `HeroDetailPanel.refresh_synergies()` | `list_synergies_for_hero()`、筛选和表格排序 |
| `_on_info_edit()` | `HeroDetailPanel` | "编辑武将"按钮 | `HeroEditDialog`, `update_hero()`, `show_toast()` |
| `_on_info_delete()` | `HeroDetailPanel` | "删除武将"菜单 | `delete_hero_with_relations()`, 模态结果 |
| `_on_guide_edit()` | `HeroDetailPanel` | "编辑攻略"按钮 | `GuideEditDialog`, `update_guide()`, `show_toast()` |
| `_on_guide_delete()` | `HeroDetailPanel` | "删除攻略"菜单 | `delete_guide()`, 模态结果 |
| `refresh_synergies()` | `HeroDetailPanel` | `show_hero()`、`HeroBrowser.refresh_synergies()`、保存后 | `HeroSynergyView.refresh()`、按钮状态 |
| `_on_synergy_edit()` | `HeroDetailPanel` | 双击相性行或"编辑相性" | `SynergyEditDialog`、`update_synergy()`、`show_toast()` |
| `HeroEditDialog.get_hero()` | `HeroEditDialog` | `_on_info_edit()` accepted | 读取控件值 → 重新校验的 `Hero` 副本 |
| `GuideEditDialog.get_guide()` | `GuideEditDialog` | `_on_guide_edit()` accepted | 读取表单与搭配 ID → 重新校验的 `HeroGuide` 副本 |
| `GuideEditDialog._open_relation_selector()` | `GuideEditDialog` | 搭配推荐选择按钮 | `HeroRelationSelectDialog.exec()` → 回填 ID 列表 |
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
       -> PageHeader(title, tip)
       -> QLineEdit(搜索) → textChanged → _apply_filter
       -> CheckableComboBox(彩色势力标签 + 多选下拉) → checked_values_changed → _apply_filter
          -> 右侧上下箭头随浮动筛选层展开/收起切换，同一按钮再次点击显式收起
          -> 对话框内浮动层: QLineEdit(搜索势力) + 浅蓝色 QListWidget + 全选/反选/确定
       -> QLabel(计数: "已选 N / 共 M")
       -> [MULTI_LIMIT] QLabel(上限提示)
       -> QPushButton("全选") / "取消全选"
       -> QListWidget (武将列表)
       -> DialogFooter("取消" / "确定")

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
| `GuideFetchDialog` | `BaseHeroSelectDialog` | `MULTI` | 无限制 | `HEROES_DICT` | 攻略状态筛选、状态标签和重新生成提示 |
| `SynergyPairDialog` | `BaseHeroSelectDialog` | `MULTI_LIMIT` | 8 | `HEROES_DICT` | `_on_accept`: 允许 2~8 个 |
| `SynergySingleDialog` | `BaseHeroSelectDialog` | `SINGLE` | 1 | `HEROES_DICT` | 无 |

### 6.3 模拟器配置对话框

```
MainWindow._open_faction_colors()
  -> FactionColorDialog(parent=self).exec()
  -> FactionColorDialog._add_faction()
     -> 校验非空且未重复 -> 加入颜色草稿列表
  -> ColorPicker._open_picker()
     -> QColorDialog(DontUseNativeDialog)
     -> HSB 调整 / 屏幕取色
  -> save_faction_colors(colors, config/faction_colors.json)
  -> reload_faction_colors()
  -> RecommendationPanel.refresh_faction_colors()

MumuConfigDialog.__init__(config, capture_service, ocr_service, parent)
  -> _setup_ui()
     -> MumuDeviceSection()                                  [设备控件与操作信号]
     -> MumuTemplateSection()                                [模板状态与操作信号]
     -> MumuOcrPollingSection()                              [轮询参数与 ROI 操作信号]
     -> 各 Section 信号连接到 MumuConfigDialog 槽函数
  -> MumuConfigCoordinator(config, capture_service, ocr_service)
  -> _load_config()
    -> MumuConfigCoordinator.sync_capture_config()              [唯一 ADB 会话]
    -> _on_refresh_devices()
       -> MumuConfigCoordinator.refresh_devices()
       -> [signal] devices_changed -> _on_devices_refreshed() -> 填充设备下拉列表
       -> [signal] device_refresh_failed -> _on_device_refresh_failed() -> 保留当前选择
    -> _refresh_template_status()
       -> OcrService.is_template_loaded()

  → 模板制作:
  _on_make_template()
    -> MumuConfigCoordinator.start_template_capture()
      -> EmulatorOperationService.capture_template_screenshot() [后台]
      -> CaptureService.capture_screenshot()                    [共享 ADB 会话]
      -> [signal] template_screenshot_ready -> _on_template_screenshot_ready()
    -> pil_to_qpixmap(image)                                    [UI 线程 PIL→QPixmap]
    -> RoiSelectorDialog(pixmap, title, parent)                 [UI 框选 ROI]
       -> 鼠标拖拽: mousePress → mouseMove → mouseRelease       [绘制矩形框]
       -> 确认: _on_confirm → ROI = (x, y, w, h)
    -> [accepted] MumuConfigCoordinator.create_template(image, roi, template_name)
    -> _refresh_template_status()

  [任意连接/刷新状态变化]
    -> _update_ui()
    -> 模板截图进行中？保持“正在截图...”与禁用状态

  → 保存配置:
  _on_save()
    -> DialogFooter.set_busy(True, "正在保存...")
    -> 收集控件值到 self._config
    -> show_toast("识别参数已保存") -> self.accept()
  [MainWindow._open_mumu_config() 中]
    -> dialog.get_config()
    -> save_env_file(DEFAULT_ENV_FILE, config)
    -> CaptureService.update_config(config)
    -> OcrService.update_config(config)
    -> PollCoordinator.sync_with_connection()
    -> OcrService.start_poll(interval * 1000)（仅 connected）/ stop_poll()
```

| 函数 | 所在类 | 调用方 | 被调用方 |
|------|--------|--------|----------|
| `RoiSelectorDialog` 鼠标事件 | `RoiSelectorDialog` | Qt 事件 | `_on_mouse_press/move/release`, `_update_info()`, `_on_paint()` |
| `RoiSelectorDialog._on_confirm()` | `RoiSelectorDialog` | "确认"按钮 | 计算 ROI → `self.accept()` |
| `MumuDeviceSection` 用户操作信号 | `mumu_config_sections.py` | 设备区按钮/下拉框 | `MumuConfigDialog` 对应槽函数 |
| `MumuTemplateSection` 用户操作信号 | `mumu_config_sections.py` | 模板选择/制作按钮 | `MumuConfigDialog` 对应槽函数 |
| `MumuOcrPollingSection` 用户操作信号 | `mumu_config_sections.py` | 轮询与 ROI 控件 | `MumuConfigDialog` 对应槽函数 |
| `MumuConfigDialog._on_auto_detect()` | `MumuConfigDialog` | "自动探测"按钮 | `MumuConfigCoordinator.detect_adb()` |
| `MumuConfigDialog._on_make_template()` | `MumuConfigDialog` | "制作模板"按钮 | 协调器截图 → `RoiSelectorDialog` → 协调器保存模板 |
| `MumuConfigDialog._on_save()` | `MumuConfigDialog` | "保存"按钮 | 收集表单 → 协调器校验草稿 → `accept()` |

### 6.4 进度对话框

```
GuideProgressDialog.__init__(hero_count, title, parent)
  -> _setup_ui(hero_count)
    -> QLabel(status: "正在准备...")
    -> QProgressBar(0 -> hero_count)                            [固定总量]
    -> QLabel(detail: 当前处理项)
    -> QLabel(error: 隐藏, 红色)                                 [仅失败时显示]
    -> DialogFooter("中止" / "关闭", 关闭初始禁用)

  → 进度更新:
  update_status(text):
    -> 正则 r"\[(\d+)/(\d+)\]\s*(.+?)\s+OK"
    -> [匹配 OK] _status_label.setText("已生成 XXX..."), update_progress(current, total)
    -> 正则 r"\[(\d+)/(\d+)\]\s*(.+?)\s+FAIL"
    -> [匹配 FAIL] _status_label.setText("生成失败: XXX..."), 不更新进度条位置
    -> 正则 r"\[重试\]\s*(.+?)，第\s*(\d+)/(\d+)\s*次，(\d+)\s*秒后重试"
    -> [匹配重试] _status_label.setText("⏳ 重试中（n/N），w 秒后重试"),
                       _detail_label.setText("当前进度 cur/total，原因：..."), 不推进进度条
    -> [均不匹配] _detail_label.setText(text)

  → 完成:
  on_process_finished(success, message):
    -> 启用"关闭"按钮
    -> [成功] _status_label = "生成完成 ✓", progress = max
    -> [失败] _status_label = "生成失败 ✗", set_error(message)
      -> 失败信息由 BaseFetchService 根据非零退出码生成

> **注意**: 进度对话框显示 CLI 输出中的 `OK`/`FAIL`/`[重试]` 行，但 FetchService 的完成状态只依赖子进程退出码；`RESULT: FAIL=` 不参与状态判定。`[重试]` 行由 `api_generator` 限流退避时输出，经 `fetch_utils` 进度白名单放行后到达进度窗口。
```

### 6.5 实战配队维护对话框

```
RecommendationPanel._open_combo_management()
  -> ComboManagementDialog(hero_mgr, ComboService(combo_mgr), parent).exec()
     -> _setup_ui()
        -> PageHeader("实战配队管理", "全量列表；手工记录在导入合并时优先保留")
        -> QComboBox(hero_filter, editable=True, completer 包含匹配)
        -> QCheckBox("仅看手工")
        -> 按钮行：[＋ 新增配队] [编辑] [删除]
        -> QListWidget(千级数据，不做逐行控件渲染)
        -> DialogFooter("关闭")
     -> _refresh_list()
        -> combo_manager.list_combos() 排序 (-rating, hero1_name, hero2_name)
        -> 应用筛选：hero_id ∈ (combo.hero1_id, combo.hero2_id) AND (not manual_only OR combo.manual)
        -> 每行显示：★rating hero1[seats] ＋ hero2[seats]  🖊 手工/📥 导入  note
     -> itemSelectionChanged → _update_action_buttons()
     -> itemDoubleClicked → _on_edit_selected()

ComboManagementDialog._on_add()
  -> ComboEditDialog(hero_mgr, combo_service, parent=None).exec()
     -> _setup_ui()
        -> PageHeader("新增实战配队", "记录武将组合、实战评级与座次要求；手工记录在导入合并时优先保留")
        -> QFormLayout:
           -> 武将 1: [选择武将] + 势力色徽章  → _select_hero(1)
           -> 武将 2: [选择武将] + 势力色徽章  → _select_hero(2)
           -> 实战评级: QSpinBox(1..10, 默认 5)
           -> 武将 1 座次: 4 个 QCheckBox (1..4 号位)
           -> 武将 2 座次: 4 个 QCheckBox (1..4 号位)
        -> note: QPlainTextEdit (fixed 72px)
     -> _select_hero(slot)
        -> BaseHeroSelectDialog(SINGLE, title=f"选择武将 {slot}")
        -> [其他位置已选且相同] QMessageBox.warning("武将 1 与武将 2 不能相同")
        -> _refresh_hero_slots() [按钮文字 + 势力色徽章 + 座次复选可用性]
     -> _on_save()
        -> [hero1/hero2 空或相同] QMessageBox.warning
        -> [两个武将均未勾选座次] QMessageBox.question("按不限座次保存？", No)
        -> [existing 且非编辑同配对] QMessageBox.question("组合已存在，覆盖？", Yes)
        -> hero1_id < hero2_id 规范化 + 座次并集 position
        -> combo = Combo(manual=True, hero1_seats=seats1, hero2_seats=seats2, ...)
        -> ComboService.save_manual_combo(combo, previous=self._original)
        -> self.accept()
  -> [Accepted] combos_changed.emit() + _refresh_list()

ComboManagementDialog._on_delete_selected()
  -> QMessageBox.question("确定删除 …？", No)
  -> [Yes] ComboService.delete_combo(combo) + combos_changed.emit() + _refresh_list()

RecommendationPanel._on_combos_imported(count)                    [CombosImportDialog 返回]
  -> ComboManager.load()
  -> HeroBrowser.refresh_synergies()
  -> show_toast(f"实战配队已导入 {count} 条，相性板块与选将推荐已更新。")

AiGenerationWorkflow.request_synergy_combos()                     [菜单"实战配队生成"]
  -> SynergyCombosDialog(synergy_mgr, combo_manager=_, parent=window).exec()
     -> 按 RATING_FILTERS (9-10 / 8 / 6-7 / 1-5) + 座次 + 生成状态 筛选
     -> selected_pairs = [{"hero_a_id": int, "hero_b_id": int}, ...]
     -> overwrite_existing = bool
  -> estimate_generation_cost(len(pairs), "synergy")
  -> _choose_backend("实战配队相性生成", estimation) → (backend, use_rag)
  -> _start_synergy_generation(len(pairs), "实战配队相性生成进度",
                               lambda: synergy_service.fetch_pairs_list(
                                    pairs, backend=backend,
                                    overwrite=dialog.overwrite_existing,
                                    use_rag=use_rag))
```

---

## 七、外部调用关系总览

### 7.1 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.hero_manager.HeroManager` | 武将 CRUD 和查询 |
| `src.data.synergy_manager.SynergyManager` | 相性 CRUD 和查询 |
| `src.data.guide_manager.GuideManager` | 攻略 CRUD 和查询 |
| `src.data.combo_manager.ComboManager` | 实战配队 CRUD，选将推荐与巅峰赛匹配共用 |
| `src.data.combo_seats.format_seats` | 座次字段格式化 |
| `src.data.recommendation_index_repository` | 推荐指数快照加载 |
| `src.data.peak_win_rate_repository` | 巅峰赛胜率/出场排行加载 |
| `src.data.hero_classification_repository` | 武将分类（分类建议 worker 调用） |
| `src.business.fetching.hero_fetch_service.HeroFetchService` | 武将采集 QProcess 管理 |
| `src.business.fetching.guide_fetch_service.GuideFetchService` | 攻略生成 QProcess 管理 |
| `src.business.fetching.synergy_fetch_service.SynergyFetchService` | 相性获取 QProcess 管理 |
| `src.business.emulator.capture_service.CaptureService` | 截图业务编排 |
| `src.business.emulator.emulator_operation_service.EmulatorOperationService` | 配置页后台 ADB 操作 |
| `src.business.recognition.ocr_service.OcrService` | OCR 控制、模板管理、轮询 |
| `src.business.recognition.ocr_worker.OcrWorker` | 由 CaptureService 持有的唯一后台识别队列 |
| `src.business.analysis.recommendation_service.RecommendationService` | 推荐指数加载与手动重建 |
| `src.business.analysis.peak_ban_advice` | 巅峰赛禁选建议 |
| `src.business.analysis.match_analysis_service` | 对局攻略分析 |
| `src.business.card_catalog.CardCatalogService` | 卡牌目录服务（`CardManagementPanel`） |
| `src.business.announcement.AnnouncementService` | 公告与百科 diff 检查 |
| `src.business.maintenance.data_management_service` | 武将/攻略/相性变更的快照与备份写入 |
| `src.business.maintenance.corpus_services` | 实战配队/专属牌/分类写路径服务 |
| `src.business.maintenance.classification_suggest` | 武将分类 LLM 建议（后台 worker 调用） |
| `src.business.ai_cost.estimate_generation_cost` | AI 成本估算 |
| `src.config.env.*` | 配置文件读取/保存 |
| `src.capture.prober.*` | ADB/模拟器探测 |
| `src.capture.adb_screen.AdbCapture` | CaptureService 持有的唯一 ADB 会话 |
| `src.ocr.ocr_loader.get_template_manager()` | 仅由 OcrService 管理模板 |
| `src.ocr.recognizer.GeneralRecognizer` | OCR 预热 |
| `src.ui.shared.persist.run_edit_dialog` | 模态编辑对话框的标准保存循环 |
| `src.ui.shared.master_detail.MasterDetailPane` | 主从列表骨架 |
| `src.ui.shared.capture_lock.CaptureRequestLock` | 截图/文件导入单飞锁 |

---

## 八、函数清单总表

### MainWindow

| 函数 | 调用方（触发方式） | 被调用方 |
|------|-------------------|----------|
| `__init__(hero_manager, synergy_manager, guide_manager)` | `main.py:main()` | `AppServices(...)` 构造、`attach(self)`、`_setup_ui()`、`_load_data()`、`_setup_status_bar()` |
| `start_ocr_warmup()` | 启动画面 | `CaptureService.warmup_ocr_model()` |
| `wait_ocr_warmup(timeout_ms)` | 启动画面阻塞等待 | `CaptureService.wait_ocr_warmup()` |
| `_load_data()` | `__init__()`, `_reload_data()` | `DataFacade.load_all()` + `DataMutationService.repair_missing_references()` |
| `_reload_data()` | 菜单”重新加载数据” (F5) | `_load_data()`, `HeroBrowser.reload_data()`, `CardManagementPanel.reload_data()`, `RagMaintenancePanel.reload_data()`, `RecommendationPanel.refresh_synergies()`, `_update_status()` |
| `_update_status()` | 各 fetch 完成回调 | `DataFacade.get_stats()`, `status_label.setText()` |
| `_request_fetch_all()` | 菜单 | `QMessageBox.question` → `HeroFetchService.fetch_all()` |
| `_request_fetch_incremental()` | 菜单 | `QMessageBox.question` → `HeroFetchService.fetch_incremental()` |
| `_request_fetch_specific()` | 菜单 | `HeroFetchDialog` → `fetch_specific(ids)` |
| `_request_guide_*()` | 菜单 | `AiGenerationWorkflow.request_guide_*()` |
| `_request_synergy_pair()` / `_request_synergy_single()` / `_request_synergy_combos()` | 菜单 | `AiGenerationWorkflow.request_synergy_*()` |
| `_open_settings()` | 菜单”配置→API 配置” | `SettingsDialog` |
| `_open_faction_colors()` | 菜单”配置→势力配色” | `FactionColorDialog` → `reload_faction_colors()` → `RecommendationPanel.refresh_faction_colors()` + `MatchGuidePanel.refresh_faction_colors()` |
| `_open_data_management()` | 菜单”配置→数据管理” | `DataManagementDialog` |
| `_open_mumu_config()` | 菜单”配置→模拟器配置” / StatusChips 点击 | `MumuConfigDialog` → `save_env_file()` → `PollCoordinator.sync_with_connection()` |
| `_open_official_data_import()` | 菜单”导入→官方数据导入” | `OfficialDataImportDialog` + 轮询暂停/恢复 |
| `_open_combos_import()` | 菜单”导入→实战配队导入” | `CombosImportDialog` |
| `_on_combos_imported(count)` | `CombosImportDialog.combos_imported` | `ComboManager.load()` + `HeroBrowser.refresh_synergies()` |
| `_check_announcements()` | 菜单”数据→检查公告更新” | `AnnouncementService.check_now()` + is_busy/cooldown 检查 |
| `_open_announcement_dialog()` | 菜单”数据→公告记录” | `AnnouncementDialog` (非模态) |
| `_update_hero_data_from_announcements()` | 横幅/对话框按钮 | `AnnouncementService.collect_base_candidates()` + `prepare_update_candidates()` |
| `_on_hero_update_prepared(payload)` | `AnnouncementService.update_candidates_prepared` | `HeroUpdateConfirmDialog` → `_pending_update_phases` 队列 |
| `_start_next_update_phase()` / `_dispatch_update_phase()` | 上一阶段完成回调 | `fetch_specific(ids)` / `fetch_incremental()` + is_busy 检查 |
| `_on_fetch_completed(success)` | `HeroFetchService.fetch_completed` | 消费阶段队列 → `mark_applied()` / 普通 toast |
| `_on_poll_result(result)` | `PollCoordinator.poll_result_ready` | 按 `task_results` 分派到推荐页/对局攻略页 |
| `_on_peak_exited_to_match()` | `PeakSelectPanel.board_exited` | 激活对局攻略任务 |
| `_deactivate_match_guide_if_idle()` | `healthy_no_match` 回调 | 激活后 90s 空闲失活对局攻略任务 |
| `_on_ocr_warmup_state_changed(state)` | `CaptureService.ocr_warmup_state_changed` | 状态栏 OCR 预热提示 |
| `_on_capture_connection_changed(state, detail)` | `CaptureService.connection_changed` | `_update_emulator_status()` + `warmup_ocr_model()` + `sync_with_connection()` |
| `_on_guides_generated()` | `AiGenerationWorkflow.guides_changed` | `_update_status()` |
| `_on_synergies_generated()` | `AiGenerationWorkflow.synergies_changed` | `HeroBrowser.refresh_synergies()`, `RecommendationPanel.refresh_synergies()`, `_update_status()` |
| `_on_synergies_changed()` | `HeroBrowser.synergies_changed` | `RecommendationPanel.refresh_synergies()`, `_update_status()` |

### AiGenerationWorkflow

| 函数 | 调用方（触发方式） | 被调用方 |
|------|-------------------|----------|
| `request_guide_all()` | 主窗口”全量获取”菜单 | `_get_heroes_as_dicts()`、`_start_guide_generation()`、`GuideFetchService.fetch_all()` |
| `request_guide_incremental()` | 主窗口”增量获取”菜单 | `GuideManager.list_guides()`、缺失筛选、`fetch_incremental(missing)` |
| `request_guide_specific()` | 主窗口”指定获取”菜单 | `GuideFetchDialog`、`_start_guide_generation()`、`fetch_specific()` |
| `request_synergy_pair()` | 主窗口”指定获取”菜单 | `SynergyPairDialog`、后端选择、`SynergyFetchService.fetch_pair()` |
| `request_synergy_single()` | 主窗口”选定武将”菜单 | `SynergySingleDialog`、后端选择、`fetch_single()` |
| `request_synergy_combos()` | 主窗口”实战配队生成”菜单 | `SynergyCombosDialog`、`estimate_generation_cost`、`fetch_pairs_list(selected_pairs)` |
| `set_window(window)` | `AppServices.attach()` | 更新弹窗归属（组合根无头构造后回填） |
| `_on_guide_completed()` | `GuideFetchService.fetch_completed` | 关闭进度、`GuideManager.load()`、`guides_changed` |
| `_on_guide_error(msg)` | `GuideFetchService.error_occurred` | `GuideProgressDialog.on_process_finished(False)`；读取 `failed_items` 构建”失败武将清单”详情弹窗 + `install_details_button_translator()` |
| `_on_guide_cancelled()` | `GuideFetchService.cancelled` | 进度中止、`GuideManager.load()`、`guides_changed` |
| `_on_synergy_completed()` | `SynergyFetchService.fetch_completed` | 关闭进度、`SynergyManager.load()`、`synergies_changed` |
| `_on_synergy_error(msg)` | `SynergyFetchService.error_occurred` | `GuideProgressDialog.on_process_finished(False)`；读取 `failed_items` 构建”失败相性对清单”详情弹窗 + `install_details_button_translator()` |
| `_on_synergy_cancelled()` | `SynergyFetchService.cancelled` | 进度中止 + `SynergyFetchService.reload_from_disk()`（后台重载已提交数据） |
| `_on_synergy_reload_finished/failed()` | `SynergyFetchService.reload_finished/failed` | `synergies_changed` 或状态栏错误 |

### HeroCardWidget

| 函数 | 说明 |
|------|------|
| `set_hero(hero or None)` | 设置 Hero → `_update_display()` + `cardState=”ready”/”empty”` |
| `set_pending_name(raw_name, candidates)` | 显示不加载推荐数据的待确认名称，`cardState=”pending”`，显示候选数量 |
| `set_unrecognized_name(name)` | 显示未匹配到武将资料的 OCR 名称，`cardState=”unknown”` |
| `set_combo_badge(text or None)` | 头像右上角「实战 ★N」金色徽章 |
| `set_recommendation_index(index)` | 显示”推荐指数：分数 / 评级”，`_update_confidence_display()` |
| `set_recommendation_stale(stale)` | 标记推荐指数是否需要重建，`cardState=”indexStale”` |
| `set_guide_available(available)` | 攻略可用性，`cardState=”missingGuide”` |
| `set_win_rate(rate)` | 设置胜率百分比 |
| `set_medal(rank 1/2/3)` | 设置 `rank` 动态属性与固定”胜率 TOP N”徽章 |
| `set_synergies(pairs)` | 设置高相性组合展示（2 列 GridLayout，最多 2 条 + 最佳搭档） |
| `refresh_faction_color()` | 使用当前势力配色刷新卡片 |
| `hero_id` / `hero_name` | 读取卡片当前武将身份 |

### 对话框

| 对话框类 | 输入 | 输出 |
|----------|------|------|
| `BaseHeroSelectDialog` | 搜索文本、势力筛选、多/单选、可选 `allowed_names` | `selected_ids`, `selected_heroes`, `selected_hero` |
| `HeroFetchDialog` | HeroManager | `selected_ids` |
| `GuideFetchDialog` | HeroManager + GuideManager | `selected_heroes` + 攻略状态筛选 |
| `SynergyPairDialog` | HeroManager | `selected_heroes` (2~8) + `overwrite_existing` |
| `SynergySingleDialog` | HeroManager | `selected_hero` |
| `SynergyCombosDialog` | SynergyManager + ComboManager | `selected_pairs` + `overwrite_existing` |
| `ComboManagementDialog` | HeroManager + ComboService | `combos_changed` 信号（面板刷新） |
| `ComboEditDialog` | HeroManager + ComboService + combo? | 保存后 accept（服务已落盘） |
| `SettingsDialog` | env_path, pricing_path, profiles_path | 保存到 config.env / model_pricing.json / api_profiles.json |
| `DataManagementDialog` | GuideManager + SynergyManager | 备份后批量清空 |
| `MumuConfigDialog` | ADB 路径/端口/模板/OCR 配置 | config dict + 服务状态更新 |
| `FactionColorDialog` | 势力配色字典 | 保存到 `config/faction_colors.json` |
| `BackendChooseDialog` | Token/费用估算 + 语料增强单选（RAG 增强/经典） | `(“api” 或 “browser”, use_rag)` |
| `GuideProgressDialog` | 总数量、子进程进度信号 | 实时进度条 + 完成/失败提示 |
| `RoiSelectorDialog` | 截图 QPixmap | ROI (x, y, w, h) |
| `HeroEditDialog` | Hero 对象 | 修改后的 Hero 对象 |
| `GuideEditDialog` | HeroGuide + HeroManager | 修改后的 HeroGuide 对象 |
| `HeroRelationSelectDialog` | HeroManager + 预选 ID | 按英雄 ID 稳定排序的 `selected_ids` |
| `SynergyEditDialog` | HeroManager + SynergyScore | 修改后的 SynergyScore 对象 |
| `HeroUpdateConfirmDialog` | 候选武将列表（含字段级差异） | `selected_ids` + `update_new` |
| `HeroDiffDetailDialog` | 本地全文 + 官网全文 | Git 风格 diff（差异对比/本地原文/官网原文三页） |
| `AnnouncementDialog` | AnnouncementManager + diff | 非模态查看；`check_requested` / `update_requested` 信号 |
| `OfficialImportReviewDialog` | 待复核数据 | 只读查看 |
| `CardAnnotationEditDialog` | CardCatalogService + card_id | 保存追加字段 |
| `CardFieldSchemaDialog` | CardCatalogService | 字段定义管理 |


## 九、公告更新菜单、横幅与对话框链路

```
菜单: 数据 > 检查公告更新 -> _check_announcements() -> AnnouncementService.check_now()
菜单: 数据 > 公告记录 -> _open_announcement_dialog() -> AnnouncementDialog（非模态）

AnnouncementService.check_finished -> _on_announcement_check_finished()
  -> _refresh_announcement_banner()
     ready>0: 横幅“武将数据可更新” + [查看][更新武将数据]（success 色调）
     pending>0: 横幅“检测到武将相关公告，等待百科更新”（info 色调，更新按钮禁用）
     diff 非空: 横幅“检测到百科数据变化”（warning 色调）
     否则隐藏
  -> _refresh_announcement_dialog()（对话框打开时刷新列表与 diff）

进度可视化链路:
  AnnouncementService.check_started -> MainWindow._on_announcement_check_started()
    -> _show_indeterminate_progress("正在检查公告更新...")
  AnnouncementService.progress_changed -> _on_announcement_progress()   [阶段文字]
  AnnouncementService.check_finished -> _on_announcement_check_finished() -> _hide_progress()
  HeroFetchService.progress_updated -> _on_fetch_progress(current, total, text)
    -> _set_progress() [QProgressBar setRange/setValue/setFormat]
  HeroFetchService.fetch_completed -> _on_fetch_completed() -> _hide_progress()

AnnouncementDialog / 顶部横幅:
  update_requested -> MainWindow._update_hero_data_from_announcements()
    -> AnnouncementService.collect_base_candidates(local_heroes, announcements, diff)
       [公告 matched + 内存 diff，无摘要]
    -> 无候选 -> 状态栏“没有需要更新的武将数据” + 状态栏 Toast（info 色调）
    -> 有候选 -> AnnouncementService.prepare_update_candidates(...)
       [后台线程拉取官网数据并计算字段级差异；返回 False 表示服务忙碌]
       -> AnnouncementService.update_candidates_prepared -> _on_hero_update_prepared()
    -> HeroUpdateConfirmDialog.exec()              [勾选要覆盖的武将]
       -> 双击/按钮 -> HeroDiffDetailDialog        [本地 vs 官网全文]
    -> 全取消: mark_applied() 刷新快照，不执行采集，toast“已保留本地武将内容”
    -> 部分确认:
       -> phases = [("specific", selected_ids)]  [有勾选时]
       -> phases += [("incremental", None)]        [update_new=True 时]
       -> _pending_update_phases = phases
       -> _start_next_update_phase()
          -> [HeroFetchService.is_busy or _dispatch_update_phase() 失败]
             -> _abort_pending_update_phases()      [作废整条流，横幅保留可重试]
          -> [成功] _update_phase_fetch_in_flight = True
             -> _dispatch_update_phase()
                -> fetch_specific(hero_ids) / fetch_incremental()
       -> fetch_completed 信号 -> _on_fetch_completed(success)
          -> [_update_phase_fetch_in_flight] pop 阶段
             -> [success and 有剩余] _start_next_update_phase()
             -> [success 且空] mark_applied() + 清空 diff + 刷新横幅/对话框 + toast“请重新加载数据（F5）”
             -> [failure] QMessageBox.warning("采集失败")
          -> 未置令牌 -> 视为普通采集，toast“武将数据已采集完成”
```


---

## 十、知识库维护界面（已迁出）

知识库维护工作台（`MaintenanceWorkspace` / `RagMaintenancePanel`）、索引精化对话框（`IndexRefinementDialog` → `SuggestController` → `RefinementSession`）、元规则母本面板（`RuleDocPanel`）与四个数据源页签（`CardPointsPanel` / `EquipAttrsPanel` / `SpecialCardsPanel` / `HeroClassificationPanel`）的调用链已整体迁至 [./call_graph_rag.md](./call_graph_rag.md)，此处不再重复。
