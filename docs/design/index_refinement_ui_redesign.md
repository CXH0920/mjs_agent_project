# 索引精化 UI 重设计（语料状态页入口 + 精化对话框）

> 适用范围：「知识库维护」→「语料状态」页内的索引精化入口（`rag_maintenance_panel.py`）
> 与索引精化对话框（`index_refinement_dialog.py`），含配套样式（`style.py`）。
> 状态：**设计方案（待评审）**。评审通过后按「实现映射」落地，并同步更新
> `docs/spec/spec_rag_maintenance_ui.md` 第五节。
>
> 与既有文档的关系：`docs/design/rag_maintenance_ui_layout.md` 是 2026-08 第一期
> 布局改造（语料状态页三段式 + 页签重排），其中对索引精化仅写「保持现状」；
> 本文档是该对话框的专项重设计，不推翻第一期布局，只增强入口与对话框本体。

## 1. 背景与现状

索引精化的工作流：`list_pending()` 扫描卡牌/武将语料中「无 `curated` 且任一索引字段为空」的块
→ 用户在对话框中人工填写或请求 LLM 建议 → `apply_curated()` 写回（`curated` 分层，重建不覆盖）。

现状实现（`index_refinement_dialog.py`，1080×680 模态对话框）：

```
┌───────────────────────────────────────────────────────────────┐
│ PageHeader: 索引精化（副标题一行）                              │
│ 状态文字（待精化 N 块）                                         │
├──────────────────────────┬────────────────────────────────────┤
│ 清单表（4 列）            │ 条目标题（名称 · block_id）          │
│ 语料|名称|缺失字段|block_id│ 原文（只读，maxHeight 150）        │
│ [LLM建议当前][LLM建议全部] │ 时机 / 触发条件 / 关键词 / 关联     │
│ [跳过当前]                │ （4 个等宽 QPlainTextEdit 平铺）    │
├──────────────────────────┴────────────────────────────────────┤
│ [保存全部] [保存当前] [关闭]                                    │
└───────────────────────────────────────────────────────────────┘
```

### 现状问题诊断

| # | 问题 | 影响 | 对策（本文档） |
|---|------|------|----------------|
| P1 | 对话框无专属样式（`style.py` 无任何 `indexRefine*` 规则），4 个字段等高平铺、无分组视觉 | 编辑区主次不分，工作台质感弱 | §5.4 字段卡片化 + §6.2 新增 QSS |
| P2 | 清单表 `block_id` 占一列 Stretch，信息密度低；无搜索/筛选/分组 | 待精化量大（如数百技能块）时难以定位目标 | §5.3 搜索框 + 类型筛选 + 状态列 |
| P3 | 无「当前条目是否已 LLM 建议 / 是否被人工改动」的视觉反馈 | 用户不知道哪些字段来自 LLM、哪些是自己改的；`method=llm/manual` 逻辑 UI 不可见 | §5.4 字段状态徽标 + 卡片左边框色 |
| P4 | `_suggest_all` 是同步 for 循环，LLM 请求期间 UI 冻结，仅文字进度 | 批量建议时窗口无响应，体验差 | §5.6 事件循环化（QTimer 队列） |
| P5 | 切换条目/关闭不检查未保存修改，编辑内容静默丢失 | 误操作丢数据 | §5.6 脏状态保护（确认弹窗） |
| P6 | 无进度总览，不知道已完成多少、剩余多少 | 长清单缺乏完成感 | §5.2 进度条 + 统计 |
| P7 | 入口按钮无待精化数量提示；全部完成后无空状态反馈 | 用户不知道是否有活要干、干完没有 | §4 入口角标 + §5.3 空状态 |
| P8 | 批量建议失败无汇总反馈（单块失败仅静默跳过） | 用户不知道哪些块没生成成功 | §5.6 失败汇总提示 |

## 2. 设计目标与原则

1. **延续项目现有视觉体系**：全部复用 `style.py` 设计令牌（SURFACE/BORDER/PRIMARY/SUCCESS/WARNING
   及 SOFT 变体、SPACE_*、RADIUS_*、FONT_SIZE_*），只新增 `indexRefine*` objectName 的 QSS 规则，
   不改全局样式；观感与「语料状态」页工作台一致。
2. **三明治信息架构**：总览（进度）→ 清单（导航）→ 工作区（编辑），自上而下单一视线流。
3. **状态可见**：每个字段、每个条目、整体进度都有明确的状态呈现（空/LLM/人工/已保存）。
4. **防误操作**：未保存修改必须可感知、可确认。
5. **功能零缩减**：LLM 建议（当前/全部）、人工填写、保存当前/全部、跳过全部保留；
   服务层 `refinement_service.py` 不修改。
6. **测试兼容**：`IndexRefinementDialog` 内部接口（`_table`/`_field_editors`/`_llm_baseline`/
   `_pending`/`_current`/`_suggest_current`/`_save_current`/`_skip_current`/`_save_all`）保持原名，
   现有测试不改即绿。

## 3. 总体信息架构

```
知识库维护（主导航第 4 页）
└─ 页签「语料状态」
   ├─ 操作栏：索引精化（N）按钮 ── 带待精化数量角标 ──────────────┐
   ├─ 审计横幅：新增「索引字段待精化 N 块 [去精化]」条目（推荐增强） │
   └─ 点击入口 ────────────────────────────────────────────────▶ 索引精化对话框（模态）
        ┌──────────────────────────────────────────────────────┐
        │ A. 顶部总览条：进度条 + 已完成统计 + 类型筛选          │
        │ B. 清单区（左）：搜索 + 分组表 + LLM 建议按钮          │
        │ C. 工作区（右）：条目头 + 原文卡片 + 4 字段卡片        │
        │ D. 底部操作条：跳过 / 保存当前 / 保存全部 / 关闭       │
        └──────────────────────────────────────────────────────┘
```

## 4. 语料状态页入口设计（`rag_maintenance_panel.py`）

### 4.1 按钮数量角标（必做）

- `refresh()` 时调用 `list_pending(self._root / "data" / "rag_corpus")` 统计待精化块数 N
  （两个 JSON 文件，量小，成本可忽略）。
- 按钮文案动态：
  - N > 0：`索引精化（N）`，保持 `ROLE_SECONDARY`；
  - N == 0：`索引精化 ✓`，`setEnabled(False)`（全部完成，无需进入）。
- 重建任务执行期间按钮照常随 `_set_busy(False)` 恢复可用。
- `_open_refinement()` 不变（打开对话框，`exec()` 后 `refresh()` 已存在）。

### 4.2 审计横幅新增待办条目（推荐，工作量小）

在 `audit_summary()` 末尾追加一条结构化审计条目：

```
AuditIssue(
    kind="pending_refinement",
    message=f"索引字段待精化 {N} 块（卡牌/武将语料）",
    severity="warning",
    target_tab="",          # 不跳页签
)
```

- 只有 N > 0 且语料目录可读时追加；文件缺失/解析失败时静默跳过（语料未构建属正常态，
  不应作为「缺源」级别的红色告警）。
- 排序放在审计清单**第一条**（精化待办是最日常的人工维护事项）。
- `_jump_to_issue()` 增加分支：`kind == "pending_refinement"` → `self._open_refinement()`。
- 兼容性：现有测试对 `audit_summary` 全部用 `in` / `next(by_kind[...])` 断言，无精确条数断言；
  测试临时目录无 `rag_corpus`，该条目不会出现，全部测试保持绿。

## 5. 索引精化对话框详细设计（`index_refinement_dialog.py`）

### 5.1 总体布局（尺寸 1160×720，三段纵向 + 左右分栏）

```
┌────────────────────────────────────────────────────────────────────┐
│ A 顶部总览条（QFrame#indexRefineOverview）                          │
│   [████████░░ 已完成 3/8]  待精化 5 块 · 已完成 3 块   [全部|卡牌|武将]│
├───────────────────────────────────┬────────────────────────────────┤
│ B 清单区（QFrame#indexRefineListPane，宽 40%）                       │
│   [搜索框 QLineEdit#indexRefineSearch .....................]        │
│   ┌─────────────────────────────┐  C 工作区（QFrame#indexRefineWorkPane）│
│   │ 语料 | 名称 | 缺失字段 | 状态 │  ┌──────────────────┬─────────────┐ │
│   │ 卡牌 | 诸葛连弩| 时机、关键词 │  │ 原文（持续展示）  │ 时机 [徽标] │ │
│   │      |        | ○ 未处理    │  │ 占满高度、只读、  │ ┌─────────┐ │ │
│   │ 武将 | 曹操   | 触发条件    │  │ 不可折叠          │ │ editor  │ │ │
│   │      |        | ✎ 已修改    │  │                  │ └─────────┘ │ │
│   │ …                            │  │                  │ 触发条件…   │ │
│   └─────────────────────────────┘  │                  │ 关键词…     │ │
│   [LLM 建议（当前）] [LLM 建议（全部）]│                  │ 关联…       │ │
│                                    │  └──────────────────┴─────────────┘ │
├───────────────────────────────────┴────────────────────────────────┤
│ D 底部操作条：[跳过当前]                    [保存当前] [保存全部] [关闭] │
└────────────────────────────────────────────────────────────────────┘
```

### 5.2 A 顶部总览条

| 元素 | 规格 |
|------|------|
| 进度条 | `QProgressBar#indexRefineProgress`，范围 0..总块数，值=已完成（保存+跳过）；全部完成时 `tone=success`，否则 `tone=info`（复用全局 `QProgressBar[tone]` 规则） |
| 统计文字 | `QLabel#indexRefineOverviewText`，格式「待精化 N 块 · 已完成 M 块」；N=0 时「全部完成，已无待精化条目」 |
| 类型筛选 | 3 个可勾选互斥按钮（`QPushButton` + `setCheckable(True)` + QButtonGroup 互斥）：全部 / 卡牌 / 武将；点选后 `_apply_filter()` 重建表格行并保留当前选中 |

进度条计数规则：M = 初始待精化总数 − 当前 `_pending` 长度（保存/跳过即计入完成）。

### 5.3 B 清单区

| 元素 | 规格 |
|------|------|
| 搜索框 | `QLineEdit#indexRefineSearch`，placeholder「搜索名称 / block_id…」，`textChanged` 过滤（不区分大小写子串匹配）；过滤为空显示空态行 |
| 表格 | `QTableWidget#indexRefineTable`，4 列，`SelectRows` + 单选，无编辑（对齐 `ragTaskTable` 视觉） |
| 列定义 | ① 语料：文本「卡牌」/「武将」；② 名称：`Stretch`；③ 缺失字段：`ResizeToContents`，中文顿号连接；④ 状态：`ResizeToContents` |
| 状态列 | 文本前缀 + 前景色（见 §6.1 状态映射表）：`○ 未处理`（MUTED_TEXT）/ `◉ 已建议`（PRIMARY）/ `✎ 已修改`（SUCCESS） |
| block_id | 不再独占列：保存为条目 UserRole 数据 + 名称列 tooltip 显示完整 block_id |
| 空状态 | `_pending` 为空时：隐藏表格，显示 `EmptyState`（复用 `shared/widgets.py`）「没有待精化条目」+ 副标题「卡牌/武将语料的索引字段已全部补全，重建语料不会被覆盖」；同时禁用 LLM 建议按钮 |
| 按钮组（表格下方） | `LLM 建议（当前）`（ROLE_SECONDARY，作用于选中行）、`LLM 建议（全部）`（ROLE_SECONDARY）；批量进行中两者禁用 |

行数达到数百时表格天然滚动，无需分页。

### 5.4 C 工作区（重设计：左原文常驻 / 右字段编辑）

工作区内为横向 QSplitter（`setChildrenCollapsible(False)`，不可折叠）：

| 区块 | 规格 |
|------|------|
| 条目头 | 名称 `QLabel#indexRefineItemTitle`（FONT_SIZE_LG 加粗）；右侧类型徽标（卡牌/武将 → `StatusBadge` tone=info）；`block_id` 小字 `QLabel#indexRefineItemMeta`（MUTED_TEXT）；缺字段徽标 `StatusBadge` tone=warning「缺：时机、关键词」 |
| 左栏·原文卡片 | `QFrame#indexRefineSourceCard`（SURFACE 底 + BORDER + RADIUS_MD），标题行「原文」+ 只读 `QPlainTextEdit#indexRefineSource`（**占满高度、持续展示**，`setChildrenCollapsible(False)` 保证不可被折叠/挤压）；未选中时显示 placeholder「选中条目后显示原文……」 |
| 右栏·字段编辑区 | 4 个 `QFrame#indexRefineFieldCard` 纵向均分（stretch 1），间距 SPACE_MD；每卡：字段名（加粗）→ 状态徽标 → `QPlainTextEdit#indexRefineFieldEditor`（`setMaximumBlockCount(30)` 保留） |
| 字段提示 | 不再常驻每卡一行 hint（去密集化）：提示移入编辑器 placeholder——时机「每行一个值，如：出牌阶段、回合开始时」；触发条件「每行一个值，如：打出时」；关键词「每行一个值，检索用」；关联「每行一个值，如：卡牌:诸葛连弩、规则:时机-回合开始」 |
| 未选中态 | 工作区整体置灰（条目头「未选择条目」，字段卡片 `fieldState=empty`、编辑器禁用） |

字段状态判定（`_field_state(field) -> "empty" | "llm" | "manual"`）：

```
text = editor.toPlainText().strip()
empty  : text == ""（或与初始空一致）
llm    : text != "" 且 llm_baseline 存在且 text == baseline[field]
manual : text != "" 且（无 baseline 或 text != baseline[field]）
```

任一字段为 `manual` 时，条目级状态列显示「✎ 已修改」；全部为 `llm` 时显示「◉ 已建议」；
否则「○ 未处理」。

### 5.5 D 底部操作条

| 按钮 | 角色 | 可用条件 | 行为 |
|------|------|----------|------|
| 跳过当前 | ROLE_SECONDARY | 有选中条目 | 弹确认（QMessageBox.question：「跳过将丢弃当前编辑，且该块不再出现在清单中」）→ 从 `_pending` 移除（现状逻辑），计入进度 |
| 保存当前 | ROLE_PRIMARY | 有选中条目且至少一个字段非空 | 现 `_save_current()` 逻辑；保存后 toast 保留 |
| 保存全部 | ROLE_SECONDARY | `_pending` 非空 | 弹确认（「将按当前编辑内容保存全部 N 块，LLM/人工混合记录，是否继续？」）→ 现 `_save_all()` 逻辑 |
| 关闭 | ROLE_GHOST | 恒可用 | 有脏编辑时先确认（§5.6）再 `reject()` |

### 5.6 状态机与交互细节

```
条目生命周期：pending（未处理）→ 已建议（LLM 填入）→ 已修改（人工改动）→ 已保存 / 已跳过（移出清单）
                                  └────────── 人工直接填写 ──────────┘
```

- **LLM 建议（当前）**：`_suggest_current()` 流程不变；成功后刷新该行状态列与字段卡片 `fieldState`；
  失败弹 warning（现状文案保留）。
- **LLM 建议（全部）**：**事件循环化改造**（解决 P4）：
  - 用 `QTimer.singleShot(0, ...)` 建立队列，逐块处理；每块完成后更新进度条、状态列，
    窗口保持响应；
  - 队列进行中：LLM 按钮禁用，状态文字「正在生成建议：i/N（名称）」；
  - 结束后汇总：成功 n 块 / 失败 m 块（失败 > 0 时弹一次 warning 列出失败块名，P8）；
  - 队列可被「关闭」打断（对话框 reject 时中止剩余队列）。
- **脏状态保护**（P5）：
  - 维护 `self._dirty: bool`，任一编辑器 `textChanged` 且内容与 `llm_baseline`（或空基线）不同 → True；
    保存/跳过成功后清 False；
  - 切换表格行时若 `_dirty`：`QMessageBox.question`「当前条目有未保存修改，放弃并切换？」→
    确认才切换；取消则保持原行；
  - `reject()`（关闭/ESC）前同样检查。
- **保存全部**：先确认（§5.5），随后逐块收集当前编辑器内容保存；每块保存后立即从 `_pending`
  移除并更新进度，中断（单块失败）不阻塞其余。

## 6. 组件与样式规格

### 6.1 状态映射表（全部复用现有令牌）

| 状态 | 文本 | 前景色 | 字段卡片 `fieldState` | 卡片左边框 |
|------|------|--------|----------------------|-----------|
| 未处理 | `○ 未处理` | MUTED_TEXT | `empty` | BORDER |
| 已建议 | `◉ 已建议` | PRIMARY | `llm` | PRIMARY |
| 已修改 | `✎ 已修改` | SUCCESS | `manual` | SUCCESS |

条目级「已修改」判定覆盖「已建议」（人工改动优先展示）。

### 6.2 新增 QSS 清单（追加到 `style.py`「知识库维护」区块之后）

全部使用现有令牌与格式化方式（f-string + 双大括号），不触碰全局规则：

```
/* === 索引精化（对话框） === */
QWidget#indexRefineDialog { background-color: {CANVAS}; }
QFrame#indexRefineOverview { background-color: {SUBTLE_SURFACE};
    border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; }
QLabel#indexRefineOverviewText { color: {TEXT_PRIMARY}; font-size: {FONT_SIZE_SM}px; }
QFrame#indexRefineListPane, QFrame#indexRefineWorkPane { background-color: {SURFACE};
    border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; }
QTableWidget#indexRefineTable { 对齐 ragTaskTable 全套规则（背景/边框/item/hover/selected/表头） }
QLabel#indexRefineItemTitle { font-size: {FONT_SIZE_LG}px; font-weight: bold; color: {TEXT_PRIMARY}; }
QLabel#indexRefineItemMeta { color: {MUTED_TEXT}; font-size: {FONT_SIZE_SM}px; }
QFrame#indexRefineSourceCard, QFrame#indexRefineFieldCard { background-color: {SURFACE};
    border: 1px solid {BORDER}; border-radius: {RADIUS_MD}px; }
QFrame#indexRefineFieldCard[fieldState="llm"] { border-left: 3px solid {PRIMARY};
    background-color: {PRIMARY_SOFT}; }
QFrame#indexRefineFieldCard[fieldState="manual"] { border-left: 3px solid {SUCCESS};
    background-color: {SUCCESS_SOFT}; }
QFrame#indexRefineFieldCard[fieldState="empty"] { border-left: 3px solid {BORDER}; }
QLabel#indexRefineFieldName { font-weight: bold; color: {TEXT_PRIMARY}; }
QPlainTextEdit#indexRefineSource, QPlainTextEdit#indexRefineFieldEditor {
    background-color: {SURFACE}; border: 1px solid {BORDER};
    border-radius: {RADIUS_SM}px; padding: 6px; }
QPlainTextEdit#indexRefineSource { font-size: {FONT_SIZE_SM}px; }
```

> 说明：`fieldState` 用 `set_style_property()` 动态属性驱动（与 `uiRole`/`tone` 同一机制），
> 变更后自动重绘。字段卡片底色 SOFT 变体：LLM=淡蓝、人工=淡绿、空=白，大面积弱化、
> 仅左侧 3px 强调色，符合工作台克制风格。
> 已实现版本较本清单的差异：字段提示不再使用常驻 `QLabel#indexRefineFieldHint`（§5.4 去密集化），
> 该规则已移除，提示词移入编辑器 placeholder。

### 6.3 复用组件清单

| 组件 | 来源 | 用途 |
|------|------|------|
| `PageHeader` | `shared/widgets.py` | 对话框标题 + 副标题（流程说明） |
| `EmptyState` | `shared/widgets.py` | 清单空状态 |
| `StatusBadge` | `shared/widgets.py` | 类型徽标（卡牌/武将）与缺失字段徽标 |
| `QProgressBar` + `set_tone` | 全局样式 | 顶部进度条 |
| `set_ui_role` / `set_tone` | `shared/style.py` | 按钮角色、状态着色 |
| `show_toast` | `shared/widgets.py` | 保存/跳过成功反馈 |

## 7. 实现映射

### 7.1 `src/ui/maintenance/index_refinement_dialog.py`

| 改动 | 说明 |
|------|------|
| `__init__` | `resize(1160, 720)`；`setObjectName("indexRefineDialog")`；新增 `self._dirty = False`、筛选/搜索状态字段 |
| `_setup_ui` | 重构为 A 总览条 + B/C 分栏 + D 底部条四段；`QSplitter` 保留，`setSizes([460, 700])` |
| `_build_table_pane` | 新增搜索框、类型筛选按钮组；表格列改为 语料/名称/缺失字段/状态；block_id 存入条目 UserRole；新增状态列填充与 `_apply_filter` |
| `_build_editor_pane` | 条目头 → 横向 QSplitter（左 `_build_source_pane` 原文占满常驻 / 右 `_build_fields_pane` 4 字段卡片均分）；`_build_field_card(field)` 字段卡片容器 `_field_cards[field]` 用于 `fieldState` 刷新；提示词移入编辑器 placeholder |
| `_on_table_selected` | 保留；开头增加脏检查（`_confirm_discard()`），选中后刷新条目头、字段卡片状态 |
| `_fill_suggestion` | 保留写入逻辑；末尾调用 `_refresh_field_states()` 与表格状态列刷新 |
| `_suggest_all` | 改为 QTimer 队列逐块处理（`_suggest_queue`），更新进度/忙碌态/失败汇总 |
| `_collect_update` | 不变（`method=llm/manual` 判定已隐含在 baseline 对比） |
| `_save_current` / `_save_all` / `_skip_current` | 逻辑不变；保存/跳过后置 `_dirty=False`、刷新总览进度与空状态 |
| 新增 `_refresh_field_states()` / `_field_state()` / `_confirm_discard()` / `_update_overview()` / `_apply_filter()` | 见 §5 规格 |

**兼容性红线**（现有测试依赖，必须保留原名与语义）：
`_table`、`_field_editors`（dict[str, QPlainTextEdit]）、`_llm_baseline`、`_pending`、`_current`、
`_suggest_current()`、`_save_current()`、`_save_all()`、`_skip_current()`。

### 7.2 `src/ui/maintenance/rag_maintenance_panel.py`

- `refresh()`：统计 `list_pending(...)` → 更新 `_refine_button` 文案与可用态（§4.1）。
- `audit_summary()`：追加 `pending_refinement` 条目（§4.2），`list_pending` 导入自 `refinement_service`。
- `_jump_to_issue()`：新增 `pending_refinement` 分支 → `_open_refinement()`。
- 其余不动（`_open_refinement` 已含 `exec()` 后 `refresh()`）。

### 7.3 `src/ui/shared/style.py`

- 在「知识库维护」区块末尾追加「索引精化」区块（§6.2 清单）。

### 7.4 文档同步

- 评审通过后更新 `docs/spec/spec_rag_maintenance_ui.md` 第五节（布局/交互描述改为新设计）。

## 8. 测试影响与新增用例

### 8.1 现有测试（保持全绿，不改动）

- `tests/test_index_refinement_dialog.py`：5 个用例全部经「兼容性红线」接口驱动，不受列结构/布局变化影响。
- `tests/test_rag_maintenance_panel.py`：审计断言为 `in` / `next(by_kind[...])` 无精确条数；
  临时目录无 `rag_corpus`，`pending_refinement` 条目不出现。
- `tests/test_rag_refinement_service.py`：服务层零改动。

### 8.2 新增用例（建议）

| 用例 | 验证点 |
|------|--------|
| `test_filter_filters_rows` | 搜索「诸葛」后表格仅剩匹配行；清空恢复 |
| `test_kind_filter_filters_rows` | 选「卡牌」仅剩卡牌行 |
| `test_field_state_tracks_manual_edit` | LLM 建议后 `fieldState=llm`；人工改一个字 → `manual`；清空 → `empty` |
| `test_dirty_guard_on_switch` | 编辑后触发行切换：monkeypatch `QMessageBox.question` 返回 No → 当前行不变；返回 Yes → 切换 |
| `test_suggest_all_queue_finishes` | monkeypatch `_suggest_one` 后 `_suggest_all`（或队列驱动函数）处理完全部块并更新进度 |
| `test_empty_state_after_save_all` | 全部保存后 EmptyState 可见、进度满 |
| `test_entry_badge_count`（panel） | 构造含待精化块的 `rag_corpus` → `_refine_button.text()` 含数量；全清后禁用 |

## 9. 验收标准

- [ ] 语料状态页按钮显示待精化数量；N=0 时禁用且文案带 ✓；对话框关闭后数量刷新。
- [ ] 对话框打开后：总览条显示真实进度；清单可搜索、可按类型筛选；状态列三态正确。
- [ ] LLM 建议（当前）填入后字段卡片变淡蓝（llm）；人工改动后变淡绿（manual）并显示「✎ 已修改」。
- [ ] LLM 建议（全部）执行期间窗口可拖动、可重绘，无冻结；结束给出成功/失败汇总。
- [ ] 未保存修改时切换条目/关闭均有确认；确认放弃后不丢失已保存数据。
- [ ] 全部完成后清单区显示 EmptyState，进度条满格 success，LLM 按钮禁用。
- [ ] `pytest tests/test_index_refinement_dialog.py tests/test_rag_maintenance_panel.py tests/test_rag_refinement_service.py` 全绿（用 `G:\CONDA\Anaconda3\envs\myenv\python.exe -m pytest`）。
- [ ] 新增 §8.2 用例全部通过。

## 10. 范围外（本设计不包含）

- 服务层 `refinement_service.py` 的任何改动（含并发/线程化 LLM 调用 —— 事件循环化已满足需求，QThread 留作后续演进）。
- 把精化做成「语料状态」页内嵌面板/页签（模态对话框与第一期布局一致，维持现状形态）。
- 语料状态页任务表、审计横幅整体布局调整（属第一期 `rag_maintenance_ui_layout.md` 范围）。
- 精化历史查看、curated 撤销/回滚。
