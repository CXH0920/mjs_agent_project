# 模块：UI 界面层

> 对应目录：`src/ui/`
> 职责：PySide6 桌面用户界面，包含主窗口、武将浏览器、推荐面板和各种对话框

---

## 一、模块职责

本模块是用户与程序交互的**门户**，提供完整的桌面应用界面：

- **主窗口** — 菜单栏、Tab 切换、状态栏，协调所有业务服务的信号连接
- **武将浏览器** — 武将列表搜索/筛选、详情查看、攻略展示、武将/攻略编辑
- **选将推荐** — 4×2 网格推荐卡片、OCR 截图导入、相性/胜率展示
- **对话框体系** — 武将选择、配置编辑、后端选择、进度显示等
- **全局样式** — 统一的天蓝色调样式表

---

## 二、文件结构

```
src/ui/
├── app_icon.py                 # 应用图标加载、缓存与窗口图标维护
├── __init__.py
├── style.py                    # 全局样式表（天蓝色调）
├── main_window.py              # 主窗口（菜单栏/Tab/状态栏 + 轮询编排）
├── hero_browser.py             # 武将浏览（列表+详情+Tab 栏编辑按钮）
├── hero_select_dialog.py       # 武将选择对话框基类
├── recommendation_panel.py     # 选将推荐面板（4×2 网格+头像+相性+OCR 导入）
│
├── settings_dialog.py          # API 配置对话框
├── mumu_config_dialog.py       # 模拟器配置对话框
├── backend_choose_dialog.py    # 后端选择对话框（API/浏览器）
├── cost_confirm_dialog.py      # AI 成本确认对话框
├── guide_progress_dialog.py    # 攻略生成进度条
├── roi_selector.py             # 模板 ROI 框选对话框
│
├── fetch_dialog.py             # 武将获取选择（继承基类）
├── guide_fetch_dialog.py       # 攻略获取选择（继承基类）
├── synergy_pair_dialog.py      # 相性指定获取（选 2~8 武将）
└── synergy_single_dialog.py    # 相性选定武将（选 1 武将）
```

---

## 三、核心逻辑

### 3.1.1 轮询匹配后的选将推荐跳转

`MainWindow._on_poll_result()` 使用 `_selection_page_active` 记录是否已经进入选将页面。首次收到 `matched` 结果时切换到“选将推荐”Tab；冷却期间的后续匹配只调用 `RecommendationPanel.load_from_ocr()`。收到 `healthy_no_match` 后才重置状态，避免截图暂时失败或 OCR 重试导致页面状态抖动。

### 3.1 主窗口信号拓扑

`MainWindow` 在初始化时连接所有业务服务的 Signal：

```
MainWindow
 ├── HeroFetchService ─── 武将采集
 ├── GuideFetchService ─── 攻略生成（含进度条 + 成本确认）
 ├── SynergyFetchService ─ 相性获取
 ├── CaptureService ──── 截图
 └── OcrService ──────── OCR + 轮询
```

每个服务连接 3-6 个信号，分别映射到不同的槽函数：
- `status_changed` → 更新状态栏
- `fetch_completed` → 弹窗提示 + 数据重载
- `error_occurred` → 弹窗警告
- `progress_output/value` → 进度条更新
- `cost_estimated` → 成本确认对话框

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

**编辑功能：**
- `HeroEditDialog` — 编辑武将信息（名称/称号/势力/定位/体力/手牌/性别/难度）
- `GuideEditDialog` — 编辑攻略内容（核心要点/新手提示/关系武将选择/攻略正文）
- `HeroRelationSelectDialog` — 关系武将多选弹窗，提供搜索、按推荐面板势力配色显示的可删除标签下拉框、全选当前筛选和清空选择
- 关系标签统一为固定尺寸可点击按钮，名称过长时通过悬浮提示查看完整名称
- 修改保存后 `data_changed` 信号触发列表刷新，`_last_hero_id` 确保选中项不变

**攻略展示布局：**
- 主浏览页保留列表与详情摘要，方便快速切换武将。
- Markdown 正文区域支持双击，打开 `GuideMarkdownDialog`（默认约 900×680）阅读完整攻略。
- 攻略 Tab 外层使用 `QScrollArea`，避免长内容超出窗口边界。
- 核心要点、新手提示、被克制、搭配推荐和 Markdown 预览按单列顺序堆叠。
- `QTextBrowser` 占满内容宽度作为正文预览，双击后打开 `GuideMarkdownDialog` 查看完整内容。
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
       └── HeroSkillDialog（头像双击弹窗：按技能名称分 Tab 展示描述和结算）
```

**数据接口：**
```python
def update_recommendations(self, data: list[dict]) -> None
# data 格式: [{"index": 1, "name": "诸葛亮", "confidence": 0.9823}, ...]
```

**势力配色**从 `data/faction_colors.json` 加载，启动后缓存到全局变量。文件不存在时使用内建兜底配色。

### 3.5 后端选择 + 进度条

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
| `_get_heroes_as_dicts()` | 获取所有武将的 dict 格式列表 |

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
| `CostConfirmDialog` | AI 成本确认 |
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
