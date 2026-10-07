# 调用链路：巅峰赛与实战配队

> 对应源码：`src/ui/match/peak_*` + `src/ui/match/match_lineup_state.py` + `src/ui/match/match_analysis_view.py` + `src/business/analysis/peak_ban_advice.py` + `src/business/recognition/peak_select_watcher.py` + `src/data/combo_*` + `src/data/peak_win_rate_repository.py` + `src/ocr/card_grid_detector.py` + `src/business/maintenance/combo_import_service.py` + `src/scripts/import_combos.py`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。

---

## 当前实现基线（2026-10-06）

**48b0f99 fix(peak) 巅峰赛选将人工确认残留修复：** 巅峰赛识别循环会话状态随停止一并清空（stop() 清 `_resolutions`/`_resolution_raws`/`_stale_rounds`/`_ban_names`/`_last_board`）；图片导入前经 `verified_resolutions_for_import()` 按内容校验旧确认（无后续拍可宽限，无法定位立即丢弃）；`confirm_pending()` 加牌面在位守卫（`last_board is None` 时拒绝并提示）；面板 `_on_pool_updated()` 加同名槽位显性告警（duplicate_count>0 时日志 + 状态栏 TONE_WARNING）；停止/牌面退出经 `_mark_stale()` 摘待确认行防误点，卡片保留供复盘。此前残留确认会被后续图片导入按槽位号盲目套用（2026-09-30 卓文君被顶成孙尚香事故）。

## 一、巅峰赛选将实时识别链路

### 1.1 识别循环主链

```
PeakSelectPanel._on_toggle_watcher()
  -> [已运行] PeakSelectWatcher.stop()
     -> _timer.stop()
     -> [_state_lock] _session += 1 — 先作废在途旧拍再恢复任务
     -> [_state_lock] 清空 _resolutions / _resolution_raws / _stale_rounds / _ban_names / _last_board
        — 会话状态随停止一并清空（48b0f99）：槽位号是跨牌面不稳定键，残留确认会被
        后续图片导入按槽位号盲目套用（2026-09-30 卓文君事故）
     -> ocr_service.set_task_hold("hero_selection", False) — 解除持有（顺序先于恢复：否则恢复激活会被自己持有拒绝）
      -> _restore_standard_tasks() — 恢复 hero_selection/match_guide 原状态
     -> PeakSelectPanel._mark_stale("阶段：已停止") — 摘待确认行防误点，卡片保留供复盘
  -> [未连接] request_mumu_config 信号 + 状态提示
  -> PeakSelectWatcher.start()
     -> [_state_lock] _session += 1 / 重置 _signature/_ban_names/_resolutions/_resolution_raws/_stale_rounds/_last_board
     -> _miss_ticks = 0
     -> _suspend_standard_tasks() — 挂起即时生效，记录原状态快照
     -> ocr_service.set_task_hold("hero_selection", True) — 持有加固：会话期间不得被任何入口激活
      -> ocr_service.clear_task_cooldown("hero_selection") / ("match_guide")
     -> ocr_service.invalidate_inflight_poll() — 作废点击开始前在途轮询
     -> _timer.start() 1.5s
     -> PeakSelectPanel._reset_view() — 清空上一局残影
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
        -> signature = board_signature(cards) # 生成原始 bbox 元组签名
           -> [_state_lock]
              -> session != _session -> return
              -> _miss_ticks = 0 — 检出牌面即归零
              -> _suspend_standard_tasks() — 每拍幂等重挂，在 unchanged 短路之前
              -> unchanged = board_signature_equal(signature, _signature) # 逐卡容差判等
           -> unchanged -> return — 牌面未变化，沿用上一次结果
        -> ocr_results = _recognize_board(result, cards)
           -> hero_names = list(self._hero_names_provider())
           -> rois = [list(roi) for roi in derive_name_rois(cards)]
           -> capture_service.submit_ocr_task(image, hero_names, "peak_board", rois, match_template=False) # 独立页名
           -> if not task.completed.wait(15) -> status_changed("识别超时"), return None
           -> outcome != "matched" -> status_changed, return None
           -> (task.result or {}).get("ocr_results") or []
        -> ocr_results is None -> [_state_lock] 世代校验后 _signature = None（下一拍强制重试）
        -> [_state_lock]
           -> session != _session -> return — 不写签名、不沿用确认、不发布
           -> _signature = signature
           -> _refresh_resolutions(ocr_results) # 逐拍验证人工确认存续（连续失验达上限才丢弃）
              -> 按内容沿用人工确认：原地保留 / 重排迁移 / 内容失效 / 歧义丢弃
        -> _publish_pool(ocr_results, len(cards))
           -> [_state_lock]
              -> _last_board = (ocr_results, card_count)
              -> display = {slot: name for slot, name in _resolutions.items() if slot not in _stale_rounds}
               -> snapshot = parse_pool(ocr_results, card_count, _ban_names, display) # 宽限期内确认不参与展示，回退为识别结果
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
           -> last_board = _last_board
           -> accepted = last_board is not None — 牌面在位守卫（48b0f99）
           -> [accepted] _resolutions[slot] = name / _stale_rounds.pop(slot)
           -> [accepted] 0 <= slot < len(last_board[0]) 时登记 _resolution_raws[slot] = raw_name
        -> [not accepted] status_changed("牌面已不在识别中，确认未生效") + return
           — 停止/退出后残留的待确认行点击兜底（配合 _mark_stale 摘除）
        -> record_confirmation(raw_name, name, slot_candidates) — 白名单确认集成
        -> _publish_pool(*last_board)
           -> parse_pool() 校验 name 在候选内才生效
           -> pool_updated.emit

**白名单确认集成（9ca1b91）：** `confirm_pending(slot, name)` 确认时同步调用 `pending_stats.record_confirmation(raw_name, name, candidates)` 收集人工答案，供白名单治理闭环使用。

**确认持久化增强：** `_resolutions` 中的确认结果按内容（而非槽位索引）沿用到后续牌面，`refresh_resolutions()` 确保用户人工确认不因牌面重排而丢失——原地保留（同名在候选内）、重排迁移（同名唯一命中）、歧义丢弃（同名多命中或无命中）。
  -> remaining = set(old_resolutions.values())
  -> for slot, item in enumerate(ocr_results):
     -> item.resolution 不在 _CONFIRM_RESOLUTIONS -> 跳过（已自动确认的槽位不接入）
     -> candidates 为空 -> 跳过
     -> old_name = old_resolutions.get(slot)
        -> old_name in candidates -> carried[slot] = old_name（槽位未重排，原地保留）
     -> hits = remaining & candidates
        -> len(hits) == 1 -> carried[slot] = 该名 + remaining.discard（重排迁移，同名不扩散）
        -> len(hits) != 1 -> 丢弃（内容消失或候选歧义）
  -> 由 `_do_work()` 在新牌面写入签名同一把锁内经 `_refresh_resolutions()` 调用，基准取当前 `_resolutions`
     （覆盖 OCR 期间用户对新牌面 pending 的点击）
  -> 返回 (carried, carried_raws, unverified)：调用方 `_refresh_resolutions()` 维护 `_stale_rounds`
     连续失验达 `_STALE_MISS_LIMIT` 上限才丢弃，宽限期内确认回退为该槽识别结果
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
        -> [_state_lock] verified_resolutions_for_import(_resolutions, _resolution_raws, ocr_results)
           — 48b0f99：导入前校验旧确认，返回 (沿用, 读数指纹, 丢弃数)；与实时循环
           同一套定位语义，但导入是一次性快照、没有后续拍可宽限，无法定位的确认
           立即丢弃；顺带把 _stale_rounds 清零
        -> [dropped > 0] status_changed("图片识别完成（N 条旧人工确认与该牌面不符，已丢弃）")
        -> [migrated > 0] status_changed("图片识别完成（N 条旧人工确认已按牌面重新定位）")
        -> [均未] status_changed("图片识别完成")
        -> _publish_pool(ocr_results, len(cards)) — 不动 _signature、不校验会话世代
        -> _import_lock.release()
```

### 1.4 标准任务挂起与恢复（会话制 + 持有加固）

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

_restore_standard_tasks() — 仅 stop() 调用（set_task_hold(False) 之后），恢复全部标准任务原状态
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
     -> exiting -> _ban_names = () / _resolutions = {} / _resolution_raws = {} / _stale_rounds = {} / _last_board = None
  -> exiting -> _restore_match_guide()
  -> exiting -> board_exited.emit() — 对局攻略的激活/跳转交由主窗口决定
  -> exiting -> status_changed("未检测到巅峰赛选将页牌面")
```

### 1.5 三板块流程串联（主窗口侧）

```
PeakSelectWatcher.board_exited -> PeakSelectPanel._on_watcher_board_exited()
  -> _mark_stale("阶段：牌面退出") — 48b0f99：摘待确认行（旧槽位号不再可点），卡片保留供复盘
     -> _clear_pending_rows() + _pending_area.hide()
     -> _stage_badge.setText(badge_text)
  -> PeakSelectPanel.board_exited.emit() -> MainWindow._on_peak_board_exited()
      -> set_win_rate_mode(WIN_RATE_MODE_PEAK) — 先置位攻略胜率榜为巅峰赛模式
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

### 1.6 同名槽位显性告警（48b0f99）

```
PeakSelectPanel._on_pool_updated(snapshot)
  -> _render_cards() — 先渲染候选卡片（卡片端 dict.fromkeys 去重展示）
  -> names = list(dict.fromkeys(snapshot.names))
  -> duplicate_count = len(snapshot.names) - len(set(snapshot.names))
  -> [duplicate_count > 0]  # 同名槽位意味着人工确认或识别结论打架
     -> logger.warning("巅峰赛候选出现 N 个同名槽位（人工确认或识别冲突），请复核")
     -> _append_log("⚠ 同名槽位 N 个，请复核待确认行")
     -> _action_bar.set_status("检测到 N 个同名槽位，请复核", TONE_WARNING)
```

> 此前同名槽位被静默去重，"14 张牌只显示 13 个名字"的异常被藏起来。48b0f99 起显性告警，用户可回到待确认行人工复核。

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
     -> 遍历 manager.list_all_combos():                    [含逻辑删除记录，屏蔽同 key 源记录]
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

> **座次划分修复：** `match_lineup_state.py` 在名字未决（resolution 含 unresolved/conflict）时，按座次（index 0-3）划分阵营——index 0-1 为敌方、index 2-3 为友方。此前未决名字全部归入敌方，修复后按实际座次分布。

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
  -> _allies_page: "我方打法" -> for summary in analysis.allies: _add_guide_card
  -> _enemies_page: "对抗敌方" -> for summary in analysis.enemies: _add_guide_card
  -> _details_page: "单将详情" -> for summary: _add_detail_row
```

### 6.3 胜率榜模式切换

`
MatchGuidePanel.set_win_rate_mode(mode)
  -> mode not in (2v2, peak) or mode == current -> return
  -> _win_rate_mode = mode
  -> _sync_win_rate_mode_button()
  -> if _lineup.valid_count: _win_rates = _current_win_rates() + _render_cards() + _refresh_analysis()

MatchGuidePanel._current_win_rates()
  -> if _win_rate_mode == WIN_RATE_MODE_PEAK: _peak_win_rates_provider()
  -> else: _win_rates_provider()

MainWindow._on_poll_hero_selection_matched() -> set_win_rate_mode(WIN_RATE_MODE_2V2)
MainWindow._on_peak_board_exited() -> set_win_rate_mode(WIN_RATE_MODE_PEAK)
`

> **按对局链路区分**（0007fc4）：选将推荐进对局用 2v2 标准榜，巅峰赛选将进对局用巅峰赛专属榜。

## 七、函数清单总表

| 函数 | 文件 | 调用方 | 被调用方 |
|------|------|--------|----------|
| `detect_selection_cards(image)` | `card_grid_detector.py` | PeakSelectWatcher._do_work() / _do_file_recognition() | HSV 掩码、连通域过滤、行聚类排序 |
| `derive_name_rois(cards)` | `card_grid_detector.py` | PeakSelectWatcher._recognize_board() | 按卡内比例派生名条 ROI |
| `board_signature(cards)` | `peak_select_watcher.py` | PeakSelectWatcher._do_work() | 原始 bbox 元组签名（判等必须用 board_signature_equal） |
| oard_signature_equal(left, right) | peak_select_watcher.py | PeakSelectWatcher._do_work() | 逐卡容差判等（位置 8px / 尺寸 16px），消除量化边界翻转致同板反复全量 OCR |
| `PeakSelectWatcher.start/stop` | `peak_select_watcher.py` | PeakSelectPanel._on_toggle_watcher() / shutdown() | _state_lock 内会话世代递增、状态重置（start 清 6 项、stop 48b0f99 起也清 _resolutions/_resolution_raws/_stale_rounds/_ban_names/_last_board）、挂起/恢复标准任务、set_task_hold 持有加固、清双任务冷却、作废在途轮询 |
| `PeakSelectWatcher._do_work` | `peak_select_watcher.py` | _on_tick | 会话世代校验（四处）、capture、detect、每拍幂等重挂、board_signature_equal 容差判等、recognize、_refresh_resolutions、publish |
| `PeakSelectWatcher._recognize_board` | `peak_select_watcher.py` | _do_work / _do_file_recognition | submit_ocr_task、超时/未完成处理 |
| `PeakSelectWatcher._publish_pool` | `peak_select_watcher.py` | _do_work / _do_file_recognition / confirm_pending | _state_lock 全程组装、parse_pool、pool_updated 信号（锁外发出） |
| `PeakSelectWatcher._suspend_standard_tasks` | `peak_select_watcher.py` | start() / _do_work()（每拍，含签名未变的拍） | 首次记录原状态快照，之后仅挂起 active 任务（幂等） |
| `PeakSelectWatcher._restore_match_guide` | `peak_select_watcher.py` | _handle_board_absent() | 牌面自动退出时仅恢复 match_guide 原状态 |
| `PeakSelectWatcher._restore_standard_tasks` | `peak_select_watcher.py` | stop()（set_task_hold(False) 之后） | activate/deactivate_task 恢复全部标准任务原状态 |
| `PeakSelectWatcher._handle_board_absent(session)` | `peak_select_watcher.py` | _do_work | 锁内世代校验、miss_ticks 计数、_state_lock 清理（exiting 时清 _ban_names/_resolutions/_resolution_raws/_stale_rounds/_last_board）、_restore_match_guide、board_exited 信号 |
| `PeakSelectWatcher.confirm_pending` | `peak_select_watcher.py` | PeakSelectPanel._confirm_candidate | _state_lock 写入确认 + 读数指纹、**牌面在位守卫**（last_board 为 None 时拒绝并提示"牌面已不在识别中"，48b0f99）、record_confirmation 白名单登记、_publish_pool 重发快照 |
| `PeakSelectWatcher.recognize_image_file` | `peak_select_watcher.py` | PeakSelectPanel._on_import_from_file | _do_file_recognition 后台线程（独立锁，不校验会话世代） |
| `PeakSelectWatcher.board_exited` | `peak_select_watcher.py` | _handle_board_absent（经 PeakSelectPanel 透出） | MainWindow._on_peak_board_exited 切换胜率榜模式 + 衔接激活 match_guide |
| `parse_pool(ocr_results, card_count, ...)` | `peak_select_watcher.py` | _publish_pool | PoolSnapshot 构造（已确认/待确认/已禁/阶段/撞车数） |
| `refresh_resolutions(resolutions, raws, ocr_results)` | `peak_select_watcher.py` | _do_work（新牌面） | 逐拍验证人工确认内容存续（闭包/读数指纹/重排迁移），连续失验达上限才丢弃 |
| `verified_resolutions_for_import(resolutions, raws, ocr_results)` | `peak_select_watcher.py` | _do_file_recognition | 48b0f99 新增：复用 refresh_resolutions 的定位语义，但导入是一次性快照、无法定位的确认**立即丢弃**（返回 dropped 计数），避免旧槽位号盲目套用到新牌面 |
| `PeakSelectPanel._on_toggle_watcher` | `peak_select_panel.py` | 开始识别按钮 | watcher.start/stop + _mark_stale / _reset_view + 状态提示 |
| `PeakSelectPanel._mark_stale(badge_text)` | `peak_select_panel.py` | _on_toggle_watcher(停止分支) / _on_watcher_board_exited | 48b0f99 新增：_clear_pending_rows + hide(_pending_area) + 阶段徽章置灰（卡片保留供复盘） |
| `PeakSelectPanel._on_watcher_board_exited` | `peak_select_panel.py` | watcher.board_exited | _mark_stale("阶段：牌面退出") + board_exited.emit 转主窗口 |
| `PeakSelectPanel._on_import_from_file` | `peak_select_panel.py` | 三点菜单 [从图片导入] | ocr_warmup_state 守卫、QFileDialog、watcher.recognize_image_file |
| `PeakSelectPanel._update_import_availability` | `peak_select_panel.py` | capture_service.ocr_warmup_state_changed | 预热中禁用 [从图片导入]（引擎加载持 GIL 会冻结界面） |
| `PeakSelectPanel._on_capture_completed(save_path=None)` | `peak_select_panel.py` | capture_service.capture_completed | 截图未落盘时状态栏 TONE_WARNING「截图未落盘（保存中或失败，详见日志）」（80cc75b） |
| `PeakSelectPanel._on_save_screenshot` | `peak_select_panel.py` | 三点菜单 [保存截图] | CaptureRequestLock(ADB_SAVE) + do_capture(perform_ocr=False) |
| `PeakSelectPanel._on_capture_result / _on_capture_failed` | `peak_select_panel.py` | capture_service.capture_completed / capture_failed | CaptureRequestLock.finish 校验，仅 ADB_SAVE 来源时更新状态栏 |
| `evaluate_peak_ban_advice` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 缺失/弱势/冷门强势/热门强势四步判定 |
| `derive_win_rate_ranks` | `peak_ban_advice.py` | PeakSelectPanel._render_cards | 胜率排名推导 |
| `PeakHeroCard.set_hero/set_win_rate/set_ban_advice/set_combo_badge` | `peak_hero_card.py` | PeakSelectPanel._render_cards | 卡片头像/胜率/禁选徽章/实战角标渲染 |
| `PeakSelectPanel._on_pool_updated` | `peak_select_panel.py` | watcher.pool_updated | 阶段/候选汇总、_render_cards、pending、banned；48b0f99 起 **同名槽位显性告警**（duplicate_count>0 时 logger.warning + 日志追加 + 状态栏 TONE_WARNING） |
| `PeakSelectPanel._render_cards` | `peak_select_panel.py` | _on_pool_updated / 排序切换 / combos_changed | 卡片两排布局 + 禁选建议 + 实战角标 |
| `PeakSelectPanel._refresh_combo_strip` | `peak_select_panel.py` | _render_cards | ComboManager.list_combos 匹配 + best_ratings |
| `PeakSelectPanel._render_combo_chips` | `peak_select_panel.py` | _refresh_combo_strip | 实战配队 chip 渲染 |
| `PeakSelectPanel._open_combo_management` | `peak_select_panel.py` | 实战配队条 [管理] 按钮 | ComboManagementDialog |
| `PeakSelectPanel._build_pending_row/_confirm_candidate` | `peak_select_panel.py` | _render_pending | 候选按钮、_watcher.confirm_pending |
| `PeakSelectPanel.is_recognizing` | `peak_select_panel.py` | MainWindow | 识别会话运行中时主窗口丢弃泄漏的选将轮询结果（消费端守卫） |
| `PeakSelectPanel.board_exited` → `MainWindow._on_peak_board_exited` | `peak_select_panel.py` / `main_window.py` | watcher.board_exited | 切换胜率榜模式（WIN_RATE_MODE_PEAK）+ 激活 match_guide；HEALTHY_NO_MATCH 经 `_deactivate_match_guide_if_idle`（90s 上限）失活 |
| `run_import` | `combo_import_service.py` | CLI main / _ImportWorker | 名称映射、座次解析、position 交叉校验、合并、CRUD、save，返回 11 区块报告 |
| `import_combos.main` | `import_combos.py` | 命令行入口 | argparse（--source/--heroes/--output）+ run_import + 报表打印 |
| `_ImportWorker` (QThread) | `combos_import_dialog.py` | CombosImportDialog._on_accept | run_import 异步执行 |
| `CombosImportDialog._on_accept/_on_import_finished` | `combos_import_dialog.py` | 用户点击 [执行导入] | _ImportWorker 启动、报告展示 |
| `parse_seats` | `combo_seats.py` | run_import | note 座次解析 |
| `format_seats` | `combo_seats.py` | PeakSelectPanel._render_combo_chips / _combo_tooltip | 座次列表 → 展示文本 |
| `ComboManager._combo_key` | `combo_manager.py` | 内部调用 | sorted((a_id, b_id)) |
| `ComboManager._save_unlocked` | `combo_manager.py` | save_manual_combo / delete_combo / restore_combo / save() | sorted by (-rating, hero1_id, hero2_id) + atomic_write_json |
| `ComboManager.save_manual_combo` | `combo_manager.py` | ComboManagementDialog | key 迁移 + manual=True + _save_unlocked |
| `ComboManager.get_combo` | `combo_manager.py` | 编辑覆盖检查、run_import | _combo_key() + dict get（含逻辑删除） |
| `ComboManager.list_combos` | `combo_manager.py` | PeakSelectPanel、ComboManagementDialog | list_all() 过滤 deleted |
| `ComboManager.list_all_combos` | `combo_manager.py` | run_import | list_all()（含逻辑删除） |
| `ComboManager.list_combos_for_hero` | `combo_manager.py` | HeroDetailView、RecommendationPanel | 线性遍历 O(N)，过滤 deleted |
| `ComboManager.delete_combo` | `combo_manager.py` | ComboManagementDialog | 标记 deleted=True + deleted_at，原子落盘 |
| `ComboManager.restore_combo` | `combo_manager.py` | ComboManagementDialog | 标记 deleted=False，原子落盘 |
| `LineupState.load_from_ocr/set_side/validate/confirm` | `match_lineup_state.py` | MatchGuidePanel（load_from_ocr / _set_side / _replace_hero / _confirm_lineup / clear_blocks） | OCR 导入、敌我确认、完整性校验 |
| `MatchAnalysisView.render_unconfirmed/render_analysis` | `match_analysis_view.py` | MatchGuidePanel._refresh_analysis / _clear_lineup_display | 四页签渲染 |
| `ocr_service.set_task_hold(task_name, held)` | `ocr_service.py` | PeakSelectWatcher.start/stop | 持有期间任务不得被任何入口激活（activate_task 检查持有集合并拒绝） |
| `MatchGuidePanel.set_win_rate_mode(mode)` | `match_guide_panel.py` | MainWindow._on_poll_hero_selection_matched / _on_peak_board_exited | 切换胜率榜模式（WIN_RATE_MODE_2V2 / WIN_RATE_MODE_PEAK），已有阵容时重渲染 |
| `MatchGuidePanel._current_win_rates` | `match_guide_panel.py` | _refresh_analysis / _render_cards | 按当前模式返回胜率数据（巅峰赛榜 / 2v2 榜） |
| `load_peak_win_rates/load_peak_pick_ranks` | `peak_win_rate_repository.py` | _win_rates_provider/_pick_ranks_provider | CSV 读取 + 缓存 |
| `clear_peak_win_rate_cache` | `peak_win_rate_repository.py` | 数据更新后 | 清空胜率与出场排行缓存 |

---

## 八、本轮文档校准（2026-10-06）

自基线 `885ea96`（2026-10-02 校准）以来的变更：

- **截图失败可观测**（80cc75b）：`peak_select_panel` 截图保存路径为 `None` 时状态栏由"截图已保存："空路径改为"截图未落盘（保存中或失败，详见日志）"（TONE_WARNING）；`capture_service` 存盘失败同步补 warning
- 引擎口径跟随 B1：巅峰赛识别链路经 `OcrWorker` -> `GeneralRecognizer` -> `engine_loader`（v6 主 + v4 复核），链路结构不变；识别内收益（team 链式重试、回退触发线 0.75）见 `call_graph_capture_ocr.md`
- ComboStrip `Signal(object)` 修复（0ffed36）属选将推荐板块（见 `call_graph_ui.md` §4.3）；巅峰赛卡片角标同源数据走独立直调路径，不受影响
