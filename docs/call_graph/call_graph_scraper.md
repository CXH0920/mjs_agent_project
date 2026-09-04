# 调用链路：爬虫与数据采集

> 对应源码：`src/scraper/official_source/` + 根 CLI 入口
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 全量 / 增量 CLI 入口在 QProcess 子进程中执行，不阻塞 UI 主线程；公告检查在 threading 后台线程执行。

---

## 当前实现基线（2026-09-04）

```
official.py:main()      ← shim, 转调 official_source.full.main()
incremental.py:main()   ← shim, 转调 official_source.incremental.main()

full.crawl() / incremental.main()
  -> crawler.fetch(BAIKE_URL)                 [HTTP GET 首页 HTML, 可重试]
  -> adapter.find_chunk_url(html)             [正则 /_nuxt/mjbk.[a-f0-9]+\.js；失败带改版诊断]
  -> crawler.fetch(chunk_url)                 [HTTP GET JS chunk, ~300KB]
  -> adapter.parse_heroes_chunk(js_text)      [JS chunk 解析]
     -> adapter.extract_js_array()            [字符级状态机, 识别引号/转义]
     -> adapter.js_to_json()
         -> adapter._to_json_text()           [字符级状态机: 键引号/:undefined/null/尾逗号]
         -> json.loads()
  -> crawler.transform(raw) ×N                [字段清洗与映射]
  -> crawler.validate_heroes(list)            [Pydantic 批量校验]
  -> crawler.save_json_atomic(path, data)     [原子写入]
  -> crawler.download_hero_images(raw_list)   [安全头像下载, 白名单/熔断/间隔]
```

官网格式解析集中在 `official_source/adapter.py`。旧版 `extract_js_array()` + 三步正则 `js_to_json()` 已改为**字符级状态机**：`extract_js_array()` 用 `depth/quote/escaped` 忽略字符串内方括号；`js_to_json()` 委托给 `_to_json_text()` 状态机完成键加引号、`:undefined→null`、尾逗号移除，只在字符串字面量之外执行——避免把技能描述里的 `"效果{x:1}"`、`,变化:无` 等误改写。`find_chunk_url()` 与 `extract_js_array()` 均携带 HTML/JS 开头 300 字符作为改版诊断日志。全量和增量入口均通过 `save_json_atomic()` 原子写入，并对每条原始记录只调用一次 `transform()`。

---

## 一、全量采集链路

### 1.1 完整调用链

```
UI 菜单触发:
MainWindow._request_fetch_all()
  -> QMessageBox.question()                                   [用户确认]
  -> HeroFetchService.fetch_all()
    -> _is_busy()                                              [并发检查]
    -> _start_process(["-m", "src.scraper.official"])
      -> QProcess.start(sys.executable, args)                  [启动子进程]
         ─────────────────────────────────────────────────────────
         [子进程] official.py:main()
           [ shim: from src.scraper.official_source.full import main ]
         [子进程] official_source.full.main()
           -> argparse.parse_args()                             [--dry-run / --output / --skip-images / --verbose]
           -> setup_logging(log_level, log_to_file)
           -> full.crawl(dry_run, output_path, skip_images)
              -> [1/5] crawler.fetch(BAIKE_URL)                 [HTTP GET 首页 HTML, ~50KB]
                 -> urllib.request.Request(url, HEADERS)
                 -> urllib.request.urlopen(req, timeout=30)
                 -> 重试: retry ×3, interval=2s
                 -> HTTP 400/401/403/404 → 立即 raise（不可重试）
              -> adapter.find_chunk_url(html)                   [正则提取 chunk 路径]
                 -> CHUNK_URL_PATTERN.search(html)
                 -> [未命中] _NUXT_SCRIPT_PATTERN.finditer(html) → hints
                 -> logger.error + raise RuntimeError（含页面开头 300 字符）
              -> crawler.fetch(BASE_URL + chunk_url)            [HTTP GET JS chunk, ~300KB, 同重试策略]
              -> [2/5] adapter.parse_heroes_chunk(js_text)
                 -> adapter.extract_js_array(js_text)           [字符级状态机]
                    -> js_text.find("const e=[")
                    -> [未命中] logger.error + raise RuntimeError（含 JS 开头 300 字符）
                    -> 遍历字符: quote / escaped / depth 三段状态
                       引号内 char → 跳过 depth 计数
                       遇到 " / ' / ` → 进入 quote 态
                       遇到 [ → depth++，] → depth--
                       depth == 0 且 quote 空 → 返回子串
                    -> [未闭合] raise RuntimeError("JS 数组未闭合")
                 -> adapter.js_to_json(array_text)
                    -> adapter._to_json_text(text)              [字符级状态机, 三步预处理]
                       引号内 → 原样输出（识别 \\ 转义）
                       : + _UNDEFINED_VALUE_RE → 输出 "null"
                       , + _TRAILING_COMMA_RE → 丢弃
                       { 或 , 后 _KEY_POSITION_RE → 标识符键补双引号
                    -> json.loads(out)
              -> [3/5] [逐项] crawler.transform(raw)
                 -> clean_html(raw["name"])                     [去标签 / unescape / 归一化空白]
                 -> clean_html(raw["dynasty"])                  [势力字段]
                 -> clean_html(raw["p_positioning"])            [定位字段]
                 -> GENDER_MAP.get(raw["gender"], "男")         [1/2 映射, 默认男]
                 -> int(raw["p_blood_max"]) / int(raw["p_card_max"])  [失败默认 4]
                 -> [遍历 raw["skill"]]
                    -> clean_html(sk["skill_name"])
                    -> split_skill_desc(sk["skill_desc"])       [按 </p> 拆分 + 段落标题匹配]
                       -> re.sub: 合并相邻 <strong>
                       -> section_pattern.search(line + "</p>") [7 类标题]
                       -> clean_html(rest)
                    -> {"name", "description", "settlement"}
                 -> 缺 id/name → return None
              -> [4/5] [逐项] 收集 → 统计势力分布打印
              -> [5/5] crawler.validate_heroes(transformed)     [Pydantic 校验]
                 -> Hero.model_validate(h) ×N
                 -> h.model_dump(mode="json")
                 -> [失败] logger.error + logger.info，不阻断
              -> dry_run 分支：打印前 5 条，不写文件
              -> crawler.save_json_atomic(out_path, validated) [原子写入]
                 -> tmp_path = path.with_suffix(".tmp")
                 -> json.dump(data, tmp, ensure_ascii=False, indent=2)
                 -> tmp_path.replace(path)
              -> [不 skip-images] crawler.download_hero_images(raw_list)
                 -> [遍历 raw_list]
                    -> _safe_image_name(raw["name"])            [NFC + 白名单字符 + Windows 保留名]
                    -> out_dir / f"{name}.png"
                    -> dest.resolve().is_relative_to(out_dir.resolve())  [路径逃逸防护]
                    -> if skip_existing and dest.exists(): continue
                    -> _download_hero_image(icon_url, dest)
                       -> _open_image_response(icon_url)        [禁用重定向, 逐跳白名单]
                          -> urllib.request.build_opener(_NoRedirectHandler())
                          -> for _ in range(MAX_IMAGE_REDIRECTS + 1):
                             -> _validate_image_url(current_url)  [HTTPS + ALLOWED_IMAGE_HOSTS + 无凭证]
                             -> opener.open(request, timeout=30)
                             -> [301/302/303/307/308] urljoin(current_url, Location) 继续
                             -> _validate_image_url(response.geturl())
                       -> [Content-Type != image/png] raise
                       -> [Content-Length > 5MB] raise
                       -> tempfile.NamedTemporaryFile(..., delete=False)
                       -> response.read(64KB) 分块写入
                       -> _validate_image_file(temp_path)       [Image.verify + format=PNG + 像素上限]
                       -> temp_path.replace(dest)
                       -> [异常] temp_path.unlink(missing_ok=True)
                    -> [连续 5 次失败] break（熔断）
                    -> time.sleep(0.5s)                        [逐张间隔]
              -> [异常] logger.exception + sys.exit(1)
           -> [子进程结束]
         ─────────────────────────────────────────────────────────
           QProcess.finished 信号触发
    -> HeroFetchService._on_finished(exit_code)
      -> [exit_code == 0] emit fetch_completed(True)
      -> [exit_code != 0] emit fetch_completed(False)
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `MainWindow._request_fetch_all()` | `main_window.py` | 菜单 QAction | `HeroFetchService.fetch_all()` |
| `HeroFetchService.fetch_all()` | `fetch_service.py` | `_request_fetch_all()` | `_start_process()` |
| `HeroFetchService._start_process()` | `fetch_service.py` | `fetch_all()` | `QProcess.start()` |
| `official.main()` | `official.py` | QProcess 子进程 | `official_source.full.main()`（shim） |
| `official_source.full.main()` | `full.py` | 子进程入口 | `argparse`, `setup_logging`, `full.crawl()` |
| `full.crawl()` | `full.py` | `full.main()` | `fetch()`, `find_chunk_url()`, `parse_heroes_chunk()`, `transform()`, `validate_heroes()`, `save_json_atomic()`, `download_hero_images()` |
| `crawler.fetch(url, binary)` | `crawler.py` | `crawl()`, `fetch_all_raw()` | `urllib.request.urlopen()`，400-404 立即 raise |
| `adapter.find_chunk_url(html)` | `adapter.py` | `crawl()`, `fetch_all_raw()` | `CHUNK_URL_PATTERN.search()`, 改版诊断 |
| `adapter.parse_heroes_chunk(js)` | `adapter.py` | `crawl()`, `fetch_all_raw()` | `extract_js_array()`, `js_to_json()` |
| `adapter.extract_js_array(js)` | `adapter.py` | `parse_heroes_chunk()` | 字符级状态机（depth/quote/escaped） |
| `adapter.js_to_json(text)` | `adapter.py` | `parse_heroes_chunk()` | `_to_json_text()`, `json.loads()` |
| `adapter._to_json_text(text)` | `adapter.py` | `js_to_json()` | 字符级状态机（键引号/:undefined/null/尾逗号） |
| `crawler.transform(raw)` | `crawler.py` | `crawl()`, `run()`, `fetch_baike_heroes()` | `clean_html()`, `split_skill_desc()` |
| `crawler.split_skill_desc(desc)` | `crawler.py` | `transform()` | `clean_html()`，`</p>` 拆分 + 段落标题匹配 |
| `crawler.clean_html(html)` | `crawler.py` | `transform()`, `split_skill_desc()` | `re.sub()`, `html.unescape()` |
| `crawler.validate_heroes(list)` | `crawler.py` | `crawl()`, `run()`, `fetch_baike_heroes()` | `Hero.model_validate()` |
| `crawler.save_json_atomic(path, data)` | `crawler.py` | `crawl()`, `run()` | `json.dump()`, `Path.replace()` |
| `crawler.download_hero_images(raw_list)` | `crawler.py` | `crawl()`, `run()` | `_safe_image_name()`, `_download_hero_image()` |
| `crawler._safe_image_name(name)` | `crawler.py` | `download_hero_images()` | `clean_html()`, `unicodedata.normalize()`, 白名单/保留名 |
| `crawler._download_hero_image(url, dest)` | `crawler.py` | `download_hero_images()` | `_open_image_response()`, `_validate_image_file()` |
| `crawler._open_image_response(url)` | `crawler.py` | `_download_hero_image()` | `_NoRedirectHandler`, `_validate_image_url()`, `urljoin()` |
| `crawler._validate_image_url(url)` | `crawler.py` | `_open_image_response()` | `urlparse()`，HTTPS 域名白名单 |
| `crawler._validate_image_file(path)` | `crawler.py` | `_download_hero_image()` | `PIL.Image.verify()`，PNG 格式/像素上限 |

---

## 二、增量采集链路

### 2.1 调用链

```
UI 菜单触发:
MainWindow._request_fetch_incremental()
  -> QMessageBox.question()                                   [用户确认]
  -> HeroFetchService.fetch_incremental()
    -> _start_process(["-m", "src.scraper.incremental", "--incremental"])
      -> QProcess.start(sys.executable, args)
         ─────────────────────────────────────────────────────────
         [子进程] incremental.py:main()
           [ shim: from src.scraper.official_source.incremental import main ]
         [子进程] official_source.incremental.main()
           -> argparse.parse_args()                             [--incremental / --hero / --hero-id / --output / --dry-run / --skip-images / --verbose]
           -> [三者均未指定] parser.error
           -> setup_logging(...)
           -> all_raw = crawler.fetch_all_raw()                 [复用全量获取管道]
              -> fetch(BAIKE_URL) -> find_chunk_url -> fetch(chunk_url)
              -> parse_heroes_chunk -> extract_js_array -> js_to_json
           -> target_raw = list(all_raw)

           -> [if args.incremental]
              -> load_existing_ids(output_path)                 [读本地 JSON, 抽 {id} set]
                 -> _load_heroes_file(path)                     [损坏则备份为 .corrupt-{ts} 后返回 None]
                    -> json.load(f)
                    -> [OSError/JSONDecodeError] path.replace(backup_path) + logger.error
                 -> {h["id"] for h in heroes}
              -> target_raw = incremental_collect(all_raw, existing_ids)

           -> [if args.hero]
              -> names = [n.strip() for n in args.hero.split(",")]
              -> filtered = filter_by_names(all_raw, names)     [双向子串匹配]
              -> [if args.incremental] filtered ∩ target_ids
              -> target_raw = filtered

           -> [if args.hero_id]
              -> ids = {int(hid) for hid in args.hero_id.split(",")}
              -> filtered = filter_by_ids(all_raw, ids)         [ID 精确匹配]
              -> [if args.incremental] filtered ∩ target_ids
              -> target_raw = filtered

           -> [if not target_raw] 打印并 return

           -> replace_ids = None
           -> [(args.hero or args.hero_id) and not args.incremental]
              -> replace_ids = {r["id"] for r in target_raw}

           -> run(target_raw, output_path, dry_run,
                  append=args.incremental, replace_ids=replace_ids,
                  skip_images=args.skip_images)
              -> [逐项] crawler.transform(raw)                  [数据清洗]
              -> crawler.validate_heroes(transformed)           [Pydantic 校验]
              -> dry_run: 打印前 5 条并 return
              -> [写入]
                 -> [not output_path.exists()] merged = validated
                 -> [replace_ids is not None]
                    -> _load_heroes_file(output_path)          [本地已有]
                    -> existing = [h for h in existing if h["id"] not in replace_ids]
                    -> merged = existing + validated
                 -> [append=True]
                    -> _load_heroes_file(output_path)
                    -> merged = existing + [h for h in validated if h["id"] not in existing_ids]
                 -> [else] merged = validated
              -> crawler.save_json_atomic(output_path, merged)  [原子写入]
              -> [不 dry_run 且不 skip-images] crawler.download_hero_images(raw_list)
         ─────────────────────────────────────────────────────────
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `MainWindow._request_fetch_incremental()` | `main_window.py` | 菜单 QAction | `HeroFetchService.fetch_incremental()` |
| `HeroFetchService.fetch_incremental()` | `fetch_service.py` | `_request_fetch_incremental()` | `_start_process()` |
| `incremental.main()` | `official_source/incremental.py` | QProcess 子进程 | `fetch_all_raw()`, 筛选函数, `run()` |
| `crawler.fetch_all_raw()` | `crawler.py` | `incremental.main()` | `fetch()`, `find_chunk_url()`, `parse_heroes_chunk()` |
| `load_existing_ids(path)` | `official_source/incremental.py` | `incremental.main()` | `_load_heroes_file()` |
| `load_existing_names(path)` | `official_source/incremental.py` | 外部（公告服务） | `_load_heroes_file()` |
| `_load_heroes_file(path)` | `official_source/incremental.py` | `load_existing_ids()`, `load_existing_names()`, `run()` | `json.load()`，损坏时 `path.replace()` 备份 |
| `incremental_collect(raw_list, existing_ids)` | `official_source/incremental.py` | `incremental.main()` | 列表推导式差集 |
| `filter_by_names(raw_list, names)` | `official_source/incremental.py` | `incremental.main()` | 双向子串匹配 |
| `filter_by_ids(raw_list, ids)` | `official_source/incremental.py` | `incremental.main()` | ID set 交集 |
| `run(raw_list, output_path, dry_run, append, replace_ids, skip_images)` | `official_source/incremental.py` | `incremental.main()` | `transform()`, `validate_heroes()`, `save_json_atomic()`, `download_hero_images()`, `_load_heroes_file()` |

---

## 三、JS chunk 字符级状态机详解

这是爬虫模块最核心的转换管道，将官网 SPA 中的 JS 数据转换为可用 JSON。

### 3.1 管道流程

```
fetch(BAIKE_URL)
  ↓ 返回 HTML（~50KB）
adapter.find_chunk_url(html)
  ↓ 正则 /_nuxt/mjbk.[a-f0-9]+.js；失败则 _NUXT_SCRIPT_PATTERN 收集现场并 raise
fetch(chunk_url)
  ↓ 返回 JS 文本（~300KB）
adapter.extract_js_array(js_text)
  ↓ 字符级状态机: 找 "const e=[" → 遍历字符（depth/quote/escaped） → 返回数组子串
adapter.js_to_json(array_text)
  ↓ 字符级状态机 _to_json_text: 键引号 → :undefined→null → 尾逗号移除
  ↓ 关键约束：仅在字符串字面量之外执行改写，技能描述内的 "效果{x:1}" 不被误改
json.loads(out)
  ↓
Python list[dict]
  ↓
[逐项] transform()
  ↓
Pydantic 校验后的 Hero 列表
```

### 3.2 各步骤函数详情

| 函数 | 关键逻辑 | 失败处理 |
|------|----------|----------|
| `find_chunk_url()` | 正则 `CHUNK_URL_PATTERN.search(html)` | 未匹配 → 收集 `_NUXT_SCRIPT_PATTERN` 现场 + 页面开头 300 字符 → `RuntimeError` |
| `extract_js_array()` | 找 `const e=[` 起点 → 字符级状态机（`quote/escaped/depth` 三段，识别 `"` `'` `` ` `` 三种引号，忽略字符串内方括号） | 找不到起点 → 带前缀 `RuntimeError`；数组未闭合 → `RuntimeError("JS 数组未闭合")` |
| `js_to_json()` | 委托 `_to_json_text(text)` 字符级状态机三步预处理后 `json.loads()` | JSON 解析失败 → `ValueError`（来自 json） |
| `_to_json_text()` | 引号态原样输出；`:` 后 `_UNDEFINED_VALUE_RE` → `null`；`,` 后 `_TRAILING_COMMA_RE` → 丢弃；`{`/`,` 后 `_KEY_POSITION_RE` → 标识符键补双引号；键值再检查 `_UNDEFINED_VALUE_RE` | — |
| `transform()` | 字段映射、HTML 清洗、默认值、技能段落拆分 | 缺少 id/name → return None |
| `validate_heroes()` | `Hero.model_validate()` 批量校验 | 单项失败只打印 error/info，不阻断 |

### 3.3 `extract_js_array()` 字符级状态机

```python
# 核心片段（伪代码）
start = js_text.find("const e=[")
start += len("const e=[") - 1
depth = 0
quote = None
escaped = False
for index in range(start, len(js_text)):
    char = js_text[index]
    if quote:
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            quote = None
        continue
    if char in ('"', "'", "`"):
        quote = char
    elif char == "[":
        depth += 1
    elif char == "]":
        depth -= 1
        if depth == 0:
            return js_text[start : index + 1]
raise RuntimeError("JS 数组未闭合")
```

### 3.4 `_to_json_text()` 字符级状态机

```python
# 核心片段（伪代码）
out = []
quote = ""
while i < n:
    ch = text[i]
    if quote:
        out.append(ch)
        if ch == "\\" and i+1 < n:
            out.append(text[i+1]); i += 2; continue
        if ch == quote:
            quote = ""
        i += 1; continue
    if ch in ('"', "'"):
        quote = ch; out.append(ch); i += 1; continue
    if ch == ":":
        out.append(":")
        if _UNDEFINED_VALUE_RE.match(text, i+1):
            out.append("null"); i = 匹配结束位置
        i += 1; continue
    if ch == ",":
        if _TRAILING_COMMA_RE.match(text, i+1):
            i += 1; continue          # 尾逗号丢弃
        out.append(","); i += 1
    else:
        out.append(ch); i += 1
    # { 或 , 之后可能处于对象键位置：标识符键补引号
    if ch in (",", "{"):
        m = _KEY_POSITION_RE.match(text, i)
        if m:
            out.append(text[i:m.start(1)])
            out.append('"')
            out.append(m.group(1))
            out.append('"')
            out.append(text[m.end(1):m.end()])
            i = m.end()
            m2 = _UNDEFINED_VALUE_RE.match(text, i)
            if m2:
                out.append("null"); i = m2.end()
return "".join(out)
```

关键区别（对比旧版三步正则）：
- 改写操作仅在引号态之外执行，技能描述中的 `"效果{x:1}"`、`,变化:无` 等不会误改。
- `:undefined` 与尾逗号的检测使用正则但匹配位置受状态机控制，避免匹配到字符串内的文本。
- `:undefined` 处理在两个位置执行：`:` 冒号之后、以及对象键补引号之后（键位置本身就是 `key:` 形态）。

---

## 四、公告检查与百科 diff 链路

```
MainWindow._check_announcements()
  -> AnnouncementService.check_now()                 [is_busy 防重 + 60s 冷却]
     -> threading.Thread(target=_run_check, args=hero_names)
       -> _do_check(hero_names)
          -> fetch_latest_announcements()            [公告 API 单请求, 失败回退 HTML 列表]
             -> fetch(ANNOUNCEMENT_API_URL + query)
             -> json.loads(text)
             -> parse_announcement_list(raw)         [5 条含全文, id/title/content/url/publishdate]
             -> [API 失败] _parse_notice_page_html(fetch(ANNOUNCEMENT_PAGE_URL))
                -> 负数合成 id（避免与 API 正数 id 混同，去重主键是 URL）
                -> content_missing=True
          -> classify_hero_related(title, content_html, hero_names)
             -> _html_to_lines(content_html)         [去标签 / 保留换行 / unescape]
             -> _extract_section(lines, NEW_SECTION_NAMES)      [新增武将]
             -> _extract_section(lines, ADJUST_SECTION_NAMES)   [武将调整/加强/削弱/修改]
             -> [new_section] 独立短名称行 → "新增"
             -> [adjust_section] CHANGE_RE 或 known_names 命中 → "调整/加强/..."
          -> AnnouncementManager.merge_new(items, baseline)     [按 url 去重落盘]
          -> fetch_baike_heroes()                    [复用 fetch_all_raw → transform → validate_heroes；失败返回 None]
          -> build_hero_snapshot(heroes)             [每武将 {id: {name, hash}}]
          -> _snapshot_to_plain(snapshot)            [BaikeSnapshot 模型 → {id: {name, hash}} dict]
          -> load_baike_snapshot()                   [首次用本地 heroes.json 建基线]
          -> diff_heroes(current_plain, baseline_plain)
             -> {added: [name, id], modified: [...], removed: [...]}
          -> AnnouncementManager.mark_ready_if_updated(diff)
          -> build_timeline_events(announcements, cutoff_date)
             -> extract_hero_changes(title, content_html)
                -> _extract_new_hero_events(章节)
                -> _extract_adjust_events(章节)      [武将名(类型) 行 → 修改前/修改后 / 技能名:描述]
          -> append_announcement_events(events)
       -> _check_done.emit(result)                  [跨线程信号]
  -> _finalize_check(result)                         [GUI 线程收尾: 共享状态写入 + 快照落盘]
     -> save_baike_snapshot(snapshot, path)
  [信号] check_finished(result) -> MainWindow._on_announcement_check_finished()

用户点"更新武将数据":
  -> AnnouncementService.prepare_update_candidates(local_heroes, announcements, diff)
     -> threading.Thread(_run_prepare)              [后台拉取官网百科, 不阻塞 GUI]
        -> fetch_baike_heroes()                      [失败时 build_update_candidates(..., None, diff)]
        -> build_update_candidates(announcements, local, official, diff)
           -> 本地按 id 回查 → 回查不到按 name 回查
           -> 每个候选: {name, hero_id, change, source, known, summary[], local_full, official_full}
           -> [change=新增 且 local=None] 官网新增文案
           -> [local 且 official] hero_field_diff_summary(local, official)
           -> [local 且 无官网] "官网数据暂不可用"
        -> update_candidates_prepared.emit(payload)
  -> HeroUpdateConfirmDialog 确认后
     -> AnnouncementService.mark_applied()
        -> AnnouncementManager.mark_applied()
        -> save_baike_snapshot(_last_snapshot, ...)
     -> fetch_specific(ids) / fetch_incremental()
```

---

## 五、更新候选与字段级差异摘要

```
build_update_candidates(announcements, local_heroes, official_heroes, diff)
  -> local_by_name = {name: hero} (仅本地)
  -> local_by_id   = {id: hero}   (仅本地)
  -> official_by_name = {name: hero} (官网)

  [ready 公告] 遍历 announcement.matched_heroes
    -> local_by_name 命中 → hero_id = int(local["id"])
    -> add(name, change, "公告：{title}", hero_id, known)

  [diff added] 遍历 diff["added"]
    -> local_by_id 回查（id 可能缺失，再 local_by_name 回查）
    -> 本地无此武将 → add(name, "新增", "百科 diff", known=False)
    -> 本地已有 → official_by_name 回查，hero_field_diff_summary 为空则跳过；否则 add(name, "调整", "百科 diff", known=True)

  [diff modified] 遍历 diff["modified"]
    -> local_by_id 回查命中 → add(name, "调整", "百科 diff", known=True)

  最后为每个候选填充 summary/local_full/official_full
    -> format_hero_full_text(hero)  [名称/势力/定位/体力/手牌/性别/技能描述/结算]
    -> hero_field_diff_summary(local, official)
       -> 字段对比: 势力/定位/性别/体力/手牌
       -> 技能名交集: 描述不一致 / 结算不一致 / 官网新增 / 本地删除

hero_field_diff_summary(local, official)
  -> field_line("势力", ...)
  -> field_line("定位", ...)
  -> field_line("性别", ...)
  -> max_hp / max_hand 数值比对
  -> _skill_by_name(local["skills"]) / _skill_by_name(official["skills"])
  -> [name 仅本地] "本地技能【{name}】在官网已不存在"
  -> [name 仅官网] "官网新增技能：【{name}】"
  -> [name 均存在] 描述 vs 描述 → 结算 vs 结算
```

---

## 六、外部调用关系总览

### 6.1 本模块被外部调用

```
src.business.fetching.hero_fetch_service
  -> QProcess.start(["-m", "src.scraper.official"])            [全量采集]
  -> QProcess.start(["-m", "src.scraper.incremental", ...])     [增量/指定采集]

src.ui.app.main_window
  -> 菜单 → HeroFetchService.fetch_all/incremental/specific     [间接调用]
  -> MainWindow._check_announcements() → AnnouncementService.check_now()
```

### 6.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.models.Hero` | Pydantic 校验 |
| `src.config.logging_config` | 日志初始化 |
| `src.config.env` | `PROJECT_ROOT` / `BUNDLE_ROOT` / `get_runtime_params()` |
| `src.data.announcement_manager` | `AnnouncementManager`, `AnnouncementStatus`, `BaikeSnapshot`, `load_baike_snapshot`, `save_baike_snapshot` |
| `src.data.hero_timeline` | `load_timeline()`, `normalize_change_type()`, `append_announcement_events()` |
| Python 标准库 `urllib.request` | HTTP 请求（含 `_NoRedirectHandler`） |
| Python 标准库 `html` / `re` | HTML 清洗、正则 |
| Python 标准库 `json` / `hashlib` / `unicodedata` | JSON 解析、内容哈希、Unicode 归一化 |
| Python 标准库 `tempfile` / `PIL.Image` | 头像安全下载 |
| Python 标准库 `pathlib` | 原子写入、路径逃逸防护 |

### 6.3 模块内部函数关联

```
official.py:main()              [shim → official_source.full.main()]
└── official_source.full.main()
    └── full.crawl()
        ├── crawler.fetch(BAIKE_URL)                 [HTTP GET 首页]
        ├── adapter.find_chunk_url(html)              [正则提取 chunk]
        ├── crawler.fetch(chunk_url)                  [HTTP GET JS chunk]
        ├── adapter.parse_heroes_chunk(js_text)
        │   ├── adapter.extract_js_array()            [字符级状态机]
        │   └── adapter.js_to_json()
        │       └── adapter._to_json_text()           [字符级状态机]
        ├── crawler.transform() ×N                    [逐项清洗]
        │   ├── crawler.clean_html()
        │   └── crawler.split_skill_desc()
        │       └── crawler.clean_html()
        ├── crawler.validate_heroes()
        ├── crawler.save_json_atomic()
        └── crawler.download_hero_images()
            ├── crawler._safe_image_name()
            ├── crawler._download_hero_image()
            │   ├── crawler._open_image_response()
            │   │   ├── crawler._validate_image_url()
            │   │   └── urljoin()
            │   └── crawler._validate_image_file()
            └── time.sleep()                          [逐张间隔]

incremental.py:main()           [shim → official_source.incremental.main()]
└── official_source.incremental.main()
    ├── crawler.fetch_all_raw()                       [复用全量管道]
    │   ├── crawler.fetch()
    │   ├── adapter.find_chunk_url()
    │   ├── crawler.fetch()
    │   └── adapter.parse_heroes_chunk()
    ├── load_existing_ids()
    │   └── _load_heroes_file()
    ├── incremental_collect() / filter_by_names() / filter_by_ids()
    └── run()
        ├── crawler.transform() ×N
        ├── crawler.validate_heroes()
        ├── _load_heroes_file()                       [replace_ids / append 模式]
        ├── crawler.save_json_atomic()
        └── crawler.download_hero_images()

AnnouncementService (business 层)
├── check_now() → Thread → _run_check → _do_check()
│   ├── announcement.fetch_latest_announcements()
│   │   ├── parse_announcement_list()
│   │   └── _parse_notice_page_html()                [API 失败回退]
│   ├── announcement.classify_hero_related()
│   │   ├── _html_to_lines()
│   │   └── _extract_section()
│   ├── AnnouncementManager.merge_new()
│   ├── announcement.fetch_baike_heroes()
│   │   ├── crawler.fetch_all_raw()
│   │   ├── crawler.transform() ×N
│   │   └── crawler.validate_heroes()
│   ├── announcement.build_hero_snapshot()
│   │   └── announcement.hero_content_hash()
│   ├── _snapshot_to_plain()
│   ├── load_baike_snapshot()
│   ├── announcement.diff_heroes()
│   ├── AnnouncementManager.mark_ready_if_updated()
│   ├── announcement.build_timeline_events()
│   │   └── announcement.extract_hero_changes()
│   │       ├── _extract_new_hero_events()
│   │       └── _extract_adjust_events()
│   └── append_announcement_events()
├── _finalize_check()
│   └── save_baike_snapshot()
├── prepare_update_candidates() → Thread → _run_prepare()
│   ├── announcement.fetch_baike_heroes()
│   └── announcement.build_update_candidates()
│       ├── announcement.format_hero_full_text()
│       └── announcement.hero_field_diff_summary()
└── mark_applied()
    ├── AnnouncementManager.mark_applied()
    └── save_baike_snapshot(_last_snapshot)
```

---

## 七、函数清单总表

| 函数 | 文件 | 调用方（主要） | 被调用方（主要） |
|------|------|----------------|------------------|
| `official.main()` | `official.py` | QProcess 子进程 | `official_source.full.main()` |
| `official_source.full.main()` | `full.py` | 子进程入口 | `argparse`, `setup_logging`, `full.crawl()` |
| `full.crawl()` | `full.py` | `full.main()` | `fetch()`, `find_chunk_url()`, `parse_heroes_chunk()`, `transform()`, `validate_heroes()`, `save_json_atomic()`, `download_hero_images()` |
| `crawler.fetch(url, binary)` | `crawler.py` | `full.crawl()`, `fetch_all_raw()` | `urllib.request.urlopen()` |
| `crawler.save_json_atomic(path, data)` | `crawler.py` | `full.crawl()`, `run()` | `json.dump()`, `Path.replace()` |
| `crawler.fetch_all_raw()` | `crawler.py` | `incremental.main()`, `fetch_baike_heroes()` | `fetch()`, `find_chunk_url()`, `parse_heroes_chunk()` |
| `crawler.transform(raw)` | `crawler.py` | `full.crawl()`, `run()`, `fetch_baike_heroes()` | `clean_html()`, `split_skill_desc()` |
| `crawler.clean_html(html)` | `crawler.py` | `transform()`, `split_skill_desc()` | `re.sub()`, `html.unescape()` |
| `crawler.split_skill_desc(desc)` | `crawler.py` | `transform()` | `clean_html()`, `</p>` 段落拆分 |
| `crawler.validate_heroes(list)` | `crawler.py` | `full.crawl()`, `run()`, `fetch_baike_heroes()` | `Hero.model_validate()` |
| `crawler.download_hero_images(raw_list)` | `crawler.py` | `full.crawl()`, `run()` | `_safe_image_name()`, `_download_hero_image()` |
| `crawler._safe_image_name(name)` | `crawler.py` | `download_hero_images()` | `clean_html()`, `unicodedata.normalize()` |
| `crawler._download_hero_image(url, dest)` | `crawler.py` | `download_hero_images()` | `_open_image_response()`, `_validate_image_file()` |
| `crawler._open_image_response(url)` | `crawler.py` | `_download_hero_image()` | `_validate_image_url()`, `urljoin()` |
| `crawler._validate_image_url(url)` | `crawler.py` | `_open_image_response()` | `urlparse()` |
| `crawler._validate_image_file(path)` | `crawler.py` | `_download_hero_image()` | `PIL.Image.verify()` |
| `adapter.find_chunk_url(html)` | `adapter.py` | `full.crawl()`, `fetch_all_raw()` | `CHUNK_URL_PATTERN.search()` |
| `adapter.extract_js_array(js)` | `adapter.py` | `parse_heroes_chunk()` | 字符级状态机（depth/quote/escaped） |
| `adapter.js_to_json(text)` | `adapter.py` | `parse_heroes_chunk()` | `_to_json_text()`, `json.loads()` |
| `adapter._to_json_text(text)` | `adapter.py` | `js_to_json()` | 字符级状态机（键引号/:undefined/null/尾逗号） |
| `adapter.parse_heroes_chunk(js)` | `adapter.py` | `full.crawl()`, `fetch_all_raw()` | `extract_js_array()`, `js_to_json()` |
| `incremental.main()` | `official_source/incremental.py` | QProcess 子进程 | `fetch_all_raw()`, 筛选, `run()` |
| `load_existing_ids(path)` | `official_source/incremental.py` | `incremental.main()` | `_load_heroes_file()` |
| `load_existing_names(path)` | `official_source/incremental.py` | 外部 | `_load_heroes_file()` |
| `_load_heroes_file(path)` | `official_source/incremental.py` | 多入口 | `json.load()`，损坏时 `path.replace()` 备份 |
| `incremental_collect(raw, ids)` | `official_source/incremental.py` | `incremental.main()` | 列表推导式差集 |
| `filter_by_names(raw, names)` | `official_source/incremental.py` | `incremental.main()` | 双向子串匹配 |
| `filter_by_ids(raw, ids)` | `official_source/incremental.py` | `incremental.main()` | ID set 交集 |
| `run(raw, output, dry_run, append, replace_ids, skip_images)` | `official_source/incremental.py` | `incremental.main()` | `transform()`, `validate_heroes()`, `save_json_atomic()` |
| `announcement.fetch_latest_announcements()` | `announcement.py` | `AnnouncementService._do_check()` | `parse_announcement_list()`, `_parse_notice_page_html()` |
| `announcement.parse_announcement_list(raw)` | `announcement.py` | `fetch_latest_announcements()` | `clean_html()`, `urljoin()` |
| `announcement._parse_notice_page_html(html)` | `announcement.py` | `fetch_latest_announcements()` | 正则提取、负数合成 id |
| `announcement.classify_hero_related(title, content, names)` | `announcement.py` | `AnnouncementService._do_check()` | `_html_to_lines()`, `_extract_section()` |
| `announcement._html_to_lines(html)` | `announcement.py` | `classify_hero_related()`, `extract_hero_changes()` | `re.sub()`, `html.unescape()` |
| `announcement._extract_section(lines, names)` | `announcement.py` | `classify_hero_related()`, `extract_hero_changes()` | `SECTION_HEADER_RE.match()` |
| `announcement.extract_hero_changes(title, content)` | `announcement.py` | `build_timeline_events()` | `_extract_new_hero_events()`, `_extract_adjust_events()` |
| `announcement.build_timeline_events(announcements, cutoff)` | `announcement.py` | `AnnouncementService._do_check()` | `extract_hero_changes()`, `load_timeline()` |
| `announcement.fetch_baike_heroes()` | `announcement.py` | `AnnouncementService._do_check()`, `_run_prepare()` | `fetch_all_raw()`, `transform()`, `validate_heroes()` |
| `announcement.hero_content_hash(hero)` | `announcement.py` | `build_hero_snapshot()` | `_normalize_text()`, `hashlib.md5()` |
| `announcement.build_hero_snapshot(heroes)` | `announcement.py` | `AnnouncementService._do_check()` | `hero_content_hash()` |
| `announcement.diff_heroes(current, baseline)` | `announcement.py` | `AnnouncementService._do_check()` | set 运算 |
| `announcement.format_hero_full_text(hero)` | `announcement.py` | `build_update_candidates()` | `_normalize_text()` |
| `announcement.hero_field_diff_summary(local, official)` | `announcement.py` | `build_update_candidates()` | `_normalize_text()`, `_skill_by_name()` |
| `announcement.build_update_candidates(anns, local, official, diff)` | `announcement.py` | `AnnouncementService.prepare_update_candidates()` | `hero_field_diff_summary()`, `format_hero_full_text()` |