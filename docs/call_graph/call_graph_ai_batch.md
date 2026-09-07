# 调用链路：AI 批量生成

> 对应源码：`src/scraper/ai/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 生成链路（`run_*_generation()`）均在 QProcess 子进程中执行，不阻塞 UI 主线程；`complete()` 的通用对话补全调用方（语料精炼、分类建议等）在进程内直接调用，不经 QProcess。

---

## 当前实现基线（2026-09-07）

AI 生成按批原子提交：每批校验成功结果立即提交，失败项仅保留对应旧数据；任务汇总失败时以退出码 `1` 结束，成功项不受影响。

双生成器：API 模式（`AIBatchGenerator -> httpx -> 供应商端点`）/ 浏览器模式（`PlaywrightGenerator -> Edge -> chat.deepseek.com`）。API 模式按 `provider` 适配多供应商档案（deepseek / openai / ollama / openai-compatible，取自 `config/api_profiles.json` 唯一启用档案），仅 `provider=deepseek` 注入私有参数 `thinking.type=disabled`；`requires_key=False` 的 ollama 本地服务允许空 Key。

API 模式输出上限 `max_output_tokens`（默认 16384，按供应商语义可上调）；`_request_content()` 在正文被"思考过程耗尽输出额度"截断时，最多重试 `max_retries` 次；`_call_api()` 对 HTTP 429 优先读 `Retry-After` 头（钳到 3-30s，无头则 5/10/15s）退避、408/5xx/连接异常按 2^attempt 指数退避，400/401/403/404/422 立即失败，连接类异常（`_CONN_ERRORS`）关闭并重建 httpx.Client 避免级联失败；`cancel()` 置 `_cancelled` 标志使重试循环在下次循环开头退出；每次重试向 stdout 输出 `[重试]` 行，`_log_usage()` 记录 reasoning/content token 拆分。

攻略与相性默认启用 RAG 官方规则语料注入（`rag_prompt.py`），`--no-rag` 关闭；RAG 运行时异常自动降级为经典模式，循环输出一次 `[RAG]` 提示。RAG 开启时 `build_*_prompt()` 兜底注入 `load_card_system()` 卡牌体系段防止牌名串味；RAG 关闭且无语料时兜底注入 `load_core_rules()` 完整核心规则摘要。技能行由 `_skill_lines()` 构建：语料块已注入的技能指针化省 token，未注入的自动回退完整描述并附结算后缀。

```
ai_batch.main()
  -> load_heroes() + resolve_api_config() + get_runtime_params()
  -> AIBatchGenerator(...) 或 PlaywrightGenerator(...)
  -> run_guide_generation() / run_synergy_generation() /
     run_synergy_pair_generation() / run_synergy_single_generation() /
     run_synergy_list_generation()
    -> generate_*() -> _request_content() / _send_and_wait() -> extract_json() -> validate_*()
    -> 达到批量阈值时 _commit_generation_batch() -> _save_json() 原子写入正式 JSON
  -> sum(GenerationResult.prompt/completion) -> _print_token_summary()
  -> 任一 result.failed_items 非空？ -> 成功批次保留，失败项维持旧数据，exit 1
```

| 参数 | 编排函数 | 范围 |
|------|----------|------|
| `--guide` | `run_guide_generation()` | 攻略 |
| `--synergy` | `run_synergy_generation()` | 全量两两相性 |
| `--synergy-pair` | `run_synergy_pair_generation()` | 2-8 名指定英雄组合 |
| `--synergy-single` | `run_synergy_single_generation()` | 指定英雄对全体 |
| `--synergy-list` | `run_synergy_list_generation()` | 显式配对清单（新增） |
| `--browser` | 切到 PlaywrightGenerator | 浏览器模式 |
| `--no-rag` | 设置 `RAG_ENABLED=false` | 禁用 RAG 注入 |
| `--update` | 更新模式 | 重新生成已有数据 |
| `--dry-run` | `_show_cost_estimate()` | 预览 RAG/经典双模式成本 |
| `--rebuild-rag-index` | `src.rag.indexer.build_index(rebuild=True)` | 重建 RAG 向量索引后退出 |

## 一、CLI 入口总览

### 1.1 AI 批量生成入口

```
ai_batch.py -> ai/batch.py:main()                             [兼容入口 -> 实际入口]
  -> argparse.parse_args()                                    [解析命令行参数]
  -> [args.rebuild_rag_index] build_index(rebuild=True) -> exit [重建 RAG 索引]
  -> [args.no_rag] os.environ["RAG_ENABLED"]="false"           [禁用 RAG]
     [否则] is_rag_enabled()，被选择但配置禁用时输出 [RAG] 提示
  -> get_runtime_params()                                     [获取限流/重试/超时/输出额度参数]
  -> setup_logging()                                          [初始化日志]
  -> load_heroes(args.heroes_file)                            [HeroManager 完整校验武将 JSON]
  -> resolve_api_config(None)                                 [多 API 档案唯一启用档案 → config.env → 环境变量 → 默认值]
  -> [args.dry_run] _show_cost_estimate()                     [预览 RAG/经典双模式成本]
  -> [非 browser] _check_api_key(api_config)                  [空 key + requires_key 时 exit]
  -> 生成器创建:
     [args.browser]
       -> PlaywrightGenerator()
     [default]
       -> AIBatchGenerator(api_key, api_url, model, provider, rpm, retries, timeout, max_output_tokens)
  -> 数据加载:
     [args.guide] _load_existing_guides(guide_path)           [已有攻略 dict]
     [args.synergy 或 synergy-pair/single/list] _load_existing_synergies(synergy_path) [dict + keys]
  -> 模式分发（task_results 收集每个 GenerationResult）:
     [args.guide]
       -> run_guide_generation(heroes, gen, guide_path, existing_guides, api_cfg, update_mode=args.update)
     [args.synergy]
       -> run_synergy_generation(heroes, gen, synergy_path, existing_dict, existing_keys, score_threshold, api_cfg)
     [args.synergy_pair]
       -> run_synergy_pair_generation(pair_file, heroes, gen, synergy_path, existing_dict, existing_keys, update_mode)
     [args.synergy_single]
       -> run_synergy_single_generation(single_file, heroes, gen, synergy_path, existing_dict, existing_keys)
     [args.synergy_list]
       -> run_synergy_list_generation(pairs_file, heroes, gen, synergy_path, existing_dict, existing_keys, update_mode)
  -> generator.close()                                        [finally 中释放资源]
  -> sum(result.prompt_tokens, result.completion_tokens) for result in task_results
     -> _print_token_summary()                                 [汇总 Token 用量]
  -> failed_results = [r for r in task_results if not r.succeeded]
     -> [有失败项] print("[错误] 生成失败：N 项...") + sys.exit(1)
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `ai/batch.py` | QProcess 子进程入口 | `load_heroes()`, `_load_existing_*()`, `run_*_generation()` |
| `load_heroes()` | `ai/utils.py` | `ai_batch.main()` | `HeroManager.load()`, `Hero.model_validate()` |
| `resolve_api_config()` | `config/env.py` | `ai_batch.main()` | 任务侧唯一 API 解析入口：`api_profiles.json` 唯一启用档案 → config.env 旧键 → 环境变量 → 默认值；返回 provider/api_key/api_url/model |
| `get_runtime_params()` | `config/env.py` | `ai_batch.main()` | 获取 RPM、最大重试次数、HTTP 超时、输出 token 上限（默认 16384） |
| `_load_existing_guides()` | `ai/batch.py` | `main()` | `GuideManager.load()`；错误文件备份后写回有效记录 |
| `_load_existing_synergies()` | `ai/batch.py` | `main()` | `SynergyManager.load()`；错误文件备份后写回有效记录 |
| `_show_cost_estimate()` | `ai/batch.py` | `main()` | `_print_mode_estimates()` -> `estimate_cost(..., use_rag)` 分别输出 RAG/经典 |
| `_check_api_key()` | `ai/batch.py` | `main()` | `PROVIDER_PRESETS[provider].requires_key` 语义判断 |
| `_print_token_summary()` | `ai/batch.py` | `main()` | `estimate_cost_by_tokens()` |

---

## 二、攻略生成链路

### 2.1 API 模式攻略生成

```
generation.run_guide_generation(heroes, generator, guide_path, existing_guides, api_cfg, update_mode)
  -> working_guides = dict(existing_guides); new_guides = []
  -> 遍历每个 hero:
     -> [not update_mode and hero_id in existing_guides] 跳过，result_summary.skipped++
     -> generator.generate_guide(hero)
        -> load_prompt(GUIDE_PROMPT_FILE)                       [读取系统提示词]
        -> build_guide_prompt(hero)                              [构建用户提示词]
           -> build_rag_context(hero)                            [RAG 注入]
              -> [is_rag_enabled()] _get_retriever()
              -> retriever.hero_blocks(hero_name)                 [本武将语料块]
              -> 技能/机制词提取 -> retriever.search(query, top_k) [跨类召回]
              -> post-filter: combo 按 heroes 列表，hero 块按名称过滤
              -> _format_rag_chunks(blocks, extra, budget)       [官方/社区两段，整块丢弃；过时块加 ⚠️ 前缀]
           -> _skill_lines(skills, hero_id, rag)                 [语料块已注入→指针化，否则完整描述+结算]
           -> [is_rag_enabled()] load_card_system()              [RAG 开：卡牌体系段兜底防串味]
           -> [not rag_enabled and not rag] load_core_rules()    [RAG 关：完整核心规则摘要兜底]
        -> self._request_content(messages, temperature=0.7, label=hero.name)
           -> [重试循环 attempt=1..max_retries]
              -> self._call_api(messages)                         [API 请求]
                 -> [循环开头] _cancelled -> 返回 None            [取消标志：面板销毁/中止]
                 -> time.sleep(限速)                               [RPM 前置限流]
                 -> POST api_url                                  [供应商端点；max_tokens=MAX_OUTPUT_TOKENS]
                 -> [provider=="deepseek"] payload["thinking"]={"type":"disabled"} [私有参数，其他端点会 400]
                 -> resp.json() -> {content, finish_reason, usage}
                 -> [HTTP 429] _retry_wait(status, attempt, headers) [Retry-After 钳到 3-30s，无头则 max(5*attempt,3)]
                    [HTTP 408/5xx] 2^attempt 秒
                    [HTTP 400/401/403/404/422] 立即抛错失败，不重试
                 -> [连接异常 _CONN_ERRORS] close()+重建 httpx.Client [避免 RemoteProtocolError 级联]
                 -> print("[重试] ...")                            [stdout 进度白名单放行]
              -> _read_completion_content(response, max_output_tokens)
                 -> [finish_reason=="length" or content 为空] 返回 None (额度耗尽)
                 -> [否则] 返回 content + usage
              -> _log_usage(label, usage)                          [记录 reasoning/content 拆分]
              -> [content is not None] return content, usage
              -> [content is None and attempt < max_retries] 2^attempt 秒后重试
           -> return content, usage
        -> extract_json(content)                                  [4 策略回退提取]
        -> raw["hero_id"] = hero.id
        -> convert_ids_to_int(raw, ["synergizes_with"])
        -> has_required_guide_fields(raw)                          [必填字段 + 占位符标记/正文<200 字预检]
        -> validate_guide(raw)                                     [HeroGuide.model_validate -> model_dump]
        -> return (result, usage)
     -> [生成成功] working_guides[hero_id] = generated; new_guides.append(generated)
     -> [生成失败] result_summary.failed_items.append(hero_name)
     -> _report_rag_degradation()                                  [RAG 降级时输出一次 [RAG] 提示]
     -> [len(new_guides) - committed >= GUIDE_BATCH_SAVE_INTERVAL=10]
        -> _commit_generation_batch(result, guide_path, list(working_guides.values())) -> _save_json()
  -> [最终保存]
  -> return result_summary
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `run_guide_generation()` | `generation.py` | `batch.main()` | `generator.generate_guide()`, `_commit_generation_batch()` |
| `_commit_generation_batch()` | `generation.py` | `run_*_generation()` | `_save_json()` |
| `AIBatchGenerator.generate_guide()` | `api_generator.py` | `generation.py` | `load_prompt()`, `build_guide_prompt()`, `_request_content()`, `extract_json()`, `validate_guide()` |
| `AIBatchGenerator._request_content()` | `api_generator.py` | `generate_guide/synergy` | `_call_api()`, `_read_completion_content()`, `_log_usage()` |
| `AIBatchGenerator._call_api()` | `api_generator.py` | `_request_content()` / `complete()` | `httpx.Client.post()`; 失败走 `_retry_wait()` 限流退避或立即失败；连接类异常关闭并重建 client |
| `AIBatchGenerator._read_completion_content()` | `api_generator.py` | `_request_content()` | 检查 `finish_reason=="length"` 判断额度耗尽 |
| `AIBatchGenerator._log_usage()` | `api_generator.py` | `_request_content()` | 记录 reasoning/content token 拆分 |
| `AIBatchGenerator._retry_wait()` | `api_generator.py` | `_call_api()` | 429 读 Retry-After 钳到 3-30s、无头 max(5*attempt,3)；其余 2^attempt |
| `extract_json(text)` | `json_extract.py` | `generate_guide/synergy` | `_try_extract()` ×4 策略 |
| `_try_extract(candidates)` | `json_extract.py` | `extract_json()` | `_raw_parse()`, `_repair_strings()` |
| `has_required_guide_fields()` | `ai/utils.py` | `generate_guide()` | 必填字段 + 占位符标记/正文<200 字预检 |
| `validate_guide()` | `ai/utils.py` | `generate_guide()` | `HeroGuide.model_validate()` |
| `build_guide_prompt(hero, rag_max_chars)` | `prompt_utils.py` | `generate_guide()` | `build_rag_context()`, `_skill_lines()`, `load_card_system()`, `load_core_rules()` |
| `build_rag_context(hero)` | `rag_prompt.py` | `build_guide_prompt()` | `Retriever.hero_blocks()`, `Retriever.search()`, `_format_rag_chunks()` |

### 2.2 浏览器模式攻略生成

```
PlaywrightGenerator.generate_guide(hero)
  -> [上轮成功后 _guide_rest_required=True] self._random_rest()  [随机休息 60-180s，输出[休息]行]
     -> time.sleep(random.randint(60, 180))
  -> load_prompt(GUIDE_PROMPT_FILE)
  -> build_guide_prompt(hero, rag_max_chars=_browser_rag_max_chars())  [每次重发完整 system+数据]
  -> full_prompt = system + "\n\n---\n\n" + user + "\n\n" + GUIDE_FORMAT_REMINDER
  -> self._send_and_wait(full_prompt)
     -> DeepSeekBrowserSession.send_and_wait(full_prompt)
        -> self._ensure_browser()
           -> [未启动] sync_playwright().start()
           -> playwright.chromium.launch_persistent_context()    [复用 Edge 用户数据]
           -> page.goto("https://chat.deepseek.com/")
           -> _wait_for_login() -> page.wait_for_selector(input_selector)
        -> page.fill(input_selector, prompt)
        -> page.keyboard.press("Enter")
        -> [轮询] page.locator(assistant_selector).count()        [检测新回复出现]
        -> [内容稳定检测] last_len 连续 3 轮不变 -> 完成
        -> [超时] _page_diagnostics() + 返回 None
        -> 提取最后一条消息文本 -> 返回 reply
  -> extract_json(reply)
  -> [提取失败] _send_and_wait(GUIDE_RETRY_PROMPT)                [浏览器格式纠正消息重试一次]
     -> extract_json(retry_reply)
  -> raw["hero_id"] = hero.id
  -> convert_ids_to_int(raw, ["synergizes_with"])
  -> has_required_guide_fields(raw)
  -> validate_guide(raw)
  -> self._guide_rest_required = True
  -> return (result, None)                                        [浏览器模式无语费统计]
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `PlaywrightGenerator.generate_guide()` | `browser_generator.py` | `generation.py` | `_random_rest()`, `load_prompt()`, `build_guide_prompt()`, `_send_and_wait()` |
| `PlaywrightGenerator._send_and_wait()` | `browser_generator.py` | `generate_guide/synergy` | `DeepSeekBrowserSession.send_and_wait()` |
| `DeepSeekBrowserSession.send_and_wait()` | `browser_session.py` | `PlaywrightGenerator._send_and_wait()` | `_ensure_browser()`, `page.fill()`, `page.keyboard.press()`, 流式回复轮询 |
| `DeepSeekBrowserSession._ensure_browser()` | `browser_session.py` | `send_and_wait()` | `sync_playwright().start()`, `chromium.launch_persistent_context()`, `_wait_for_login()` |
| `DeepSeekBrowserSession._wait_for_login()` | `browser_session.py` | `_ensure_browser()` | `page.wait_for_selector()` |
| `DeepSeekBrowserSession._page_diagnostics()` | `browser_session.py` | 登录或等待异常 | `page.evaluate(JS dump)` |
| `PlaywrightGenerator._random_rest()` | `browser_generator.py` | 下一次请求前 | `time.sleep(random.randint(60,180))` |
| `_browser_rag_max_chars()` | `browser_generator.py` | `generate_guide/synergy` | `RAG_BROWSER_PROMPT_CHARS`（默认 3000） |

---

## 三、相性生成链路

### 3.1 四个生成循环共享核心 `_run_synergy_pairs()`

```
generation._run_synergy_pairs(pairs, generator, synergy_path, existing_dict, existing_keys, *,
                               skip_existing, score_threshold, label_of, fail_label_of, missing_score_text)
  -> working_synergies = dict(existing_dict)                      [以旧数据为起点，失败时保留]
  -> 遍历每个 (ha, hb):
     -> pair_key = tuple(sorted([ha.id, hb.id]))
     -> [skip_existing and pair_key in existing_keys] 跳过，result_summary.skipped++
     -> generator.generate_synergy(ha, hb)                        [API 或浏览器模式，见 3.2]
     -> result_summary.add_usage(usage)                            [累计 Token]
     -> _report_rag_degradation()                                  [RAG 降级时输出一次提示]
     -> [generated 非 None]
        -> [score_threshold is None] working_synergies[pair_key] = _with_synergy_updated_date(generated)
        -> [score_threshold 已设定 且 score >= threshold] working_synergies[pair_key] = _with_synergy_updated_date(generated)
        -> [score < threshold] working_synergies.pop(pair_key, None) [低于下限移除旧记录]
        # _with_synergy_updated_date() 写入 last_updated=date.today()
     -> [generated is None] result_summary.failed_items.append(fail_label_of(ha, hb))
     -> [completed - committed >= SYNERGY_BATCH_SAVE_INTERVAL=10]
        -> _commit_generation_batch() -> _save_json()               [原子写入]
  -> [循环结束且剩余] _commit_generation_batch()
  -> existing_dict / existing_keys 回写 working_synergies
  -> return result_summary
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `_run_synergy_pairs()` | `generation.py` | 四个相性生成函数 | `generator.generate_synergy()`, `_with_synergy_updated_date()`, `_commit_generation_batch()` |
| `_with_synergy_updated_date()` | `generation.py` | `_run_synergy_pairs()` | 写入 `last_updated=date.today()` |

### 3.2 全量相性

```
run_synergy_generation(heroes, generator, synergy_path, existing_dict, existing_keys, score_threshold, api_cfg)
  -> total_pairs = N*(N-1)//2
  -> _run_synergy_pairs(
       combinations(heroes, 2),  generator, synergy_path,
       existing_dict, existing_keys,
       skip_existing=False,                                          [全量模式永不跳过]
       score_threshold=score_threshold,
       label_of=_pair_label, fail_label_of=_pair_fail_label,
       missing_score_text="0")
```

### 3.3 指定配对相性（2-8 名）

```
run_synergy_pair_generation(pair_file, heroes, generator, synergy_path, existing_dict, existing_keys, update_mode)
  -> json.load(pair_file) -> pair_heroes
  -> [count < 2] failed_items.append("指定武将数量不足") -> return
  -> [count > 8] failed_items.append("指定武将数量超出上限") -> return
  -> _run_synergy_pairs(
       combinations(pair_heroes, 2), generator, synergy_path,
       existing_dict, existing_keys,
       skip_existing=not update_mode,                                [update_mode=False 时跳过已有]
       score_threshold=None,                                          [配对模式不设下限]
       label_of=_pair_label, fail_label_of=_pair_fail_label,
       missing_score_text="?")
```

### 3.4 选定武将 vs 全体

```
run_synergy_single_generation(single_file, heroes, generator, synergy_path, existing_dict, existing_keys)
  -> json.load(single_file) -> single_heroes
  -> [len != 1] failed_items.append("指定武将数量无效") -> return
  -> target = single_heroes[0]
  -> pairs = [(target, h) for h in heroes if h.id != target.id]
  -> _run_synergy_pairs(
       pairs, generator, synergy_path,
       existing_dict, existing_keys,
       skip_existing=True,                                            [始终跳过已有]
       score_threshold=None,
       label_of=lambda _ha,hb: hb.name, fail_label_of=...,
       missing_score_text="?")
```

### 3.5 显式配对清单（新增模式）

```
run_synergy_list_generation(pairs_file, heroes, generator, synergy_path, existing_dict, existing_keys, update_mode)
  -> json.load(pairs_file) -> pairs_raw = [{"hero_a_id":..,"hero_b_id":..}, ...]
  -> hero_by_id = {h.id: h for h in heroes}
  -> pairs = [(hero_by_id[ha_id], hero_by_id[hb_id]) if ha_id != hb_id else None]
  -> [配对无效] failed_items.append("#ha_id<->#hb_id（配对无效）")
  -> [pairs 为空] failed_items.append("配对清单为空或全部无效") -> return
  -> _run_synergy_pairs(
       pairs, generator, synergy_path,
       existing_dict, existing_keys,
       skip_existing=not update_mode, score_threshold=None,
       label_of=_pair_label, fail_label_of=_pair_fail_label,
       missing_score_text="?",
       result_summary=result_summary)                                [预置失败项透传]
```

### 3.6 `generator.generate_synergy()` 核心链路

```
AIBatchGenerator.generate_synergy(hero_a, hero_b)
  -> load_prompt(SYNERGY_PROMPT_FILE)
  -> build_synergy_prompt(hero_a, hero_b)
     -> build_synergy_rag_context(hero_a, hero_b)
        -> [is_rag_enabled()] _get_retriever()
        -> retriever.hero_blocks(name_a) + retriever.hero_blocks(name_b)
        -> 双 query 融合: [name_a name_b] + [skills + mech_terms]
        -> retriever.search(q, top_k=half_k) 各取半数去重合并
        -> post-filter: combo 按 heroes 列表, hero 块按名称过滤, 无武将归属块保留
        -> _format_rag_chunks(blocks, extra, RAG_SYNERGY_PROMPT_CHARS)
     -> [is_rag_enabled()] load_card_system()
     -> [not rag_enabled and not rag] load_core_rules()
  -> self._request_content(messages, temperature=0.3, label="heroA/heroB")
     -> [同攻略链路: _call_api() -> _read_completion_content() -> _log_usage()]
  -> extract_json(content)
  -> raw["hero_a_id"] = hero_a.id; raw["hero_b_id"] = hero_b.id
  -> [compat] combat_synergy -> combo_ceiling                       [兼容旧 prompt 字段]
  -> has_required_synergy_fields(raw)
  -> validate_synergy(raw)
  -> return (result, usage)
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `_run_synergy_pairs()` | `generation.py` | 四个相性生成函数 | `generator.generate_synergy()`, `_commit_generation_batch()` |
| `run_synergy_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=False, score_threshold=...)` |
| `run_synergy_pair_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=not update, score_threshold=None)` |
| `run_synergy_single_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=True, score_threshold=None)` |
| `run_synergy_list_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=not update, score_threshold=None)` |
| `AIBatchGenerator.generate_synergy()` | `api_generator.py` | `generation.py` / `scripts/run_synergy_drift.py` | `load_prompt()`, `build_synergy_prompt()`, `_request_content()`, `extract_json()`, `validate_synergy()` |
| `PlaywrightGenerator.generate_synergy()` | `browser_generator.py` | `generation.py` | `_random_rest()`, `load_prompt()`, `build_synergy_prompt()`, `_send_and_wait()`, `extract_json()`, `validate_synergy()` |
| `build_synergy_prompt(a, b, rag_max_chars)` | `prompt_utils.py` | `generate_synergy()` | `build_synergy_rag_context()`, `_skill_lines()`, `load_card_system()`, `load_core_rules()` |
| `build_synergy_rag_context(a, b)` | `rag_prompt.py` | `build_synergy_prompt()` | `Retriever.hero_blocks()` ×2, `Retriever.search()` ×2, `_format_rag_chunks()` |
| `validate_synergy()` | `ai/utils.py` | `generate_synergy()` | `SynergyScore.model_validate()` |

---

## 四、外部调用关系总览

### 4.1 本模块被外部调用

```
src.business.fetching.guide_fetch_service
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--guide", ...])
     [增量/指定模式] -> 追加 --update + --heroes-file <临时武将文件>
     [后端选择 browser] -> 追加 --browser
     [经典模式 use_rag=False] -> 追加 --no-rag

src.business.fetching.synergy_fetch_service
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--synergy-pair", tmp_file])    [指定配对]
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--synergy-single", tmp_file])  [选定武将]
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--synergy-list", tmp_file])    [实战配队清单]
     [overwrite=True] -> 追加 --update
     [后端选择 browser] -> 追加 --browser
     [经典模式 use_rag=False] -> 追加 --no-rag

src.ui.app.main_window
  -> 菜单「数据 → 攻略获取 / 武将相性」-> AiGenerationWorkflow -> GuideFetchService / SynergyFetchService [间接调用]

复用 complete() / generate_synergy() 做通用对话补全（不经生成循环，直接调 API 生成器；后两者经 refinement_service.build_generator() 取生成器）:
src.business.rag.refinement_service            -> resolve_api_config(profile_name) -> AIBatchGenerator -> complete(messages, 0.2)
src.business.maintenance.classification_suggest -> AIBatchGenerator.complete(messages, 0.2)   [武将分类建议]
src.scripts.propose_rule_changes               -> AIBatchGenerator.complete(messages, 0.2)    [规则变更提案]
src.scripts.run_synergy_drift                  -> AIBatchGenerator.generate_synergy(hero_a, hero_b) 多次采样
```

### 4.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.models.HeroGuide` | Pydantic 校验攻略 |
| `src.data.models.SynergyScore` | Pydantic 校验相性 |
| `src.data.{hero,guide,synergy}_manager` | 断点加载逐条校验；错误文件备份为 `.corrupt-时间戳.json` 后仅写回有效记录 |
| `src.config.env.resolve_api_config()` | 任务侧唯一 API 解析入口（多 API 档案唯一启用档案 → config.env 旧键 → 环境变量 → 默认值） |
| `src.config.env.PROVIDER_PRESETS` | 供应商语义：`requires_key` 判定 Key 是否必填（ollama 本地服务可空） |
| `src.config.env.get_runtime_params()` | 获取 RPM、最大重试、HTTP 超时、输出 token 上限（默认 16384） |
| `src.config.env.get_model_pricing()` | 按 `config/model_pricing.json` 取模型单价，用于 dry-run 与结束汇总估价 |
| `config/api_profiles.json` | 多 API 档案（多供应商/多账号，启用互斥；含 Key，已 gitignore） |
| `src.config.logging_config.setup_logging()` | 日志初始化 |
| `docs/prompts/hero_guide.md` | 攻略生成提示词文件 |
| `docs/prompts/synergy_score.md` | 相性生成提示词文件 |
| `src.rag`（config/indexer/retriever） | RAG 语料加载、ChromaDB 向量检索与关键词 RRF（默认只召 `is_current` 当前版本块）；`--rebuild-rag-index` 走 `build_index(rebuild=True)` |
| `data/rag_corpus/核心规则摘要.md` | 无 RAG 路径的核心规则兜底（全文 `load_core_rules()` / 卡牌体系段 `load_card_system()`） |

### 4.3 双生成器对比

| 对比项 | AIBatchGenerator | PlaywrightGenerator |
|--------|-----------------|-------------------|
| 数据源 | `api_url` 供应商端点 | 固定 `https://chat.deepseek.com/` 网页版 |
| 供应商适配 | `provider` 支持 deepseek / openai / openai-compatible / ollama；仅 `provider=="deepseek"` 注入 `thinking.type=disabled`（其他端点收到未知字段会 400）；`requires_key=False` 的 ollama 允许空 Key | 不适用 |
| 限速方式 | RPM + time.sleep 前置限流（默认 30 req/min） | 每次成功后随机休息 60-180s |
| 重试 | `_request_content` 重试额度耗尽（2^attempt）；`_call_api` 429 优先读 Retry-After（钳到 3-30s）、无头则 5/10/15s，其余 2^attempt；400/401/403/404/422 立即抛错失败；每次重试 stdout 输出 `[重试]` 行 | JSON 提取失败时发送格式纠正消息重试一次 |
| Token 统计 | API 返回 usage（含 reasoning/content 拆分） | 无（返回 None） |
| 成本估算 | 支持 dry-run（RAG/经典双模式） | 不支持 |
| 输出额度 | `max_output_tokens` 参数（默认 16384，config.env 可调） | 无限制（浏览器模式） |
| RAG 预算 | `RAG_PROMPT_CHARS`（攻略，默认 6000）/ `RAG_SYNERGY_PROMPT_CHARS`（相性，默认 6000） | `RAG_BROWSER_PROMPT_CHARS`（默认 3000） |
| 取消 | `cancel()` 置 `_cancelled`，重试循环下次循环开头退出（不打断 in-flight 请求，靠超时退出） | 无取消机制 |
| 连接健壮性 | 连接类异常（`_CONN_ERRORS`）关闭并重建 httpx.Client | 依赖页面稳定性（`_page_diagnostics()` 辅助排查选择器失效） |

---

## 五、函数清单总表

| 函数 | 文件 | 调用方（主要） | 被调用方（主要） |
|------|------|----------------|------------------|
| `main()` | `ai/batch.py` | QProcess 入口 | `load_heroes()`, `resolve_api_config()`, `get_runtime_params()`, `run_*_generation()` |
| `_load_existing_guides()` | `ai/batch.py` | `main()` | `GuideManager.load()` |
| `_load_existing_synergies()` | `ai/batch.py` | `main()` | `SynergyManager.load()` |
| `_show_cost_estimate()` | `ai/batch.py` | `main()` | `_print_mode_estimates()` -> `estimate_cost()` |
| `_print_token_summary()` | `ai/batch.py` | `main()` | `estimate_cost_by_tokens()` |
| `_check_api_key()` | `ai/batch.py` | `main()` | `PROVIDER_PRESETS.requires_key` |
| `AIBatchGenerator.__init__()` | `api_generator.py` | `batch.main()` / `refinement_service.build_generator()` | `PROVIDER_PRESETS.requires_key` Key 语义校验（不满足抛 ValueError）；`httpx.Client()`；初始化限速器与 `_cancelled` |
| `AIBatchGenerator.generate_guide()` | `api_generator.py` | `generation.py` | `load_prompt()`, `build_guide_prompt()`, `_request_content()`, `extract_json()`, `validate_guide()` |
| `AIBatchGenerator.generate_synergy()` | `api_generator.py` | `generation.py` / `scripts/run_synergy_drift.py` | `load_prompt()`, `build_synergy_prompt()`, `_request_content()`, `extract_json()`, `validate_synergy()` |
| `AIBatchGenerator._request_content()` | `api_generator.py` | `generate_guide/synergy` | `_call_api()`, `_read_completion_content()`, `_log_usage()` |
| `AIBatchGenerator._call_api()` | `api_generator.py` | `_request_content()` / `complete()` | `httpx.Client.post()`；失败走 `_retry_wait()` 退避或立即失败；连接类异常关闭并重建 client |
| `AIBatchGenerator._read_completion_content()` | `api_generator.py` | `_request_content()` | 检查 `finish_reason=="length"` |
| `AIBatchGenerator._log_usage()` | `api_generator.py` | `_request_content()` | 记录 reasoning/content 拆分 |
| `AIBatchGenerator._retry_wait()` | `api_generator.py` | `_call_api()` | 429 读 Retry-After 钳到 3-30s、无头 max(5*attempt,3)；其余 2^attempt |
| `AIBatchGenerator.complete()` | `api_generator.py` | `refinement_service` / `classification_suggest` / `scripts/propose_rule_changes.py` | `_call_api()`（公开对话补全接口，不经生成循环） |
| `AIBatchGenerator.cancel()` | `api_generator.py` | 外部取消（面板中止） | 设置 `_cancelled` 标志 |
| `PlaywrightGenerator.generate_guide()` | `browser_generator.py` | `generation.py` | `_random_rest()`, `load_prompt()`, `build_guide_prompt()`, `_send_and_wait()`, `extract_json()`, `validate_guide()` |
| `PlaywrightGenerator.generate_synergy()` | `browser_generator.py` | `generation.py` | `_random_rest()`, `load_prompt()`, `build_synergy_prompt()`, `_send_and_wait()`, `extract_json()`, `validate_synergy()` |
| `PlaywrightGenerator._random_rest()` | `browser_generator.py` | 下一次请求前 | `time.sleep(random.randint(60,180))` |
| `PlaywrightGenerator._send_and_wait()` | `browser_generator.py` | `generate_guide/synergy` | `DeepSeekBrowserSession.send_and_wait()` |
| `DeepSeekBrowserSession.send_and_wait()` | `browser_session.py` | `_send_and_wait()` | `_ensure_browser()`, `page.fill()`, `page.keyboard.press()`, 回复轮询 |
| `DeepSeekBrowserSession._ensure_browser()` | `browser_session.py` | `send_and_wait()` | `sync_playwright().start()`, `chromium.launch_persistent_context()`, `_wait_for_login()` |
| `DeepSeekBrowserSession._wait_for_login()` | `browser_session.py` | `_ensure_browser()` | `page.wait_for_selector()` |
| `DeepSeekBrowserSession._page_diagnostics()` | `browser_session.py` | 异常时 | `page.evaluate(JS)` |
| `run_guide_generation()` | `generation.py` | `batch.main()` | `generator.generate_guide()`, `_commit_generation_batch()` |
| `run_synergy_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=False, score_threshold=...)` |
| `run_synergy_pair_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=not update, score_threshold=None)` |
| `run_synergy_single_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=True, score_threshold=None)` |
| `run_synergy_list_generation()` | `generation.py` | `batch.main()` | `_run_synergy_pairs(skip_existing=not update, score_threshold=None)` |
| `_run_synergy_pairs()` | `generation.py` | 四个相性生成函数 | `generator.generate_synergy()`, `_with_synergy_updated_date()`, `_commit_generation_batch()` |
| `_commit_generation_batch()` | `generation.py` | 各生成循环 | `_save_json()` |
| `_report_rag_degradation()` | `generation.py` | 各生成循环 | `rag_prompt.take_degraded_reason()` |
| `extract_json(text)` | `json_extract.py` | `generate_guide/synergy` | `_try_extract()` ×4 策略 |
| `_try_extract(candidates)` | `json_extract.py` | `extract_json()` | `_raw_parse()`, `_repair_strings()` |
| `_repair_strings(s)` | `json_extract.py` | `_try_extract()` | 状态机修复字面换行 |
| `_raw_parse(s)` | `json_extract.py` | `_try_extract()` | `json.JSONDecoder.raw_decode()` |
| `validate_guide(raw)` | `ai/utils.py` | `generate_guide()` | `HeroGuide.model_validate()` |
| `validate_synergy(raw)` | `ai/utils.py` | `generate_synergy()` | `SynergyScore.model_validate()` |
| `has_required_guide_fields(raw)` | `ai/utils.py` | `generate_guide()` | 必填字段 + 占位符标记（"此处放入"/"放入此字段"/"保持原文不变"）/正文<200 字预检 |
| `has_required_synergy_fields(raw)` | `ai/utils.py` | `generate_synergy()` | 必填字段 + 同一组占位符标记/正文<200 字预检 |
| `convert_ids_to_int(data, fields)` | `ai/utils.py` | `generate_guide/synergy` | `int()` 类型转换 |
| `_save_json(path, data)` | `ai/utils.py` | `_commit_generation_batch()` | `json.dump()` 原子写入 |
| `build_guide_prompt(hero, rag_max_chars)` | `prompt_utils.py` | `generate_guide()` | `build_rag_context()`, `load_card_system()`, `load_core_rules()`, `_skill_lines()` |
| `build_synergy_prompt(a, b, rag_max_chars)` | `prompt_utils.py` | `generate_synergy()` | `build_synergy_rag_context()`, `load_card_system()`, `load_core_rules()`, `_skill_lines()` |
| `_skill_lines(skills, hero_id, rag, indent)` | `prompt_utils.py` | `build_*_prompt()` | 逐技能判定语料块是否已注入：已注入指针化省 token，未注入回退完整描述 + ` ｜结算：` 后缀 |
| `build_rag_context(hero, max_chars)` | `rag_prompt.py` | `build_guide_prompt()` | `_get_retriever()`, `Retriever.hero_blocks()`, `Retriever.search()`, `_format_rag_chunks()`；异常 `_mark_degraded()` |
| `build_synergy_rag_context(a, b, max_chars)` | `rag_prompt.py` | `build_synergy_prompt()` | `_get_retriever()`, `Retriever.hero_blocks()` ×2, `Retriever.search()` ×2, `_format_rag_chunks()`；异常 `_mark_degraded()` |
| `_format_rag_chunks(blocks, extra, budget, core_ratio)` | `rag_prompt.py` | `build_*_rag_context()` | 官方/社区独立预算池（官方未用滚给社区），combo 优先；`staleness_reason` 块加 `⚠️ 过时风险` 前缀 |
| `is_rag_enabled()` | `rag_prompt.py` | `main()`, `build_*_prompt()`, `build_*_rag_context()` | 环境变量 `RAG_ENABLED` 优先，其次 config |
| `take_degraded_reason()` | `rag_prompt.py` | `_report_rag_degradation()` | 取出并清空降级原因 |
| `_mark_degraded(reason)` | `rag_prompt.py` | `build_*_rag_context()` 异常分支 | 记录本次进程的 RAG 降级原因 |
| `load_card_system()` | `rule_summary.py` | `build_*_prompt()` | 加载"卡牌体系"段（RAG 兜底防串味） |
| `load_core_rules()` | `rule_summary.py` | `build_*_prompt()` | 加载完整核心规则摘要（无 RAG 兜底） |
| `estimate_cost(count, mode, model, use_rag)` | `prompt_utils.py` | `batch.main()` / `src.business.ai_cost`（UI 层） | `estimate_item_cost()` -> `get_model_pricing()` |
| `estimate_item_cost(item_count, mode, model, use_rag)` | `prompt_utils.py` | `estimate_cost()` / `src.business.ai_cost` | `get_model_pricing()`（RAG/经典双模式输入 token 不同） |
| `estimate_cost_by_tokens(input, output, model)` | `prompt_utils.py` | `_print_token_summary()` | `get_model_pricing()` |

---
