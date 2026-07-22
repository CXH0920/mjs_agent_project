# 模块：UI 界面层

> 对应目录：`src/ui/`
> 职责：PySide6 桌面用户界面，包含主窗口、武将浏览器、推荐面板、对局攻略页面和各种对话框

---

## 一、模块职责

本模块是用户与程序交互的**门户**，提供完整的桌面应用界面：

- **主窗口** — 菜单栏、Tab 切换、状态栏，协调所有业务服务的信号连接
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
├── mumu_config_dialog.py       # 模拟器配置对话框
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

胜率 CSV 读取位于 `src/data/win_rate_repository.py`。页面只依赖共享模块的公开名称，不再从 `recommendation_panel.py` 导入私有函数或复用其内部缓存。

---

## 三、核心逻辑

### 3.1.1 轮询匹配后的选将推荐跳转

`MainWindow._on_poll_result()` 使用 `_selection_page_active` 记录是否已经进入选将页面。首次收到 `matched` 结果时切换到“选将推荐”Tab；冷却期间的后续匹配只调用 `RecommendationPanel.load_from_ocr()`。收到 `healthy_no_match` 后才重置状态，避免截图暂时失败或 OCR 重试导致页面状态抖动。

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
- 核心要点、新手提示、被克制、搭配推荐和 Markdown 预览按单列顺序堆叠。
- 有攻略数据时，`QTextBrowser` 占满内容宽度作为正文预览，双击后打开 `GuideMarkdownDialog` 查看完整内容；无攻略数据时隐藏该正文框。
- 克制/搭配关系使用可点击标签，点击后通过 `hero_requested` 信号切换到对应武将。

### 3.4 推荐面板

```
RecommendationPanel (QWidget)
 ├── 标题行 + [截图] [📁 从图片导入] 按钮
 └── QGridLayout (4行 × 2列)
      └── HeroCardWidget × 8
           ├── 头像区（130px, 浮层显示名称 + 势力色块，左键双击打开技能详情）
           └── 信息区
               ├── 武将名 + [攻略] 按钮
               ├── 推荐指数（星级 + 百分比）
               ├── 高相性组合（OCR 模式下仅显示当前 8 人相性）
               └── 胜率 + 前三 🥇🥈🥉 奖牌
       └── HeroSkillDialog（来自 ui/shared/hero_dialogs.py，按技能名称分 Tab 展示描述和结算）
```

`recommendation_panel.py` 只保留推荐数据更新、胜率/相性加载、OCR 导入和截图信号协调。可独立维护的展示组件已拆分为：

- `hero_card_widget.py`：`HeroCardWidget`，负责头像、势力配色、推荐指数、相性摘要、胜率奖牌及卡片信号。
- `guide_detail_dialog.py`：`GuideDetailDialog`，负责攻略摘要、关系标签跳转和 Markdown 正文渲染。

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
- 三个排名保留 `TOP 1/2/3` 徽章，普通卡片保持原样。

渐变边框在 `HeroCardWidget.paintEvent()` 中绘制；头像区和信息区使用透明背景，避免子控件背景覆盖边框效果。

### 3.4.2 势力配色配置

势力配色由 `FactionColorDialog` 以紧凑列表展示，每行只显示势力名称、颜色小方块和 Hex 代码，不在主界面长期占用调色板区域。点击颜色小方块后打开 `ColorPicker` 浮层，提供 HSB 调整和屏幕取色；取消时恢复打开前的颜色，确认后才写入配置页草稿。

模拟器配置中的两个模板制作按钮在 ADB 已配置但尚未连接时仍可点击，制作流程会自动尝试建立连接；只有未配置 ADB 或正在连接时禁用。

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

`MatchGuidePanel` 与武将浏览、选将推荐处于同一主窗口 Tab 层级。页面使用 2×2 卡片展示四名武将：头像放置区域固定为 135×162px（5:6），实际头像固定为 120×160px（3:4）并在区域内居中靠上；头像左上叠加势力标签，底部叠加宽 130px、略宽于头像且无圆角的半透明名称浮层，名称使用较大加粗字体；名称浮层正下方显示放大加粗的“胜率：xx.x%”。双击头像打开复用的技能详情弹窗。卡片另有“阵营待定”预留标签。势力颜色从 `data/faction_colors.json` 读取，找不到时使用灰色，配置保存后立即刷新。未导入截图时按武将 ID 升序加载最小的四名武将。

选将推荐与对局攻略的 ADB 截图按钮均仅保存画面；本地图片导入才复用 `CaptureService` 的 OCR 流程。未配置 ADB 时通过 `request_mumu_config` 信号打开模拟器配置。

对局攻略页面由后台轮询命中独立模板后触发跳转，重复命中只更新预留数据，不重复抢占用户页面。

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
            card.set_hero(hero, confidence=0.5)  # 推荐指数固定 0.5
            self._current_hero_ids.add(hero.id)

        # 相性 + 胜率加载
        self._load_real_synergies(idx, hero.id if hero else 0)
        self._load_win_rate_by_name(idx, name)

    # 胜率排序 + 奖牌标记
    self._update_medals()
```

> **设计思路：** OCR 置信度（反映图像识别准确率）不映射为推荐置信度（反映阵容适配度），固定 0.5 表示"来自截图识别"。高相性组合在 OCR 模式下仅显示当前 8 人之间的相性，通过 `_current_hero_ids` 集合和 `_ocr_mode` 标志过滤。

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
| `MumuConfigDialog` | ADB 连接管理 + 模板制作 + OCR 配置 |
| `BackendChooseDialog` | AI 后端选择（API/浏览器） |
| `CostConfirmDialog` | 遗留 AI 成本确认组件；当前费用估算展示在 `BackendChooseDialog` |
| `GuideProgressDialog` | 攻略/相性生成进度显示 |
| `HeroEditDialog` | 武将信息编辑 |
| `GuideEditDialog` | 攻略内容编辑 |
| `HeroRelationSelectDialog` | 被克制/搭配推荐武将的搜索多选 |
| `RoiSelectorDialog` | 模板 ROI 区域框选 |
| `BaseHeroSelectDialog`（及其子类） | 武将选择 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.manager` | 通过 DataFacade 读取/写入数据 |
| 依赖 | `src.business.*` | 连接业务服务的 Signal，触发 fetch_*() |
| 依赖 | `src.config.env` | 配置文件读取 |
| 依赖 | `src.capture.*` | 模拟器配置对话框使用 AdbCapture |
| 依赖 | `src.ocr.*` | 模板管理 + OCR 识别 |
| 被调用方 | `src.main.py` | 应用入口创建 MainWindow 实例 |
