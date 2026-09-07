# 调用链路：巅峰赛与实战配队

> 对应源码：`src/ui/match/peak_*` + `src/ui/match/match_lineup_state.py` + `src/ui/match/match_analysis_view.py` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/data/peak_win_rate_repository.py` + `src/ocr/card_grid_detector.py` + `src/business/maintenance/combo_import_service.py` + `src/scripts/import_combos.py`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

## 一、巅峰赛选将实时识别链路

### 1.1 识别循环主链

```
PeakSelectPanel._on_toggle_watcher()
  -> [已运行] PeakSelectWatcher.stop()
     -> _timer.stop()
     -> [_state_lock] _session += 1 — 先作废在途旧拍再恢复任务
     -> _restore_standard_tasks() — 恢复 hero_selection/match_guide 原状态
  -> [未连接] request_mumu_config 信号 + 状态提示
  -> PeakSelectWatcher.start()
     -> [_state_lock] _session += 1 / 重置 _signature/_ban_names/_resolutions/_last_board
     -> _miss_ticks = 0
     -> _suspend_standard_tasks() — 挂起即时生效，记录原状态快照
     -> ocr_service.clear_task_cooldown("hero_selection") / ("match_guide")
     -> ocr_service.invalidate_inflight_poll() — 作废点击开始前在途轮询
     -> _timer.start() 1.5s
  -> _timer.timeout -> _on_tick()
     -> _thread_lock.acquire(blocking=False) — 上一拍未完成则跳过
     -> [后台线程] _do_work()
        -> session = self._session — 本拍所属会话世代
        -> capture = self._capture_service.capture
           -> None -> status_changed("未连接模拟器")
        -> ok, result, failure_kind = capture_service.capture_for_poll(capture)
           -> ok == False -> status_changed(截图失败)
        -> [_state_lock] session != _session -> return — 停止/重启后的旧拍放弃
        -> cv2.cvtColor(np.array(result.convert("RGB")), COLOR_RGB2BGR)
        -> detect_selection_cards(frame)
           -> HSV 掩码 + 闭运算 + 连通域过滤 + 行聚类
           -> None -> _handle_board_absent(session)
        -> signature = board_signature(cards)
           -> [_state_lock]
              -> session != _session -> return
              -> _miss_ticks = 0 — 检出牌面即归零
              -> _suspend_standard_tasks() — 每拍幂等重挂，在 unchanged 短路之前
              -> unchanged = signature == _signature
           -> unchanged -> return — 牌面未变化，沿用上一次结果
        -> ocr_results = _recognize_board(result, cards)
           -> hero_names = list(self._hero_names_provider())
           -> rois = [list(roi) for roi in derive_name_rois(cards)]
           -> capture_service.submit_ocr_task(image, hero_names, "hero_selection", rois, match_template=False)
           -> if not task.completed.wait(15) -> status_changed("识别超时"), return None
           -> outcome != "matched" -> status_changed, return None
           -> (task.result or {}).get("ocr_results") or []
        -> ocr_results is None -> [_state_lock] 世代校验后 _signature = None（下一拍强制重试）
        -> [_state_lock]
           -> session != _session -> return — 不写签名、不沿用确认、不发布
           -> _signature = signature
           -> _resolutions = carry_over_resolutions(_resolutions, ocr_results)
              -> 按内容沿用人工确认：原地保留 / 重排迁移 / 内容失效 / 歧义丢弃
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

carry_over_resolutions(old_resolutions, ocr_results) — 新牌面到达时按内容沿用
  -> remaining = set(old_resolutions.values())
  -> for slot, item in enumerate(ocr_results):
     -> item.resolution 不在 _CONFIRM_RESOLUTIONS -> 跳过（已自动确认的槽位不接入）
     -> candidates 为空 -> 跳过
     -> old_name = old_resolutions.get(slot)
        -> old_name in candidates -> carried[slot] = old_name（槽位未重排，原地保留）
     -> hits = remaining & candidates
        -> len(hits) == 1 -> carried[slot] = 该名 + remaining.discard（重排迁移，同名不扩散）
        -> len(hits) != 1 -> 丢弃（内容消失或候选歧义）
  -> 由 _do_work() 在新牌面写入签名同一把锁内调用，基准取当前 _resolutions
     （覆盖 OCR 期间用户对新牌面 pending 的点击）
```

### 1.3 图片导入链路（独立锁，不影响循环）

```
PeakSelectPanel._on_import_from_file()
  -> [OCR 预热中] 状态提示 return（预热加载持有 GIL，禁用导入避免界面冻结）
  -> QFileDialog.getOpenFileName()
  -> PeakSelectWatcher.recognize_image_file(file_path)
     -> threading.Thread(_do_file_recognition)
        -> _import_lock.acquire(blocking=False) — 已有导入则提示请稍候
        -> load_local_image(file_path)
           -> 异常 -> status_changed("图片加载失败：...")
        -> cv2.cvtColor(np.array(image.convert("RGB")), COLOR_RGB2BGR)
        -> detect_selection_cards(frame)
           -> None -> status_changed("未在图片中检测到巅峰赛牌面（需 8~14 张卡）")
        -> status_changed("检测到 N 张牌面，识别中…")
        -> _recognize_board(image, cards)
           -> None -> status_changed("图片识别未完成，请重试")
        -> _publish_pool(ocr_results, len(cards)) — 不动 _signature、不校验会话世代
        -> _import_lock.release()
```

### 1.4 标准任务挂起与恢复（会话制）

```
_suspend_standard_tasks() — 幂等，可每拍重复调用
  -> _saved_task_states is None -> 记录 {name: get_task_state(name).active}
     （仅首次记录原状态快照，重复调用不覆盖）
  -> for name in ("hero_selection", "match_guide"):
       -> get_task_state(name).active -> ocr_service.deactivate_task(name)

_restore_match_guide() — 牌面自动退出时调用，仅恢复 match_guide
  -> _saved_task_states is None -> 跳过
  -> saved["match_guide"] -> activate_task("match_guide")
  -> 否则 -> deactivate_task("match_guide") 恢复非活跃状态
  -> hero_selection 不动，留待 stop() 恢复

_restore_standard_tasks() — 仅 stop() 调用，恢复全部标准任务原状态
  -> _saved_task_states is None -> 跳过
  -> for name, active in saved_states:
       -> active -> ocr_service.activate_task(name)
       -> 否则 -> ocr_service.deactivate_task(name) 恢复非活跃状态
  -> _saved_task_states = None

_handle_board_absent(session)
  -> [_state_lock]
     -> session != _session -> return — 旧拍不累计缺席、不清理、不发信号
     -> _miss_ticks += 1（锁内自增，避免非原子更新）
     -> exiting = _miss_ticks == BOARD_EXIT_TICKS(=2)
     -> _signature = None
     -> exiting -> _ban_names = () / _resolutions = {}
  -> exiting -> _restore_match_guide()
  -> exiting -> board_exited.emit() — 对局攻略的激活/跳转交由主窗口决定
  -> exiting -> status_changed("未检测到巅峰赛选将页牌面")
```

### 1.5 三板块流程串联（主窗口侧）

```
PeakSelectWatcher.board_exited -> PeakSelectPanel.board_exited -> MainWindow
  -> MainWindow._on_peak_exited_to_match()
     -> _match_guide_page_active = False
     -> ocr_service.is_polling -> activate_task("match_guide")
        -> _match_guide_activated_at = time.monotonic() — 记录激活时刻
  -> [match_guide 轮询结果]
     -> TEMPLATE_MISSING / MATCHED -> deactivate_task("match_guide") + 复位激活时刻
        -> MATCHED 且未切过页 -> 可选自动切到对局攻略页 -> MatchGuidePanel.update_block()
     -> HEALTHY_NO_MATCH -> _deactivate_match_guide_if_idle()
        -> 距 _match_guide_activated_at > MATCH_GUIDE_IDLE_TIMEOUT_SECONDS(=90)
           -> deactivate_task("match_guide") + 复位激活时刻（停止非对局页空转）
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
     -> rating = best_ratings.get(hero.id) if hero else None
     -> card.set_combo_badge(f"实战 ★{rating}" if rating else None) — 实战配队角标
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
  -> argparse: --source(必填) --heroes(默认 data/heroes.json) --output(默认 data/combos.json)
  -> run_import(source_path, heroes_path, output_path)
     -> json.loads(source_path) — 取 combos 键或整体列表
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
        -> key in manual_by_key -> report.manual_collisions, continue（seen_keys 仍加入）
        -> status, seats1, seats2 = parse_seats(note, name1, name2)
        -> report.seat_stats[status] += 1
        -> status not in (PARSED, NONE) -> report.seat_review
        -> Combo(hero1_name, hero2_name, hero1_id, hero2_id, rating, position, note, hero1_seats, hero2_seats)
           -> 构造失败 -> report.invalid, continue
        -> STATUS_PARSED and _check_position_mismatch(combo, seats1, seats2)
           -> report.position_mismatch
        -> merged[key] = combo; report.imported += 1
     -> manual_by_key 中未进入 merged 的项 -> 保留进 merged, report.manual_kept
        （判据是"未进入 merged"而非"不在源导出中"，故同 key 冲突的手工记录
          会同时出现在 manual_collisions 与 manual_kept）
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
        -> has_player = player_item 存在 且 四名已确认名称唯一
        -> not has_player -> ""（定位不到己方主将则全员不分配敌我）
        -> source_index in ENEMY_SLOT_INDICES -> "enemy"
        -> source_index == PLAYER_SLOT_INDEX -> "ally"
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
| `board_signature(cards)` | `peak_select_watcher.py` | PeakSelectWatcher._do_work() | 坐标/尺寸量化（位置 8px、尺寸 16px） |
| `PeakSelectWatcher.start/stop` | `peak_select_watcher.py` | PeakSelectPanel._on_toggle_watcher() / shutdown() | _state_lock 内会话世代递增、状态重置、挂起/恢复标准任务、清双任务冷却、作废在途轮询 |
| `PeakSelectWatcher._do_work` | `peak_select_watcher.py` | _on_tick | 会话世代校验（四处）、capture、detect、每拍幂等重挂、signature 比较、recognize、carry_over、publish |
| `PeakSelectWatcher._recognize_board` | `peak_select_watcher.py` | _do_work / _do_file_recognition | submit_ocr_task、超时/未完成处理 |
| `PeakSelectWatcher._publish_pool` | `peak_select_watcher.py` | _do_work / _do_file_recognition / confirm_pending | _state_lock 全程组装、parse_pool、pool_updated 信号（锁外发出） |
| `PeakSelectWatcher._suspend_standard_tasks` | `peak_select_watcher.py` | start() / _do_work()（每拍，含签名未变的拍） | 首次记录原状态快照，之后仅挂起 active 任务（幂等） |
| `PeakSelectWatcher._restore_match_guide` | `peak_select_watcher.py` | _handle_board_absent() | 牌面自动退出时仅恢复 match_guide 原状态 |
| `PeakSelectWatcher._restore_standard_tasks` | `peak_select_watcher.py` | stop() | activate/deactivate_task 恢复全部标准任务原状态 |
| `PeakSelectWatcher._handle_board_absent(session)` | `peak_select_watcher.py` | _do_work | 锁内世代校验、miss_ticks 计数、_state_lock 清理、_restore_match_guide、board_exited 信号 |
| `PeakSelectWatcher.confirm_pending` | `peak_select_watcher.py` | PeakSelectPanel._confirm_candidate | _state_lock 写入确认、_publish_pool 重发快照 |
| `PeakSelectWatcher.recognize_image_file` | `peak_select_watcher.py` | PeakSelectPanel._on_import_from_file | _do_file_recognition 后台线程（独立锁，不校验会话世代） |
| `PeakSelectWatcher.board_exited` | `peak_select_watcher.py` | _handle_board_absent（经 PeakSelectPanel 透出） | MainWindow._on_peak_exited_to_match 衔接激活 match_guide |
| `parse_pool(ocr_results, card_count, ...)` | `peak_select_watcher.py` | _publish_pool | PoolSnapshot 构造（已确认/待确认/已禁/阶段/撞车数） |
| `carry_over_resolutions(old_resolutions, ocr_results)` | `peak_select_watcher.py` | _do_work（新牌面） | 人工确认按内容沿用：原地保留/重排迁移/内容失效/歧义丢弃 |
| `evaluate_peak_ban_advice` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 缺失/弱势/冷门强势/热门强势四步判定 |
| `derive_win_rate_ranks` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 胜率排名推导 |
| `PeakHeroCard.set_hero/set_win_rate/set_ban_advice/set_combo_badge` | `peak_hero_card.py` | PeakSelectPanel._render_cards | 卡片头像/胜率/禁选徽章/实战角标渲染 |
| `PeakSelectPanel._on_pool_updated` | `peak_select_panel.py` | watcher.pool_updated | 阶段/候选汇总、_render_cards、pending、banned |
| `PeakSelectPanel._render_cards` | `peak_select_panel.py` | _on_pool_updated / 排序切换 / combos_changed | 卡片两排布局 + 禁选建议 + 实战角标 |
| `PeakSelectPanel._refresh_combo_strip` | `peak_select_panel.py` | _render_cards | ComboManager.list_combos 匹配 + best_ratings |
| `PeakSelectPanel._render_combo_chips` | `peak_select_panel.py` | _refresh_combo_strip | 实战配队 chip 渲染 |
| `PeakSelectPanel._open_combo_management` | `peak_select_panel.py` | 实战配队条 [管理] 按钮 | ComboManagementDialog |
| `PeakSelectPanel._build_pending_row/_confirm_candidate` | `peak_select_panel.py` | _render_pending | 候选按钮、_watcher.confirm_pending |
| `PeakSelectPanel.board_exited` → `MainWindow._on_peak_exited_to_match` | `peak_select_panel.py` / `main_window.py` | watcher.board_exited | 激活 match_guide 并记录激活时刻；HEALTHY_NO_MATCH 经 `_deactivate_match_guide_if_idle`（90s 上限）失活 |
| `run_import` | `combo_import_service.py` | CLI main / _ImportWorker | 名称映射、座次解析、position 交叉校验、合并、CRUD、save，返回 11 区块报告 |
| `import_combos.main` | `import_combos.py` | 命令行入口 | argparse（--source/--heroes/--output）+ run_import + 报表打印 |
| `_ImportWorker` (QThread) | `combos_import_dialog.py` | CombosImportDialog._on_accept | run_import 异步执行 |
| `CombosImportDialog._on_accept/_on_import_finished` | `combos_import_dialog.py` | 用户点击 [执行导入] | _ImportWorker 启动、报告展示 |
| `parse_seats` | `combo_seats.py` | run_import | note 座次解析 |
| `format_seats` | `combo_seats.py` | PeakSelectPanel._render_combo_chips / _combo_tooltip | 座次列表 → 展示文本 |
| `ComboManager._combo_key` | `combo_manager.py` | 内部调用 | sorted((a_id, b_id)) |
| `ComboManager._save_unlocked` | `combo_manager.py` | save_manual_combo / delete_combo / save() | sorted by (-rating, hero1_id, hero2_id) + atomic_write_json |
| `ComboManager.save_manual_combo` | `combo_manager.py` | ComboManagementDialog | key 迁移 + manual=True + _save_unlocked |
| `ComboManager.get_combo/list_combos_for_hero/list_combos` | `combo_manager.py` | run_import / PeakSelectPanel / ComboManagementDialog | 查询 |
| `LineupState.load_from_ocr/set_side/validate/confirm` | `match_lineup_state.py` | MatchGuidePanel（load_from_ocr / _set_side / _replace_hero / _confirm_lineup / clear_blocks） | OCR 导入、敌我确认、完整性校验 |
| `MatchAnalysisView.render_unconfirmed/render_analysis` | `match_analysis_view.py` | MatchGuidePanel._refresh_analysis / _clear_lineup_display | 四页签渲染 |
| `load_peak_win_rates/load_peak_pick_ranks` | `peak_win_rate_repository.py` | _win_rates_provider/_pick_ranks_provider | CSV 读取 + 缓存 |
| `clear_peak_win_rate_cache` | `peak_win_rate_repository.py` | 数据更新后 | 清空胜率与出场排行缓存 |