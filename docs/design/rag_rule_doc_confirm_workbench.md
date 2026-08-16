# 元规则确认工作台设计（B2 数据段差异确认 + B3 提案确认 + 失败处理 + 日志模块）

> 适用范围：`src/ui/maintenance/rule_doc_panel.py`、`src/business/rag/rule_doc_service.py`、
> `scripts/sync_rule_stats.py`（增量改造）、`tests/test_rule_doc_panel.py`、`tests/test_rule_doc_service.py`、`tests/test_sync_rule_stats.py`。
> 定位：在既有 A 档（引导/状态建议/明细化）之上，补全「数据段差异」与「提案」的 **UI 端确认 → 脚本落地文档** 闭环。
> 状态：已实施（2026-08-16）。实施中修正一处设计：确认值校验从"禁含竖线"改为"完整表格行 + 列数与 old 一致"（diff 的 new 值本身是完整表格行，天然含竖线）。
> 第二轮改善（已实施）：① 差异表展示列（段/行号/类型/摘要）只读，仅「确认值」列可编辑；② 提案明细操作列 `[查看] [确认]` 并存，`[查看]` 打开 `ProposalDetailDialog`——复用 `rich_diff.py` 的 Git 风格 diff（红删绿增 + 字符级染色，与武将更新确认对话框同款），faq_revise/row_revise 用 `doc_target_line` 取文档当前行作 local 侧、新增类整行绿增，附「文档上下文」页签（`doc_section_context` 定位目标行 ± 附近内容）。
> 第三轮改善（已实施）：数据段差异表新增第 7 列「操作」，每行 `[查看]` 打开 `DiffDetailDialog`——local 侧现场读文档当前行（`doc_line_at`，与 `apply_confirmed` 同一行号定位）、official 侧优先取「确认值」列当前文本；文档当前行与检查时快照不一致时显示警示条（应用会失败预警）；checkpoint 行显示"无自动建议值"说明；附「文档上下文」页签（`doc_context_around` 目标行 ±3 行）。

## 一、目标

1. **差异闭环**：候选/全自动差异可在 UI 勾选、修改应用值、一键写文档（`--apply-json`），不再依赖命令行；
2. **提案闭环**：提案条目可在 UI 逐条审阅（上下文 + 可编辑文本）、选状态（通过/修改后通过/驳回），写回提案 JSON，复用现有合入脚本落地；
3. **文档唯一写者原则**：UI 永不直接改 `元规则整理-完整版.md` 文本；UI 产出"确认清单 / 已确认提案 JSON"，写文档由脚本完成（old 匹配 + audit 守门 + 回滚）；
4. **失败可定位可重试**：失败逐条报告、确认状态持久化、重试不丢已确认内容；
5. **日志可回溯**：日志区结论行分级着色（脚本原始输出不变），每次脚本执行落一条 `logs/rule_doc_ops.log` 记录。

---

## 二、B2 数据段差异确认工作台

### 2.1 差异应用策略矩阵

| 类型 | 默认勾选 | 确认值可编辑 | 应用语义 |
|---|---|---|---|
| full 全自动 | ✅ | ✅（默认=脚本 `new` 值，可改） | 一键应用 |
| candidate 候选 | ❌ | ✅（默认=脚本 `new` 值，可改） | 人工勾选确认后才应用 |
| checkpoint 校验点 | ❌ 不可勾选 | ❌（置灰） | 仅提示，无 `new` 值 |

### 2.2 UI 改造（数据段差异页签）

表格 4 列 → 6 列：`应用 | 段 | 行号 | 类型 | 差异摘要 | 确认值`

- 「应用」列：每行 `QCheckBox`（`setCellWidget`；checkpoint 行 `setEnabled(False)`），勾选变化实时刷新统计；
- 「确认值」列：可编辑 `QTableWidgetItem`（默认 = diff 的 `new`；checkpoint 行去 `ItemIsEditable` 且置灰）；
- 新增 `_diff_summary_label`（`specialCardEditMeta` 样式）：`全自动 8 · 候选 17 · 校验点 1 ｜ 已勾选 9 项可应用`；
- 「应用已确认差异」按钮文案 `应用已确认差异（N）`，N=0 时禁用；
- 保存本次 diffs 到 `self._diffs`（收集 payload 时取 `old/new/section/message`）。

### 2.3 确认数据流

```
[勾选 + 编辑确认值]
   │ _collect_confirmed_rows（UI 校验：空值 / 含竖线 → 拦截）
   ▼
写 scripts/.sync_confirmed_diffs.json
   │ QProcess: sync_rule_stats.py --apply-json <清单>
   ▼
脚本 apply_confirmed：预检全部行（line_no 越界 / 当前行 != old / 确认值含竖线）
   │ 任一失败 → all-or-nothing 整批拒绝，文档零改动
   ▼
全部通过 → 单次写回文档 → append_changelog → refresh_snapshot（默认文档）
   ▼
UI：refresh_diffs() 重跑 --json → 成功行从差异表消失
```

### 2.4 脚本改造（scripts/sync_rule_stats.py）

新增纯函数（可测试）：

```python
def apply_confirmed(confirmed: list[dict], doc_text: str) -> tuple[str, list[dict], list[str]]:
    """按确认清单逐行替换；返回 (新文档文本, applied, errors)。

    预检全部行后再写：任一 hard error（行号越界 / old 不匹配 / 新值含竖线）
    → 整批拒绝（返回原文本 + errors），文档零副作用。
    """
```

新增 CLI 参数 `--apply-json <path>`（读确认清单 JSON 数组，每项含 `section/line_no/old/new/message`）。

**退出码协议**（与 `--json` 哨兵 1 不混用）：

| 退出码 | 含义 | UI 结论 |
|---|---|---|
| 0 | 全部应用成功 | `✔ 已应用 N 处` |
| 1 | 预检失败（≥1 行 old 不匹配等），未修改文档 | `✘ 应用失败：文档可能已被改动，未修改文档（见上方明细）` |
| 2 | 前置失败（清单/文档不可读或格式错） | `✘ 前置失败：确认清单或文档不可读，未修改文档` |

---

## 三、B3 提案确认工作台

### 3.1 UI 改造（提案工作台页签）

明细表 5 列 → 6 列：`提案号 | 类型 | 目标 | 建议文本 | 状态 | 操作`

- 「状态」列中文映射：`pending→待确认 / approved→已确认 / revised→已修订 / rejected→已驳回`；
- 「操作」列每行 `[确认]` 按钮（`setCellWidget`）→ 打开确认对话框；
- 新增 `_proposal_summary_label`：`待确认 N · 已确认 N · 已驳回 N`；
- 保存当前提案路径 `self._current_proposal_path`（对话框写回用）。

### 3.2 确认对话框（新类 `ProposalItemConfirmDialog`）

布局：只读上下文（提案号 / 类型中文 / 目标位置 / 来源 / 依据 / 理由）+ 可编辑「合入文本」+ 四个动作按钮。

| 按钮 | 写入 status | edited_text |
|---|---|---|
| 通过 | `approved` | 文本未改动 → `None`；改动过 → 当前文本 |
| 修改后通过 | `revised` | 当前文本（**必填非空**，空则拦截） |
| 驳回 | `rejected` | `None` |
| 取消 | 不变 | 不变 |

- 类型中文：`faq_new→新增FAQ / faq_revise→修订FAQ / term_new→新增术语 / row_revise→修订表格行 / section_new→新增小节 / none→无需动文档`；
- 表格类型（faq_new/faq_revise/term_new/row_revise）文本含 `|` → 拦截提示（破坏表格行）。

### 3.3 数据流

```
[确认对话框：审阅 + 改文本 + 选状态]
   │ rds.update_proposal_item(path, item_id, status, edited_text)（原子写）
   ▼
提案 JSON 原位更新（status/edited_text 永久留痕）→ 刷新明细表与统计
   │ [合入已确认提案]（现有按钮，复用 apply_rule_proposal.py）
   ▼
脚本合入 approved/revised（edited_text 优先）→ audit --strict 失败回滚
→ 重建元规则语料 → changelog → 归档 → UI 刷新列表
```

### 3.4 rds 服务层

```python
_VALID_PROPOSAL_STATUSES = {"pending", "approved", "revised", "rejected"}

def update_proposal_item(root, proposal_path, item_id, status, edited_text=None) -> dict:
    """原位更新提案 JSON 中指定条目的 status/edited_text（临时文件 + os.replace 原子写）。

    非法 status / 未知 item_id → ValueError；OSError 由调用方处理（原文件不变）。
    """

def confirmed_diff_path(root: Path) -> Path:
    """B2 确认清单路径（scripts/.sync_confirmed_diffs.json）。"""
```

---

## 四、失败处理细节

### 4.1 总原则

1. **文档绝不半更新**：批量改文档先全量预检，任一硬错误整批拒绝（all-or-nothing），与提案合入"errors 非空则不写回"一致；
2. **失败可定位可重试**：逐条报告（段/行号/原因），确认状态持久化（提案 JSON / 确认清单），修复后重试不丢内容；
3. **退出码即状态**：0 成功 / 1 业务失败 / 2 前置失败，UI 按码渲染结论；
4. **UI 校验前置**：能在 UI 拦住的（空值、竖线）不进脚本。

### 4.2 B2 失败场景全表

**阶段 0 UI 校验**（不启动进程）：

| 场景 | 检测 | 处理 |
|---|---|---|
| 未勾选任何行 | 按钮禁用 | 无法触发 |
| 确认值为空 | 收集时校验 | `QMessageBox` 列出行号，中止 |
| 确认值含 `\|` | 收集时校验 | 提示"将破坏表格行"，中止 |
| 勾选 checkpoint | checkbox 置灰 | 无法触发 |

**阶段 1 清单写入**：

| 场景 | 检测 | 处理 |
|---|---|---|
| 路径不可写/磁盘满 | UI 捕获 OSError | `QMessageBox.critical` + 日志 ✘，不启动进程 |
| 清单损坏/缺失 | 脚本读失败 | 退出码 2，stderr 打印 → UI ✘ 前置失败 |

**阶段 2 脚本预检与应用**（all-or-nothing）：

| 场景 | 检测 | 处理 |
|---|---|---|
| line_no 越界/行不存在 | 定位失败 | errors（附请求行号），整批拒绝 |
| 当前行 ≠ old（文档被其他途径改过） | old 精确匹配 | errors（附当前行前 40 字符），整批拒绝——防错位替换 |
| 确认值含 `\|` | 应用前校验 | errors，整批拒绝 |
| 文档读取失败/编码错 | 顶层异常 | 整批拒绝，退出码 2 |
| 预检全通过 | — | 内存构建新文档 → 单次 write |

**幂等与重试**：失败后文档未变 → 刷新差异表（失败行仍在）→ 核对 old 提示 → 重新勾选/改值 → 重试。成功行应用后从表消失，不会重复应用。

### 4.3 B3 失败场景全表

**确认对话框**：

| 场景 | 检测 | 处理 |
|---|---|---|
| 修改后通过但文本为空 | 对话框内校验 | 弹提示，拦截提交 |
| 表格类型文本含 `\|` | 对话框内校验 | 弹提示，拦截 |
| 写回 JSON 失败 | update_proposal_item 抛 OSError | `QMessageBox.critical` + 日志 ✘，对话框不关闭可重试 |

**写回（rds.update_proposal_item）**：原子写（临时文件 + os.replace），异常时原 JSON 不变；合入进程运行中确认按钮禁用（复用"正在执行"），不做文件锁。

**合入阶段**（复用现有 apply_rule_proposal.py）：

| 场景 | 脚本行为 | UI 反馈 | 恢复路径 |
|---|---|---|---|
| 无 approved/revised 项 | 退出 0，"无可合入" | ✔ 无可合入的已确认项 | 去确认 |
| 单项合入错误 | errors → 退出 1，未写回 | ✘ 合入失败 N 项 + 明细 | 提案未动，改后重试 |
| audit --strict 失败 | 文档自动回滚 → 退出 1 | ✘ audit 未通过，文档已回滚 | 修文档/提案后重试 |
| maintain 失败 | 退出 1 | ✘ 文档已合入，但语料重建失败，请到语料状态页重试 | 语料状态页重建 |
| 成功 | 退出 0 → changelog + 归档 | ✔ 已合入 X 项 + 刷新 | — |

---

## 五、日志模块设计

### 5.1 现状与痛点

| 现状 | 痛点 |
|---|---|
| 日志区纯文本混排 | 一眼分不清成功失败 |
| UI 生成行无时间戳 | 无法回溯 |
| 日志只在内存 | 排查无据可查 |
| `_last_output()` 依赖日志区全文 | 改造必须保持纯文本可读 |

### 5.2 行分类标记（`_append_marked`）

| 标记 | 含义 | 色 |
|---|---|---|
| `$ python …` | 命令回显 | 默认 |
| （无前缀） | 脚本 stdout/stderr 原样 | 默认 |
| `✔ …` | 成功结论 | 绿 |
| `⚠ …` | 部分成功/哨兵/警告 | 橙 |
| `✘ …` | 失败结论 | 红 |

- 实现：`QPlainTextEdit.appendHtml` + `html.escape`（防脚本输出注入），脚本原始输出仍走 `appendPlainText`；
- `toPlainText()` 不受影响 → `_last_output()` 解析兼容零破坏；
- 追加后 `ensureCursorVisible()` 自动滚底；
- 失败明细（`·` 前缀）由脚本输出原样呈现，不做二次提取。

### 5.3 执行记录持久化（logs/rule_doc_ops.log）

每次脚本执行落一条（命令 / 退出码 / 结论）：

```
2026-08-16 09:18:17 [INFO] sync_rule_stats.py --json → exit=1 ⚠ 执行完成（哨兵：检测到差异）
2026-08-16 09:18:40 [INFO] sync_rule_stats.py --apply-json → exit=0 ✔ 执行完成
```

- 模块级 logger `logging.getLogger("rule_doc_ops")`（`propagate=False`，独立文件不混入 app.log）；
- `RotatingFileHandler`（10MB × 5 备份，仿 `logging_config.py` 的 `_MANAGED_HANDLER_ATTR` 防重复注册），路径 `root/logs/rule_doc_ops.log`（root 变化时换 handler）；
- `_on_finished` 统一出口写记录（记录 `self._last_command`），不散落；
- UI 内部异常仍走现有 `logging`（app.log）——职责分离：**rule_doc_ops = 用户操作轨迹，app.log = 内部异常**。

### 5.4 不做的事

- 不做日志级别过滤器 UI / 搜索 / 错误面板（当前量级不需要）；
- 不给脚本输出加时间戳（破坏现有 `parse_audit_output` 等解析）；
- 不做执行超时强杀（脚本秒级完成）。

---

## 六、测试计划（失败路径优先）

**脚本层（tests/test_sync_rule_stats.py）**
- `apply_confirmed`：全部成功 / 单行 old 不匹配整批拒绝且文档不变 / line_no 越界 / 确认值含 `|` / 部分失败计数；
- CLI `--apply-json`：清单缺失 → 退出 2；混合结果 → 退出 1；成功 → 0 且 changelog 追加（subprocess 或 main 注入）。

**服务层（tests/test_rule_doc_service.py）**
- `update_proposal_item`：四状态写回 / 原子写（异常时原文件不变）/ 非法 status、未知 item_id 拒绝；
- `confirmed_diff_path` 路径正确。

**UI 层（tests/test_rule_doc_panel.py）**
- diff 表 6 列渲染（full 默认勾选、checkpoint 置灰不可勾、确认值默认 new）；
- `_collect_confirmed_rows`：payload 字段正确 / 空值、竖线拦截（返回 None）；
- proposal 表 6 列 + 状态中文 + 统计标签；
- 确认流程：直接调 `rds.update_proposal_item` + 刷新断言 JSON 变化；
- `_append_marked` 后 `toPlainText()` 仍含纯文本（兼容 `_last_output`）；
- `_on_finished` 落 rule_doc_ops 记录（tmp root 注入验证文件存在且含结论）。

---

## 七、实施顺序

1. **脚本层**：`sync_rule_stats.py` 新增 `apply_confirmed` + `--apply-json` + 脚本测试（无 UI 依赖，风险最低）；
2. **服务层**：`rds.confirmed_diff_path` + `rds.update_proposal_item` + 服务测试；
3. **UI 层 diff tab**：6 列表格 / 勾选统计 / 应用按钮 / payload 构造 / 退出码结论映射（`failure_codes`）；
4. **UI 层 proposal tab**：6 列表格 / 状态中文 / 统计 / `ProposalItemConfirmDialog`；
5. **日志模块**：`_append_marked` 着色 + rule_doc_ops 记录；
6. **集成冒烟**：临时项目勾选差异应用 → 文档实际变化；提案确认 → 合入链路走通。

## 八、相关文件

- `src/ui/maintenance/rule_doc_panel.py`
- `src/business/rag/rule_doc_service.py`
- `scripts/sync_rule_stats.py`
- `tests/test_sync_rule_stats.py`、`tests/test_rule_doc_service.py`、`tests/test_rule_doc_panel.py`
- `docs/design/rag_maintenance_ui_layout.md`（前置：A 档 + B1 已实施）
