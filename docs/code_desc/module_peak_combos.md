# 模块：巅峰赛与实战配队

> 对应目录：`src/ui/match/peak_*` + `src/ui/match/match_lineup_state.py` + `src/ui/match/match_analysis_view.py` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/data/peak_win_rate_repository.py` + `src/ocr/card_grid_detector.py` + `src/ui/data_admin/combos_import_dialog.py` + `src/scripts/import_combos.py`
> 职责：巅峰赛（2v2 模式）选将实时识别循环（会话世代校验、牌面签名去重、标准轮询互斥）、禁选建议象限判定、卡牌网格检测、实战配队（combos）数据管理与座次解析、配队异步导入、对局攻略阵容状态与离线分析渲染

---

## 一、模块职责

巅峰赛（2v2 模式）与标准选将页共用同一套截图 OCR 基础设施，但牌面布局截然不同：标准选将是固定 8 个 ROI，而 2v2 牌面先发 14 张 → 双方同时禁选 3 名（可撞车）后剩余 8~11 张 → 卡牌按行重排（7+7 / 5+5 / 4+5 等），不能沿用固定 ROI。因此本模块在 `src/ocr/card_grid_detector.py` 中引入**内容驱动**的卡位检测：从整页截图定位剩余候选武将卡牌 bbox，再派生名条 ROI 交给通用 OcrWorker。

巅峰赛选将完整链路：`card_grid_detector`（卡位检测）→ `PeakSelectWatcher`（识别循环）→ `peak_ban_advice`（象限判定）→ `PeakHeroCard`（卡片渲染）→ `ComboManager`/`combo_seats`（配队匹配与座次）。识别循环与标准轮询并存但互斥，采用**会话制挂起**：点击「开始识别」即挂起 `hero_selection` / `match_guide` 两个标准任务（并清除二者冷却、作废在途轮询），牌面出现期间每拍幂等重挂；`hero_selection` 整个识别会话保持挂起（手动停止识别才恢复原状态），`match_guide` 牌面出现期间挂起、牌面自动退出时恢复原状态并另发 `board_exited` 信号供主窗口衔接激活对局攻略轮询。

候选面板持续刷新受两处机制治理：**牌面签名**量化步长取位置 8px / 尺寸 16px，覆盖候选阶段卡面 idle 浮动动画实测漂移（纵向 ±3~4px、剪影尺寸 ±5px）的 2 倍，避免逐拍误判新牌面；真实换牌表现为卡数变化或整排重排（位移 ≥ 一个卡位宽），远超步长不会漏。**人工确认**在新牌面上按内容沿用而非清空（`carry_over_resolutions`），确认跟着武将走而不是槽位走，槽位重排时迁移、内容消失时自然失效。

本模块同时承载**对局攻略**（2v2 标准选将后的离线分析）：`LineupState` 维护四名武将的敌我确认与主将选择状态（纯逻辑无 Qt），`MatchAnalysisView` 将已确认阵容的分析结果渲染为四个攻略页。`ComboManager` 供巅峰赛候选池匹配与对局攻略共享使用。

禁选建议采用双维度象限判定（出场热度 × 胜率强度），仅强势象限出标签：

- **Ban 位首选** — 强势冷门（胜率 ≥ 50% 且出场排名 > 50），BPI 权重 1000
- **热门强将** — 版本热门强将（胜率 ≥ 50% 且出场排名 ≤ 50），BPI 权重 500
- 弱势象限不打标签；任一维度缺失也不打标签

实战配队数据由 `ComboManager` 管理：外部工具导出 JSON → 异步导入合并（手工记录 `manual=True` 同 key 冲突优先保留）→ 落盘按 `(-rating, hero1_id, hero2_id)` 稳定排序（物理行序与武将名解绑，消除 combos.json 名序抖动）→ 巅峰赛候选池中按 rating 匹配并显示。

---

## 二、文件结构

```
src/data/
  ├── combo_manager.py                  # ComboManager — Combo 数据 CRUD + 手工记录管理 + 稳定排序落盘
  ├── combo_seats.py                    # parse_seats() / format_seats() — 从 note 文本解析双方武将座次
  └── peak_win_rate_repository.py       # 巅峰赛专属胜率/出场排行 CSV 读取（独立于 2v2）

src/ocr/
  └── card_grid_detector.py             # detect_selection_cards() / derive_name_rois() — 内容驱动卡位检测

src/business/
  ├── analysis/peak_ban_advice.py       # evaluate_peak_ban_advice() / derive_win_rate_ranks() — 双维度象限判定
  ├── recognition/peak_select_watcher.py # PeakSelectWatcher / parse_pool() / board_signature() — 识别循环
  └── maintenance/combo_import_service.py # run_import() — 实战配队导入合并（CLI 与 UI 共用）

src/ui/match/
  ├── peak_select_panel.py              # PeakSelectPanel — 巅峰赛选将工作台
  ├── peak_hero_card.py                 # PeakHeroCard — 候选武将卡片
  ├── match_lineup_state.py             # LineupState — 对局攻略阵容纯状态与确认规则
  └── match_analysis_view.py            # MatchAnalysisView — 对局攻略四页分析渲染

src/ui/data_admin/
  └── combos_import_dialog.py           # CombosImportDialog — 异步导入对话框（QThread 后台执行）

src/scripts/
  └── import_combos.py                  # main() — 实战配队导入 CLI 入口（共用 run_import）

数据（data/，仅列本模块链路上读取的）：
  ├── combos.json                       # 实战配队（ComboManager 读写，落盘稳定排序）
  ├── 巅峰赛胜率排行.csv                # 列头 排名,武将,胜率（175 条武将）→ load_peak_win_rates()
  └── 巅峰赛出场排行.csv                # 列头 排名,武将（175 条武将）→ load_peak_pick_ranks()
```

---

## 三、核心逻辑

### 3.1 卡位检测（card_grid_detector.py）

2v2 模式牌面在禁选前 14 张、候选期 8~11 张，且行内数量可变化。固定 ROI 不适用，改为**内容驱动**：

1. 全图 HSV 转掩码：背景为低饱和宣纸（S≈8 / V≈230），卡面立绘远超对比阈值 → `S>90 或 V<90` 掩码
2. 闭运算核 = 5~7px（1440p 基准 5px，自适应缩放，下限 `CLOSE_KERNEL_MIN=3`），避免上下两行 bbox 粘连（等待期两行间隙仅 0~3px，核 ≥9 会把两行粘成整块）
3. 连通域过滤：面积 > 0.55% 全图像素（`AREA_MIN_RATIO=0.0055`）、宽度落在 [0.086, 0.115]×图宽、高度 [0.215, 0.245]×图高、宽高比 [0.60, 0.95]、位于卡片区 [0.12, 0.88]×[0.16, 0.67] 比例范围（排除顶部序章图标、底部席位标签与进度条）
4. 行聚类 + 行内 x 排序 → 行优先返回 bbox 列表（以半卡高为聚类阈值，避免绝对桶边界受 y 方向数像素抖动影响）
5. 卡数不在 [8, 14]（`CARD_COUNT_RANGE`）内时返回 None，语义对齐轮询的 `healthy_no_match`

**名条 ROI 派生**（`derive_name_rois`）：以卡 bbox 左缘锚定，名条位置 [x+0.06w, y+0.15h, 0.30w, 0.38h]。纵向实测（多张卡标定）：阵营徽章 0~13%、名字 17%~49%（三字名起点更高）、等级数字 55~64%、费用角标 80~95%，故取 15%~53% 避开徽章与数字污染。

参数均为相对比例（基准 2560×1440 实测），分辨率变化时自适应。

### 3.2 巅峰赛识别循环（PeakSelectWatcher）

`PeakSelectWatcher` 是独立 QObject，与标准轮询并存：

```
Tick（每 1.5s，POLL_INTERVAL_MS=1500）
  └─ _thread_lock 非阻塞获取失败 → 跳过本轮（上一拍截图+OCR 尚未完成）
  └─ _do_work() 后台线程（session = 当前 _session）
      ├─ CaptureService.capture_for_poll() 截图
      ├─ [_state_lock] session != _session → 停止/重启后的旧拍直接放弃（不检测、不清理、不发布）
      ├─ detect_selection_cards(frame) 卡位检测
      │   └─ None → _handle_board_absent(session) → miss_ticks++ → BOARD_EXIT_TICKS=2 后仅恢复 match_guide 并发 board_exited
      ├─ 检出牌面 → [_state_lock] miss_ticks 归零 + _suspend_standard_tasks()（幂等重挂）
      │   └─ 重挂发生在签名 unchanged 短路之前：签名未变的拍也会重新挂起，
      │      覆盖 ADB 重连 start_poll 等外部重新激活标准任务的场景
      ├─ board_signature(cards) 量化坐标/尺寸
      │   └─ == 上次（_state_lock 内读）→ 牌面未变化，沿用结果 return
      ├─ 新牌面 → [_state_lock] carry_over_resolutions() 按内容沿用人工确认
      ├─ _recognize_board(image, cards)
      │   └─ 提交 OcrTask 到 OcrWorker，OCR_WAIT_TIMEOUT_SECONDS=15 超时保护
      │   └─ 失败 → [_state_lock]（世代校验后）清签名 _signature=None，下一拍强制重试
      └─ _publish_pool() → parse_pool() → PoolSnapshot → pool_updated 信号
```

**会话世代校验**：`_session` 在 `start()` / `stop()` 各于状态锁内递增一次；在途识别拍在截图完成后、挂起前、OCR 失败清签名前、写签名/沿用确认/发布前四处校验世代，过期旧拍直接放弃。这消除了「停止识别瞬间在途旧拍反向重新挂起标准任务」「旧签名写回新会话」「停止后面板仍被旧快照刷新」三类竞态；缺席计数自增同步移入锁内，消除非原子更新。

**并发安全**：`_thread_lock` 仅保证识别拍单飞；`_state_lock` 串行化 GUI 线程（start / confirm_pending）、识别线程与图片导入线程对 `_session` / `_miss_ticks` / `_signature` / `_ban_names` / `_resolutions` / `_last_board` 的读写。锁内只做纯内存读写，不发 IO、不 emit 信号（`pool_updated` 在锁外发出）。

`PoolSnapshot` 数据类：
- `card_count`: 当前牌面卡牌数
- `names`: 已确认武将名
- `pending`: 待确认槽位
- `stage`: "ban"（≥12 张，`_BAN_PHASE_MIN_CARDS=12`）或 "pick"（8~11 张）
- `overlap`: 候选阶段双方撞车数（池大小 − 8）
- `banned`: 相对禁选期已确认名单的差集

**人工确认**：`confirm_pending(slot, name)` 写入 `_resolutions[slot]` 后立即用 `_last_board` 重发快照；`parse_pool` 校验确认名必须在候选内（且该槽仍处于 `_CONFIRM_RESOLUTIONS = {"unresolved", "unknown", "conflict"}` 未自动确认态）才生效，避免旧牌面的确认串到新牌面。新牌面到来时由 `carry_over_resolutions()` 按内容沿用：确认名仍在原槽候选集则原地保留；否则在「仍未自动确认且候选集同时只命中一个已确认名」的槽位迁移过去（同名不扩散到多个槽位），内容消失（换人/选走）的确认自然失效，歧义槽保守丢弃。沿用基准取当前 `_resolutions`，覆盖 OCR 期间用户对新牌面 pending 的点击。

**图片导入**：`recognize_image_file()` 在独立锁（`_import_lock`）下执行，不影响循环签名与标准任务挂起状态（不写 `_signature`、不校验会话世代）。

**与三板块流程的衔接**（选将推荐 / 巅峰赛 / 对局攻略共用统一 OCR 队列与标准轮询任务）：标准轮询在提速后由「单次触发 + 完成后重排」改为常驻重复定时器，周期收敛为 max(间隔, 处理耗时)，失败退避改为动态调整定时器间隔；不再有排程空档，也就无法依赖「进入牌面挂一次」维持互斥，这是每拍幂等重挂的直接原因。巅峰赛自身拍节奏不变：QTimer 1.5s 触发 + `_thread_lock` 非阻塞单飞，单拍 OCR 等待上限 15s。标准轮询另有一套页面指纹去重（名条 ROI 缩放后取 16×16 灰度块容差比较，仅标准轮询显式开启）用于复用 OCR 结果，与本模块的 `board_signature`（决定是否重新识别整牌面）是两套独立机制。牌面自动退出后经 `board_exited` 衔接激活对局攻略轮询，并受主窗口 90 秒空闲守护约束——超时仍未命中即失活，兜住巅峰赛后回大厅的 fallback 空转。

**标准任务协调**（会话制）：
- `start()` 挂起即时生效而非等首拍检测到牌面——首拍之前标准轮询用固定 ROI 在巅峰页只会跑出垃圾结果，还可能误触冷却与自动跳页。挂起后依次清除 `hero_selection` / `match_guide` 双任务冷却、调用 `invalidate_inflight_poll()` 作废点击开始前已发出的在途轮询（其冷却/激活/跳转副作用会对抗刚建立的挂起状态），最后启动定时器。
- `_suspend_standard_tasks()` 幂等：仅首次调用记录原状态快照（`_saved_task_states`），随后只挂起当前活跃任务，可随每拍重复调用。
- `stop()` 先递增会话世代作废在途旧拍，再 `_restore_standard_tasks()` 恢复全部标准任务原状态（活跃→activate、非活跃→deactivate），并清空快照。
- 牌面自动退出（连续 `BOARD_EXIT_TICKS=2` 拍未检出）只调 `_restore_match_guide()` 恢复 match_guide 原状态，`hero_selection` 留待停止识别恢复；同时发 `board_exited` 信号交由主窗口决定对局攻略轮询的激活与跳页。

### 3.3 禁选建议（peak_ban_advice.py）

纯函数，无状态。阈值常量：`HOT_PICK_RANK_MAX=50`、`STRONG_WIN_RATE_MIN=50.0`。

```python
def evaluate_peak_ban_advice(
    win_rate: float | None, pick_rank: int | None, win_rate_rank: int | None,
) -> PeakBanAdvice | None:
    if win_rate is None or pick_rank is None or win_rate_rank is None:
        return None
    if win_rate < STRONG_WIN_RATE_MIN:
        return None
    if pick_rank > HOT_PICK_RANK_MAX:
        return PeakBanAdvice(key="ban_first", label="Ban 位首选",
                             weight=1000, bpi=1000 + pick_rank - win_rate_rank, ...)
    return PeakBanAdvice(key="hot_pick", label="热门强将",
                         weight=500, bpi=500 + pick_rank - win_rate_rank, ...)
```

`derive_win_rate_ranks(win_rates)` 按胜率降序推导 1-based 排名（同分按名称稳定排序）。BPI = 权重 + 出场排名 − 胜率排名，用于卡片 tooltip 排序依据。`PeakBanAdvice` 为 frozen dataclass，含 `key`（供配色）、`label`、`detail`（tooltip 文案）、`weight`、`bpi`。

### 3.4 实战配队数据（ComboManager + combo_seats.py）

`ComboManager` 继承 `DataManager[Combo]`，key = 排序后的 `(hero1_id, hero2_id)`（`_combo_key` 取 `sorted((a_id, b_id))`，确保 (A,B) 与 (B,A) 一致）：

- `get_combo(hero_a_id, hero_b_id)` / `list_combos_for_hero(hero_id)` / `list_combos()` 查询
- `save_manual_combo(combo, previous=None)`: 编辑时若 key 变化则迁移（删除旧 key）；`combo.manual = True` 标记；导入合并时同 key 冲突优先保留手工记录
- `delete_combo(combo)`: 原子落盘

**稳定排序落盘**（`_save_unlocked`）：按 `(-c.rating, c.hero1_id, c.hero2_id)` 排序后写入。物理行序与武将名解绑——新增武将（id 较大）自然落到各 rating 段末尾，避免按名排序时新名字插入中段、其后条目整体平移造成的 diff 噪音。

`combo_seats.parse_seats(note, hero1, hero2) -> (status, hero1_seats, hero2_seats)` 从 note 自由文本解析座次：

1. 优先匹配 "武将名+数字" 或 "数字+武将名" 两种写法（含 ALIAS 别名：牢布→吕布、甄姬→甄宓、夏侯停→夏侯惇），两位数字 = 可选区间（如 "34"=3 或 4 号）
2. 回退：剥离武将名后取开头的纯数字 token（最多 2 个），按顺序对应 hero1/hero2
3. "0" 表示无座次要求（返回空列表 `[]`）
4. 状态：`STATUS_PARSED`（parsed）/ `STATUS_PARTIAL`（partial）/ `STATUS_NONE`（none，note 无任何数字）/ `STATUS_UNPARSED`（unparsed，有数字但无法归类）
5. 号位范围校验 1~4，越界返回 None

`format_seats(seats)` 将号位列表转为展示文本，空列表显示 "任意"。规则全量验证：1170 条可 100% 分类（1144 解析出座次 + 26 无座次要求，0 失败）。

### 3.5 实战配队导入（combo_import_service.py + combos_import_dialog.py）

**业务层** `run_import(source_path, heroes_path, output_path) -> dict`（幂等合并）：

1. 读取 heroes.json 建立武将名→ID 映射，未匹配项进 `report["unmatched"]`
2. 现有记录分为 `manual_by_key`（手工）与 `imported_keys`（导入）
3. 逐条源记录：重复 key 进 `duplicates`；同 key 存在手工记录则跳过进 `manual_collisions`
4. `parse_seats` 解析座次，非 parsed/none 进 `seat_review`；与 `position` 字段交叉校验（`_check_position_mismatch`：seat 全座 vs 单一 14/23；`position=="both"` 不校验），不一致进 `position_mismatch`
5. 合并：源导出 upsert → 手工记录原样保留（进 `manual_kept`）→ 非手工旧记录若源中已不存在则移除（进 `removed_stale`）
6. `manager.clear_all()` + 逐条 `update` + `save()` 原子落盘

报告 dict 含 11 个区块：`total` / `imported` / `unmatched` / `duplicates` / `invalid` / `seat_stats`（parsed/none/partial/unparsed 计数）/ `seat_review` / `position_mismatch` / `manual_kept` / `manual_collisions` / `removed_stale`。保留判据是「手工记录未进入 merged」而非「不在源导出中」，因此同 key 发生手工冲突的条目会同时出现在 `manual_collisions` 与 `manual_kept`（保留的正是手工版本）。

**UI 层** `CombosImportDialog`（异步化）：通过 `_ImportWorker(QThread)` 后台执行 `run_import`，主线程不冻结。导入完成后 `combos_imported(int)` 信号通知调用方（main_window 侧栏刷新）。`_LIVE_WORKERS` 集合持有运行中 worker，防止对话框销毁后 QThread 被 GC 析构。报告渲染到 QTextBrowser，预览上限 50 条，超限省略剩余。

### 3.6 对局攻略阵容状态（match_lineup_state.py）

纯逻辑无 Qt，供 UI 层调用。四个 dataclass：

- `LineupSlot`: 单个槽位状态（hero / recognized_name / raw_name / candidates / resolution / evidence / confidence / team / side）
- `LineupMutationResult`: 编辑结果（accepted + reason）
- `LineupValidationResult`: 阵容可用性判定（is_valid + reason + message）
- `LineupState`: 四名武将阵容状态机

**槽位索引约定**：`SLOT_COUNT=4`、`PLAYER_SLOT_INDEX=5`（OCR 排序键，非槽位索引）、`ENEMY_SLOT_INDICES={1,2}`、`TEAMMATE_SLOT_INDICES={3,4}`。

**敌我判定**（`_side_from_position`）：需先识别出 player slot 且四名已确认名称唯一。player slot → 我方；enemy slots → 敌方；唯一 teammate → 我方。阵营标签校验（`_check_team_labels`）：楚军/汉军标签必须与固定席位规则一致，标签缺失返回 None（不阻断流程）。

**确认链**：`validate()` 依次检查待确认名称、四人齐备、无重复武将、敌我均确认、双方各 2 名；`can_confirm()` = `validate().is_valid`；`confirm()` 置 `_analysis_confirmed=True`。任何 `set_side` / `replace_hero` / `clear` 操作都会重置 `_analysis_confirmed`，要求重新确认。

`set_side(index, side)` 限制每方最多 2 名（返回 `side_full` 拒绝），主将 slot 随敌我变更自动重选。`replace_hero` 清除全部敌我状态并重置主将与确认标记。

### 3.7 对局攻略分析渲染（match_analysis_view.py）

`MatchAnalysisView` 为 QWidget，含 QTabWidget 四个标签页：总览 / 我方打法 / 对抗敌方 / 单将详情。

`render_unconfirmed(heroes, win_rates, lineup_ready)` — 确认前提示页：显示待确认通知（`NoticeBanner`）+ 已识别单将速览（名字/定位/历史胜率）；其他三页显示 "请先完成阵容核对并生成攻略" 占位文本。

`render_analysis(analysis: MatchAnalysis)` — 已确认阵容的四页渲染：

- **总览**：数据缺失提示（可折叠展开）→ 本局行动优先级（编号卡片）→ 敌方威胁卡片 → 我方速览卡片
- **我方打法**：逐个我方武将攻略卡片（`key_points` 前 3 条 + 新手提示）
- **对抗敌方**：逐个敌方武将攻略卡片（克制类型 + 应对建议）
- **单将详情**：逐个武将详情行（名字/阵营/定位/胜率 + "完整攻略" 按钮 → `GuideDetailDialog`）

---

## 四、关键代码片段

### 4.1 卡位检测核心

```python
def detect_selection_cards(image: np.ndarray) -> list[Roi] | None:
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = (
        np.logical_or(hsv[:, :, 1] > MASK_SATURATION_MIN, hsv[:, :, 2] < MASK_VALUE_MAX)
        .astype(np.uint8) * 255
    )
    kernel_size = max(CLOSE_KERNEL_MIN, round(height / 1440 * CLOSE_KERNEL_BASE))
    kernel = np.ones((kernel_size, kernel_size), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    # 面积/宽高比/卡片区过滤 → 行聚类 → 行内 x 排序
    # 卡数不在 CARD_COUNT_RANGE=(8,14) 内时返回 None
```

> 参数均为相对比例，基准 2560×1440 实测；分辨率变化时自适应。

### 4.2 牌面签名去重与确认沿用

```python
SIGNATURE_POSITION_QUANTUM_PX = 8
SIGNATURE_SIZE_QUANTUM_PX = 16

def board_signature(cards: list[Roi]) -> tuple:
    return tuple(
        (
            round(x / SIGNATURE_POSITION_QUANTUM_PX),
            round(y / SIGNATURE_POSITION_QUANTUM_PX),
            round(w / SIGNATURE_SIZE_QUANTUM_PX),
            round(h / SIGNATURE_SIZE_QUANTUM_PX),
        )
        for x, y, w, h in cards
    )

def carry_over_resolutions(old_resolutions, ocr_results) -> dict[int, str]:
    """新牌面上按内容沿用人工确认：确认跟着武将走，不跟槽位走。"""
    remaining = set(old_resolutions.values())
    carried = {}
    for slot, item in enumerate(ocr_results):
        if item.get("resolution") not in _CONFIRM_RESOLUTIONS:
            continue  # 已自动确认的槽位不接入人工确认
        candidates = {str(c) for c in (item.get("candidates") or [])}
        if not candidates:
            continue
        old_name = old_resolutions.get(slot)
        if old_name in candidates:
            carried[slot] = old_name
            continue
        hits = remaining & candidates
        if len(hits) == 1:          # 歧义槽位（命中多个已确认名）保守丢弃
            name = next(iter(hits))
            carried[slot] = name
            remaining.discard(name)  # 同名不扩散到多个槽位
    return carried
```

> 位置/尺寸分开量化，吸收卡位检测像素级抖动，仅布局变化才触发 OCR。候选阶段卡面有 idle 浮动动画（日志实测剪影纵向 ±3~4px、尺寸 ±5px），步长须覆盖 2 倍漂移幅度，故取 8/16；真实换牌表现为卡数变化或整排重排（位移 ≥ 一个卡位宽），远超步长不会漏。

### 4.3 会话世代校验与每拍幂等重挂

```python
def _do_work(self) -> None:
    session = self._session  # 本拍所属会话世代；start/stop 后旧拍即过期
    ...
    with self._state_lock:
        if session != self._session:
            return  # 停止/重启后的旧拍：不检测、不清理、不发布
    ...
    with self._state_lock:
        if session != self._session:
            return
        self._miss_ticks = 0
        # 每拍确认牌面存在即幂等挂起（含签名未变的拍）：外部如 ADB 重连
        # start_poll 会重新激活标准任务，若只在签名变化时挂起，unchanged
        # 短路会让垃圾轮询在巅峰页常驻
        self._suspend_standard_tasks()
        unchanged = signature == self._signature
    if unchanged:
        return  # 牌面未变化，沿用上一次结果
    ...
    with self._state_lock:
        if session != self._session:
            return  # 不写签名、不沿用确认、不发布
        self._signature = signature
        self._resolutions = carry_over_resolutions(self._resolutions, ocr_results)
```

> 四处世代校验（截图后、挂起前、失败清签名前、写签名/发布前）加锁内缺席计数，保证停止或重启后的在途旧拍不会反向挂起标准任务、写回旧签名或刷新面板。

### 4.4 象限判定

```python
def evaluate_peak_ban_advice(
    win_rate: float | None, pick_rank: int | None, win_rate_rank: int | None,
) -> PeakBanAdvice | None:
    if win_rate is None or pick_rank is None or win_rate_rank is None:
        return None
    if win_rate < STRONG_WIN_RATE_MIN:          # 50.0
        return None
    if pick_rank > HOT_PICK_RANK_MAX:            # 50
        return PeakBanAdvice(key="ban_first", label="Ban 位首选",
                             weight=1000, bpi=1000 + pick_rank - win_rate_rank, ...)
    return PeakBanAdvice(key="hot_pick", label="热门强将",
                         weight=500, bpi=500 + pick_rank - win_rate_rank, ...)
```

### 4.5 座次解析

```python
def parse_seats(note: str, hero1: str, hero2: str) -> tuple[str, list[int], list[int]]:
    # 规则 1：武将名+数字 / 数字+武将名（含 ALIAS）
    for name in candidates:  # {hero1, hero2} ∪ ALIAS
        for pattern in (name + r"\s*([0-9]{1,2})", r"([0-9]{1,2})\s*" + name):
            ...
    # 规则 2：剥离武将名后取开头纯数字 token
    stripped = note
    for name in candidates:
        stripped = stripped.replace(name, " ")
    tokens = [...]
    if len(tokens) == 1 and tokens[0] == "0": return STATUS_PARSED, [], []
    if len(tokens) == 2: return STATUS_PARSED, seats1, seats2
```

### 4.6 配队稳定排序落盘

```python
def _save_unlocked(self) -> None:
    """落盘前按 rating 降序、hero1_id/hero2_id 升序稳定排序。
    物理行序与武将名解绑：新增武将（id 较大）自然落到各 rating 段末尾，
    避免按名排序时新名字插入中段、其后条目整体平移造成的 diff 噪音。
    """
    ordered = sorted(
        self._items.values(),
        key=lambda c: (-c.rating, c.hero1_id, c.hero2_id),
    )
    data = [v.model_dump(mode="json") for v in ordered]
    atomic_write_json(self.file_path, data, indent=2)
```

### 4.7 异步导入 Worker

```python
class _ImportWorker(QThread):
    """后台执行组合导入：数据量大时避免主线程冻结。"""
    finished_ok = Signal(dict)
    failed = Signal(str)

    def run(self) -> None:
        _LIVE_WORKERS.add(self)
        try:
            report = run_import(self._source, self._heroes_path, self._output_path)
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.finished_ok.emit(report)
        finally:
            _LIVE_WORKERS.discard(self)
```

---

## 五、接口说明

| 类/函数 | 说明 |
|---------|------|
| `detect_selection_cards(image) -> list[Roi] \| None` | 返回行优先 2v2 牌面 bbox 列表或 None |
| `derive_name_rois(cards) -> list[Roi]` | 按卡内相对比例生成名条 ROI |
| `evaluate_peak_ban_advice(win_rate, pick_rank, win_rate_rank) -> PeakBanAdvice \| None` | 象限判定，返回建议或 None |
| `derive_win_rate_ranks(win_rates) -> dict[str, int]` | 按胜率降序推导 1-based 排名 |
| `PeakSelectWatcher.start() / stop()` | 识别循环启停；各递增会话世代，start 即时挂起标准任务并清冷却/作废在途轮询，stop 恢复原状态 |
| `PeakSelectWatcher.is_running() -> bool` | 识别循环是否在运行 |
| `PeakSelectWatcher.recognize_image_file(path)` | 手动图片导入（独立锁，不影响循环签名与挂起状态） |
| `PeakSelectWatcher.confirm_pending(slot, name)` | 人工确认待确认槽位，立即重发快照 |
| `PeakSelectWatcher.shutdown()` | 由主窗口关闭时调用，停止识别循环 |
| `PeakSelectWatcher.pool_updated` | PoolSnapshot 信号 |
| `PeakSelectWatcher.status_changed` | 状态文本信号 |
| `PeakSelectWatcher.board_exited` | 牌面自动退出信号（经面板透出），供主窗口衔接对局攻略轮询 |
| `parse_pool(ocr_results, card_count, ban_names, resolutions) -> PoolSnapshot` | OCR 槽位结果整理为候选池快照 |
| `carry_over_resolutions(old_resolutions, ocr_results) -> dict[int, str]` | 新牌面上按内容沿用人工确认 |
| `board_signature(cards) -> tuple` | 牌面布局签名（量化去重，位置 8px / 尺寸 16px） |
| `ComboManager.get_combo(a_id, b_id) -> Combo \| None` | 按配对查询 |
| `ComboManager.list_combos_for_hero(hero_id) -> list[Combo]` | 按武将查询 |
| `ComboManager.list_combos() -> list[Combo]` | 获取全部 |
| `ComboManager.save_manual_combo(combo, previous) -> None` | 手工配队保存（含 key 迁移） |
| `ComboManager.delete_combo(combo) -> None` | 删除配队并原子落盘 |
| `parse_seats(note, hero1, hero2) -> tuple[str, list[int], list[int]]` | note 座次解析 |
| `format_seats(seats) -> str` | 号位列表→展示文本 |
| `run_import(source_path, heroes_path, output_path) -> dict` | 实战配队导入合并（幂等，CLI 与 UI 共用） |
| `CombosImportDialog.combos_imported` | 导入成功信号（携带导入条数） |
| `LineupState.load_from_ocr(ocr_results, hero_by_name, recognized_at) -> bool` | OCR 导入阵容 |
| `LineupState.set_side(index, side) -> LineupMutationResult` | 设置敌我（限制每方 ≤2） |
| `LineupState.set_ally_leader(index) -> bool` | 设置我方主将 |
| `LineupState.replace_hero(index, hero) -> None` | 替换槽位（重置全部敌我状态） |
| `LineupState.validate() -> LineupValidationResult` | 阵容可用性判定 |
| `LineupState.confirm() -> bool` | 确认阵容，允许生成攻略 |
| `LineupState.clear() -> None` | 清空全部状态 |
| `MatchAnalysisView.render_unconfirmed(heroes, win_rates, lineup_ready)` | 确认前提示页渲染 |
| `MatchAnalysisView.render_analysis(analysis)` | 四页分析结果渲染 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src/ocr/official_board_parser` | 卡位检测复用 HSV 掩码思路 |
| 依赖 | `src/capture/adb_screen` | 巅峰赛截图 |
| 依赖 | `src/business/emulator/capture_service` | capture_for_poll 截图、submit_ocr_task 提交 OCR、do_capture 保存截图、OCR 预热状态 |
| 依赖 | `src/business/recognition/ocr_service` | 任务状态查询与挂起/恢复（get_task_state / activate_task / deactivate_task / clear_task_cooldown / invalidate_inflight_poll） |
| 依赖 | `src/business/recognition/ocr_worker` | OCR 队列提交（统一队列，模板名 hero_selection） |
| 依赖 | `src/capture/image_validation` | 图片导入加载（load_local_image） |
| 依赖 | `src/data/combo_manager` | 实战配队查询与匹配 |
| 依赖 | `src/data/combo_seats` | 座次解析与格式化 |
| 依赖 | `src/data/peak_win_rate_repository` | 巅峰赛专属胜率/出场排行 |
| 依赖 | `src/config/env` | SCREENSHOTS_DIR 截图与导入起始目录 |
| 依赖 | `src/business/analysis/match_analysis_service` | MatchAnalysis 对象供分析渲染 |
| 依赖 | `src/ui/shared/guide_detail_dialog` | 完整攻略弹窗 |
| 依赖 | `src/ui/shared/combo_detail` | 实战配队详情弹窗（show_combo_detail） |
| 依赖 | `src/ui/shared/capture_lock` | 截图保存互斥（CaptureRequestLock / CaptureSource） |
| 依赖 | `src/ui/shared/portrait` | 武将头像加载（load_portrait） |
| 依赖 | `src/ui/shared/faction_colors` | 阵营徽章配色 |
| 依赖 | `src/ui/shared/style` | 统一样式（set_ui_role / set_tone / set_style_property） |
| 依赖 | `src/ui/shared/widgets` | EmptyState / FlowLayout / PageActionBar / StatusBadge / NoticeBanner |
| 依赖 | `src/business/maintenance/corpus_services` | ComboService 供 ComboManagementDialog 使用 |
| 依赖 | `src/business/maintenance/combo_import_service` | 异步导入 Worker 调用 run_import |
| 依赖 | `src/ui/library/combo_management_dialog` | 实战配队全量管理对话框 |
| 协作方 | `src/ui/match/match_guide_panel` | match_guide 轮询结果宿主；与巅峰赛识别会话互斥（牌面出现期间挂起、自动退出时恢复） |
| 被调用方 | `src/ui/app/main_window` | 侧导航第 4 页 + 实战配队导入菜单项；board_exited → `_on_peak_exited_to_match` 衔接激活 match_guide，另设 MATCH_GUIDE_IDLE_TIMEOUT_SECONDS=90 空闲守护停止非对局页空转 |
| 被调用方 | `src/ui/match/peak_select_panel` | 巅峰赛选将页面入口 |
| 被调用方 | `src/scripts/import_combos.py` | CLI 入口共用 run_import |
| 被调用方 | `src/ui/recommendation/recommendation_panel` | 推荐页共享 ComboManager / combo_seats |
| 被调用方 | `src/ui/library/hero_detail_views` | 武将详情页展示配队 |
| 被调用方 | `src/ui/generation/synergy_combos_dialog` | 攻略生成页共享配队数据 |
