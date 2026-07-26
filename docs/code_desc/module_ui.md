# 模块：UI 界面层

> 对应目录：`src/ui/`
> 职责：PySide6 桌面用户界面，包含主窗口、武将浏览器、推荐面板、对局攻略页面和各种对话框

---

## 一、模块职责

本模块是用户与程序交互的**门户**，提供完整的桌面应用界面：

- **主窗口** — 菜单栏、Tab 切换、状态栏，协调所有业务服务的信号连接；默认尺寸为 1100×760px
- **武将浏览器** — 武将列表搜索/筛选、详情查看、攻略展示、武将/攻略编辑
- **选将推荐** — 4×2 网格推荐卡片、OCR 截图导入、相性/胜率展示
- **对局攻略** — 同级 Tab 页面，以 2×2 卡片展示四名武将及胜率
- **对话框体系** — 武将选择、配置编辑、后端选择、进度显示等
- **全局样式** — 统一的天蓝色调样式表

---

## 二、文件结构

```
src/ui/
├── app_icon.py                 # 应用图标加载、缓存与窗口图标维护
├── __init__.py
├── shared/                     # 跨页面控件、技能弹窗和势力配色访问器
│   ├── widgets.py              # DoubleClickLabel 等共享控件
│   ├── hero_dialogs.py         # HeroSkillDialog
│   └── faction_colors.py       # 势力配色读取、兜底和缓存
├── style.py                    # 全局样式表（天蓝色调）
├── main_window.py              # 主窗口（菜单栏/Tab/状态栏 + 轮询编排）
├── official_data_import_dialog.py # 官方 2v2/武将放逐榜单图片导入
├── ai_generation_workflow.py   # 攻略/相性生成的选择、进度与完成工作流
├── hero_browser.py             # 武将浏览（列表+详情+Tab 栏编辑按钮）
├── hero_edit_dialog.py          # 武将基础信息编辑
├── hero_relation_select_dialog.py # 攻略关系武将多选
├── guide_edit_dialog.py         # 攻略内容编辑
├── synergy_edit_dialog.py       # 相性评分编辑
├── hero_select_dialog.py       # 武将选择对话框基类
├── recommendation_panel.py     # 选将推荐面板（4×2 网格+头像+相性+OCR 导入）
├── match_guide_panel.py        # 对局攻略页面（四名武将卡片+双导入）
│
├── settings_dialog.py          # API 配置对话框
├── data_management_dialog.py   # 攻略与相性批量清空对话框
├── mumu_config_dialog.py       # 模拟器配置对话框（表单、状态与 ROI 框选）
├── backend_choose_dialog.py    # 后端选择对话框（API/浏览器）
├── cost_confirm_dialog.py      # 遗留的 AI 成本确认对话框（当前流程未调用）
├── guide_progress_dialog.py    # 攻略生成进度条
├── roi_selector.py             # 模板 ROI 框选对话框
├── faction_color_dialog.py     # 势力配色列表、Color Picker 与保存
│
├── fetch_dialog.py             # 武将获取选择（继承基类）
├── guide_fetch_dialog.py       # 攻略获取选择（继承基类）
├── synergy_pair_dialog.py      # 相性指定获取（选 2~8 武将）
└── synergy_single_dialog.py    # 相性选定武将（选 1 武将）
```

胜率 CSV 读取位于 `src/data/win_rate_repository.py`；推荐指数快照由 `src/data/recommendation_index_repository.py` 根据三份官方榜单生成。页面只依赖共享模块的公开名称，不再从 `recommendation_panel.py` 导入私有函数或复用其内部缓存。

`OfficialDataImportDialog` 由“数据 > 官方数据导入”打开，包含“2v2数据导入”和“武将放逐数据导入”两个可独立选择的图片框。确认后通过后台线程调用 `OfficialDataImportService`：服务按两种样图各自的表头和列比例切分榜单，再依据 OpenCV 检测到的横线确定实际数据行数，逐单元格 OCR。2v2 图片左表覆盖 `data/2v2胜率排行.csv`（`排名,武将,胜率`），右表独立覆盖 `data/2v2出场排行.csv`（`排名,武将`）；放逐榜左右表合并覆盖 `data/武将放逐.csv`。名称可靠性由业务层保证：完整词表候选优先，单字才逐字补识别；同首字无法唯一确认时由业务层按需使用繁体模型，仍不能确认才进入待复核。任一异常行仍保留期望排名，并写入对应的 `*_待复核.csv` 与 `screenshot_data/official_import/` 行截图。

### 2.1 官方数据导入对话框

`OfficialDataImportDialog` 是“数据 > 官方数据导入”的唯一入口。两个只读文件框均使用图片过滤器；用户可以只选择其中一种，也可以同时选择。点击“导入”后创建一个后台 `OfficialDataImportWorker`，按钮在任务期间禁用，避免同一对话框重复提交或关闭时销毁运行中的线程。弹窗会先显示准备中的不定进度，检测到表格行后切换为当前文件的精确 OCR 进度；进度总量包含胜率数字模板准备和逐行识别，同时选择两张图片时会标明当前文件序号。成功导入后，弹窗将推荐指数快照持久化标记为“待重建”，并通知推荐页面更新按钮状态；不会自动重建，避免未复核 OCR 数据直接影响推荐。罕见字兜底时仅更新为对应状态文字，保留已完成行的进度值。

```python
paths = {key: widget.text() for key, widget in self._paths.items() if widget.text()}
self._worker = OfficialDataImportWorker(paths, self)
self._worker.progress_changed.connect(self._on_progress_changed)
self._worker.completed.connect(self._on_completed)
self._worker.failed.connect(self._on_failed)
self._worker.start()
```

**公共交互接口：**

| 方法/状态 | 输入 | 结果 |
|---|---|---|
| `_choose_file(path_input)` | 图片路径 | 回填对应文件框 |
| `_start_import()` | 已选择的 2v2 和/或放逐路径 | 启动后台导入；无路径时弹出提示 |
| `_on_progress_changed(status, current, total)` | 工作线程进度 | 更新准备状态或当前文件的进度条 |
| `_on_completed(summaries)` | 服务摘要列表 | 显示导入条数与复核条数后关闭 |
| `_on_failed(message)` | 错误文本 | 恢复按钮并显示失败原因 |

对话框依赖 `src.business.official_data_import_service`，由 `MainWindow._open_official_data_import()` 创建；它不直接读取图片、不直接写 CSV。

---

## 三、核心逻辑

### 3.1.1 轮询匹配后的选将推荐刷新

`MainWindow._on_poll_result()` 使用 `PollResult`、`PollTaskResult` 与 `PollOutcome` 接收轮询结果；后台线程边界将 OCR worker 的原始字典转换为强类型对象，旧版字典调用仍由 `from_raw()` 兼容。首次收到 `matched` 时只更新页面数据；在“配置 → 模拟器配置”勾选“识别后自动跳转到结果页面”后，才切换到对应 Tab。冷却期间的后续匹配同样调用 `RecommendationPanel.load_from_ocr()`。收到 `healthy_no_match` 后才重置状态，避免截图暂时失败或 OCR 重试导致页面状态抖动。每轮后台 OCR 等待最多 10 秒；停止轮询、关闭窗口或切换 ADB 会话会设置取消标记，已取消任务不会回写界面。

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

工作流负责 `status_changed`、完成、错误和进度信号，创建后端选择与进度对话框；成功后重载对应 Manager，再发出 `guides_changed` 或 `synergies_changed`。主窗口将状态写入状态栏，并在相性变更后刷新武将浏览与选将推荐页面。

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

SynergyPairDialog 覆盖了 `_on_accept()` 方法，允许选择 2~8 个武将（不要求正好选满 8 个）。

四类选择界面统一使用 `CheckableComboBox` 作为势力筛选控件：输入区显示彩色可删除标签，超过 5 个势力显示剩余数量；展开后提供势力搜索、浅蓝色复选列表、全选、反选和确定操作。

### 3.3 武将浏览器

```
HeroBrowser (QWidget)
 ├── HeroListPanel (左, 280px)
 │   ├── 搜索框 + 势力 ComboBox 筛选
 │   ├── QListWidget（武将列表）
 │   ├── _last_hero_id 跟踪选中项（编辑后恢复定位）
 │   ├── signal: hero_selected(int)
 └── HeroDetailPanel (右, 720px)
     ├── Tab「武将信息」→ QLabel(HTML) + 技能滚动区
     └── Tab「攻略指南」→ 可滚动单列摘要 + Markdown 预览 + 关系标签
```

武将详情刷新时会先隐藏并延迟删除旧技能卡片；这避免“重新加载数据”后立即弹出模态提示框时，延迟删除的旧控件与新控件重叠绘制。

**编辑功能：**
- `HeroEditDialog` — 编辑武将信息（名称/称号/势力/定位/体力/手牌/性别/难度）
- `GuideEditDialog` — 编辑攻略内容（核心要点/新手提示/关系武将选择/攻略正文）
- `HeroRelationSelectDialog` — 关系武将多选弹窗，提供搜索、按推荐面板势力配色显示的可删除标签下拉框、全选当前筛选和清空选择
- `SynergyEditDialog` — 编辑相性评分、配合维度和说明
- 关系标签统一为固定尺寸可点击按钮，名称过长时通过悬浮提示查看完整名称
- 修改保存后 `data_changed` 信号触发列表刷新，`_last_hero_id` 确保选中项不变

四个编辑/选择对话框位于独立模块；`HeroDetailPanel` 仅负责打开它们、调用现有 Manager 保存数据并刷新展示。为兼容现有外部导入，`hero_browser.py` 继续导入并暴露这些对话框名称。

**攻略展示布局：**
- 主浏览页保留列表与详情摘要，方便快速切换武将。
- Markdown 正文区域支持双击，打开 `GuideMarkdownDialog`（默认约 900×680）阅读完整攻略。
- 攻略 Tab 外层使用 `QScrollArea`，避免长内容超出窗口边界。
- 核心要点、新手提示、劣势/优势对局类型、对抗建议、搭配推荐和 Markdown 预览按单列顺序堆叠。
- 有攻略数据时，`QTextBrowser` 占满内容宽度作为正文预览，双击后打开 `GuideMarkdownDialog` 查看完整内容；无攻略数据时隐藏该正文框。
- 搭配推荐使用可点击标签，点击后通过 `hero_requested` 信号切换到对应武将；对局类型以文本展示。

### 3.4 推荐面板

```
RecommendationPanel (QWidget)
 ├── 标题行 + 最近识别状态 + [识别当前阵容] [保存截图] [📁 从图片导入] 按钮
 └── QScrollArea → QGridLayout (4行 × 2列，小尺寸时滚动)
      └── HeroCardWidget × 8
           ├── 头像区（130px, 浮层显示名称 + 势力色块，左键双击或 [技能] 打开技能详情）
           └── 信息区
               ├── 武将名 + [技能] [攻略] 按钮
               ├── 推荐指数（“推荐指数：”+ 星级 + S/A/B/C/D 级；悬停或点击星级查看明细，右侧圆形感叹号悬停查看口径）
               ├── 最佳搭档优先展示，其余高相性组合（OCR 模式下仅显示当前 8 人相性）
               └── 胜率 + 数据状态 + 前三 🥇🥈🥉 奖牌
       └── HeroSkillDialog（来自 ui/shared/hero_dialogs.py，按技能名称分 Tab 展示描述和结算）
```

`recommendation_panel.py` 只保留推荐数据更新、相性加载、OCR 导入、手动重建推荐指数和截图信号协调；初始显示待识别空状态，用户可从当前模拟器画面或本地图片识别阵容。胜率和推荐指数快照由 `RecommendationService` 一次读取，前三胜率排名基于数值快照计算，不再从卡片文本反解析。卡片按“推荐等级、最佳搭档、胜率与数据状态、技能/攻略操作”呈现；数据状态明确区分“数据已更新”“指数待更新”“数据不足”和“OCR 待确认”。官方榜单导入后，“重建指数”按钮会显示“待更新”；用户确认三份榜单后重建并覆盖 `武将推荐指数.csv`，同时清除待重建状态。OCR 导入和轮询仅读取已有快照。`style.py` 提供主色、成功、警告、弱化文本、表面和边框等语义色 token，推荐页与对局页的固定样式优先复用，势力色和前三名徽章仍按业务数据动态计算。可独立维护的展示组件已拆分为：

- `hero_card_widget.py`：`HeroCardWidget`，负责头像、势力配色、推荐指数、相性摘要、胜率奖牌及卡片信号。
- `guide_detail_dialog.py`：`GuideDetailDialog`，以与武将浏览器一致的单列区块展示攻略；弹窗高度受限，超出内容由滚动区承载，正文预览支持双击打开完整攻略弹窗。

为兼容既有调用，`recommendation_panel.py` 仍导入并暴露两个公开类名；页面创建卡片与打开攻略弹窗的调用方式不变。

**数据接口：**
```python
def update_recommendations(self, data: list[dict]) -> None
# data 格式: [{"index": 1, "name": "诸葛亮", "confidence": 0.9823}, ...]
```

### 3.4.1 胜率前三视觉锚点

`RecommendationPanel._apply_medal_rankings()` 仍按胜率降序计算前三名，业务排序不变；视觉强化由 `HeroCardWidget.set_medal()` 和 `paintEvent()` 完成：

- 第 1 名使用 `#FFD700` 到 `#FFA500` 的 2px 金色渐变边框，卡片保持白色背景，胜率文字使用 `#FFD700` 加粗显示。
- 第 2 名使用 `#C0C0C0` 到 `#A9A9A9` 的 1.5px 银色渐变边框，卡片保持白色背景，胜率文字使用 `#C0C0C0` 加粗显示。
- 第 3 名使用 `#CD7F32` 到 `#B87333` 的 1.5px 铜色渐变边框，卡片保持白色背景，胜率文字使用 `#CD7F32` 加粗显示。
- 三个排名保留 `TOP 1/2/3` 徽章，普通卡片保持原样；徽章行最少 28px，为 24px 徽章上下预留边距。
- 网格内容置于 `QScrollArea`，每张卡片保持自身最小尺寸；窗口尺寸不足时滚动，不压缩卡片至裁切徽章边框。

渐变边框在 `HeroCardWidget.paintEvent()` 中绘制；头像区和信息区使用透明背景，避免子控件背景覆盖边框效果。

### 3.4.2 势力配色配置

势力配色由 `FactionColorDialog` 以紧凑列表展示，每行只显示势力名称、颜色小方块和 Hex 代码，不在主界面长期占用调色板区域。点击颜色小方块后打开 `ColorPicker` 浮层，提供 HSB 调整和屏幕取色；取消时恢复打开前的颜色，确认后才写入配置页草稿。

模拟器配置中的两个模板制作按钮在 ADB 已配置但尚未连接时仍可点击。`EmulatorOperationService` 在后台通过共享 `CaptureService` 自动建立连接并获取截图，UI 线程只负责打开 `RoiSelectorDialog` 和展示结果；只有未配置 ADB 或正在连接时禁用。

保存流程如下：

```text
ColorPicker.color()
  -> FactionColorDialog._save()
  -> save_faction_colors()
  -> data/faction_colors.json
  -> ui/shared/faction_colors.reload_faction_colors()
  -> RecommendationPanel.refresh_faction_colors()
```

保存前校验每个值是否为六位 Hex 颜色，保存成功后刷新推荐卡片中的势力标签。文件不存在时使用内建兜底配色。配色页及颜色浮层的常用操作使用中文；Qt 样式统一使用 `background-color`，避免按钮刷新时出现 `Could not parse stylesheet of object QPushButton`。

### 3.5 对局攻略页面

`MatchGuidePanel` 与武将浏览、选将推荐处于同一主窗口 Tab 层级。页面标题行显示最近识别状态，并提供“识别当前阵容”与“从图片导入”入口；初始显示待识别空状态。页面使用 2×2 卡片展示四名武将：头像放置区域固定为 135×162px（5:6），实际头像固定为 120×160px（3:4）并在区域内居中靠上；头像左上叠加势力标签，底部叠加宽 130px、略宽于头像且无圆角的半透明名称浮层，名称使用较大加粗字体；名称浮层正下方显示放大加粗的“胜率：xx.x%”。双击头像打开复用的技能详情弹窗。卡片另有“阵营待定”预留标签。势力颜色从 `data/faction_colors.json` 读取，找不到时使用灰色，配置保存后立即刷新。

选将推荐与对局攻略的“识别当前阵容”均通过 `CaptureService` 截图并强制执行对应模板的 OCR；本地图片导入复用同一识别流程。未配置 ADB 时通过 `request_mumu_config` 信号打开模拟器配置。

后台轮询命中独立模板后只刷新对应页面数据，不自动切换 Tab，避免抢占用户当前页面。

### 3.6 后端选择 + 进度条

所有 AI 生成操作在 `MainWindow` 中的标准流程：

```
菜单操作
  ↓
BackendChooseDialog（API 方式 / 浏览器方式 双 Tab）
  ↓ 确认执行
GuideProgressDialog（实时进度条 + 完成/失败提示）
  ↓ 完成
数据重载 + 状态栏更新
```

### 3.7 API 配置对话框

`SettingsDialog` 由菜单“配置 → API 配置”打开，内容分为两个 Tab：

- **参数配置**：保留原 API Key、API URL、模型名称、请求频率、HTTP 超时和最大重试次数表单，仍写入 `config.env`。
- **价格配置**：维护 `data/model_pricing.json` 的币种、计价单位（百万tokens）、更新时间，以及每个模型的输入、输出和可选缓存命中单价。支持新增和删除模型。

两个 Tab 共用“保存/取消”按钮。保存价格前会校验模型名称非空、名称不重复、单价为合法非负数字；写入过程使用临时文件替换，避免配置文件被部分写入。价格未配置的模型仍不会套用默认价格，成本确认界面会显示无法自动估算。

### 3.8 数据管理对话框

`DataManagementDialog` 由菜单“配置 → 数据管理”打开，可勾选批量清空武将攻略和武将相性。界面显示两类数据当前条数，提交前要求输入“清空”确认；攻略或相性生成任务运行时拒绝执行。`DataManagementService` 会先将所选 JSON 复制到 `data/backups/` 的时间戳备份文件，再清空 Manager 并原子保存正式 JSON。完成后主窗口刷新攻略详情、相性表、推荐摘要和状态栏计数。

---

## 四、关键代码片段

### 4.1 Tab 栏 Corner Widget 按钮

```python
def _setup_corner_buttons(self) -> None:
    corner = QWidget()
    hlayout = QHBoxLayout(corner)
    hlayout.setContentsMargins(0, 0, 4, 0)
    hlayout.setSpacing(4)

    btn_style = "QPushButton { padding: 2px 12px; font-size: 12px; border-radius: 3px; }"

    self._info_edit_btn = QPushButton("修改")
    self._info_edit_btn.setStyleSheet(btn_style + "background: #e8f4e8; color: #2e7d32;")
    self._info_delete_btn = QPushButton("删除")
    self._info_delete_btn.setStyleSheet(btn_style + "background: #fde8e8; color: #c62828;")

    self._detail_tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)
    self._detail_tabs.currentChanged.connect(self._on_tab_changed)
```

> **设计思路：** `setCornerWidget` 将按钮固定在 Tab 栏右上角，与页签文字同水平高度，不占用内容区域空间。切换 Tab 时切换按钮组可见性，每个 Tab 对应自己的修改/删除按钮。按钮颜色区分操作类型——绿色安全、红色危险。

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
            card._hero_id = hero.id
            card.set_hero(hero)
            card.set_recommendation_index(indexes.get(name))
            self._current_hero_ids.add(hero.id)

        # 相性 + 胜率加载
        self._load_real_synergies(idx, hero.id if hero else 0)
        self._load_win_rate_by_name(idx, name)

    # 胜率排序 + 奖牌标记
    self._update_medals()
```

> **设计思路：** OCR 置信度不参与推荐指数。页面按当前版本三份官方榜单生成快照，卡片以星级和评级显示推荐指数，右侧圆形感叹号悬停说明计算口径；胜率、出场或禁用数据缺失时显示“数据不足”。高相性组合在 OCR 模式下仅显示当前 8 人之间的相性，通过 `_current_hero_ids` 集合和 `_ocr_mode` 标志过滤。

---

## 五、接口说明

### 主窗口公共方法

| 方法 | 说明 |
|------|------|
| `reload_data()` | 重新加载所有数据并刷新 UI |

### HeroBrowser 公共方法

| 方法 | 说明 |
|------|------|
| `reload_data()` | 重新加载武将列表 |

`HeroBrowser` 在连接 `HeroListPanel.hero_selected` 信号后，会主动读取并展示列表初始选中的首个武将，避免列表构造阶段发出的首项选择信号丢失。

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
| `MumuConfigDialog` | ADB/OCR 表单与状态展示、ROI 框选；后台操作委托 `EmulatorOperationService` |
| `BackendChooseDialog` | AI 后端选择（API/浏览器） |
| `CostConfirmDialog` | 遗留 AI 成本确认组件；当前费用估算展示在 `BackendChooseDialog` |
| `GuideProgressDialog` | 攻略/相性生成进度显示 |
| `HeroEditDialog` | 武将信息编辑 |
| `GuideEditDialog` | 攻略内容编辑 |
| `HeroRelationSelectDialog` | 搭配推荐武将的搜索多选 |
| `RoiSelectorDialog` | 模板 ROI 区域框选 |
| `BaseHeroSelectDialog`（及其子类） | 武将选择 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.manager` | 通过 DataFacade 读取/写入数据 |
| 依赖 | `src.business.*` | 连接业务服务的 Signal，触发 fetch_*() |
| 依赖 | `src.config.env` | 配置文件读取 |
| 依赖 | `src.business.emulator_operation_service` | 模拟器配置对话框委托后台 ADB 操作 |
| 依赖 | `src.ocr.*` | 模板管理 + OCR 识别 |
| 被调用方 | `src.main.py` | 应用入口创建 MainWindow 实例 |
