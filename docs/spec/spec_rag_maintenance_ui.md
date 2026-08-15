# 知识库维护工作台规格（RAG 本地化维护）

> 状态：已实施
> 范围：主导航「知识库维护」页面；武将/卡牌/专属牌等数据变更后，在应用内本地重建 RAG 语料与向量索引。
> 不再依赖外部 mjs_rag_project 仓库：数据源、文档源、构建脚本、嵌入模型与向量索引均已收编到 test_project。

## 一、目标

- 武将新增/调整、卡牌注解、专属牌维护后，能直观看到哪些 RAG 语料任务已过期（待重建）。
- 一键在本地执行「重建语料 → 重建向量索引」，实时展示日志与块数校验，无需手工跑命令行或跨仓库同步。

## 二、UI 位置

- 主导航第 4 页「知识库维护」（`src/ui/maintenance/rag_maintenance_panel.py`，`MainWindow.PAGE_CONTEXTS` 与 `NavigationRail.PAGE_LABELS` 同步追加）。
- 页面为多页签结构（复用 `librarySectionTabs` 样式），三个页签：
  1. **语料状态**：PageActionBar（状态文字 + 刷新状态 / 重建武将语料 / 重建全部语料 / 重建语料+索引）+ 任务状态表（ragTableSurface 卡片，8 个语料任务 × 状态/输出块数/数据源）+ 人工维护 NoticeBanner（未归类武将、专属牌引用未知武将等 warning；无问题时显示成功）+ 执行日志区（ragLogSurface 卡片内只读等宽 QPlainTextEdit，QProcess 实时追加）；
  2. **专属牌维护**：复用 `src/ui/library/special_cards_panel.py` 的 `SpecialCardsPanel`（从资料库移入，数据源 `data/special_cards.json`）；
  3. **武将分类维护**：`src/ui/library/hero_classification_panel.py`，维护 `data/hero_classification.json` 的三个子页签——分类管理（分类 CRUD，删除时清理归类/克制链引用）、克制链（分类 → 克制说明文本）、武将归类（全部/未归类/已归类筛选 + 分类多选 + 未归类定位）。
- 子面板保存后发 `data_changed`，知识库维护页自动刷新「语料状态」并标记待重建。
- 武将分类数据约束：分类 `name` 唯一；`hero_categories` 与 `counter_chain` 引用的分类必须存在；`updated_at` 保存时自动刷新为当天。

## 三、数据流与本地管线

- 权威源：`data/heroes.json`、`data/cards.json`、`data/card_annotations.json`、`data/special_cards.json`、`data/hero_classification.json`、`data/mjs卡牌点数.xlsx`、`docs/元规则整理-完整版.md`。
- 语料产物：`data/rag_corpus/*.json`（build 脚本直接输出到此目录）；向量索引：`data/rag_index/chroma`。
- 执行命令：`python scripts/maintain_rag.py --force [--only 武将] [--build-index]`（工作目录为项目根）。
- 任务状态判定：任一源文件 mtime 晚于对应语料输出 → 待重建；输出缺失 → 待重建；源缺失 → 缺源。
- 审计：未归类武将（heroes 未出现在 hero_classification.hero_categories）、专属牌 hero 不在武将库且非「通用」。

## 四、验收标准

- 页面显示 8 个语料任务与真实块数；数据变更后「待重建」标记正确出现。
- 点击「重建武将语料」仅重建武将/武将分类语料；「重建全部语料」重建 8 个任务；「重建语料+索引」额外重建向量索引。
- 执行期间按钮禁用，日志实时滚动；结束后自动刷新任务状态。
- 全部过程不访问 `G:\py_savepoint\mjs_rag_project`（嵌入模型缓存 `data/rag_models/` 为本地副本）。

## 五、索引精化（2026-08 新增）

- 入口：知识库维护页 → 「索引精化」按钮，打开 IndexRefinementDialog。
- 维护对象：卡牌RAG语料.json（20 块）与 武将RAG语料.json（123 技能块）中无 curated 且索引字段为空的块。
- 字段：	iming / 	rigger_condition / keywords / elated；支持 LLM 建议（DeepSeek）与人工填写。
- 写回：pply_curated 更新块顶层索引字段并写入 curated（method=llm/manual、updated_at），build 脚本重跑时通过 scripts/rag_curated.py 保留精化值。
