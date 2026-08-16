# 知识库维护工作台规格（RAG 本地化维护）

> 状态：已实施
> 范围：主导航「知识库维护」页面；武将/卡牌/专属牌等数据变更后，在应用内本地重建 RAG 语料与向量索引。
> 不再依赖外部 mjs_rag_project 仓库：数据源、文档源、构建脚本、嵌入模型与向量索引均已收编到 test_project。

## 一、目标

- 武将新增/调整、卡牌注解、专属牌维护后，能直观看到哪些 RAG 语料任务已过期（待重建）。
- 一键在本地执行「重建语料 → 重建向量索引」，实时展示日志与块数校验，无需手工跑命令行或跨仓库同步。

## 二、UI 位置

- 主导航第 4 页「知识库维护」（`src/ui/maintenance/rag_maintenance_panel.py`，`MainWindow.PAGE_CONTEXTS` 与 `NavigationRail.PAGE_LABELS` 同步追加）。
- 页面为多页签结构（复用 `librarySectionTabs` 样式），五个页签：
  1. **语料状态**：PageActionBar（状态文字 + 刷新状态 / 重建武将语料 / 重建全部语料 / 重建语料+索引）+ 任务状态表（ragTableSurface 卡片，8 个语料任务 × 状态/输出块数/数据源）+ 人工维护 NoticeBanner（未归类武将、专属牌引用未知武将、点数/装备/结算回填校验等 warning；无问题时显示成功）+ 执行日志区（ragLogSurface 卡片内只读等宽 QPlainTextEdit，QProcess 实时追加）；
  2. **专属牌维护**：复用 `src/ui/library/special_cards_panel.py` 的 `SpecialCardsPanel`（从资料库移入，数据源 `data/special_cards.json`；专属牌/专属战法牌条目含牌面事实字段：花色/点数/攻击范围/结算详情，由原 xlsx【专属牌】sheet 迁移回填）；
  3. **卡牌点数维护**：`src/ui/maintenance/card_points_panel.py`，维护 `data/card_points.json`（原 xlsx sheet1 + 硬编码判定规则迁移）——牌面明细 162 张（只读浏览 + 新增/编辑/删除牌行）、卜卦判定规则（12 条，增删改）；「从 xlsx 导入」按钮调用 `scripts/migrate_excel_to_json.py --only points` 基于归档 xlsx 全量覆盖点数数据；
  4. **装备属性维护**：`src/ui/maintenance/equip_attrs_panel.py`，维护 `data/equip_attrs.json`（原 xlsx sheet2 与 build_equip_attr.py 硬编码迁移）——26 件装备表格编辑（名称/备注只读，细分类型/攻击范围/距离修正可编辑，保存时校验）；
  5. **武将分类维护**：`src/ui/library/hero_classification_panel.py`，维护 `data/hero_classification.json` 的三个子页签——分类管理（分类 CRUD，删除时清理归类/克制链引用）、克制链（分类 → 克制说明文本）、武将归类（全部/未归类/已归类筛选 + 分类多选 + 未归类定位）。
- 子面板保存后发 `data_changed`，知识库维护页自动刷新「语料状态」并标记待重建。
- 武将分类数据约束：分类 `name` 唯一；`hero_categories` 与 `counter_chain` 引用的分类必须存在；`updated_at` 保存时自动刷新为当天。

## 三、数据流与本地管线

- 权威源：`data/heroes.json`、`data/cards.json`、`data/card_annotations.json`、`data/special_cards.json`、`data/hero_classification.json`、`data/card_points.json`、`data/equip_attrs.json`、`docs/元规则整理-完整版.md`（原 `data/mjs卡牌点数.xlsx` 已归档至 `data/archive/`，仅由迁移脚本作为重新导入通道）。
- 语料产物：`data/rag_corpus/*.json`（build 脚本直接输出到此目录）；向量索引：`data/rag_index/chroma`。
- 执行命令：`python scripts/maintain_rag.py --force [--only 武将] [--build-index]`（工作目录为项目根）。
- 任务状态判定：任一源文件 mtime 晚于对应语料输出 → 待重建；输出缺失 → 待重建；源缺失 → 缺源。
- 审计：未归类武将（heroes 未出现在 hero_classification.hero_categories）；专属牌 hero 不在武将库且非「通用/—/众多武将/以“等”结尾」（按顿号/逗号拆分后逐项校验）；技能描述中的疑似牌名/道具名（启发式提取 + 黑名单/已知名称/排除清单过滤，非专属牌，仅作人工确认提示）；card_points.json 花色（♥♣♠♦太极）/点数（1~8）/张数（162）合法性；equip_attrs.json 件数（26）/细分类型/距离修正合法性；专属牌/战法牌结算详情回填完整性（死士为非实体牌标记，豁免）。

## 四、验收标准

- 页面显示 8 个语料任务与真实块数；数据变更后「待重建」标记正确出现。
- 点击「重建武将语料」仅重建武将/武将分类语料；「重建全部语料」重建 8 个任务；「重建语料+索引」额外重建向量索引。
- 执行期间按钮禁用，日志实时滚动；结束后自动刷新任务状态。
- 全部过程不访问 `G:\py_savepoint\mjs_rag_project`（嵌入模型缓存 `data/rag_models/` 为本地副本）。

## 五、索引精化（2026-08 新增，UI 重设计 2026-08 实施，2026-08 扩展已精化浏览/再编辑/取消精化）

- 入口：知识库维护页 →「语料状态」操作栏「索引精化」按钮（文案带待精化数量角标，如「索引精化（5）」；无待精化时显示「索引精化 ✓」但仍可点击，进入浏览/管理已精化块）；审计横幅同步展示「索引字段待精化 N 块 [去精化]」条目（kind=pending_refinement，排第一位，点击直接打开对话框）。
- 维护对象：卡牌RAG语料.json 与 武将RAG语料.json 中无 curated 且索引字段为空的块；已精化（curated）块可在「已精化」范围浏览、再编辑与取消精化（详见 docs/design/refinement_curated_browse_edit.md）。
- 字段：timing / trigger_condition / keywords / related；支持 LLM 建议（DeepSeek）与人工填写。
- 写回：apply_curated 更新块顶层索引字段并写入 curated（method=llm/manual、updated_at），build 脚本重跑时通过 scripts/rag_curated.py 保留精化值。

### 5.1 对话框布局（1160×720，详见 docs/design/index_refinement_ui_redesign.md 与 docs/design/refinement_curated_browse_edit.md）

- A 顶部总览条：进度条（已完成 x/总数，全部完成变绿，仅待精化模式显示）+ 统计文字（按模式：待精化 N 块·已处理 D 块 / 已精化 M 块（人工 X·LLM Y）/ 共 T 块三分类）+ 模式切换（待精化/已精化/全部，右对齐）。
- B 清单区：搜索框（名称/block_id 子串过滤，250ms 防抖）与类型筛选（全部/卡牌/武将）同行；4 列表格（语料/名称/说明/状态，固定列宽，block_id 在名称列 tooltip；说明列：待精化=缺失字段、已精化=来源·时间、普通块=—）；空状态（EmptyState）；批量操作行（LLM 建议（全部）/ 保存全部，**仅待精化模式可见**）。
- C 工作区：条目头（名称+类型徽标+来源/时间徽标（已精化块）+缺字段徽标+block_id）→ 横向分栏（左原文卡片**占满高度持续展示**、不可折叠；右 4 个字段状态卡片纵向均分，fieldState=empty/llm/saved/manual 左边框 BORDER/PRIMARY/BORDER_STRONG/SUCCESS 着色 + 徽标；字段提示词在编辑器 placeholder，不占常驻行）→ **条目操作行**（横跨分栏底部：LLM 建议（当前）/ 跳过当前（仅待精化）/ 取消精化（仅已精化/全部，且当前块有 curated）/ 保存当前（唯一 PRIMARY，最右））。
- D 底部操作条：仅「关闭」（右对齐，GHOST）。

### 5.2 交互规则

- 清单行状态：○ 未处理 / ◉ 已建议 / ✎ 已修改 / ✓ 已精化 / ○ 已生成（后两者用于已精化与普通块）。
- 按钮按模式**显隐**（隐藏而非禁用）：批量行与跳过仅待精化模式；取消精化仅已精化/全部模式；保存当前/LLM 建议（当前）全模式可用，未选中条目时禁用。
- LLM 建议（全部）事件循环化（QTimer 队列逐块处理），窗口不冻结；结束汇总成功/失败块数；已精化/全部范围仅提供「LLM 建议（当前）」。
- 未保存修改保护：切换条目/关闭/批量建议前弹确认，拒绝则保持原条目。
- 保存当前：与磁盘基线（_saved_baseline）一致时提示「无修改，未保存」不写文件；有改动才写回（method 与本次 LLM 建议完全一致为 llm，否则 manual；已精化块再编辑保存后 method=manual、updated_at=今天）。
- 保存全部：当前选中块用编辑器内容，其余块用已生成的 LLM 建议（baseline）；无任何内容的块跳过并保持待精化。
- 取消精化：删除 curated 字段后，字段有空缺的块退回待精化池，字段全满的块转为普通块。
- 切回已建议条目时还原 LLM 建议内容（存于 _llm_baseline），不因切换丢失。
