# 模块：巅峰赛与实战配队

> 对应目录：`src/ui/match/peak_*` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/data/peak_win_rate_repository.py` + `src/ocr/card_grid_detector.py`
> 职责：巅峰赛（2v2 模式）选将实时识别循环、禁选建议象限判定、实战配队（combos）数据管理、座次解析与配队导入

---

## 一、模块职责

巅峰赛（2v2 模式）与标准选将页共用同一套截图 OCR 基础设施，但牌面布局截然不同：标准选将是固定 8 个 ROI，而 2v2 牌面先发 14 张 → 双方同时禁选 3 名（可撞车）后剩余 8~11 张 → 卡牌按行重排（7+7 / 5+5 / 4+5 等），不能沿用固定 ROI。因此本模块在 `src/ocr/card_grid_detector.py` 中引入**内容驱动**的卡位检测：从整页截图定位剩余候选武将卡牌 bbox，再派生名条 ROI 交给通用 OcrWorker。

同时，巅峰赛需要"谁该被 Ban"的实时决策依据，本模块提供双维度象限判定（出场热度 × 胜率强度）：

- **Ban 位首选** — 强势冷门（胜率 ≥ 50% 且出场排名 > 50）
- **热门强将** — 版本热门强将（胜率 ≥ 50% 且出场排名 ≤ 50）
- 弱势象限不打标签；任一维度缺失也不打标签

实战配队（combos）数据由 `ComboManager` 管理：外部工具导出 JSON → 导入合并（手工记录 manual=True 优先）→ 巅峰赛候选池中按 rating 匹配并显示。

---

## 二、文件结构

```
src/data/
  ├── combo_manager.py                # ComboManager — Combo 数据 CRUD + 手工记录管理
  ├── combo_seats.py                  # parse_seats() — 从 note 文本解析双方武将座次
  └── peak_win_rate_repository.py     # 巅峰赛专属胜率/出场排行 CSV 读取（独立于 2v2）

src/ocr/
  └── card_grid_detector.py           # detect_selection_cards() — 内容驱动卡位检测 + 派生名条 ROI

src/business/
  ├── analysis/peak_ban_advice.py     # evaluate_peak_ban_advice() — 双维度象限判定
  ├── recognition/peak_select_watcher.py  # PeakSelectWatcher — 巅峰赛识别循环
  └── maintenance/combo_import_service.py # run_import() — 实战配队导入合并

src/ui/match/
  ├── peak_select_panel.py            # PeakSelectPanel — 巅峰赛选将工作台
  └── peak_hero_card.py               # PeakHeroCard — 候选武将卡片
```

---

## 三、核心逻辑

### 3.1 卡位检测（card_grid_detector.py）

2v2 模式牌面在禁选前 14 张、候选期 8~11 张，且行内数量可变化。固定 ROI 不适用，改为**内容驱动**：

1. 全图 HSV 转掩码：背景为低饱和宣纸（S≈8 / V≈230），卡面立绘远超对比阈值 → `S>90 或 V<90` 掩码
2. 闭运算核 = 5~7px（1440p 基准），避免上下两行 bbox 粘连
3. 连通域过滤：面积 > 0.55% 全图像素、尺寸/宽高比落在 [0.086, 0.115] × [0.215, 0.245] × [0.60, 0.95] 内、位于卡片区 [0.12, 0.88] × [0.16, 0.67] 比例范围
4. 行聚类 + 行内 x 排序 → 行优先返回 bbox 列表
5. 卡数不在 [8, 14] 内时返回 None，语义对齐轮询的 `healthy_no_match`

**名条 ROI 派生**：以卡 bbox 左缘锚定，名条位置 [x+0.06w, y+0.15h, 0.30w, 0.38h]，避开阵营徽章（0~13%）、等级数字（55~64%）和费用角标（80~95%）的污染。

参数均为相对比例（基准 2560×1440 实测），分辨率变化时自适应。

### 3.2 巅峰赛识别循环（PeakSelectWatcher）

`PeakSelectWatcher` 是独立 QObject，与标准轮询并存：

```
Tick（每 1.5s）
  └─ _thread_lock 非阻塞获取失败 → 跳过本轮
  └─ _do_work() 后台线程
      ├─ CaptureService.capture_for_poll() 截图
      ├─ detect_selection_cards(frame) 卡位检测
      │   └─ None → _handle_board_absent() → miss_ticks++ → BOARD_EXIT_TICKS 后恢复标准任务
      ├─ board_signature(cards) 量化坐标/尺寸
      │   └─ == 上次 → 牌面未变化，沿用结果（避免翻页动画误触发 OCR）
      ├─ _suspend_standard_tasks() 挂起 hero_selection/match_guide 轮询
      ├─ _recognize_board(image, cards)
      │   └─ 提交 OcrTask 到 OcrWorker，15s 超时保护
      └─ _publish_pool() → parse_pool() → PoolSnapshot → pool_updated 信号
```

`PoolSnapshot` 数据类：
- `card_count`: 当前牌面卡牌数
- `names`: 已确认武将名
- `pending`: 待确认槽位
- `stage`: "ban"（≥12 张）或 "pick"（8~11 张）
- `overlap`: 候选阶段双方撞车数（池大小 - 8）
- `banned`: 相对禁选期已确认名单的差集（禁选完成后的差集名）

**人工确认**：`confirm_pending(slot, name)` 由 `parse_pool` 校验候选是否在白名单内，确认后立即重发快照。

**图片导入**：`recognize_image_file()` 在独立锁（`_import_lock`）下执行，不影响循环签名与标准任务挂起状态。

**标准任务协调**：首次进入牌面挂起 `hero_selection` / `match_guide` 两个标准轮询任务（记录原状态），连续多拍未见牌面（`BOARD_EXIT_TICKS=2`）后恢复原状态，避免翻页动画误触发。

### 3.3 禁选建议（peak_ban_advice.py）

纯函数，无状态：

```python
def evaluate_peak_ban_advice(win_rate, pick_rank, win_rate_rank):
    if any None or win_rate < 50.0: return None
    if pick_rank > 50:
        return PeakBanAdvice("ban_first", "Ban 位首选", weight=1000, ...)
    return PeakBanAdvice("hot_pick", "热门强将", weight=500, ...)
```

胜率排名由 `derive_win_rate_ranks()` 按胜率降序推导（同分按名称稳定排序）。`BPI = 权重 + 出场排名 − 胜率排名` 用于卡片排序。

### 3.4 实战配队数据（ComboManager + combo_seats.py）

`ComboManager` 继承 `DataManager[Combo]`，key = 排序后的 `(hero1_id, hero2_id)`：

- `get_combo()` / `list_combos_for_hero()` / `list_combos()` 查询
- `save_manual_combo(combo, previous=None)`: 编辑时若 key 变化则迁移；`manual=True` 标记；导入合并时同 key 冲突优先保留手工记录
- `delete_combo()`: 原子落盘

`combo_seats.parse_seats(note, hero1, hero2)` 从 note 自由文本解析座次：
1. 优先匹配 "武将名+数字" 或 "数字+武将名"（含 ALIAS 别名如"牢布"→"吕布"）
2. 回退：剥离武将名后取开头纯数字 token，按顺序对应 hero1/hero2
3. "0" 表示无座次要求（返回空列表）
4. 两位数字 = 可选区间（如 "34"=3 或 4 号）
5. 状态：`parsed` / `partial` / `none` / `unparsed`

规则 1170 条可 100% 分类（1144 解析出座次 + 26 无座次要求）。

### 3.5 实战配队导入（combo_import_service.py）

`run_import(source, heroes, output)` 幂等合并：

1. 武将名 → ID 映射，未匹配进 `unmatched` 报告
2. 座次解析 + position 字段交叉校验（`_check_position_mismatch`），不一致进报告
3. 手工记录 `manual_by_key` 优先保留，源导出版本跳过
4. 合并语义：upsert 源导出 → 非手工旧记录若源中已不存在则移除 → 幂等重复执行输出稳定

---

## 四、关键代码片段

### 4.1 卡位检测核心

```python
def detect_selection_cards(image: np.ndarray) -> list[Roi] | None:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask = np.logical_or(hsv[:,:,1] > 90, hsv[:,:,2] < 90).astype(np.uint8) * 255
    kernel = np.ones((max(3, round(height/1440*5)), ...) np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    # 尺寸/宽高比/面积/位置过滤 → 行聚类 → 行内 x 排序
    # 卡数不在 [8, 14] 时返回 None
```

> 参数均为相对比例，基准 2560×1440 实测；分辨率变化时自适应。

### 4.2 牌面签名去重

```python
SIGNATURE_POSITION_QUANTUM_PX = 4
SIGNATURE_SIZE_QUANTUM_PX = 8

def board_signature(cards) -> tuple:
    return tuple(
        (round(x/4), round(y/4), round(w/8), round(h/8))
        for x, y, w, h in cards
    )
```

> 位置/尺寸分开量化，吸收卡位检测像素级抖动，仅布局变化才触发 OCR。

---

## 五、接口说明

| 类/函数 | 说明 |
|---------|------|
| `detect_selection_cards(image)` | 返回行优先 2v2 牌面 bbox 列表或 None |
| `derive_name_rois(cards)` | 按卡内比例生成名条 ROI |
| `evaluate_peak_ban_advice(win_rate, pick_rank, win_rate_rank)` | 返回 `PeakBanAdvice` 或 None |
| `derive_win_rate_ranks(win_rates)` | 胜率排名推导 |
| `PeakSelectWatcher.start/stop` | 识别循环启停 |
| `PeakSelectWatcher.recognize_image_file(path)` | 手动图片导入 |
| `PeakSelectWatcher.confirm_pending(slot, name)` | 人工确认待确认槽位 |
| `PeakSelectWatcher.pool_updated` | PoolSnapshot 信号 |
| `ComboManager.save_manual_combo(combo, previous)` | 手工配队保存（含 key 迁移） |
| `parse_seats(note, hero1, hero2)` | note 座次解析 |
| `run_import(source, heroes, output)` | 实战配队导入合并（幂等） |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src/ocr/official_board_parser` | 卡位检测复用 HSV 掩码思路 |
| 依赖 | `src/capture/adb_screen` | 巅峰赛截图 |
| 依赖 | `src/business/recognition/ocr_worker` | OCR 队列提交 |
| 依赖 | `src/data/combo_manager` | 实战配队查询 |
| 依赖 | `src/data/peak_win_rate_repository` | 巅峰赛专属胜率/出场排行 |
| 依赖 | `src/ui/match/peak_hero_card` | 卡片展示 |
| 依赖 | `src/business/maintenance/corpus_services` | ComboService 供 ComboManagementDialog 使用 |
| 被调用方 | `src/ui/app/main_window` | 侧导航第 4 页 |
| 被调用方 | `src/ui/match/peak_select_panel` | 巅峰赛选将页面入口 |
