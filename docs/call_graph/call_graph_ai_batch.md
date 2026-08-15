# 调用链路：AI 批量生成

> 对应源码：`src/scraper/ai/`
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 所有链路均在 QProcess 子进程中执行，不阻塞 UI 主线程。

---

## 当前实现基线（2026-07-22）

AI 生成按批原子提交正式 JSON：每批校验成功结果立即提交，任一任务失败时仅保留对应旧数据并以退出码 `1` 结束。
攻略与相性默认启用 RAG 官方规则语料注入（`src/scraper/ai/rag_prompt.py`），CLI 支持 `--no-rag` 关闭；RAG 运行时异常自动降级为经典模式，并在生成循环输出一次 `[RAG]` 提示。

```
ai_batch.main()
  -> load_heroes() + get_api_config()
  -> AIBatchGenerator(...) 或 PlaywrightGenerator(...)
  -> run_guide_generation() / run_synergy_generation()
     / run_synergy_pair_generation() / run_synergy_single_generation()
    -> generate_*() -> validate_*() -> 达到批量阈值时原子写入正式 JSON
  -> GenerationResult.succeeded ?
    -> 是：exit 0
    -> 否：已成功批次保留，失败项维持旧数据，exit 1
```

| 参数 | 编排函数 | 范围 |
|------|----------|------|
| `--guide` | `run_guide_generation()` | 攻略 |
| `--synergy` | `run_synergy_generation()` | 全量两两相性 |
| `--synergy-pair` | `run_synergy_pair_generation()` | 2-8 名指定英雄组合 |
| `--synergy-single` | `run_synergy_single_generation()` | 指定英雄对全体 |

## 一、CLI 入口总览

### 1.1 AI 批量生成入口

```
ai_batch.py -> ai/batch.py:main()                             [兼容 CLI -> 实际入口]
  -> argparse.parse_args()                                    [解析命令行参数]
  -> [args.no_rag] os.environ["RAG_ENABLED"]="false"            [禁用 RAG 增强]
     [否则] 检查 _rag_enabled()，被选择但配置禁用时输出 [RAG] 提示
  -> setup_logging()                                          [初始化日志]
  -> load_heroes(args.heroes_file)                            [HeroManager 完整校验武将 JSON]
  -> get_api_config()                                         [从 config.env 读取 API 配置]
  -> get_runtime_params()                                     [获取运行时参数]
  -> [args.dry_run] _show_cost_estimate()                     [预览 Token/费用]
  -> _check_api_key()                                          [空 key 直接 exit]

  -> 生成器创建:
     [args.browser]
       -> PlaywrightGenerator(browser_cfg, chat_cfg)
     [default]
       -> AIBatchGenerator(api_key, api_url, model, rpm, retries, timeout)

  -> 模式分发:
     [args.guide]
       -> _load_existing_guides(guide_path)                   [读取已有攻略]
       -> ai_generation.run_guide_generation(heroes, gen, path, existing, api_cfg, update)
     [args.synergy]
       -> _load_existing_synergies(synergy_path)              [读取已有相性]
       -> ai_generation.run_synergy_generation(heroes, gen, path, ...)
     [args.synergy_pair]
       -> json.load(pair_file)                                [读取指定配对 JSON]
       -> ai_generation.run_synergy_pair_generation(pair_file, heroes, gen, ...)
     [args.synergy_single]
       -> json.load(single_file)                              [读取选定武将 JSON]
       -> ai_generation.run_synergy_single_generation(single_file, heroes, gen, ...)

  -> _print_token_summary(prompt_tokens, completion_tokens)   [汇总 Token 用量]
  -> generator.close()                                        [释放资源]
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `main()` | `ai/batch.py` | QProcess 子进程入口 | `load_heroes()`, `_load_existing_*()`, `run_*_generation()` |
| `load_heroes()` | `utils.py` | `ai_batch.main()` | `HeroManager.load()`, `Hero.model_validate()` |
| `get_api_config()` | `config/env.py` | `ai_batch.main()` | `parse_env_file()` + 类型转换 |
| `_load_existing_guides()` | `ai/batch.py` | `main()` | `GuideManager.load()`；错误文件备份后写回有效记录 |
| `_load_existing_synergies()` | `ai/batch.py` | `main()` | `SynergyManager.load()`；错误文件备份后写回有效记录 |
| `_show_cost_estimate()` | `ai/batch.py` | `main()` | `estimate_cost(..., use_rag)` 分别输出 RAG 增强 / 经典模式 |
| `_print_token_summary()` | `ai/batch.py` | `main()` | `_estimate_cost()` |

---

## 二、攻略生成链路

### 2.1 全量/增量攻略生成

```
generation.run_guide_generation(heroes, generator, guide_path, existing_guides, api_cfg, update)
  -> [update=True] 删除旧数据后重新生成全部
     [update=False] 跳过已有 guide_id（断点续传）
  -> [遍历每个 hero]
     -> generator.generate_guide(hero)                        [调用 AI]
        -> load_prompt(GUIDE_PROMPT_FILE)                     [读取系统提示词]
        -> build_guide_prompt(hero)                           [构建用户提示词]
           -> build_rag_context(hero)                      [RAG 注入: Retriever.hero_blocks + search(heroes 过滤)]
        -> self._call_api(messages)                           [API 模式]
           -> time.sleep(限速)                                 [RPM 控制]
           -> POST /v1/chat/completions                       [thinking.type=disabled, max_tokens=16384]
           -> 仅保留 content / finish_reason / usage          [丢弃思考内容]
           -> [content 为空或 length] 输出额度耗尽错误         [透传 UI]
           -> [失败] 指数退避重试: 2s/4s/8s, 最多 max_retries 次
        -> extract_json(response_text)                        [从 AI 回复提取 JSON]
           -> _try_extract(text, [0])                         [策略 1: 全文解析]
              -> json.JSONDecoder().raw_decode(text)
           -> [失败] _try_extract(text, [1])                  [策略 2: ```json 块]
              -> re.search(r"```(?:json)?\s*\n(.+?)\n```")
           -> [失败] _try_extract(text, [2])                  [策略 3: --- 分隔线后]
              -> text[text.rfind("---"):]
           -> [失败] _try_extract(text, [3])                  [策略 4: 首 { 到尾 }]
              -> text[text.find("{"):text.rfind("}")+1]
        -> convert_ids_to_int(raw, ["synergizes_with"])       [搭配 ID 转为 int]
        -> validate_guide(raw)                                [Pydantic 校验]
           -> HeroGuide.model_validate(raw) -> model_dump()
        -> return (guide_dict, usage_dict)                    [usage 仅 API 模式有]
     -> _report_rag_degradation()                        [RAG 降级时输出一次 [RAG] 提示]
     -> [batch save] _save_json(guide_path, batch_data)       [每 GUIDE_BATCH_SAVE_INTERVAL=10 条]
  -> [最终保存] _save_json(guide_path, all_data)
  -> return (total_prompt_tokens, total_completion_tokens)
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `run_guide_generation()` | `generation.py` | `ai_batch.main()` | `generator.generate_guide()`, `_save_json()` |
| `AIBatchGenerator.generate_guide()` | `api_generator.py` | `generation.py` | `load_prompt()`, `build_guide_prompt()`, `_call_api()` |
| `AIBatchGenerator._call_api()` | `api_generator.py` | `generate_guide()`, `generate_synergy()` | `httpx.Client.post()` |
| `extract_json()` | `json_extract.py` | `generate_guide()`, `generate_synergy()` | `_try_extract()` ×4 |
| `_try_extract()` | `json_extract.py` | `extract_json()` | `_raw_parse()`, `_repair_strings()` |
| `_raw_parse()` | `json_extract.py` | `_try_extract()` | `json.JSONDecoder.raw_decode()` |
| `_repair_strings()` | `json_extract.py` | `_try_extract()` | 状态机修复字面换行 |
| `validate_guide()` | `utils.py` | `generate_guide()` | `HeroGuide.model_validate()` |
| `convert_ids_to_int()` | `utils.py` | `generate_guide()` | `int()` 类型转换 |
| `_save_json()` | `utils.py` | `run_*_generation()` | `json.dump()`, 原子写入 |
| `load_prompt()` | `prompt_utils.py` | `generate_guide()`, `generate_synergy()` | 文件读取 |

### 2.2 浏览器模式攻略生成

```
PlaywrightGenerator.generate_guide(hero)
  -> load_prompt(GUIDE_PROMPT_FILE)                           [读取系统提示词]
  -> build_guide_prompt(hero)                                 [构建用户提示词]
  -> [首次] 发送 system_prompt + user_prompt 拼接全文
     [后续] 仅发送 user_prompt（保留对话上下文）
  -> self._send_and_wait(full_prompt)                         [发送并等待回复]
     -> DeepSeekBrowserSession.send_and_wait()                [页面会话边界]
     -> _ensure_browser()                                     [懒加载浏览器]
        -> sync_playwright().start()                          [启动 Playwright]
        -> chromium.launch_persistent_context()               [复用 Edge 用户数据]
        -> page.goto("https://chat.deepseek.com")             [导航到 DeepSeek]
        -> _wait_for_login()                                  [等待登录完成]
           -> page.wait_for_selector(input_selector, timeout)
     -> page.fill(input_selector, prompt)                     [填充输入框]
     -> page.keyboard.press("Enter")                          [发送]
     -> page.wait_for_timeout(5000)                           [等待回复生成]
     -> [轮询] page.locator(last_message).inner_text()        [读取回复]
     -> [3 轮稳定检测] 同一长度连续 3 次 → 完成
     -> [超时] _page_diagnostics()
  -> extract_json(response_text)                              [同 API 模式]
  -> validate_guide(raw)                                      [Pydantic 校验]
  -> [下一次请求前] self._random_rest()                       [上次成功后随机休息 60-180s]
     -> time.sleep(random.randint(60, 180))
  -> return (guide_dict, None)                                [浏览器模式无语费统计]
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `PlaywrightGenerator.generate_guide()` | `browser_generator.py` | `generation.py` | `load_prompt()`, `build_guide_prompt()`, `_send_and_wait()` |
| `PlaywrightGenerator._send_and_wait()` | `browser_generator.py` | `generate_guide()`, `generate_synergy()` | `DeepSeekBrowserSession.send_and_wait()` |
| `DeepSeekBrowserSession.send_and_wait()` | `browser_session.py` | `PlaywrightGenerator._send_and_wait()` | `_ensure_browser()`, `page.fill()`, 流式回复轮询 |
| `DeepSeekBrowserSession._ensure_browser()` | `browser_session.py` | `send_and_wait()` | `sync_playwright().start()`, `_wait_for_login()` |
| `DeepSeekBrowserSession._wait_for_login()` | `browser_session.py` | `_ensure_browser()` | `page.wait_for_selector()` |
| `DeepSeekBrowserSession._page_diagnostics()` | `browser_session.py` | 登录或收发异常 | `page.evaluate(JS dump)` |
| `PlaywrightGenerator._random_rest()` | `browser_generator.py` | `generate_guide()`, `generate_synergy()` | `time.sleep(random)` |

---

## 三、相性生成链路

### 3.1 全量相性生成

```
ai_generation.run_synergy_generation(heroes, generator, synergy_path, existing, keys, threshold, api_cfg)
  -> [清空旧数据]
  -> heroes_len = len(heroes)
  -> total_pairs = heroes_len * (heroes_len - 1) // 2
  -> [遍历所有 C(N,2) 组合]
     -> for idx, (ha, hb) in enumerate(combinations(heroes, 2), start=1):
        -> generator.generate_synergy(ha, hb)
           -> load_prompt(SYNERGY_PROMPT_FILE)
           -> build_synergy_prompt(ha, hb)
              -> build_synergy_rag_context(ha, hb)    [RAG 注入: 双方 hero_blocks + 跨类 search(机制词查询, 过滤非目标武将块)]
           -> self._call_api(messages)
           -> extract_json(response_text)
           -> validate_synergy(raw)
              -> SynergyScore.model_validate(raw) -> model_dump()
           -> return (synergy_dict, usage_dict)
        -> [score >= threshold] 保留; [score < threshold] 丢弃
        -> _report_rag_degradation()                        [RAG 降级时输出一次 [RAG] 提示]
        -> [batch commit] _save_json 每 10 对校验成功结果
  -> [最终保存]
  -> return (total_prompt_tokens, total_completion_tokens)
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `run_synergy_generation()` | `generation.py` | `ai_batch.main()` | `generator.generate_synergy()`, `itertools.combinations()`, `_save_json()` |
| `AIBatchGenerator.generate_synergy()` | `api_generator.py` | `generation.py` | `load_prompt()`, `build_synergy_prompt()`, `_call_api()` |
| `validate_synergy()` | `utils.py` | `generate_synergy()` | `SynergyScore.model_validate()` |

### 3.2 指定配对相性生成

```
ai_generation.run_synergy_pair_generation(pair_file, heroes, generator, synergy_path, existing, keys)
  -> json.load(pair_file)  [读取 2-8 个武将的 JSON]
  -> pair_heroes = [h for h in heroes if h["id"] in pair_ids]
  -> for idx, (ha, hb) in enumerate(combinations(pair_heroes, 2), start=1):
     -> generator.generate_synergy(ha, hb)
     -> [成功] 删除旧配对数据 + 追加新数据 + _save_json
     -> [失败] 打印 FAIL，不阻断
```

| 函数 | 所在文件 | 调用方 | 说明 |
|------|----------|--------|------|
| `run_synergy_pair_generation()` | `generation.py` | `ai_batch.main()` | `combinations()` 遍历 + 逐对保存 |
| `run_synergy_single_generation()` | `generation.py` | `ai_batch.main()` | 选定武将 vs 全体，跳过已有配对 |

---

## 四、外部调用关系总览

### 4.1 本模块被外部调用

```
src.business.fetching.guide_fetch_service
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--guide", ...])
     [经典模式 use_rag=False] -> 参数追加 --no-rag

src.business.fetching.synergy_fetch_service
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--synergy-pair", tmp_file])
  -> QProcess.start(["-m", "src.scraper.ai_batch", "--synergy-single", tmp_file])
     [经典模式 use_rag=False] -> 参数追加 --no-rag

src.ui.app.main_window
  -> 菜单 → GuideFetchService/SynergyFetchService    [间接调用]
```

### 4.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.models.HeroGuide` | Pydantic 校验攻略 |
| `src.data.models.SynergyScore` | Pydantic 校验相性 |
| `src.config.env.get_api_config()` | 读取 API Key/URL/Model |
| `src.config.env.get_runtime_params()` | 读取运行时参数 |
| `src.config.logging_config.setup_logging()` | 日志初始化 |
| `docs/prompts/hero_guide.md` | 攻略生成提示词文件 |
| `docs/prompts/synergy.md` | 相性生成提示词文件 |
| `src.rag`（config/indexer/retriever） | RAG 语料加载、ChromaDB 向量检索与关键词 RRF |

### 4.3 双生成器对比

| 对比项 | AIBatchGenerator | PlaywrightGenerator |
|--------|-----------------|-------------------|
| 限速方式 | RPM + time.sleep 前置限流 | 每次成功生成后，在下一次请求前随机休息 60-180s |
| 重试 | 指数退避 2s/4s/8s, 3 次 | 无重试 |
| Token 统计 | API 返回 usage | 无（返回 None） |
| 成本估算 | 支持 dry-run | 不支持 |
| 生成成功率 | 高（自动重试） | 依赖页面稳定性 |

---

## 五、函数清单总表

| 函数 | 文件 | 调用方（主要） | 被调用方（主要） |
|------|------|----------------|------------------|
| `main()` | `ai/batch.py` | QProcess 入口 | `load_heroes()`, `run_*_generation()` |
| `AIBatchGenerator.__init__()` | `api_generator.py` | `ai_batch.main()` | `httpx.Client()` |
| `AIBatchGenerator.generate_guide()` | `api_generator.py` | `generation.py` | `_call_api()`, `extract_json()`, `validate_guide()` |
| `AIBatchGenerator.generate_synergy()` | `api_generator.py` | `generation.py` | `_call_api()`, `extract_json()`, `validate_synergy()` |
| `AIBatchGenerator._call_api()` | `api_generator.py` | `generate_guide/synergy` | `time.sleep()`, `httpx.Client.post()` |
| `PlaywrightGenerator.generate_guide()` | `browser_generator.py` | `generation.py` | `_send_and_wait()`, `extract_json()` |
| `PlaywrightGenerator.generate_synergy()` | `browser_generator.py` | `generation.py` | `_send_and_wait()`, `extract_json()` |
| `PlaywrightGenerator._random_rest()` | `browser_generator.py` | 下一次浏览器请求前 | 随机等待 60-180 秒 |
| `DeepSeekBrowserSession._ensure_browser()` | `browser_session.py` | `send_and_wait()` | `Playwright.start()` |
| `PlaywrightGenerator._send_and_wait()` | `browser_generator.py` | `generate_guide/synergy` | `DeepSeekBrowserSession.send_and_wait()` |
| `run_guide_generation()` | `generation.py` | `ai_batch.main()` | `generator.generate_guide()`, `_save_json()` |
| `run_synergy_generation()` | `generation.py` | `ai_batch.main()` | `combinations()`, `generator.generate_synergy()` |
| `run_synergy_pair_generation()` | `generation.py` | `ai_batch.main()` | `combinations()`, 逐对保存 |
| `run_synergy_single_generation()` | `generation.py` | `ai_batch.main()` | 跳过已有项 + generate + 保存 |
| `extract_json(text)` | `json_extract.py` | `generate_guide/synergy` | `_try_extract()` ×4 |
| `_try_extract(text, strategy)` | `json_extract.py` | `extract_json()` | `_raw_parse()`, `_repair_strings()` |
| `_repair_strings(s)` | `json_extract.py` | `_try_extract()` | 状态机跟踪 in_string |
| `validate_guide(raw)` | `utils.py` | `generate_guide()` | `HeroGuide.model_validate()` |
| `validate_synergy(raw)` | `utils.py` | `generate_synergy()` | `SynergyScore.model_validate()` |
| `build_guide_prompt(hero)` | `prompt_utils.py` | `generate_guide()` | 格式化提示词 |
| `build_synergy_prompt(a, b)` | `prompt_utils.py` | `generate_synergy()` | 格式化提示词 |
| `estimate_cost(count, mode)` | `prompt_utils.py` | `batch.main()`, UI 层 | Token/费用估算 |
| `_save_json(path, data)` | `utils.py` | `run_*_generation()` | `json.dump()`, 原子写入 |
