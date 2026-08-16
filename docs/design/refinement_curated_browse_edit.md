# 索引精化扩展：已处理（curated）块浏览与再编辑

> 适用范围：「知识库维护」→「语料状态」→「索引精化」对话框（`index_refinement_dialog.py`）
> 与语料状态页入口（`rag_maintenance_panel.py`）、精化服务（`src/business/rag/refinement_service.py`）。
> 状态：**已实施（2026-08）**。配套测试 `tests/test_rag_refinement_service.py` /
> `tests/test_index_refinement_dialog.py` / `tests/test_rag_maintenance_panel.py` 已更新并全量通过；
> `docs/spec/spec_rag_maintenance_ui.md` 第五节、README、project_doc、周更操作手册已同步。
>
> 与既有文档的关系：`docs/design/index_refinement_ui_redesign.md` 是 2026-08 的对话框专项重设计，
> 本文档在其之上**只增不改**：保留待精化流程全部能力，新增已处理块的浏览/再编辑/取消精化。

## 1. 背景与现状

现状工作流：`list_pending()` 扫描卡牌/武将语料中「无 `curated` 且任一索引字段为空」的块
→ 对话框人工填写或 LLM 建议 → `apply_curated()` 写回（curated 分层，重建不覆盖）。

**现状缺口**：

| # | 问题 | 影响 |
|---|------|------|
| P1 | 对话框只加载待精化块，已精化（curated）块无任何浏览入口 | 无法查看历次精化成果（method/updated_at/四字段内容） |
| P2 | 已精化块无法在应用内修正 | 发现错误精化只能手动改 JSON 或删 curated 等重建，易出错 |
| P3 | 无待精化时入口按钮直接禁用（`索引精化 ✓` 不可点） | 即使只想浏览已精化成果也进不去 |
| P4 | 无「撤销精化」能力 | 误精化/想重新精化的块无法退回待精化池 |

## 2. 设计目标与原则

1. **零功能缩减**：待精化流程（LLM 建议当前/全部、保存当前/全部、跳过）原样保留。
2. **一个对话框**：在现有 `IndexRefinementDialog` 内加**范围筛选**（待精化/已精化/全部），
   复用同一套工作台，不新增对话框、不改语料状态页布局。
3. **服务层最小扩展**：新增 `list_curated()` / `clear_curated()`，`apply_curated()` 复用不改；
   `list_pending()` 对外行为不变（审计/入口/现有测试零改动）。
4. **统一保存模型**：引入「磁盘基线」概念，保存语义唯一化——**有改动才写回**；
   改动后 `method="manual"`、`updated_at=今天`（已确认）。
5. **防误操作**：取消精化必须二次确认；未修改点保存给出明确反馈。
6. **测试兼容**：现有测试除 §8 列明的 1 处入口断言外全部保持绿。

## 3. 块状态模型（三态）

| 状态 | 判定 | 范围归属 | 支持操作 |
|------|------|----------|----------|
| `pending` 待精化 | 无 `curated` 且任一索引字段为空 | 待精化 | LLM 建议 / 保存 / 跳过（现状） |
| `curated` 已精化 | 有 `curated` | 已精化 | 浏览 / 再编辑保存 / 取消精化 / 单块重新建议 |
| `normal` 已生成 | 无 `curated` 且四字段全非空 | 全部 | 浏览 / 编辑固化（保存即写入 curated） |

状态迁移：

```
pending ──保存(写 curated)──▶ curated
curated ──再编辑保存─────────▶ curated（覆盖写，method→manual、updated_at→今天）
curated ──取消精化(删 curated)▶ pending（字段有空缺）| normal（字段全满）
normal  ──保存(写 curated)──▶ curated
```

说明：`normal` 是构建脚本规则抽取已填满的块，历史上不参与精化；「全部」范围下允许
对其编辑固化（保存=固化当前四字段为 curated），语义与 pending 保存完全一致。

## 4. 服务层扩展（`src/business/rag/refinement_service.py`）

### 4.1 块视图统一

`PendingBlock` 扩展两个可选字段（不新增 dataclass，最小改动）：

```python
@dataclass
class PendingBlock:
    corpus: str
    block_id: str
    name: str
    kind: str  # "card" | "skill"
    text: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    method: str = ""        # 新增：curated 块来源（"llm" | "manual"），其余为空
    updated_at: str = ""    # 新增：curated 块更新时间（ISO 日期）
```

### 4.2 一次扫描三分类（避免重复读文件）

新增 `scan_blocks(corpus_dir) -> dict[str, list[PendingBlock]]`：
- 与 `list_pending` 相同的文件清单（卡牌/武将）与遍历规则（武将跳过 overview 块）；
- 按 §3 判定分入 `pending` / `curated` / `normal`；
- `curated` 块的 `fields` **以 curated 内容为权威**（与顶层字段一致，但 curated 是重建保留的成果）；
- `list_pending()` 改为薄封装返回 `scan_blocks()["pending"]`（对外行为不变）。

### 4.3 新增 `clear_curated(corpus_dir, block_id, fname) -> bool`

- 删除该块顶层 `curated` 字段，`atomic_write_json` 原子写回（与 `apply_curated` 一致）；
- 块不存在抛 `ValueError`（与 `apply_curated` 风格一致）；删除成功返回 `True`。

### 4.4 `apply_curated()` 零改动复用

已支持对任意 block_id 覆盖写（含已有 curated 的块）：更新顶层四字段 + 重写 `curated`
（method/updated_at 由 `RefinementUpdate` 提供），重建时 `merge_curated` 保留最新成果。

## 5. 对话框 UI 设计（`index_refinement_dialog.py`）

### 5.1 顶部总览条：模式切换（右对齐）

```
[██████░░ 已完成 3/8]  待精化 5 块 · 已完成 3 块        [待精化|已精化|全部]
```

- 总览条仅保留进度条 + 统计文字 + 模式切换 `QButtonGroup`（`待精化` 默认选中，右对齐）；
- 类型筛选（全部/卡牌/武将）移入清单区与搜索框同行（贴近数据、与模式切换物理隔离，消除两个「全部」）；
- **待精化**：进度条 + 统计文案（现状不变）；
- **已精化**：隐藏进度条，统计文案 `已精化 M 块（人工 X · LLM Y）`；
- **全部**：隐藏进度条，统计文案 `共 T 块：待精化 N · 已精化 M · 其他 K`；
- 范围切换只过滤内存快照（打开时一次 `scan_blocks` 全量加载，642 块量级），不重复读文件；
- 搜索框与类型筛选对三个范围均生效。

### 5.2 清单表（4 列固定列宽，列 2 语义按范围扩展）

| 范围 | 列 2（说明） | 列 3（状态） |
|------|--------------|--------------|
| 待精化 | 缺失字段（现状） | `○ 未处理` / `◉ 已建议` / `✎ 已修改`（现状） |
| 已精化 | `method · updated_at`（如 `人工 · 2026-08-14`） | `✓ 已精化` / `✎ 已修改` |
| 全部 | 按块状态：缺失字段 / `method · 时间` / `—` | 对应上述状态 |

- 列 0/2/3 固定列宽（60/210/130）、列 1 Stretch——大清单（全部范围 470+ 行）下
  `ResizeToContents` 逐行 sizeHint 计算会卡 UI；
- 搜索输入 250ms 防抖（QTimer 合并击键，避免逐键全表重建）；
- 批量操作行（LLM 建议（全部）/ 保存全部）位于表格下方，**仅待精化模式可见**。

行状态值域扩展（`_ROW_STATE_TEXT` / `_ROW_STATE_COLOR`）：

```python
{"pending": "○ 未处理", "suggested": "◉ 已建议", "modified": "✎ 已修改",
 "refined": "✓ 已精化", "generated": "○ 已生成"}
```

### 5.3 统一保存模型（核心改动）

**新增 `self._saved_baseline: dict[str, dict[str, str]]`**（block_id → {field: 文本}），
表示「磁盘上的当前精化成果」，打开对话框初始化：
- pending 块：现有顶层字段文本；
- curated 块：curated 字段文本；
- normal 块：顶层字段文本。

现有 `self._llm_baseline`（本次会话 LLM 建议内容）语义不变。

**字段状态判定**（`_field_state`，优先级从高到低）：

1. 文本为空 → `empty`（待填写）；
2. 文本 == `llm_baseline` 且 != `saved_baseline` → `llm`（本次 LLM 建议）；
3. 文本 == `saved_baseline` → `saved`（已精化/原有内容，新增徽标文案「已精化」，`TONE_SUCCESS`）；
4. 其余 → `manual`（已修改）。

兼容性验证：现有测试 `test_field_state_tracks_manual_edit`（空字段 pending 块）
在建议→改值→改回建议值三步下的判定与旧逻辑一致，保持绿。

**行状态**（`_on_field_edited` 扩展）：curated 块初始 `refined`、normal 块初始 `generated`；
任一字段为 `manual` 时置 `modified`；与 `_saved_baseline` 一致时还原初始状态。

**保存当前**（`_save_current` / `_collect_update` 改造）：
- 文本 == `saved_baseline`（无改动）→ 不写文件，toast「无修改」；
- 有改动：`method = 与 llm_baseline 完全一致 ? "llm" : "manual"`（沿用现状规则）；
  写回后更新 `_saved_baseline[block_id]` 为新文本、`_llm_baseline.pop`、行状态置 `refined`，
  并把该块从内存 pending 列表移入 curated 列表（全部范围下状态同步刷新）；
- 行为微调说明：现状对 pending 块「未改动也保存」会写 curated；新模型下未改动保存为 no-op
  （防止误写，需在操作手册中注明）。

**保存全部**：仅待精化范围可用（现状语义不变：无建议且非当前编辑的块跳过）。

**LLM 建议**：
- 待精化范围：当前/全部（现状不变）；
- 已精化/全部范围：仅「LLM 建议（当前）」可用（对当前块重新建议，结果只填编辑器，
  保存后才写回）；「全部」按钮禁用，避免批量覆盖已精化成果。

### 5.4 取消精化（新增）

- 工作区条目操作行新增「取消精化」按钮（`ROLE_DANGER`，仅已精化/全部模式可见，且当前块为 curated 时可用）；
- 点击弹确认框：`将删除该块的 curated 字段，退回待精化池（字段有空缺）或转为普通块，是否继续？`；
- 确认后调 `clear_curated()`；成功后从内存 curated 列表移除，按 §3 迁移规则加入
  pending（missing 非空）或 normal 列表；统计与表格同步刷新。

### 5.5 操作布局（2026-08 布局重规划：模式显性化 + 操作归位）

```
A 总览条: [进度] [统计]                    [待精化|已精化|全部]
B 清单区: [搜索........] [类型: 全部|卡牌|武将]
          [表格 语料|名称|说明|状态]
          [LLM建议(全部)] [保存全部]   ← 仅待精化模式可见
C 工作区: 条目头 → 原文|字段分栏
          [LLM建议(当前)]        [跳过当前] [取消精化] [保存当前 PRIMARY]
D 底部:                                       [关闭]
```

按钮按模式**显隐**（隐藏而非禁用）：

| 操作 | 待精化 | 已精化 | 全部 | 位置 |
|------|:---:|:---:|:---:|------|
| LLM 建议（当前） | ✓ | ✓ | ✓ | 工作区操作行（SECONDARY） |
| 跳过当前 | ✓ | ✗ | ✗ | 工作区操作行（SECONDARY） |
| 取消精化 | ✗ | ✓（仅 curated） | ✓（仅 curated） | 工作区操作行（DANGER） |
| 保存当前 | ✓ | ✓ | ✓ | 工作区操作行（**PRIMARY 唯一**，最右） |
| LLM 建议（全部） | ✓ | ✗ | ✗ | 清单区批量行（SECONDARY） |
| 保存全部 | ✓ | ✗ | ✗ | 清单区批量行（SECONDARY） |
| 关闭 | ✓ | ✓ | ✓ | 底部右对齐（GHOST） |

- 未选中条目时工作区操作行按钮全部禁用（置灰）；
- 保存当前为全对话框唯一 PRIMARY——编辑完手指自然落在保存上。

### 5.6 空态与条目头

- 空态文案按范围区分：已精化范围空 →「还没有已精化条目，先去待精化处理」；
  全部范围空 → 沿用「没有待精化条目」文案的通用版本；
- 条目头：curated 块追加方法徽标（`LLM` / `人工`）+ `updated_at`（复用 `StatusBadge`），
  「缺：xx」徽标保留（curated 块仍可能有空字段）。

## 6. 语料状态页入口（`rag_maintenance_panel.py`）

- `refresh()` 计算待精化数 N：
  - N > 0：`索引精化（N）`，可用（现状）；
  - N == 0：`索引精化 ✓`，**保持可用**（✓ 表示待办清零，仍可点击浏览/管理已精化块）；
- 审计横幅 `pending_refinement` 条目与跳转不变（仍只提示待办）；
- 重建任务执行期间按钮随 `_set_busy` 禁用逻辑不变。

## 7. 样式（`src/ui/shared/style.py`）

- 全部复用现有令牌：`saved` 字段态用 `TONE_SUCCESS`，`refined` 行态用 `SUCCESS`，
  `generated` 用 `MUTED_TEXT`，取消精化按钮用 `ROLE_DANGER`/`TONE_DANGER`（均已存在）；
- 预计仅需新增少量 `indexRefine*` QSS（范围筛选按钮组可用现有 `ROLE_GHOST`，可能为零新增）。

## 8. 测试计划

服务层（`tests/test_rag_refinement_service.py` 新增）：
1. `test_list_curated_returns_curated_blocks`：返回 curated 块，fields 以 curated 为权威，
   method/updated_at 正确，跳过 pending/normal/overview 块；
2. `test_clear_curated_removes_field`：删除后无 curated、原子写回、未知 block_id 抛 `ValueError`；
3. `test_apply_curated_overwrites_existing_curated`：对已 curated 块再保存，curated 被覆盖，
   method/updated_at 更新；
4. `test_list_pending_unchanged`：`scan_blocks` 重构后 `list_pending` 结果与现状一致。

对话框（`tests/test_index_refinement_dialog.py` 新增）：
5. `test_scope_filter_switches_lists`：待精化/已精化/全部三范围行数与统计正确；
6. `test_curated_block_loads_saved_state`：选中已精化块，字段=curated 内容、徽标 `已精化`、
   行状态 `✓ 已精化`；
7. `test_save_curated_without_change_noop`：未修改点保存 → 文件内容不变；
8. `test_save_curated_modified_flips_manual`：修改保存 → `curated.method=="manual"`、
   `updated_at==今天`；
9. `test_clear_curated_moves_back_to_pending`：取消精化 → curated 删除，字段有空缺的块
   出现在待精化范围。

入口（`tests/test_rag_maintenance_panel.py` 更新 1 处）：
10. `test_refine_button_shows_pending_count`：N=0 断言由 `not isEnabled()` 改为 `isEnabled()`。

回归：其余现有测试全部保持绿。

## 9. 实现映射

| 文件 | 改动 |
|------|------|
| `src/business/rag/refinement_service.py` | `PendingBlock` +2 字段；新增 `scan_blocks` / `list_curated` / `clear_curated`；`list_pending` 薄封装 |
| `src/ui/maintenance/index_refinement_dialog.py` | 范围筛选；`_saved_baseline` 统一模型；`_field_state` / `_on_field_edited` / `_collect_update` / `_save_current` 改造；取消精化；行状态扩展；按钮可用性矩阵；空态/条目头 |
| `src/ui/maintenance/rag_maintenance_panel.py` | N=0 时按钮保持可用（去掉 `setEnabled(False)`） |
| `src/ui/shared/style.py` | 按需少量 `indexRefine*` QSS（预计极小或为零） |
| `tests/test_rag_refinement_service.py` | +4 测试 |
| `tests/test_index_refinement_dialog.py` | +5 测试 |
| `tests/test_rag_maintenance_panel.py` | 1 处断言更新 |
| 文档 | `docs/spec/spec_rag_maintenance_ui.md` 第五节、README/project_doc 相关段落 |

工作量估算：服务层约 60 行、对话框约 200 行、面板约 5 行、测试约 150 行。

## 10. 风险与边界

- **快照过期**：对话框打开期间语料文件被重建（QProcess 执行中入口已禁用，但外部进程仍可能写），
  快照与磁盘不一致——现状已存在，关闭重开即刷新；可选增强为总览条「刷新」按钮（本期不做）。
- **徽标语义**：字段状态 `saved`（已精化）与 `llm`（本次建议）需在 UI 上区分清楚，
  避免用户误以为 curated 内容也是"LLM 建议"。
- **保存 no-op 微调**：未改动保存从"写 curated"变为"提示无修改"，属有意的行为收紧，
  需同步更新 `docs/周更操作手册.md` 与项目文档描述。
- **normal 块编辑**：全部范围允许固化 normal 块（保存即写 curated），属新增能力；
  若评审认为范围过大，可裁剪为 normal 只读（仅减 `_save_current` 的一处可用性判断）。
