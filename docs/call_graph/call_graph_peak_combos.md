# 调用链路：巅峰赛与实战配队

> 对应源码：`src/ui/match/peak_*` + `src/ui/match/match_lineup_state.py` + `src/ui/match/match_analysis_view.py` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/data/peak_win_rate_repository.py` + `src/ocr/card_grid_detector.py`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

## 一、巅峰赛选将实时识别链路

### 1.1 识别循环主链

```
PeakSelectPanel._on_toggle_watcher()
  -> PeakSelectWatcher.start()
     -> [_state_lock] 重置 _signature/_ban_names/_resolutions/_last_board
     -> _timer.start() 1.5s
  -> _timer.timeout -> _on_tick()
     -> _thread_lock.acquire(blocking=False) — 上一拍未完成则跳过
     -> [后台线程] _do_work()
        -> capture = self._capture_service.capture
           -> None -> status_changed("未连接模拟器")
        -> ok, result, failure_kind = capture_service.capture_for_poll(capture)
           -> ok == False -> status_changed(截图失败)
        -> cv2.cvtColor(np.array(result.convert("RGB")), COLOR_RGB2BGR)
        -> detect_selection_cards(frame)
           -> HSV 掩码 + 闭运算 + 连通域过滤 + 行聚类
           -> None -> _handle_board_absent() -> miss_ticks++
        -> _miss_ticks = 0 — 检出牌面即清零
        -> signature = board_signature(cards)
           -> [_state_lock] 与 _signature 比较
              -> 相同 -> 沿用结果，return
              -> 不同 -> _resolutions = {}（新牌面，人工确认不跨牌沿用）
        -> _suspend_standard_tasks()
           -> {name: ocr_service.get_task_state(name).active} — 记录原状态
           -> 仅对 active=True 的任务调用 deactivate_task
        -> _recognize_board(result, cards)
           -> hero_names = list(self._hero_names_provider())
           -> rois = [list(roi) for roi in derive_name_rois(cards)]
           -> capture_service.submit_ocr_task(image, hero_names, "hero_selection", rois, match_template=False)
           -> if not task.completed.wait(15) -> status_changed("识别超时"), return None
           -> outcome != "matched" -> status_changed, return None
           -> (task.result or {}).get("ocr_results") or []
        -> ocr_results is None -> [_state_lock] _signature = None（下一拍强制重试）
        -> [_state_lock] _signature = signature
        -> _publish_pool(ocr_results, len(cards))
           -> [_state_lock]
              -> _last_board = (ocr_results, card_count)
              -> snapshot = parse_pool(ocr_results, card_count, _ban_names, _resolutions)
              -> stage == "ban" -> _ban_names = snapshot.names
           -> pool_updated.emit(snapshot) — 锁外发出
```

### 1.2 人工确认链路

```
PeakSelectPanel._build_pending_row(item)
  -> 渲染候选按钮行
  -> 点击 -> _confirm_candidate(slot, name)
     -> PeakSelectWatcher.confirm_pending(slot, name)
        -> [_state_lock]
           -> _resolutions[slot] = name
           -> last_board = _last_board
        -> if last_board: _publish_pool(*last_board)
           -> parse_pool() 校验 name 在候选内才生效
           -> pool_updated.emit
```

### 1.3 图片导入链路（独立锁，不影响循环）

```
PeakSelectPanel._on_import_from_file()
  -> [OCR 预热中] return
  -> QFileDialog.getOpenFileName()
  -> PeakSelectWatcher.recognize_image_file(file_path)
     -> threading.Thread(_do_file_recognition)
        -> _import_lock.acquire(blocking=False)
        -> load_local_image(file_path)
        -> cv2.cvtColor(np.array(image.convert("RGB")), COLOR_RGB2BGR)
        -> detect_selection_cards(frame)
           -> None -> status_changed("未在图片中检测到牌面")
        -> _recognize_board(image, cards)
           -> None -> status_changed("图片识别未完成")
        -> _publish_pool(ocr_results, len(cards))
        -> _import_lock.release()
```

### 1.4 标准任务挂起与恢复

```
_suspend_standard_tasks()
  -> _saved_task_states 非空 -> 跳过（已挂起）
  -> {name: ocr_service.get_task_state(name).active} — 记录原状态
  -> for name, active in saved_states:
       -> active -> ocr_service.deactivate_task(name)

_restore_standard_tasks()
  -> _saved_task_states 为 None -> 跳过
  -> for name, active in saved_states:
       -> active -> ocr_service.activate_task(name)
       -> 否则 -> ocr_service.deactivate_task(name) 恢复非活跃状态
  -> _saved_task_states = None

_handle_board_absent()
  -> _miss_ticks += 1
  -> exiting = _miss_ticks == BOARD_EXIT_TICKS(=2)
  -> [_state_lock]
     -> _signature = None
     -> exiting -> _ban_names = () / _resolutions = {}
  -> exiting -> _restore_standard_tasks()
  -> exiting -> status_changed("未检测到巅峰赛选将页牌面")
```

## 二、禁选建议判定链路

```
PeakSelectPanel._render_cards()
  -> snapshot = _last_snapshot
  -> win_rates = _win_rates_provider()
  -> pick_ranks = _pick_ranks_provider()
  -> derive_win_rate_ranks(win_rates)
     -> sorted(items, key=(-rate, name)) -> {name: 1-based rank}
  -> entries: [(name, hero, win_rates.get(name)), ...]
  -> if _sort_by_win_rate:
       -> entries.sort(key=(1,0.0) if rate is None else (0, -rate))
  -> best_ratings = _refresh_combo_strip(entries) — 见第三节
  -> 遍历 entries:
     -> evaluate_peak_ban_advice(rate, pick_ranks.get(name), win_rate_ranks.get(name))
        -> None if win_rate/pick_rank/win_rate_rank 任一缺失
        -> None if win_rate < 50.0（弱势象限不出标签）
        -> pick_rank > 50 -> PeakBanAdvice(key="ban_first", label="Ban 位首选", ...)
        -> PeakBanAdvice(key="hot_pick", label="热门强将", ...)
     -> PeakHeroCard.set_ban_advice(advice)
        -> None -> 隐藏徽章
        -> ban_first -> 红底 #c0392b 徽章 + tooltip 含 BPI
        -> hot_pick -> 蓝底 #2b6cb0 徽章 + tooltip 含 BPI
     -> rating = best_ratings.get(hero.id)
     -> card.set_combo_badge(f"实战 ★{rating}") — 实战配队角标
  -> 两排布局: half = (len+1)//2, (row, col) = divmod(index, half)
```

## 三、实战配队匹配与展示链路

```
_refresh_combo_strip(entries) -> dict[int, int]
  -> hero_ids = {hero.id for _, hero, _ in entries if hero}
  -> ComboManager.list_combos()
  -> 遍历: hero1_id in hero_ids and hero2_id in hero_ids -> 命中
  -> _matched_combos.sort(key=(-rating, hero1_name, hero2_name))
  -> best: dict[int, int]
     -> for combo in _matched_combos:
          -> for hero_id in (combo.hero1_id, combo.hero2_id):
               -> best[hero_id] = max(best.get(hero_id, 0), combo.rating)
  -> _render_combo_chips()
  -> return best — 供卡片角标使用

_render_combo_chips()
  -> 清空 _combo_chip_flow
  -> for combo in _matched_combos:
     -> chip = QPushButton(f"★{rating} {hero1}[{format_seats(seats1)}] + {hero2}[{format_seats(seats2)}]")
     -> chip.setToolTip(_combo_tooltip(combo)) — 座次 + note 展示
     -> chip.clicked -> show_combo_detail(self, combo)

_open_combo_management()
  -> ComboManagementDialog(hero_manager, ComboService(combo_manager), parent)
     -> combos_changed -> _render_cards()
  -> exec()
```

## 四、实战配队导入链路

### 4.1 CLI 入口

```
import_combos.py main()
  -> argparse: --source --heroes --output
  -> run_import(source_path, heroes_path, output_path)
     -> json.loads(source_path)
     -> _load_hero_name_map(heroes_path)
        -> {hero.name: hero.id}
     -> ComboManager(output_path).load()
     -> 遍历 manager.list_combos():
        -> manual -> manual_by_key[key] = combo
        -> 否则 -> imported_keys.add(key)
     -> 遍历 combos_raw:
        -> name1/name2 -> id1/id2
        -> id1 或 id2 缺失 -> report.unmatched, continue
        -> key in seen_keys -> report.duplicates, continue
        -> key in manual_by_key -> report.manual_collisions, continue
        -> status, seats1, seats2 = parse_seats(note, name1, name2)
        -> report.seat_stats[status] += 1
        -> status not in (PARSED, NONE) -> report.seat_review
        -> Combo(hero1_id, hero2_id, rating, position, note, hero1_seats, hero2_seats)
        -> STATUS_PARSED and _check_position_mismatch(combo, seats1, seats2)
           -> report.position_mismatch
        -> merged[key] = combo; report.imported += 1
     -> manual_by_key - seen_keys -> merged 保留, report.manual_kept
     -> imported_keys - seen_keys -> report.removed_stale
     -> manager.clear_all()
     -> for key, combo in merged: manager.update(combo, key)
     -> manager.save() -> _save_unlocked()
        -> sorted(key=(-rating, hero1_id, hero2_id)) — 稳定排序
        -> atomic_write_json
     -> return report dict
  -> _print_report(report)
```

### 4.2 UI 导入（异步）

```
CombosImportDialog._on_accept()
  -> source_edit 为空 -> QMessageBox.warning
  -> _worker 运行中 -> 跳过
  -> _ImportWorker(source, heroes_path, output_path) — QThread
     -> run():
        -> run_import(self._source, self._heroes_path, self._output_path)
        -> failed.emit(str(error)) / finished_ok.emit(report)
  -> _worker.finished_ok -> _on_import_finished(report)
     -> _format_report(report) -> report_browser
     -> combos_imported.emit(report.imported)
  -> _worker.failed -> _on_import_failed(error)
```

### 4.3 座次解析链路（combo_seats.py）

```
parse_seats(note, hero1, hero2) -> (status, hero1_seats, hero2_seats)
  -> candidates = {hero1, hero2} + ALIAS[hero1] + ALIAS[hero2]
  -> for name in candidates:
     -> 匹配 re.escape(name) + r"\s*([0-9]{1,2})" 或 r"([0-9]{1,2})\s*" + re.escape(name)
     -> seats = _seats_of(matched.group(1)):
        -> "0" -> []
        -> 数字 -> sorted(set(...)), 每位 1~4 合法
        -> 非法 -> None
     -> found[real_hero] = seats
  -> hero1 in found and hero2 in found -> STATUS_PARSED
  -> 回退：stripped = note - candidates tokens
     -> 取开头纯数字 token 列表
     -> len=1 "0" -> PARSED, [], []
     -> len=2 -> seats1, seats2 = _seats_of(tokens[0]), _seats_of(tokens[1])
        -> 均非 None -> PARSED
  -> found 部分 -> STATUS_PARTIAL
  -> note 无数字 -> STATUS_NONE
  -> note 有数字但无法归类 -> STATUS_UNPARSED
```

## 五、巅峰赛胜率数据加载链路

```
PeakSelectPanel._render_cards()
  -> win_rates = self._win_rates_provider() if self._win_rates_provider else {}
     -> load_peak_win_rates(path)
        -> [默认路径缓存命中] return cache
        -> 否则: csv.DictReader(path) -> {武将: float(胜率.replace("%",""))}
        -> 文件不存在 -> logger.debug, 返回空 dict
        -> 默认路径时写入 _peak_win_rate_cache
  -> pick_ranks = self._pick_ranks_provider() if self._pick_ranks_provider else {}
     -> load_peak_pick_ranks(path)
        -> csv.DictReader(path) -> {武将: int(排名)}
        -> 默认路径时写入 _peak_pick_rank_cache
  -> clear_peak_win_rate_cache()
     -> 清空 _peak_win_rate_cache 与 _peak_pick_rank_cache
```

## 六、阵容状态与对局攻略链路

### 6.1 阵容状态维护

```
LineupState.load_from_ocr(ocr_results, hero_by_name, recognized_at)
  -> recognized_items = [item for item in sorted(ocr_results, key=_ocr_sort_key) if _has_name_identity]
  -> 定位 player_item (sort_key == PLAYER_SLOT_INDEX)
     -> player_item 存在:
        -> teammate_items = [item for sort_key in ENEMY_SLOT_INDICES?] 取 TEAMMATE_SLOT_INDICES 项
        -> selected_items = [item for item != player_item][:3] + [player_item]
     -> player_item 不存在:
        -> selected_items = recognized_items[:4]
  -> has_unique_teammate = len(teammate_items) == 1
  -> has_unique_names = len(confirmed_names) == len(selected_items) and 无重名
  -> for item in selected_items:
     -> _side_from_position(source_index, has_player, has_unique_teammate)
        -> source_index in ENEMY_SLOT_INDICES -> "enemy"
        -> source_index == PLAYER_SLOT_INDEX and has_unique_names -> "ally"
        -> has_unique_teammate and source_index in TEAMMATE_SLOT_INDICES -> "ally"
        -> 否则 -> ""
  -> _ally_leader_slot = 首个 side=="ally" 且 sort_key==PLAYER_SLOT_INDEX 的索引
  -> _team_labels_match_positions = _check_team_labels(selected_items)
  -> _analysis_confirmed = False
  -> return bool(slots)

LineupState.set_side(index, side) -> LineupMutationResult
  -> side 不在 ("", "ally", "enemy") -> raise ValueError
  -> slot.hero is None -> (False, "missing_hero")
  -> side 非空且与当前不同且 sides.count(side) >= 2 -> (False, "side_full")
  -> _slots[index] = replace(slot, side=side)
  -> side == "ally" and _ally_leader_slot is None -> 设 leader
  -> index == _ally_leader_slot and side != "ally" -> 重新选 leader
  -> _analysis_confirmed = False

LineupState.validate() -> LineupValidationResult
  -> pending_names > 0 -> (False, "unresolved_name")
  -> len(heroes) != 4 -> (False, "missing_hero")
  -> len({hero.id}) != 4 -> (False, "duplicate_hero")
  -> unresolved_count > 0 -> (False, "side_unconfirmed")
  -> allies != 2 or enemies != 2 -> (False, "invalid_side_count")
  -> (True)

LineupState.confirm()
  -> can_confirm() -> validate().is_valid
  -> _analysis_confirmed = True
```

### 6.2 对局攻略渲染

```
MatchAnalysisView.render_unconfirmed(heroes, win_rates, lineup_ready)
  -> show_overview() — 切换总览页签
  -> NoticeBanner("阵容待确认" / "完成阵容核对")
  -> for hero in valid: QLabel(f"{hero.name} · {hero.position} · 历史单将胜率：{rate}")
  -> 其余三个页签: "请先完成阵容核对并生成攻略。"

MatchAnalysisView.render_analysis(analysis: MatchAnalysis)
  -> overview:
     -> missing_data 非空 -> NoticeBanner + 展开/收起 toggle
     -> "本局行动优先级" -> for item in analysis.priorities: _add_priority_card
     -> "敌方威胁" -> _add_threats
     -> "我方速览" -> _add_ally_tips
  -> allies_page: "我方打法" -> for summary in analysis.allies: _add_guide_card
  -> enemies_page: "对抗敌方" -> for summary in analysis.enemies: _add_guide_card
  -> details_page: "单将详情" -> for summary: _add_detail_row
```

## 七、函数清单总表

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `detect_selection_cards(image)` | `card_grid_detector.py` | PeakSelectWatcher._do_work() / _do_file_recognition() | HSV 掩码、连通域过滤、行聚类排序 |
| `derive_name_rois(cards)` | `card_grid_detector.py` | PeakSelectWatcher._recognize_board() | 按卡内比例派生名条 ROI |
| `board_signature(cards)` | `peak_select_watcher.py` | PeakSelectWatcher._do_work() | 坐标全量量化 |
| `PeakSelectWatcher.start/stop` | `peak_select_watcher.py` | PeakSelectPanel._on_toggle_watcher() | _state_lock 重置状态、_timer.start/stop、标准任务恢复 |
| `PeakSelectWatcher._do_work` | `peak_select_watcher.py` | _on_tick | capture、detect、signature 比较、recognize、publish |
| `PeakSelectWatcher._recognize_board` | `peak_select_watcher.py` | _do_work / _do_file_recognition | submit_ocr_task、超时/未完成处理 |
| `PeakSelectWatcher._publish_pool` | `peak_select_watcher.py` | _do_work / _do_file_recognition / confirm_pending | _state_lock 组装、parse_pool、pool_updated 信号 |
| `PeakSelectWatcher._suspend_standard_tasks` | `peak_select_watcher.py` | _do_work | get_task_state、deactivate_task（仅 active） |
| `PeakSelectWatcher._restore_standard_tasks` | `peak_select_watcher.py` | stop / _handle_board_absent | activate/deactivate_task 恢复原状态 |
| `PeakSelectWatcher._handle_board_absent` | `peak_select_watcher.py` | _do_work | miss_ticks 计数、_state_lock 清理、标准任务恢复 |
| `PeakSelectWatcher.confirm_pending` | `peak_select_watcher.py` | PeakSelectPanel._confirm_candidate | _state_lock 写入确认、_publish_pool 重发快照 |
| `PeakSelectWatcher.recognize_image_file` | `peak_select_watcher.py` | PeakSelectPanel._on_import_from_file | _do_file_recognition 后台线程 |
| `parse_pool(ocr_results, card_count, ...)` | `peak_select_watcher.py` | _publish_pool | PoolSnapshot 构造（已确认/待确认/已禁） |
| `evaluate_peak_ban_advice` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 缺失/弱势/冷门强势/热门强势四步判定 |
| `derive_win_rate_ranks` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 胜率排名推导 |
| `PeakHeroCard.set_hero/set_win_rate/set_ban_advice/set_combo_badge` | `peak_hero_card.py` | PeakSelectPanel._render_cards | 卡片头像/胜率/禁选徽章/实战角标渲染 |
| `PeakSelectPanel._on_pool_updated` | `peak_select_panel.py` | watcher.pool_updated | 阶段/候选汇总、_render_cards、pending、banned |
| `PeakSelectPanel._render_cards` | `peak_select_panel.py` | _on_pool_updated / 排序切换 / combos_changed | 卡片两排布局 + 禁选建议 + 实战角标 |
| `PeakSelectPanel._refresh_combo_strip` | `peak_select_panel.py` | _render_cards | ComboManager.list_combos 匹配 + best_ratings |
| `PeakSelectPanel._render_combo_chips` | `peak_select_panel.py` | _refresh_combo_strip | 实战配队 chip 渲染 |
| `PeakSelectPanel._open_combo_management` | `peak_select_panel.py` | 实战配队条 [管理] 按钮 | ComboManagementDialog |
| `PeakSelectPanel._build_pending_row/_confirm_candidate` | `peak_select_panel.py` | _render_pending | 候选按钮、_watcher.confirm_pending |
| `run_import` | `combo_import_service.py` | CLI main / _ImportWorker | 名称映射、座次解析、合并、CRUD、save |
| `_ImportWorker` (QThread) | `combos_import_dialog.py` | CombosImportDialog._on_accept | run_import 异步执行 |
| `CombosImportDialog._on_accept/_on_import_finished` | `combos_import_dialog.py` | 用户点击 [执行导入] | _ImportWorker 启动、报告展示 |
| `parse_seats` | `combo_seats.py` | run_import | note 座次解析 |
| `format_seats` | `combo_seats.py` | PeakSelectPanel._render_combo_chips / _combo_tooltip | 座次列表 → 展示文本 |
| `ComboManager._combo_key` | `combo_manager.py` | 内部调用 | sorted((a_id, b_id)) |
| `ComboManager._save_unlocked` | `combo_manager.py` | save_manual_combo / delete_combo / save() | sorted by (-rating, hero1_id, hero2_id) + atomic_write_json |
| `ComboManager.save_manual_combo` | `combo_manager.py` | ComboManagementDialog | key 迁移 + manual=True + _save_unlocked |
| `ComboManager.get_combo/list_combos_for_hero/list_combos` | `combo_manager.py` | run_import / PeakSelectPanel / ComboManagementDialog | 查询 |
| `LineupState.load_from_ocr/set_side/validate/confirm` | `match_lineup_state.py` | 对局攻略识别流程 | OCR 导入、敌我确认、完整性校验 |
| `MatchAnalysisView.render_unconfirmed/render_analysis` | `match_analysis_view.py` | 对局攻略主窗口 | 四页签渲染 |
| `load_peak_win_rates/load_peak_pick_ranks` | `peak_win_rate_repository.py` | _win_rates_provider/_pick_ranks_provider | CSV 读取 + 缓存 |
| `clear_peak_win_rate_cache` | `peak_win_rate_repository.py` | 数据更新后 | 清空胜率与出场排行缓存 |