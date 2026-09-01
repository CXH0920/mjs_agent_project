# 调用链路：巅峰赛与实战配队

> 对应源码：`src/ui/match/peak_*` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/data/peak_win_rate_repository.py` + `src/ocr/card_grid_detector.py`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

## 一、巅峰赛选将实时识别链路

### 1.1 识别循环主链

```
PeakSelectPanel._on_toggle_watcher()
  -> PeakSelectWatcher.start()
     -> _timer.start() 1.5s
  -> _timer.timeout -> _on_tick()
     -> _thread_lock.acquire(blocking=False)
     -> [后台线程] _do_work()
        -> CaptureService.capture_for_poll(capture)
        -> cv2.cvtColor()  RGB -> BGR
        -> detect_selection_cards(frame)
           -> HSV 掩码 + 闭运算 + 连通域过滤 + 行聚类
           -> None -> _handle_board_absent() -> miss_ticks++
        -> board_signature(cards)
           -> == _signature -> 沿用结果，return
        -> _suspend_standard_tasks()
           -> OcrService.deactivate_task("hero_selection")
           -> OcrService.deactivate_task("match_guide")
        -> _recognize_board(image, cards)
           -> hero_names_provider()
           -> derive_name_rois(cards)
           -> CaptureService.submit_ocr_task(image, hero_names, "hero_selection", rois, match_template=False)
           -> task.completed.wait(15)
           -> [outcome == "matched"] -> ocr_results
        -> _publish_pool(ocr_results, len(cards))
           -> parse_pool(ocr_results, card_count, _ban_names, _resolutions)
              -> 遍历槽位：已确认 / 待确认 / 人工确认回填
              -> stage = "ban" if card_count >= 12 else "pick"
              -> banned = ban_names - 已确认
              -> overlap = card_count - 8 if "pick" else 0
           -> PoolSnapshot -> pool_updated 信号
           -> [ban 阶段] _ban_names = snapshot.names
```

### 1.2 人工确认链路

```
PeakSelectPanel._build_pending_row(item)
  -> 渲染候选按钮行
  -> 点击 -> _confirm_candidate(slot, name)
     -> PeakSelectWatcher.confirm_pending(slot, name)
        -> _resolutions[slot] = name
        -> _publish_pool(*_last_board)
           -> parse_pool() 校验 name 在候选内才生效
```

### 1.3 图片导入链路（独立锁，不影响循环）

```
PeakSelectPanel._on_import_from_file()
  -> [OCR 预热中] return
  -> QFileDialog.getOpenFileName()
  -> PeakSelectWatcher.recognize_image_file(path)
     -> threading.Thread(_do_file_recognition)
        -> _import_lock.acquire(blocking=False)
        -> load_local_image(path)
        -> detect_selection_cards()
        -> _recognize_board(image, cards)
        -> _publish_pool(ocr_results, len(cards))
        -> _import_lock.release()
```

### 1.4 标准任务恢复链路

```
_do_work() -> _handle_board_absent()
  -> _miss_ticks++
  -> _signature = None
  -> _miss_ticks == BOARD_EXIT_TICKS=2
     -> _restore_standard_tasks()
        -> OcrService.activate/deactivate_task(name)
        -> _saved_task_states = None
     -> _ban_names = ()
     -> _resolutions = {}
     -> status_changed("未检测到巅峰赛选将页牌面")
```

## 二、禁选建议判定链路

```
PeakSelectPanel._render_cards()
  -> entries = [(name, hero, win_rates.get(name)), ...]
  -> derive_win_rate_ranks(win_rates)
     -> sorted(items, key=(-rate, name)) -> {name: rank}
  -> 遍历 entries:
     -> evaluate_peak_ban_advice(rate, pick_ranks.get(name), win_rate_ranks.get(name))
        -> None if rate < 50.0 or pick_rank > 50 -> "hot_pick"
        -> pick_rank > 50 -> "ban_first"
        -> None if any dimension missing
     -> PeakHeroCard.set_ban_advice(advice)
        -> 红底 "Ban 位首选" / 蓝底 "热门强将" / None 隐藏
```

## 三、实战配队匹配与展示链路

```
PeakSelectPanel._on_pool_updated(snapshot)
  -> _render_cards()
     -> entries = [(name, hero, rate)]
     -> _refresh_combo_strip(entries)
        -> hero_ids = {hero.id for _, hero, _ in entries if hero}
        -> ComboManager.list_combos()
        -> 遍历: hero1_id ∈ hero_ids and hero2_id ∈ hero_ids -> 命中
        -> sorted(key=(-rating, hero1_name, hero2_name))
        -> _matched_combos 排序
        -> 计算 {hero_id: max(rating)} -> 卡片角标
     -> PeakHeroCard.set_combo_badge("实战 ★N")

PeakSelectPanel._render_combo_chips()
  -> for combo in _matched_combos:
     -> chip = QPushButton(f"★{rating} {n1}[{seats1}] + {n2}[{seats2}]")
     -> chip.clicked -> show_combo_detail(self, combo)
        -> _combo_tooltip(combo): 座次 + note 展示

PeakSelectPanel._open_combo_management()
  -> ComboManagementDialog(hero_manager, ComboService(combo_manager), parent)
     -> combos_changed -> _render_cards()
  -> exec()
```

## 四、实战配队导入链路

```
CombosImportDialog / CLI import_combos.py
  -> run_import(source, heroes, output)
     -> _load_hero_name_map(heroes_path)
     -> ComboManager(output_path).load()
     -> 遍历 combos_raw:
        -> name1/name2 -> id1/id2
        -> key = _combo_key(id1, id2)
        -> key ∈ manual_by_key -> manual_collisions, skip
        -> parse_seats(note, name1, name2)
        -> Combo(hero1_id, hero2_id, rating, position, note, hero1_seats, hero2_seats)
        -> status == PARSED -> _check_position_mismatch(combo, seats1, seats2)
        -> merged[key] = combo
     -> merged.update(manual_by_key - seen_keys)
     -> imported_keys - seen_keys -> removed_stale
     -> manager.clear_all() -> manager.update(combo, key) -> manager.save()
     -> return report dict
```

### 4.1 座次解析链路（combo_seats.py）

```
parse_seats(note, hero1, hero2)
  -> candidates = {hero1, hero2, *ALIAS.get(hero1), *ALIAS.get(hero2)}
  -> for name in candidates:
     -> 匹配 re.escape(name) + r"\s*([0-9]{1,2})" 或 反序
     -> _seats_of(digits): "0"->[], 数字->sorted(set(...))
     -> found[real_hero] = seats
  -> hero1 ∈ found and hero2 ∈ found -> STATUS_PARSED
  -> 回退：stripped = note - name tokens
     -> 开头纯数字 token 列表
     -> len=1 "0" -> parsed [],[]
     -> len=2 -> parsed seats1, seats2
  -> found 部分 -> STATUS_PARTIAL
  -> note 无数字 -> STATUS_NONE
  -> note 有数字但无法归类 -> STATUS_UNPARSED
```

## 五、巅峰赛胜率数据加载链路

```
PeakSelectPanel._render_cards()
  -> self._win_rates_provider()
     -> load_peak_win_rates()
        -> [缓存命中] return cache
        -> 否则: csv.DictReader(path) -> {武将名: float(胜率)}
        -> 默认路径时写入缓存
  -> self._pick_ranks_provider()
     -> load_peak_pick_ranks()
        -> csv.DictReader(path) -> {武将名: int(排名)}
        -> 默认路径时写入缓存
```

## 六、函数清单总表

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `detect_selection_cards(image)` | `card_grid_detector.py` | PeakSelectWatcher._do_work() | HSV 掩码、连通域过滤、行聚类 |
| `derive_name_rois(cards)` | `card_grid_detector.py` | _recognize_board() | 按卡内比例派生名条 ROI |
| `PeakSelectWatcher.start/stop` | `peak_select_watcher.py` | PeakSelectPanel._on_toggle_watcher() | _timer.start/stop、任务挂起/恢复 |
| `PeakSelectWatcher._do_work` | `peak_select_watcher.py` | _on_tick | capture、detect、recognize、publish |
| `PeakSelectWatcher._recognize_board` | `peak_select_watcher.py` | _do_work | CaptureService.submit_ocr_task、15s 等待 |
| `PeakSelectWatcher._publish_pool` | `peak_select_watcher.py` | _do_work | parse_pool + pool_updated 信号 |
| `parse_pool(ocr_results, card_count, ...)` | `peak_select_watcher.py` | _publish_pool | PoolSnapshot 构造 |
| `PeakSelectWatcher.confirm_pending` | `peak_select_watcher.py` | PeakSelectPanel | _publish_pool 重发快照 |
| `PeakSelectWatcher.recognize_image_file` | `peak_select_watcher.py` | PeakSelectPanel._on_import_from_file | _do_file_recognition 后台线程 |
| `evaluate_peak_ban_advice` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 双维度象限判定 |
| `derive_win_rate_ranks` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 胜率排名推导 |
| `PeakHeroCard.set_hero/set_win_rate/set_ban_advice/set_combo_badge` | `peak_hero_card.py` | PeakSelectPanel | 卡片渲染 |
| `PeakSelectPanel._on_pool_updated` | `peak_select_panel.py` | watcher.pool_updated | _render_cards + pending + banned + combo strip |
| `PeakSelectPanel._refresh_combo_strip` | `peak_select_panel.py` | _render_cards | ComboManager.list_combos 匹配 |
| `PeakSelectPanel._open_combo_management` | `peak_select_panel.py` | 实战配队条 [管理] 按钮 | ComboManagementDialog |
| `run_import` | `combo_import_service.py` | CLI / CombosImportDialog | ComboManager CRUD + parse_seats |
| `parse_seats` | `combo_seats.py` | run_import | note 座次解析 |
| `ComboManager.save_manual_combo` | `combo_manager.py` | ComboManagementDialog | _combo_key + atomic save |
| `load_peak_win_rates/load_peak_pick_ranks` | `peak_win_rate_repository.py` | win_rates_provider/pick_ranks_provider | CSV 读取 + 缓存 |
| `clear_peak_win_rate_cache` | `peak_win_rate_repository.py` | 数据更新后 | 清空缓存 |