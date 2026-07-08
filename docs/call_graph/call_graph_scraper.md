# 调用链路：爬虫与数据采集

> 对应源码：`src/scraper/`（不含 `ai_*.py` 的 AI 部分）
> 调用链路说明：箭头 `A() -> B()` 表示函数 A 直接调用函数 B，缩进表示调用嵌套层次。
> 所有链路均在 QProcess 子进程中执行，不阻塞 UI 主线程。

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
           -> argparse.parse_args()
           -> setup_logging()
           -> official.crawl(dry_run, output_path, skip_images)
              -> crawler.fetch(BAIKE_URL)                      [GET 百科首页 HTML]
                 -> urllib.request.urlopen()
                 -> 重试: retry ×3, interval=2s
              -> crawler.find_chunk_url(html)                  [正则 /_nuxt/mjbk.[a-f0-9]+\.js]
              -> if not found: raise RuntimeError
              -> crawler.fetch(BASE_URL + chunk_url)           [GET JS chunk (~300KB)]
              -> crawler.extract_js_array(js_text)             [括号深度计数器提取数组]
                 -> 查找 "const e=[" 定位起点
                 -> 深度计数器: depth++/depth--, 找到 matching ]
              -> crawler.js_to_json(array_text)                [JS 语法修复 → JSON]
                 -> re.sub: key 加引号
                 -> re.sub: undefined → null
                 -> re.sub: 尾部多余逗号
                 -> json.loads()
              -> [遍历每个 raw]
                 -> crawler.transform(raw)                     [字段清洗与映射]
                    -> clean_html(raw["name"])                 [去标签、HTML 解码]
                    -> clean_html(raw["dynasty"])              [势力字段清洗]
                    -> split_skill_desc(raw["skill"])          [拆分技能描述/结算]
                       -> clean_html(desc_html)                [去标签]
                       -> 按 <p><strong>段落标题匹配段落类型
                    -> _safe_int(raw["p_blood_max"], 4)        [int 转型，失败默认值]
                    -> _safe_int(raw["p_card_max"], 4)
                    -> GENDER_MAP.get(int(raw["gender"]), "男")
                 -> 过滤: id/name 缺失跳过整条
              -> 过滤 None 结果
              -> crawler.validate_heroes(filtered)             [批量 Pydantic 校验]
                 -> Hero.model_validate(h) ×N
                 -> h.model_dump(mode="json")
              -> crawler.download_hero_images(raw_list, ...)   [头像下载]
                 -> clean_html(icon_url)
                 -> urlparse(icon_url)
                 -> fetch(icon_url, binary=True)               [GET 头像图片]
                 -> Path.write_bytes(img_data)                 [写入 images/{name}.png]
                 -> 失败仅 warning，不阻断
              -> [原子写入 JSON]
                 -> json.dump(data, tmp_file)
                 -> tmp_path.replace(output_path)
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
| `official.main()` | `official.py` | QProcess 子进程 | `official.crawl()` |
| `official.crawl()` | `official.py` | `main()` | `crawler.*`, `validate_heroes()` |
| `crawler.fetch()` | `crawler.py` | `crawl()` | `urllib.request.urlopen()` |
| `crawler.find_chunk_url()` | `crawler.py` | `crawl()` | `re.search()` |
| `crawler.extract_js_array()` | `crawler.py` | `crawl()` | 括号深度计数器 |
| `crawler.js_to_json()` | `crawler.py` | `crawl()` | `json.loads()`, `re.sub()` |
| `crawler.transform()` | `crawler.py` | `crawl()` | `clean_html()`, `split_skill_desc()` |
| `crawler.split_skill_desc()` | `crawler.py` | `transform()` | `clean_html()` |
| `crawler.clean_html()` | `crawler.py` | `transform()`, `split_skill_desc()` | `re.sub()`, `html.unescape()` |
| `crawler.validate_heroes()` | `crawler.py` | `crawl()` | `Hero.model_validate()` |
| `crawler.download_hero_images()` | `crawler.py` | `crawl()` | `fetch(binary)`, `Path.write_bytes()` |

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
           -> argparse.parse_args()
           -> crawler.fetch_all_raw()                          [复用全量采集管道]
              -> fetch(BAIKE_URL) -> find_chunk_url -> fetch(chunk_url)
              -> extract_js_array -> js_to_json
           -> load_existing_ids(output_path)                   [读取已有 JSON 的 ID 集合]
              -> json.load() -> set({id, ...})
           -> incremental_collect(raw_list, existing_ids)      [差集筛选]
              -> [r for r in raw_list if r["id"] not in existing_ids]
           -> [--incremental 模式] run(new_raw, output_path, append=True)
              -> [逐项] transform(raw)                         [数据清洗]
              -> [逐项] validate_heroes([transformed])         [Pydantic 校验]
              -> download_hero_images(raw_list)                [下载新头像]
              -> [原子写入: 读原文件 → 追加新数据 → 覆写]
           -> [--hero "关羽,诸葛亮" 模式]
              -> filter_by_names(raw_list, target_names)       [子串匹配]
              -> run(filtered, ..., replace_ids=...)           [覆写指定 ID]
           -> [--hero-id 52,114 模式]
              -> filter_by_ids(raw_list, target_ids)
              -> run(filtered, ..., replace_ids=...)
         ─────────────────────────────────────────────────────────
```

| 函数 | 所在文件 | 调用方 | 被调用方 |
|------|----------|--------|----------|
| `MainWindow._request_fetch_incremental()` | `main_window.py` | 菜单 QAction | `HeroFetchService.fetch_incremental()` |
| `HeroFetchService.fetch_incremental()` | `fetch_service.py` | `_request_fetch_incremental()` | `_start_process()` |
| `incremental.main()` | `incremental.py` | QProcess 子进程 | `fetch_all_raw()`, `run()` |
| `crawler.fetch_all_raw()` | `crawler.py` | `incremental.main()` | `fetch()`, `find_chunk_url()`, ... |
| `load_existing_ids()` | `incremental.py` | `incremental.main()` | `json.load()` |
| `incremental_collect()` | `incremental.py` | `incremental.main()` | 列表推导式差集 |
| `filter_by_names()` | `incremental.py` | `incremental.main()` | 子串匹配 |
| `filter_by_ids()` | `incremental.py` | `incremental.main()` | set intersection |
| `run()` | `incremental.py` | `incremental.main()` | `transform()`, `validate_heroes()`, 原子写入 |

---

## 三、JS 解析管道详解

这是爬虫模块最核心的转换管道，将官网 SPA 中的 JS 数据转换为可用 JSON。

### 3.1 管道流程

```
fetch(BAIKE_URL)
  ↓ 返回 HTML（~50KB）
find_chunk_url(html)
  ↓ 提取 /_nuxt/mjbk.3a6f2b.js 类路径
fetch(chunk_url)
  ↓ 返回 JS 文本（~300KB）
extract_js_array(js_text)
  ↓ 定位 const e=[...]，提取括号内的 JSON-like 字符串
js_to_json(array_text)
  ↓ 三步预处理: key 加引号 → undefined→null → 移除尾部逗号
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
| `find_chunk_url()` | 正则 `/\.\/_nuxt\/mjbk\.[a-f0-9]+\.js/` | 未匹配 → raise RuntimeError |
| `extract_js_array()` | 查找 `const e=[` 后遍历字符，depth++/-- | 找不到起点 → ValueError，找不到终点 → ValueError |
| `js_to_json()` | 三步正则替换后 `json.loads()` | JSON 解析失败 → raise ValueError |
| `transform()` | 字段映射、HTML 清洗、默认值 | 缺少 id/name → return None |
| `validate_heroes()` | `Hero.model_validate()` 批量校验 | 单项失败只打印 warning，不阻断 |

### 3.3 三步预处理详解

```python
def js_to_json(text):
    # 第 1 步: key 加引号 —— 正则匹配 a:1 → "a":1
    text = re.sub(r'(?<!")(\b[a-zA-Z_]\w*)(\s*:)', r'"\1"\2', text)
    # 第 2 步: undefined → null
    text = text.replace("undefined", "null")
    # 第 3 步: 移除尾部多余逗号
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return json.loads(text)
```

---

## 四、外部调用关系总览

### 4.1 本模块被外部调用

```
src.business.fetch_service
  -> QProcess.start(["-m", "src.scraper.official"])            [全量采集]
  -> QProcess.start(["-m", "src.scraper.incremental", ...])     [增量/指定采集]

src.ui.main_window
  -> 菜单 → HeroFetchService.fetch_all/incremental/specific     [间接调用]
```

### 4.2 本模块调用的外部模块

| 被调用方 | 说明 |
|----------|------|
| `src.data.models.Hero` | Pydantic 校验 |
| `src.config.logging_config` | 日志初始化 |
| Python 标准库 `urllib.request` | HTTP 请求 |
| Python 标准库 `html.parser` / `re` | HTML 清洗 |
| Python 标准库 `json` | JSON 解析和写入 |

### 4.3 模块内部函数关联

```
official.py:main() / incremental.py:main()          [CLI 入口]
  └── crawler.fetch_all_raw()                       [全量数据获取]
  │     ├── crawler.fetch()                         [HTTP GET]
  │     ├── crawler.find_chunk_url()                [正则提取]
  │     ├── crawler.fetch()                         [第二次 HTTP GET]
  │     ├── crawler.extract_js_array()              [括号深度计数]
  │     └── crawler.js_to_json()                    [JS→JSON 转换]
  └── crawler.transform()                           [逐项清洗]
  │     ├── crawler.clean_html()                    [标签/实体清理]
  │     └── crawler.split_skill_desc()              [技能段落拆分]
  └── crawler.validate_heroes()                     [Pydantic 校验]
  └── crawler.download_hero_images()                [头像下载]
```

---

## 五、函数清单总表

| 函数 | 文件 | 调用方（主要） | 被调用方（主要） |
|------|------|----------------|------------------|
| `crawl()` | `official.py:48` | `official.main()` | `fetch()`, `find_chunk_url()`, `transform()`, ... |
| `fetch(url, binary)` | `crawler.py` | `crawl()`, `fetch_all_raw()` | `urllib.request.urlopen()` |
| `find_chunk_url(html)` | `crawler.py` | `crawl()`, `fetch_all_raw()` | `re.search()` |
| `extract_js_array(js)` | `crawler.py` | `crawl()`, `fetch_all_raw()` | 括号深度遍历 |
| `js_to_json(text)` | `crawler.py` | `crawl()`, `fetch_all_raw()` | `re.sub()`, `json.loads()` |
| `transform(raw)` | `crawler.py` | `crawl()`, `run()` | `clean_html()`, `split_skill_desc()` |
| `clean_html(html)` | `crawler.py` | `transform()`, `split_skill_desc()` | `re.sub()`, `html.unescape()` |
| `split_skill_desc(desc)` | `crawler.py` | `transform()` | `clean_html()`, HTML 段落解析 |
| `validate_heroes(list)` | `crawler.py` | `crawl()`, `run()` | `Hero.model_validate()` |
| `download_hero_images()` | `crawler.py` | `crawl()`, `run()` | `fetch(binary)`, `Path.write_bytes()` |
| `fetch_all_raw()` | `crawler.py` | `incremental.main()` | `fetch()`, `find_chunk_url()`, ... |
| `run()` | `incremental.py` | `incremental.main()` | `transform()`, `validate_heroes()` |
