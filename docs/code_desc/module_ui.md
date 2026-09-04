# 模块：UI 界面层

> 对应目录：`src/ui/`
> 职责：PySide6 桌面用户界面，包含主窗口、武将浏览器、推荐面板、对局攻略页面和各种对话框

---

## 一、模块职责

本模块是用户与程序交互的**门户**，提供完整的桌面应用界面：

- **主窗口** — 左侧工作区导航、顶部上下文栏、兼容菜单和底部状态栏，协调所有业务服务的信号连接；默认尺寸为 1100×760px
- **武将浏览器** — 武将列表搜索/筛选、详情查看、攻略展示、武将/攻略编辑
- **选将推荐** — 固定 2 列 × 4 行推荐卡片、OCR 导入、相性/胜率与异常状态展示
- **对局攻略** — 42/58 阵容核对与临场攻略工作台
- **对话框体系** — 武将选择、配置编辑、后端选择、进度显示等
- **全局样式** — 集中管理视觉 Token、控件语义角色和渐进迁移兼容层

---

## 二、文件结构

```
src/ui/
├── __init__.py
├── app/                        # 应用外壳与全局轮询编排
│   ├── main_window.py          # 主窗口（菜单栏/Tab/状态栏 + PollCoordinator 界面绑定）
│   ├── shell_widgets.py        # NavigationRail 与 ContextHeader 应用外壳组件
│   ├── poll_coordinator.py     # 轮询后台编排、结果过滤与状态提交
│   ├── app_icon.py             # 应用图标加载、缓存与窗口图标维护
│   └── chinese_translator.py   # Qt 标准控件中文翻译 + QMessageBox 详情按钮过滤器
├── library/                    # 资料库：武将浏览、编辑、卡牌与武将获取
│   ├── hero_browser.py
│   ├── hero_detail_views.py
│   ├── hero_edit_dialog.py
│   ├── hero_relation_select_dialog.py
│   ├── guide_edit_dialog.py
│   ├── synergy_edit_dialog.py
│   ├── fetch_dialog.py
│   └── card_management_panel.py
├── recommendation/             # 选将推荐页面与推荐卡片
│   ├── recommendation_panel.py
│   └── hero_card_widget.py
├── match/                      # 对局攻略页面、分析视图和阵容状态
│   ├── match_guide_panel.py
│   ├── match_analysis_view.py
│   ├── match_lineup_state.py
│   ├── peak_select_panel.py    # 巅峰赛（2v2）选将工作台：实时识别 + 候选卡片 + 实战配队
│   └── peak_hero_card.py       # 巅峰赛候选武将卡片（复用对局攻略阵容卡 objectName 样式）
├── maintenance/                # 知识库维护工作台（RAG 语料与元规则 T0 文档）
│   ├── maintenance_workspace.py # 布局外壳：左栏维护对象导航 + 折叠执行日志
│   ├── rag_maintenance_panel.py # 业务逻辑：语料状态、审计提示与本地一键执行
│   ├── rule_doc_panel.py       # 元规则维护四页签（audit/差异/提案/疑难）
│   ├── index_refinement_dialog.py # 索引精化对话框（LLM 建议 + 人工补全）
│   ├── card_points_panel.py
│   └── equip_attrs_panel.py
├── generation/                 # AI 攻略/相性生成流程及专用对话框
│   ├── ai_generation_workflow.py
│   ├── backend_choose_dialog.py
│   ├── guide_fetch_dialog.py
│   ├── guide_progress_dialog.py
│   ├── synergy_pair_dialog.py
│   └── synergy_single_dialog.py
├── configuration/              # 应用、势力与模拟器配置
│   ├── settings_dialog.py
│   ├── faction_color_dialog.py
│   ├── mumu_config_dialog.py
│   ├── mumu_config_sections.py
│   └── roi_selector.py
├── data_admin/                 # 数据维护、官方榜单导入与公告更新
│   ├── data_management_dialog.py
│   ├── official_data_import_dialog.py
│   ├── combos_import_dialog.py         # 实战配队导入对话框
│   ├── official_import_review_dialog.py # 官方榜单导入待复核数据审查
│   ├── hero_update_confirm_dialog.py   # 公告更新武将确认对话框
│   └── announcement_dialog.py
├── shared/                     # 跨功能控件、展示与样式
│   ├── widgets.py              # DoubleClickLabel 等共享控件
│   ├── hero_dialogs.py         # HeroSkillDialog
│   ├── faction_colors.py       # 势力配色读取、兜底和缓存
│   ├── hero_select_dialog.py   # 武将选择对话框基类
│   ├── checkable_combo.py      # 势力多选筛选控件
│   ├── guide_detail_dialog.py  # 攻略详情与 Markdown 阅读
│   ├── markdown_renderer.py    # 安全 Markdown 渲染
│   └── style.py                # 视觉 Token、全局 QSS 与动态语义属性
```

### 2.1 UI 设计系统基础

`src.ui.shared.style` 集中定义颜色、字号、间距、圆角、控件高度和图标尺寸。按钮通过 `uiRole` 动态属性区分 `primary`、`secondary`、`ghost`、`danger`；展示状态通过 `tone` 区分 `neutral`、`info`、`success`、`warning`、`danger`。`set_ui_role()` 和 `set_tone()` 会在运行时刷新 QSS，适用于状态变化后的即时反馈。

`src.ui.shared.widgets` 提供 `PageHeader`、`PageActionBar`、`EmptyState`、`StatusBadge`、`NoticeBanner`、`DialogFooter`、`ToastOverlay` 和 `show_toast()`。这些组件只负责稳定的布局与语义属性，不持有业务服务，也不读写数据。`DialogFooter.set_busy()` 统一提交中的按钮禁用与文案恢复；同一父窗口复用一个 Toast 并重置隐藏计时器。`PageActionBar` 已用于选将推荐和对局攻略，避免在应用外壳标题下重复页面标题。

2026-08 知识库维护优化新增两个公共组件：
- **`ScriptRunner`** — QProcess 异步执行 Python 脚本的公共封装（`output(bytes)` / `finished(int)` 信号 + `is_running()` 防并发），知识库维护、元规则维护、卡牌点数 xlsx 导入三个面板共用，消除各自维护 QProcess 生命周期的重复代码；
- **`clear_layout(layout)`** — 递归清空布局（销毁直接控件与子布局控件，含 CheckableComboBox 弹层），收敛武将分类/专属牌/对局分析等面板各自的 `_clear_layout` 副本。

阶段三由 `src.ui.app.shell_widgets` 提供 `NavigationRail` 作为左侧导航外壳；顶部基于 `PageHeader` 的 `ContextHeader` 壳（含按钮菜单）在后续重构中已移除，入口收敛至菜单栏。左侧导航只暴露资料库、选将推荐、对局攻略三个长期工作区；主 `QTabWidget` 隐藏 `TabBar` 但继续持有原页面实例，资料库内部的“武将资料 / 卡牌图鉴”二级页签保持可见。导航请求调用现有 Tab 容器切换，`currentChanged` 反向同步导航选中态与顶部标题；OCR 自动跳转仍只调用 `setCurrentWidget()`，不侵入外壳组件。小于 1040px 时导航强制折叠，回到宽屏后恢复用户在本次会话中的选择。

传统菜单栏在阶段三继续作为兼容路径。顶部的“官方数据导入”“生成与维护”和全局设置菜单均复用 `MainWindow._actions` 中的同一组 `QAction`，因此原业务回调以及 `Ctrl+Q`、`F5` 快捷键保持不变。

完整规范见 [UI 设计系统规范](../spec/spec_ui_design_system.md) 和 [UI 导航与页面归属规范](../spec/spec_ui_navigation.md)；改造前几何基线位于 `docs/ui_baseline/`。

胜率 CSV 读取位于 `src/data/win_rate_repository.py`；推荐指数快照由 `src/data/recommendation_index_repository.py` 根据三份官方榜单生成。页面只依赖共享模块的公开名称，不再从 `recommendation_panel.py` 导入私有函数或复用其内部缓存。

`OfficialDataImportDialog` 由“数据 > 官方数据导入”打开，包含“2v2数据导入”和“武将放逐数据导入”两个可独立选择的有序图片列表。确认后通过 `CaptureService` 把整批任务提交到唯一 `OcrWorker`，再调用 `OfficialDataImportService`：服务按两种样图各自的表头和列比例切分榜单，再依据 OpenCV 检测到的横线或分页行锚点确定实际数据行数，逐单元格 OCR。2v2 图片左表覆盖 `data/2v2胜率排行.csv`（`排名,武将,胜率`），右表独立覆盖 `data/2v2出场排行.csv`（`排名,武将`）；放逐榜左右表合并覆盖 `data/武将放逐.csv`。名称可靠性由业务层保证：完整词表候选优先，单字才逐字补识别；同首字无法唯一确认时由业务层按需使用繁体模型，仍不能确认才进入待复核。任一异常行仍保留期望排名，并写入对应的 `*_待复核.csv` 与 `screenshot_data/official_import/` 行截图。

### 2.1 官方数据导入对话框

`OfficialDataImportDialog` 是“数据 > 官方数据导入”的唯一入口。两个有序列表均使用图片过滤器；用户可以只选择其中一种，也可以同时选择，并可调整分页顺序。点击“导入”后通过 `CaptureService.submit_official_import()` 提交整批任务，`DialogFooter` 在任务期间进入 busy 状态，避免重复提交或关闭。弹窗会先显示等待 OCR 队列和准备阶段的不定进度，检测到表格行后切换为当前榜单的精确 OCR 进度；进度总量包含胜率数字模板准备和逐行识别。成功导入后使用 Toast 汇总结果，将推荐指数快照持久化标记为“待重建”，并通知推荐页面更新按钮状态；不会自动重建。失败时恢复底栏和路径控件。弹窗结束时主动断开 `CaptureService` 信号，避免带主窗口 parent 的旧弹窗在后续导入中重复接收通知。

```python
paths = {key: self._list_paths(widget) for key, widget in self._paths.items()}
self._task = self._capture_service.submit_official_import(paths)
self._capture_service.official_import_progress.connect(self._on_progress_changed)
self._capture_service.official_import_completed.connect(self._on_completed)
self._capture_service.official_import_failed.connect(self._on_failed)
```

**公共交互接口：**

| 方法/状态 | 输入 | 结果 |
|---|---|---|
| `_choose_file(path_input)` | 图片路径 | 回填对应文件框 |
| `_start_import()` | 已选择的 2v2 和/或放逐路径 | 启动后台导入；无路径时弹出提示 |
| `_on_progress_changed(status, current, total)` | 工作线程进度 | 更新准备状态或当前文件的进度条 |
| `_on_completed(summaries)` | 服务摘要列表 | 显示导入条数与复核条数后关闭 |
| `_on_failed(message)` | 错误文本 | 恢复按钮并显示失败原因 |

对话框依赖 `src.business.recognition.official_data_import_service`，由 `MainWindow._open_official_data_import()` 创建；它不直接读取图片、不直接写 CSV。

---

## 三、核心逻辑

### 3.1.1 轮询匹配后的选将推荐刷新

`MainWindow._on_poll_result()` 使用 `PollResult`、`PollTaskResult` 与 `PollOutcome` 接收轮询结果；后台线程边界将 OCR worker 的原始字典转换为强类型对象，旧版字典调用仍由 `from_raw()` 兼容。首次收到 `matched` 时只更新页面数据；在“配置 → 模拟器配置”勾选“识别后自动跳转到结果页面”后，才通过主 `QTabWidget.setCurrentWidget()` 切换到对应工作区，并由 `currentChanged` 同步外壳。对局攻略至少需要 3 个 `name` 已确认的槽位，待确认、未知和冲突状态不计数。冷却期间的后续匹配同样调用 `RecommendationPanel.load_from_ocr()`。收到 `healthy_no_match` 后才重置状态，避免截图暂时失败或 OCR 重试导致页面状态抖动。

### 3.1 主窗口信号拓扑

`MainWindow` 直接连接武将采集、截图和 OCR 服务；攻略/相性服务的任务信号由 `AiGenerationWorkflow` 统一连接和处理，主窗口只接收工作流的状态与数据刷新通知：

```
MainWindow
 ├── HeroFetchService ─── 武将采集
 ├── AiGenerationWorkflow ─ 攻略/相性任务协调
 │   ├── GuideFetchService ─── 攻略子进程
 │   └── SynergyFetchService ─ 相性子进程
 ├── CaptureService ──── 截图
 └── OcrService ──────── OCR + 轮询
```

工作流负责 `status_changed`、完成、错误和进度信号，创建后端选择与进度对话框；后端选择对话框同时返回 `(backend, use_rag)`（API/浏览器 + RAG 增强/经典模式），工作流将 `use_rag` 透传给攻略/相性获取服务；成功后重载对应 Manager，再发出 `guides_changed` 或 `synergies_changed`。主窗口将状态写入状态栏，并在相性变更后刷新武将浏览与选将推荐页面。

底部状态栏按职责分为三部分：普通状态文本显示数据统计及采集、生成、截图、OCR 预热等当前任务进度；模拟器状态常驻显示 ADB 配置和连接状态；OCR 状态常驻显示轮询的未启用、运行、恢复、冷却或暂停状态。任务消息不会覆盖后两类连接状态，点击模拟器或 OCR 状态可打开模拟器配置。

攻略全量、增量、指定与相性配对、选定武将共五个菜单入口仍保留在 `MainWindow`，但均只委托对应的 `AiGenerationWorkflow.request_*()` 方法。增量攻略仅向服务传递缺少攻略的武将，因此成本估算和进度对话框总数与实际任务一致。

### 3.2 对话框基类体系

所有武将选择对话框继承 `BaseHeroSelectDialog`，通过 `SelectionMode` 控制行为：

```
BaseHeroSelectDialog
 ├── SelectionMode.MULTI       → 多选 checkbox，无限制
 ├── SelectionMode.MULTI_LIMIT → 多选 checkbox，有上限（如 max=8）
 ├── SelectionMode.SINGLE      → 单选列表选中
 │
 ├── HeroFetchDialog         → MULTI + IDS
 ├── GuideFetchDialog        → MULTI + HEROES_DICT
 ├── SynergyPairDialog       → MULTI_LIMIT + HEROES_DICT (max=8)
 └── SynergySingleDialog     → SINGLE + HEROES_DICT
```

所有多选对话框使用独立的已选 ID 集合，因此搜索或切换势力只会刷新可见列表，不会丢失已勾选的武将。多选界面提供“全选当前筛选”“清空已选”、可删除的已选标签和随选择数量变化的任务确认按钮。`GuideFetchDialog` 注入 `GuideManager`，按攻略记录与武将资料更新时间判定“未生成”“待更新”“已有攻略”；默认筛选未生成，列表显示状态标签。只选已有或待更新攻略时确认按钮显示“重新生成 N 篇攻略”，混选时明确其中的重新生成数量。

`SynergyPairDialog` 允许选择 2~8 个武将，并实时显示总组合数、已有相性数和本次待生成数。默认跳过已有相性，用户可选“重新生成并覆盖”；工作流将该选择传递给相性服务，服务以 CLI `--update` 参数执行覆盖生成。

四类选择界面统一使用 `CheckableComboBox` 作为势力筛选控件：输入区显示彩色可删除标签，超过 5 个势力显示剩余数量；右侧按钮用向下/向上箭头表示筛选浮动层的收起/展开状态，同一按钮再次点击会显式收起；展开后提供势力搜索、浅蓝色复选列表、全选、反选和确定操作，点击筛选区域外也会收起。

阶段七将 API、模拟器、势力配色、数据管理、编辑、选择、导入、进度、ROI、技能和攻略详情弹窗统一为 `PageHeader + 内容区 + DialogFooter`。普通保存成功使用 Toast；字段或写入失败继续使用模态提示。数据清空和字段归档使用 `danger` 角色并保留确认，其中数据清空完成仍以模态结果列出备份路径。

### 3.3 武将浏览器

```
HeroBrowser (QWidget)
 ├── HeroListPanel (左, 240–360px，默认 280px)
 │   ├── 搜索框 + 势力 ComboBox 筛选
 │   ├── 当前筛选结果计数
 │   ├── QListWidget（武将列表）
 │   ├── _last_hero_id 跟踪选中项（编辑后恢复定位）
 │   ├── signal: hero_selected(int)
 └── HeroDetailPanel (右，占据剩余宽度)
     ├── 身份头部（名称与势力/定位/体力/手牌摘要）
     │   └── 当前内容“编辑” + “更多”中的删除
     ├── Tab「武将信息」→ HeroInfoView
     ├── Tab「攻略指南」→ HeroGuideSummaryView
     └── Tab「武将相性」→ HeroSynergyView
```

左栏与详情区均不可完全折叠；身份头部文字允许换行，内容层级固定为“当前武将身份 → 弱化内容页签 → 详情区块”。武将信息和攻略使用仅纵向滚动的 `QScrollArea`，相性表关闭横向滚动条，三档窗口下均不得产生详情区横向滚动。

武将详情刷新时会先隐藏并延迟删除旧技能卡片；这避免“重新加载数据”后立即弹出模态提示框时，延迟删除的旧控件与新控件重叠绘制。

**编辑功能：**
- 身份头部只保留一个上下文编辑按钮；随当前页签映射为“编辑武将”“编辑攻略”或“编辑相性”，对应删除入口收纳在相邻“更多”菜单中
- `HeroEditDialog` — 编辑武将信息（名称/称号/势力/定位/体力/手牌/性别/难度）
- `GuideEditDialog` — 编辑攻略内容（核心要点/新手提示/关系武将选择/攻略正文）
- `HeroRelationSelectDialog` — 关系武将多选弹窗，提供搜索、按推荐面板势力配色显示的可删除标签下拉框、全选当前筛选和清空选择
- `SynergyEditDialog` — 编辑相性评分、配合维度和说明
- 关系标签采用自适应流式布局，按标签实际宽度换行并支持点击跳转
- 修改保存后 `data_changed` 信号触发列表刷新，`_last_hero_id` 确保选中项不变

三个 Tab 的控件构造和只读渲染位于 `hero_detail_views.py`；`HeroDetailPanel` 保留当前武将/攻略状态、编辑对话框、`DataMutationService` 写入和视图刷新协调，并通过 `current_hero_id`、`refresh_synergies()` 提供公开边界。四个编辑/选择对话框仍位于独立模块；编辑器返回模型副本，服务统一创建快照和备份，写入失败时恢复原数据并重新显示保留输入的编辑弹窗。保存成功使用 Toast，删除完成使用模态结果反馈；相性说明只读窗口同样使用统一标题区和固定底栏。为兼容现有外部导入，`hero_browser.py` 继续导入并暴露这些对话框名称。

**攻略展示布局：**
- 主浏览页保留列表与详情摘要，方便快速切换武将。
- 右侧顶部固定展示当前武将的名称、势力、定位、体力和手牌信息；内容切换使用弱化样式，避免与外层资料库导航竞争。
- 攻略 Tab 外层使用 `QScrollArea`，首屏以“核心建议”突出核心要点和面对该武将的应对。
- 新手提醒、对局关系和完整攻略入口按需出现在摘要之后。
- 点击“阅读完整攻略”打开 `GuideDetailDialog`；其中的正文预览明确标注双击方式，双击后由 `GuideMarkdownDialog` 阅读完整 Markdown 正文。两处预览均通过 `markdown_renderer.render_markdown()` 转义原始 HTML，并在超过 20,000 字时跳过解析。
- 搭配推荐和对局类型均使用自适应流式标签；搭配标签点击后通过 `hero_requested` 信号切换到对应武将。

### 3.4 推荐面板

```
RecommendationPanel (QWidget)
 ├── PageActionBar：最近识别状态 + [识别当前阵容] [更多]
 ├── NoticeBanner：指数过期或可恢复错误
 └── QScrollArea → QGridLayout (2列 × 4行，仅纵向滚动)
      └── HeroCardWidget × 8
           ├── 头像区（100×129px, 浮层显示名称 + 势力色块，左键双击或 [技能] 打开技能详情）
           └── 信息区
               ├── 定位 · 推荐指数（例如 `辅助 · 推荐指数：92 / S`；悬停或点击指数查看明细，右侧口径图标始终保留）+ [技能] [查看攻略]
               ├── 最佳搭档 + 两条相性摘要，完整列表保留在 Tooltip
               └── 历史单将胜率 + 异常状态 + 固定 TOP 1/2/3 徽章
       └── HeroSkillDialog（来自 ui/shared/hero_dialogs.py，按技能名称分 Tab 展示描述和结算）
```

`recommendation_panel.py` 只保留推荐数据更新、相性加载、OCR 导入、手动重建推荐指数和截图信号协调；初始空状态直接提供当前模拟器识别和本地图片导入入口，结果态把图片导入、保存截图和重建收纳到“更多”。胜率和推荐指数快照由 `RecommendationService` 一次读取，前三胜率排名基于数值快照计算。卡片固定高 141px、宽 390～640px，1100×760 默认窗口的 588px 视口正好容纳四行与三段间距；宽屏余量留在网格底部，960×640 才启用纵向滚动。卡片按“定位、推荐指数、最佳搭档、相性摘要、历史单将胜率、技能/攻略操作”呈现；完整相性列表通过 Tooltip 保留。重建成功后同步刷新当前卡片指数、胜率和 TOP 排名；空 OCR、模板未匹配和捕获失败通过页内通知反馈。

- `hero_card_widget.py`：`HeroCardWidget`，负责头像、势力配色、推荐指数、相性摘要、胜率奖牌及卡片信号。
- `guide_detail_dialog.py`：`GuideDetailDialog` 以外层滚动区展示摘要和正文预览；双击预览会打开 `GuideMarkdownDialog` 阅读完整 Markdown 正文。
- `markdown_renderer.py`：统一将攻略与相性 Markdown 转为安全 HTML，负责原始 HTML 转义和解析前长度限制。

为兼容既有调用，`recommendation_panel.py` 仍导入并暴露两个公开类名；页面创建卡片与打开攻略弹窗的调用方式不变。`RecommendationPanel` 通过 `hero_id`、`hero_name`、`set_unrecognized_name()` 和 `refresh_faction_color()` 使用卡片状态，不访问卡片内部字段或重绘方法。

**数据接口：**
```python
def update_recommendations(self, data: list[dict]) -> None
# data 格式: [{"index": 1, "name": "诸葛亮", "confidence": 0.9823}, ...]
```

### 3.4.1 胜率前三视觉锚点

`RecommendationPanel._apply_medal_rankings()` 按当前快照的历史单将胜率降序计算前三名。`HeroCardWidget.set_medal()` 只设置 `rank=1/2/3` 动态属性和固定 78×20px 的“胜率 TOP N”徽章；全局 QSS 使用深金、银灰、铜色的实色边框和浅色徽章背景。卡片尺寸与普通卡一致，窗口不足时只由外层 `QScrollArea` 纵向滚动。

### 3.4.2 势力配色配置

势力配色由 `FactionColorDialog` 以紧凑列表展示，每行只显示势力名称、颜色小方块和 Hex 代码，不在主界面长期占用调色板区域。对话框可输入势力名称并选定初始颜色新增势力；名称不能为空且不能重复，现有势力仅能调整颜色，不能删除或改名。点击颜色小方块后打开 `ColorPicker` 浮层，提供 HSB 调整和屏幕取色；取消时恢复打开前的颜色，点击“保存”后才写入配置文件。

模拟器配置使用“设备与连接”“识别与自动化”两个左侧导航页，顶部共享 ADB 状态和底部保存栏固定显示。识别页先显示 OCR/轮询开关，再由 `MumuTemplateSection` 将武将选择、对局攻略各自的模板、阈值和 ROI 操作组织在同一任务面板中；窄窗口上下排列，宽窗口双列展示。`MumuDeviceSection`、`MumuTemplateSection` 和 `MumuOcrPollingSection` 只构造控件并发出用户操作信号；`MumuConfigDialog` 连接信号、处理文件选择与 ROI 框选，`MumuConfigCoordinator` 仍是唯一业务协调器。两个模板制作按钮在 ADB 已配置但尚未连接时仍可点击，后台自动建立连接并获取截图，只有未配置 ADB 或正在连接时禁用模板制作；“恢复轮询”仅在轮询暂停时显示。

保存流程如下：

```text
ColorPicker.color()
  -> FactionColorDialog._save()
  -> save_faction_colors()
  -> config/faction_colors.json
  -> ui/shared/faction_colors.reload_faction_colors()
  -> RecommendationPanel.refresh_faction_colors()
```

保存前校验每个值是否为六位 Hex 颜色，保存成功后刷新推荐卡片中的势力标签。文件不存在时使用内建兜底配色。配色页及颜色浮层的常用操作使用中文；Qt 样式统一使用 `background-color`，避免按钮刷新时出现 `Could not parse stylesheet of object QPushButton`。

### 3.5 对局攻略页面

`MatchGuidePanel` 是左侧导航中的独立工作区。`PageActionBar` 显示最近识别状态、唯一主要识别按钮和“更多”菜单；空状态保留状态与菜单，仅隐藏重复的主要按钮。结果区使用不可折叠的 42/58 水平 `QSplitter`：左栏顶部固定阵容确认区，下方独立纵向滚动四张 176～250px 宽的紧凑卡片；右栏由 `MatchAnalysisView` 展示总览、我方打法、对抗敌方和单将详情。两侧均禁用横向滚动。

卡片分离识别状态与敌我席位状态，使用互斥“我方 / 敌方 / 未定”分段控件，并稳定显示“我、队友、敌方 1、敌方 2”。蓝色仅表达我方和可执行信息，红色仅表达敌方与威胁，势力色只保留阵营归属语义。新 OCR 结果会清除旧分析并回到总览，任何调整都要求重新确认。

选将推荐与对局攻略的“识别当前阵容”均通过 `CaptureService` 截图并强制执行对应模板的 OCR；本地图片导入复用同一识别流程。OCR 结果携带原文、候选、确认状态和多路证据。选将推荐的待确认卡片不加载推荐指数、胜率或相性，只允许在当前候选集合中人工确认；其他已确认卡片照常更新。对局攻略保留未决槽位，替换窗口优先只显示该槽候选，人工替换后标记为 `manual`；任何名称未决时阵容确认按钮保持禁用。未配置 ADB 时通过 `request_mumu_config` 信号打开模拟器配置。

后台轮询命中独立模板后只刷新对应页面数据，不自动切换 Tab，避免抢占用户当前页面。

`MatchGuidePanel` 只保留截图/图片导入、四张武将卡片和信号绑定。`LineupState` 是阵容的唯一状态来源，负责 OCR 槽位导入、两名我方/两名敌方限制、主将选择、重复武将校验和显式确认；它不依赖 Qt，可独立测试。确认前的提示页和确认后的总览、我方、敌方、详情四个攻略页由 `MatchAnalysisView` 渲染；主面板只将确认后的阵容交给 `MatchAnalysisService`。

### 3.6 后端选择 + 进度条

所有 AI 生成操作在 `MainWindow` 中的标准流程：

```
菜单操作
  ↓
BackendChooseDialog（API 方式 / 浏览器方式 双 Tab + 语料增强单选）
  ↓ 确认执行（返回 backend + use_rag）
GuideProgressDialog（实时进度条 + 中止按钮 + 完成/失败提示）
  ↓ 完成/中止
数据重载 + 状态栏更新
```

相性浏览器任务中止时，进度窗口会等待 AI 子进程及其浏览器后代清理完成；随后工作流在后台重载相性 JSON，主线程仅接收校验后的数据并刷新当前武将详情和推荐卡片，避免用户紧接着导入截图时主界面被同步解析阻塞。

进度对话框运行中提供”中止”按钮；关闭窗口或按 Esc 也会请求中止，避免任务在后台无提示继续执行。中止后已分批提交的数据会重新加载并保留。相性任务使用”相性评分”文案：`START` 显示当前请求但保持原进度，冷却日志显示”冷却中”和当前已完成数量，只有单组配对得到 `OK`、`FAIL` 或确认 `SKIP` 后才推进进度条。`[重试]` 行（API 限流退避时由 `api_generator` 输出）被解析后状态栏显示”⏳ 重试中（n/N），w 秒后重试”，详情栏标注”当前进度 current / total，原因：...”，不推进进度条。

**失败弹窗**：攻略/相性生成失败时，工作流 `_on_guide_error`/`_on_synergy_error` 读取对应 FetchService 的 `failed_items`（从子进程 stdout 的 `[i/N] 名字 FAIL` 行收集），在 `QMessageBox` 的”查看详情”中列出失败武将/相性对清单与数量；无失败项时回退显示原始错误消息。该弹窗通过 `install_details_button_translator()` 安装详情按钮翻译过滤器，确保展开/收起时按钮文字保持中文。相性失败弹窗由原来的简单 `QMessageBox.warning` 升级为带详情的 Critical 弹窗，与攻略失败体验一致。

**语料增强选择**：`BackendChooseDialog` 顶部新增「语料增强：RAG 语料增强（推荐）/ 经典模式（无 RAG 注入）」单选组，默认 RAG 增强；`get_selected_rag()` 返回选择，`AiGenerationWorkflow._choose_backend()` 组合为 `(backend, use_rag)` 元组。API Tab 的成本估算在切换选择时按 `estimate_item_cost(..., use_rag=...)` 实时重算（经典模式输入 token 更少）。

### 3.6.1 巅峰赛选将页面（PeakSelectPanel，2026-08 新增）

`PeakSelectPanel` 是左侧导航中新增的独立工作区（第 4 页），专用于 2v2 巅峰赛选将辅助：

```
PeakSelectPanel
  ├── PageActionBar：阶段徽标 + 候选汇总 + [开始识别/停止识别] + [⋯ 更多]
  │   └── 更多菜单：[从图片导入]
  ├── EmptyState：未识别提示
  ├── 候选武将卡片区
  │   ├── 标题 + [按胜率排序] 复选
  │   └── QScrollArea → QGridLayout（两排卡片）
  │       └── PeakHeroCard × N
  │           ├── 头像区（103×140px，复用 matchPortrait objectName 样式）
  │           ├── 阵营徽章 + 实战角标
  │           ├── 状态徽章（待确认/已确认）
  │           ├── 单将胜率标签（peakHeroWinRate objectName）
  │           └── 禁选建议徽章（Ban 位首选红底/热门强将蓝底）
  ├── 待确认交互区：逐槽位显示原文与候选按钮，点击候选即确认
  ├── 已禁区：灰底带删除线展示已禁武将名
  └── 实战配队条：
      ├── 标题 + [管理] + [展开/收起]
      └── FlowLayout 芯片：★N 武将1[座次] + 武将2[座次]
  └── 识别日志
```

**核心逻辑**：
- `PeakSelectWatcher` 驱动识别循环，`pool_updated` 信号 → `_on_pool_updated()` 刷新整页
- `stage` 徽标区分"禁选阶段"（warning）与"候选阶段"（success）
- 候选池按 `_sort_by_win_rate` 开关可选按巅峰赛胜率降序排列（无胜率沉底）
- 实时匹配实战配队：`ComboManager.list_combos()` 中 hero1/hero2 均在当前池内 → 按 rating 降序显示 chip，点击 chip 打开 `show_combo_detail`
- 禁选建议徽章通过 `evaluate_peak_ban_advice(rate, pick_rank, win_rate_rank)` 渲染，`PeakHeroCard.set_ban_advice()`
- 待确认槽位点击候选即触发 `PeakSelectWatcher.confirm_pending(slot, name)`，确认后计入候选与已禁口径
- OCR 模型预热（`ocr_warmup_state == "warming"`）时禁用图片导入，避免界面冻结

### 3.7 API 配置对话框

`SettingsDialog` 由菜单“配置 → API 配置”打开，内容分为两个 Tab：

- **参数配置**：保留原 API Key、API URL、模型名称、请求频率、HTTP 超时和最大重试次数表单，仍写入 `config.env`。
- **价格配置**：维护 `config/model_pricing.json` 的币种、计价单位（百万tokens）、更新时间，以及每个模型的输入、输出和可选缓存命中单价。支持新增和删除模型。

两个 Tab 共用固定底栏中的“取消/保存”。保存价格前会校验模型名称非空、名称不重复、单价为合法非负数字；写入期间底栏进入 busy 状态，完成后显示 Toast 并关闭。写入过程使用临时文件替换，避免配置文件被部分写入。价格未配置的模型仍不会套用默认价格，成本确认界面会显示无法自动估算。

### 3.8 数据管理对话框

`DataManagementDialog` 由菜单“配置 → 数据管理”打开，可勾选批量清空武将攻略和武将相性。“清空选中数据”使用危险角色，与普通“关闭”明显区分；提交前要求输入“清空”确认，服务执行期间底栏禁用。`DataManagementService` 会先将所选 JSON 复制到 `data/backups/` 的时间戳备份文件，再清空 Manager 并原子保存正式 JSON。完成结果以模态消息列出清空数量和备份路径，随后主窗口刷新攻略详情、相性表、推荐摘要和状态栏计数。

---

### 3.9 公告更新对话框与横幅

- 菜单“数据 > 检查公告更新 / 公告记录”；工作区顶部 `NoticeBanner`：待生效公告显示“已发布，等待百科更新”，可更新公告显示“百科已更新，涉及：…”，纯 diff 变化也提示“检测到百科数据变化”。
- `AnnouncementDialog` 展示公告列表（日期/标题/变更标签/未收录标记/状态）与全文、百科 diff 变更清单。
- “更新武将数据”必须先经 `HeroUpdateConfirmDialog` 确认：列出待更新武将（名称/类型/来源/字段级差异摘要），可勾选、全选/清空、双击或按钮打开 `HeroDiffDetailDialog` 查看本地 vs 官网全文对比（Git 风格 diff，三页：差异对比/本地原文/官网原文）；未勾选的保留本地。确认后按勾选执行：调整类 → `fetch_specific(ids)`，新增类 → `fetch_incremental()`，两阶段经 `fetch_completed` 信号链式串行，完成后 `mark_applied()` 并刷新快照；全取消仅刷新快照、不执行采集。
- 状态栏新增 `_progress_bar`：公告检查/更新前置拉取用不确定动画（`_show_indeterminate_progress`），武将采集子进程用确定进度（`_on_fetch_progress` 解析 `[n/N]`），完成/失败 `_hide_progress()` 隐藏。

## 四、关键代码片段

### 4.1 当前内容上下文操作

```python
def _update_context_actions(self, _index=None) -> None:
    labels = (
        ("编辑武将", "删除武将"),
        ("编辑攻略", "删除攻略"),
        ("编辑相性", "删除相性"),
    )
    edit_label, delete_label = labels[self._detail_tabs.currentIndex()]
    self._context_edit_btn.setText(edit_label)
    self._context_delete_action.setText(delete_label)
```

> **设计思路：** 身份头部始终只显示一个当前内容编辑入口，删除作为危险低频命令进入“更多”菜单。切换页签只更新操作文字、处理方法和可用状态，不改变原编辑、删除回调及二次确认。

### 4.2 推荐面板 OCR 导入

```python
def load_from_ocr(self, ocr_results: list[dict]) -> None:
    self._ocr_mode = True
    self._current_hero_ids.clear()

    for item in ocr_results:
        idx = item.get("index", 0) - 1
        name = item.get("name", "")
        confidence = item.get("confidence", 0.0)

        hero = self._hero_mgr.get_hero_by_name(name)
        if hero:
            card.set_hero(hero)
            card.set_recommendation_index(indexes.get(name))
            self._current_hero_ids.add(hero.id)
        else:
            card.set_unrecognized_name(name, confidence)

        # 相性 + 推荐快照加载
        self._load_real_synergies(idx, hero.id if hero else 0)
        self._load_card_stats(idx, name, recommendation_data)

    # 历史单将胜率排序 + TOP 徽章
    self._apply_medal_rankings()
```

> **设计思路：** OCR 置信度不参与推荐指数。页面按当前版本三份官方榜单生成快照，卡片以“推荐指数：分数 / 评级”显示，例如“推荐指数：92 / S”；右侧圆形感叹号悬停说明计算口径。胜率、出场或禁用数据缺失时显示“推荐指数：-- / 数据不足”。高相性组合在 OCR 模式下仅显示当前 8 人之间的相性，通过 `_current_hero_ids` 集合和 `_ocr_mode` 标志过滤。

---

## 五、接口说明

### 主窗口公共方法

| 方法 | 说明 |
|------|------|
| `reload_data()` | 重新加载所有数据并刷新 UI |

`_load_data()` 由启动与「重载数据」触发：调用 `DataFacade.load_all()` 后检查返回报告中的 `missing_reference` 问题，有则弹「发现数据关联问题」Yes/No 确认；选 Yes 即经 `DataMutationService.repair_missing_references()` 修复失效相性/攻略关联并重载、最后弹信息汇总（删除相性/攻略数、清理攻略关联数），选 No 弹警告保留原始数据；加载异常统一兜底为「数据加载失败」警告弹窗并提示核对 `heroes.json`。

### HeroBrowser 公共方法

| 方法 | 说明 |
|------|------|
| `reload_data()` | 重新加载武将列表 |

`HeroBrowser` 在连接 `HeroListPanel.hero_selected` 信号后，会主动读取并展示列表初始选中的首个武将，避免列表构造阶段发出的首项选择信号丢失。

### 3.3.1 卡牌图鉴

主级“资料库”内容页以“资料库”标题建立页面归属，再用浅色下划线式二级切换器提供“武将资料”和“卡牌图鉴”。`CardManagementPanel` 保持紧凑的左侧列表/右侧详情布局：顶部单行工具栏提供搜索、类型、调整状态、重置和更多操作；左栏限制为 240–360px，类型分组显示数量，卡牌行显示名称、ID、牌堆数量及生效中/待核实摘要。卡牌项的默认显示角色保持为空，名称只由自定义行绘制，并通过无障碍文本角色保留语义，避免透明行控件与列表默认委托重复绘制造成文字残影。右侧以单一白色基础资料表面展示名称、类型、牌堆数量、简述和规则详解，版本调整位于其下方；“编辑版本调整”只打开当前卡牌的追加内容编辑，“字段配置”仍位于右上角省略号菜单。切换卡牌后滚动位置回到顶部，回顶回调以详情滚动区作为生命周期上下文，页面销毁时由 Qt 自动取消；详情不产生横向滚动。搜索覆盖基础字段和追加内容，支持类型及加强/削弱、生效中、待核实状态筛选。

追加内容仅由 `CardAnnotationEditDialog` 保存：效果记录可通过“新增效果记录”加入列表，也可点击“编辑”修正已保存记录；修改时保留创建时间并更新修改时间，两个时间字段仅写入数据文件、不在界面展示。底部“保存”会一并收集尚未点击新增按钮的已填写效果表单；效果说明未填时显示中文提示，保存失败时对话框保持草稿。`active`、`pending`、`expired` 分别显示“生效中”“待核实”“已失效”，同时使用成功色、警示色、中性灰和对应左侧强调线；`active` 置顶，其余记录按状态和修改时间排序。`CardFieldSchemaDialog` 支持新增、修改显示属性、停用和归档字段，已有值的字段禁止改类型。归档字段及已失去定义的历史字段在详情页只读保留。卡牌切换时，详情区会递归释放控件和嵌套布局，避免旧卡片的操作栏残留。

### HeroDetailPanel 公共方法/信号

| 方法/信号 | 说明 |
|-----------|------|
| `show_hero(hero_id)` | 显示指定武将详情 |
| `data_changed()` | 数据修改后发射，通知列表刷新 |

### RecommendationPanel 公共方法

| 方法 | 说明 |
|------|------|
| `update_recommendations(data)` | 更新推荐武将数据 |
| `load_from_ocr(ocr_results)` | OCR 识别结果导入 |

### 对话框列表

| 对话框 | 用途 |
|--------|------|
| `SettingsDialog` | API 配置编辑 |
| `DataManagementDialog` | 备份后批量清空攻略或相性数据 |
| `MumuConfigDialog` | 组装配置区块、状态协调、文件选择和 ROI 框选；服务操作委托 `MumuConfigCoordinator` |
| `MumuDeviceSection` / `MumuTemplateSection` / `MumuOcrPollingSection` | 设备、模板和 OCR 参数控件及用户操作信号，不调用业务服务 |
| `BackendChooseDialog` | AI 后端选择（API/浏览器）+ 语料增强（RAG/经典），返回 `(backend, use_rag)` |
| `GuideProgressDialog` | 攻略/相性生成进度显示 |
| `HeroEditDialog` | 武将信息编辑 |
| `GuideEditDialog` | 攻略内容编辑 |
| `HeroRelationSelectDialog` | 搭配推荐武将的搜索多选 |
| `RoiSelectorDialog` | 模板 ROI 区域框选 |
| `BaseHeroSelectDialog`（及其子类） | 武将选择 |
| `IndexRefinementDialog` | 索引精化：LLM 建议/人工补全卡牌与武将语料索引字段（curated，重建不覆盖） |
| `ProposalDetailDialog` | 元规则提案项差异对比 + 文档上下文（只读） |
| `ProposalItemConfirmDialog` | 元规则提案项逐条确认（approved/revised/rejected + 可编辑文本） |
| `DiffDetailDialog` | 数据段差异详情（行号定位 + Git 风格 diff + 文档过期警示） |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.manager` | 通过 DataFacade 读取/写入数据 |
| 依赖 | `src.data.combo_manager` | PeakSelectPanel 匹配实战配队 |
| 依赖 | `src.data.peak_win_rate_repository` | 巅峰赛胜率/出场排行数据 |
| 依赖 | `src.business.*` | 连接业务服务的 Signal，触发 fetch_*() |
| 依赖 | `src.business.analysis.peak_ban_advice` | 巅峰赛禁选建议 |
| 依赖 | `src.business.recognition.peak_select_watcher` | 巅峰赛识别循环驱动 |
| 依赖 | `src.business.maintenance.corpus_services` | 实战配队 ComboService |
| 依赖 | `src.config.env` | 配置文件读取 |
| 依赖 | `src.business.emulator.mumu_config_coordinator` | 模拟器配置对话框委托配置草稿、设备和模板协调 |
| 依赖 | `src.ocr.*` | 模板管理 + OCR 识别 + 卡位检测 |
| 被调用方 | `src.main.py` | 应用入口创建 MainWindow 实例 |

## 七、专属牌维护与知识库维护（2026-08 新增，2026-08 布局重排）

- **布局重排（2026-08）**：主导航第 4 页「知识库维护」由「6 个一级页签平铺」重排为「左栏维护对象导航 + 右侧数据源工作区 + 底部折叠执行日志」三区结构，一级页签归零、导航层级由 3 级降为 2 级。外壳由新增的 `src/ui/maintenance/maintenance_workspace.py` 承载，业务逻辑仍集中在 `rag_maintenance_panel.py`（由页签装配器改为 workspace 装配器，`task_states()` / `refresh()` / `_run()` / `_jump_to_issue()` 与 `ScriptRunner` 全部保留）：
  - `MaintenanceSourceNav`（固定宽 230px）分两组共 10 项：上组「维护对象」5 项可编辑（武将分类 / 专属牌 / 卡牌点数 / 装备属性 / 元规则母本），下组「只读语料」5 项仅状态（武将语料 / 卡牌语料 / 加强削弱 / 组合语料 / 武将攻略语料）。项行高 30px、分组标题行高 24px，内容高 348px，在日志折叠 32px 与展开 180px 两种状态下均不滚动。行结构为「状态点 8px + 名称 + 状态词 + 单项重建按钮 24px」，状态点与词按 `最新`=`neutral` / `待重建`=`warning` / `缺源`=`danger` 着色，选中态 `PRIMARY_SOFT` 底 + 左侧 3px `PRIMARY` 条；信号 `source_selected` / `rebuild_requested` / `meta_requested`。
  - `MaintenanceWorkspace`：左栏 + `QStackedWidget`（5 个现有面板**实例复用**切换，保留各面板内部选中项与滚动位置）+ 底部 `QSplitter` 日志区，默认折叠 32px、点重建自动展开 180px。折叠态累计未读输出行数并以 warning `StatusBadge` 亮出「N 行新输出」，展开即 `reset_unread()`；日志标题栏右侧 `set_log_meta()` 显示「退出码 N · X.Xs」。构建期间 `set_interactive(False)` 禁用左栏切换与单项重建。
  - **保存→重建闭环**：维护对象保存后 `data_changed` → `refresh()` → 左栏对应项状态点即时变「待重建」并亮出 ↻，全程不离开当前视图（替代原 toast 提示）。重建粒度为左栏 ↻ 单项 `--only <语料>`、顶部「重建全部语料」`--force`、「重建语料+索引」`--force --build-index`（全模块唯一 `ROLE_PRIMARY`）；原「重建武将语料」按钮已移除，单项入口收敛到左栏。
  - **审计提示条上移到 workspace 顶部**（有提示才出现），最多 3 条、超出折叠为「还有 N 条提示，处理后点击「刷新状态」查看全部」；每条带跳转按钮（`unclassified_hero`→「去归类」、`missing_settlement`→「去补全」、其余「去检查」），`pending_refinement` 直接打开索引精化对话框。跳转目标由页签索引改为左栏项 key：`AuditIssue.target_tab` 仍是页签名（如「武将分类维护」），`removesuffix("维护")` 即左栏 key，故 `audit_service.py` 无需改动；`focus_unclassified()` / `focus_item()` 面板内定位逻辑不变。
  - **只读语料项**：数据源由采集流程管理、本模块不可编辑，点击弹 `QMessageBox` 元信息（任务名 / 状态 / 输出块数与期望 / 来源文件 / 上次构建时间）而**不切右侧**；「待重建」时同样可点 ↻ 单项重建（语料仍由 `scripts/maintain_rag.py` 生成）。
- 数据源维护面板「专属牌」：src/ui/library/special_cards_panel.py，维护 data/special_cards.json（专属牌/专属战法牌/特殊牌区/状态·标记/概念），数据层 src/data/special_cards_repository.py，保存后发 data_changed 信号。专属牌/专属战法牌条目含牌面事实字段（花色/点数/攻击范围/结算详情，由原 xlsx【专属牌】sheet 迁移回填）；`focus_item(category, name)` 供知识库维护审计跳转定位。
- **语料任务单一事实源与审计驱动**：`src/business/rag/task_defs.py` 的 `TASKS` 定义 10 个语料任务（名称 / 生成脚本 / 源文件 / 输出语料 / 期望块数；`expected` 为 int 精确匹配、`"snapshot"` 快照只增不删、`None` 动态仅报不校验），`rag_maintenance_panel.TASK_DEFS` 与 `scripts/maintain_rag.py` 共用。`task_states(root)` 按源文件最大 mtime 与语料输出 mtime 比较判定 `最新` / `待重建` / `缺源`（`sources` 中文件不存在即缺源），块数读取带 `(mtime, size)` 缓存 `_output_count()`。审计由 `src/business/rag/audit_service.py` 的 `AuditIssue`（kind / message / severity / target_tab / target）驱动，同一轮已算好的 `list_pending()` 清单传给 `audit_summary` 避免重复读文件：未归类武将 →「武将分类」`focus_unclassified()`；分类表引用未知武将（`orphan_category_key` 反向校验）→「武将分类」；专属牌未知武将 / 缺结算 →「专属牌」`focus_item()`；索引字段待精化 → 直接打开索引精化对话框。「索引精化」入口按钮带待精化数量角标；**无待办时文案为「索引精化 ✓」但仍可点击**进入浏览/管理已精化块；维护任务执行期间入口与左栏一并禁用。语料/索引重建仍通过 `ScriptRunner` 封装 QProcess 本地执行 `scripts/maintain_rag.py`，不依赖外部 mjs 仓库。
- 「卡牌点数」维护对象：src/ui/maintenance/card_points_panel.py + src/data/card_points_repository.py，维护 data/card_points.json（162 张牌花色点数 + 12 条卜卦判定规则，由原 xlsx sheet1 与硬编码 attr_judge 迁移）；支持牌行/规则增删改与「从 xlsx 导入」（scripts/migrate_excel_to_json.py --only points）。
- 「装备属性」维护对象：src/ui/maintenance/equip_attrs_panel.py + src/data/equip_attrs_repository.py，维护 data/equip_attrs.json（26 件装备细分/攻击范围/距离修正，由原 xlsx sheet2 与 build_equip_attr.py 硬编码 EQUIP_ATTRS 迁移），表格编辑 + 保存校验。
- 「武将分类」维护对象：src/ui/library/hero_classification_panel.py + src/data/hero_classification_repository.py，维护 data/hero_classification.json（分类 CRUD / 克制链 / 武将归类）；新增 `focus_unclassified()` 供审计跳转定位首个未归类武将。2026-08 体验优化：重载/刷新前有未保存修改先确认（`reload_data(confirm_discard=True)`）、加载失败（error）禁用「保存」防止空数据覆盖、武将搜索 150ms 防抖（QTimer）、名称标签 PlainText、归类多选 `set_items(..., default_all=False)` 避免误全选。
- 数据安全与性能（2026-08 知识库维护优化）：四个维护面板（专属牌/卡牌点数/装备属性/武将分类）统一继承 `JsonRepository`——写盘失败自动回滚内存并重新对齐界面；装备属性表格一次性分配行并恢复滚动位置；卡牌点数「从 xlsx 导入」改 `ScriptRunner` 异步执行（按钮置「导入中…」不阻塞 UI）；`recommendation_index_repository.mark_recommendation_index_stale()` 记录 traceback 调用来源日志，便于定位待重建状态意外写入。
- 「元规则母本」维护对象：src/ui/maintenance/rule_doc_panel.py + src/business/rag/rule_doc_service.py，维护规则知识库 T0 母本 docs/元规则整理-完整版.md（只增不删、机器校验）。四个内嵌子页签保留在右侧工作区内（布局重排未压平）：① 文档状态（audit_rule_doc.py 校验摘要与问题明细）② 数据段差异（sync_rule_stats.py --json 预览，勾选 + 确认值后一键应用）③ 提案工作台（propose_rule_changes.py 生成提案、apply_rule_proposal.py 合入已确认提案）④ 疑难登记（docs/rule_doc_pending.json 增查与转 FAQ 提案）。所有脚本经 QProcess 执行，日志统一汇入工作台底部可折叠日志区，顶部按状态给出下一步建议。
- 「索引精化」对话框（IndexRefinementDialog，1160×720）2026-08 重设计并扩展已处理块管理：对卡牌/武将语料中无 curated 且索引字段为空的块补 timing / trigger_condition / keywords / related 四个字段（去掉了原 target 字段）。**三态块模型**：`pending` 待精化（无 curated 且字段空缺）/ `curated` 已精化（有 curated）/ `normal` 已生成（无 curated 且四字段全非空，构建规则已填满）；纯函数服务 `refinement_service.scan_blocks()` 一次扫描三分类，`apply_curated()`/`clear_curated()` 原子读写语料文件；清单归属、磁盘/LLM 双基线与行状态由 `RefinementSession`（纯 Python 状态层，无 Qt 依赖）持有，dialog 只读透出。**模式切换**：顶部总览条右对齐「待精化 / 已精化 / 全部」三档（待精化显示进度条，已精化/全部显示统计文案），类型筛选（全部/卡牌/武将）移入清单区与搜索框同行；范围切换只过滤内存快照不重复读文件。清单列 2 按范围扩展：待精化=缺失字段、已精化=`method · updated_at`、全部=按块状态；状态列新增 `✓ 已精化`。工作区左原文卡片占满高度 + 右 4 字段状态卡片（fieldState=empty/llm/manual/saved 着色）；LLM 建议由 `SuggestController`（QObject）编排 `SuggestWorker`（QThread）后台逐块调用，结果经 `result_ready`/`finished` 信号回主线程，`LIVE_WORKERS` 全局持有与 `_zombies` 列表防止 dialog 销毁后运行中线程被析构。**统一保存模型**：dialog 收集字段文本交 `RefinementSession.collect_update()` 与磁盘基线比对判定改动（与本次 LLM 建议内容一致记 `method=llm`，否则 `manual`），单块保存经 `sync_saved()` 写回并迁移池，「保存全部」按语料文件分组经 `apply_updates()` 批量写回（单文件失败上报错误且不迁移其任何块）；切回已建议条目还原 LLM 基线内容。「取消精化」二次确认后由 `clear_curated_block()` 删除 curated 使块退回 pending/normal；关闭时 `cancel_and_shutdown()` 中止在途建议并善后。入口按钮带待精化数量角标（如「索引精化（5）」），无待办显示「索引精化 ✓」仍可进入浏览；审计横幅「索引字段待精化 N 块」点击直接打开对话框。
