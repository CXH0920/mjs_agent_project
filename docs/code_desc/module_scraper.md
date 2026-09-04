# 模块：爬虫与数据采集

> 对应目录：`src/scraper/official_source/` + 根 CLI 入口
> 职责：从官网解析武将数据、数据清洗与校验、头像下载

---

## 一、模块职责

本模块负责从[名将杀官网百科](https://mjs.ztgame.com/baike/)获取武将原始数据。官网不提供 REST API，数据通过 Nuxt.js 打包在 JS chunk 中。模块的核心工作是从 JS 文本中提取数据、清洗字段、映射到 Pydantic 模型并输出为 JSON。

功能包括：
- **全量采集** — 从官网下载全部武将数据
- **增量采集** — 只采集本地没有的新武将
- **指定采集** — 按武将名或 ID 采集特定武将
- **头像下载** — 从官网下载武将头像到 `images/` 目录
- **公告监控** — 拉取官方公告 API，仅对 `【新增武将】/【武将调整】` 章节相关公告提醒；百科逐武将哈希 diff 确认"什么真的变了"

---

## 二、文件结构

```
src/scraper/
├── __init__.py
├── official.py              # 全量采集 CLI 兼容入口（委托 official_source.full.main）
├── incremental.py           # 增量采集 CLI 兼容入口（委托 official_source.incremental.main）
└── official_source/
    ├── __init__.py          # 空
    ├── adapter.py           # 官网页面与 JS chunk 解析适配器（状态机核心）
    ├── crawler.py           # 网络请求、数据清洗、校验与头像下载
    ├── full.py              # 全量采集实现（含 CLI main）
    ├── incremental.py       # 增量/指定采集实现（含 CLI main）
    └── announcement.py      # 公告 API/回退解析、武将相关判定、百科逐武将 diff
```

根目录的 `official.py` 与 `incremental.py` 是薄薄的兼容入口（仅 6 行，导入子包 `main`），真正的业务逻辑都在 `official_source/` 下；`official_source/__init__.py` 为空文件。

---

## 三、核心逻辑

### 3.1 JS chunk 解析管道

官网武将数据经过 5 步转换才能成为可用的 JSON：

```
官网首页 HTML
  ① fetch(BAIKE_URL)  → HTML
  ② adapter.find_chunk_url(html)  → JS chunk URL
  ③ fetch(chunk_url)  → JS 文本（约 300 KB）
  ④ adapter.extract_js_array(js_text)  → 外层 [ ... ] 字符串
  ⑤ adapter.js_to_json(array_str)  → Python list[dict]
  ⑥ 等价调用：adapter.parse_heroes_chunk(js_text)  → 同上
```

**适配器边界：** `official_source/adapter.py` 集中承载官网页面与 chunk 的格式假设；官网改版时只需修改该文件。两个关键函数——`find_chunk_url`（从首页定位 `/_nuxt/mjbk.<hash>.js`）与 `extract_js_array`（定位 `const e=[...]` 变量）——在找不到预期模式时都会把现场信息（页面/JS 前 300 字符 + 已发现的其他 `_nuxt` 脚本列表）写入错误日志和异常消息，改版当天即可定位。

**字符级状态机（`js_to_json`）：** JS 对象数组（无引号键、`undefined`、尾逗号）不是合法 JSON。旧实现使用三步正则预处理，会被技能描述里的 `效果{x:1}`、`,变化:无` 等内容误改写。当前改为**字符级状态机**，单遍扫描，仅在字符串字面量之外执行改写：

- **状态维度**：仅一个 `quote: str | None` 变量——记录当前是否在 `"'/` 引号内，以及是哪种引号。
- **字符串内**：原样抄入输出；`\` 后跟任意字符算作转义（跳过两位），引号闭合同种字符时退出字符串状态。
- **字符串外**：遇到 `:` 时用 `_UNDEFINED_VALUE_RE` 探测 `undefined` → `null`；遇到 `,` 后用 `_TRAILING_COMMA_RE` 判断是否为 `]`/`}` 前的尾逗号，是则丢弃；遇到 `{` 或 `,` 后处于**对象键位置**，用 `_KEY_POSITION_RE` 匹配标识符并补双引号（`a:` → `"a":`），键后再补一次 `undefined` → `null` 处理。

关键实现节选（`src/scraper/official_source/adapter.py`，`_to_json_text`）：

```python
def _to_json_text(text: str) -> str:
    out: list[str] = []
    i = 0
    n = len(text)
    quote = ""
    while i < n:
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2; continue
            if ch == quote:
                quote = ""
            i += 1; continue
        if ch == '"' or ch == "'":
            quote = ch; out.append(ch); i += 1; continue
        if ch == ":":
            out.append(ch); i += 1
            m = _UNDEFINED_VALUE_RE.match(text, i)
            if m: out.append("null"); i = m.end()
            continue
        if ch == ",":
            if _TRAILING_COMMA_RE.match(text, i + 1):
                i += 1; continue
            out.append(ch); i += 1
        else:
            out.append(ch); i += 1
        if ch in (",", "{"):
            m = _KEY_POSITION_RE.match(text, i)
            if m:
                out.append(text[i:m.start(1)])
                out.append('"') + out.append(m.group(1)) + out.append('"')
                out.append(text[m.end(1):m.end()]); i = m.end()
                m2 = _UNDEFINED_VALUE_RE.match(text, i)
                if m2: out.append("null"); i = m2.end()
    return "".join(out)
```

**括号深度计数器提取 JS 数组（`extract_js_array`）：** 不使用正则 `r"\[.*\]"`，因为 JS 数组嵌套对象，内部方括号字符会让正则过早闭合。当前同样使用字符级状态机，与 `js_to_json` 共享"在字符串内跳过"思路——遇到 `"'/` 引号即进入字符串态，期间方括号、花括号原样计数但不增深度；这样技能描述里 `if (x<y)` 之类的字符串内容不会干扰。

```python
def extract_js_array(js_text: str) -> str:
    start_marker = "const e=["
    start = js_text.find(start_marker)
    # 失败时带出 JS 前 300 字符日志，便于改版诊断
    if start < 0:
        raise RuntimeError("官网 JS 中未找到 ... 起始标记（可能已改版）")
    start += len(start_marker) - 1
    depth = 0
    quote: str | None = None
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
        if char in ('"', "'", '`'):
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return js_text[start : index + 1]
    raise RuntimeError("JS 数组未闭合")
```

> **设计思路：** `extract_js_array` 与 `_to_json_text` 都坚持"在字符串字面量内不做任何结构化改写"——旧版简单深度计数器 + 全局正则会被技能文本里的 `变化:无`、`效果{x:1}` 误伤导致整批解析失败。

### 3.2 数据清洗（transform）

每条原始数据经过 `transform()` 映射为模型字段：

| 原始字段 | 目标字段 | 处理方式 |
|----------|----------|----------|
| `id` | `hero.id` | 直接取，缺失则跳过整条 |
| `name` | `hero.name` | `clean_html()` 去标签解码，缺失则跳过整条 |
| `dynasty` | `hero.faction` | `clean_html()` |
| `p_positioning` | `hero.position` | `clean_html()` |
| `p_blood_max` | `hero.max_hp` | int()，失败默认 4 |
| `p_card_max` | `hero.max_hand` | int()，失败默认 4 |
| `gender` | `hero.gender` | `GENDER_MAP = {1:"男", 2:"女"}`，默认男 |
| `skill`（遍历） | `hero.skills[]` | `split_skill_desc()` 拆分 |
| `icon_url` | `hero.icon_url` | 直接取 |
| _(固定值)_ | `hero.title` | 空字符串 |
| _(固定值)_ | `hero.difficulty` | 2 |
| _(固定值)_ | `hero.mode_viability` | 空字典 |
| _(固定值)_ | `hero.last_updated` | 当日 `date.today().isoformat()` |

**技能拆分逻辑（`split_skill_desc`）：**
按 HTML 的 `<p><strong>段落标题</strong></p>` 结构拆分：
- 保留「技能描述」→ `description`
- 保留「结算详情/结算详解/技能详解/技能详情」→ `settlement`（取第一个命中）
- 丢弃「技能典故」「设计思路」（lore 文本，对游戏策略无帮助）

### 3.3 增量采集

`src/scraper/official_source/incremental.py` 三种模式：

```bash
python -m src.scraper.incremental --incremental        # 只追加本地没有的新武将
python -m src.scraper.incremental --hero 诸葛亮,关羽    # 按名称采集（模糊匹配）
python -m src.scraper.incremental --hero-id 52,114      # 按 ID 采集
```

内部流程：
1. `fetch_all_raw()` 拿全量原始数据；
2. `load_existing_ids()` / `filter_by_names()` / `filter_by_ids()` 筛出目标子集；
3. `run()` 分三种写入策略：
   - **增量（`append=True`）**：追加本地缺失的武将；
   - **指定（`replace_ids`）**：先按 ID 删除旧数据再写入新数据（精确替换，覆盖已采集武将的陈旧数据）；
   - **皆否**：全量覆盖。

`load_existing_ids()` / `load_existing_names()` 用 `_load_heroes_file()` 读取本地 JSON，文件损坏时以 `corrupt-<timestamp>` 后缀备份原文件后按空集合继续，避免裸崩或静默丢数据。

### 3.4 头像下载

`download_hero_images()` 遍历官网原始数据，取 `icon_url` 和 `name`：
- 文件路径固定为 `images/{武将名}.png`；角色名经 NFC 规范化后仅允许中文、字母、数字、`_`、`-`（`SAFE_IMAGE_NAME_PATTERN`），并拒绝 Windows 保留名；
- 仅允许从 `https://siteres.ztgame.com` 下载，通过自定义 `_NoRedirectHandler` 逐跳校验重定向目标（至多 3 次），非官方域名即时拒绝；
- 响应以 64 KiB 分块下载，最大 5 MiB；`content-type` 非 `image/png` 直接拒绝；
- 临时文件经 Pillow 双重解码验证：格式必须是 PNG、像素数不超过 4,000,000、`verify()` 通过，`load()` 后再原子替换正式头像；失败临时文件由 `finally` 清理；
- 逐张请求间隔 0.5 s；连续失败 5 张熔断中止本次下载，避免对源站瞬时连发触发限流；
- `skip_existing=True` 时跳过已存在的文件。

### 3.5 公告监控

公告列表页是 Nuxt 对公开 JSON API 的 SSR 展示，接口为 `https://ucmsv2api.ztgame.com/api/news/list`（`site=mjs&type=notice&page=1&per_page=5`），单次返回 5 条公告全文。

- `fetch_latest_announcements()` — 请求公告 API；失败回退解析 `notice-1.html` 的 `<li>`（仅 title/date/url，`content_missing=True`，无真实 id 使用负数合成 id，避免与 API 正数 id 混同；去重主键是 URL）。
- `classify_hero_related(title, content_html, hero_names)` — 仅按 `【新增武将】/【武将调整】/【武将加强】/【武将削弱】/【武将修改】` 章节标题判定相关；新增章节内独立成行的 2-8 字中文/间隔号视为新武将名；调整章节内按 `名称（增强|削弱|调整|加强|修改|新增）` 或已知武将名单字面命中；正文其他位置提及武将名不判相关；不在本地名单的名字标 `known=False`。
- `extract_hero_changes(title, content_html)` — 从公告正文提取武将变更事件（`hero` + `change_type` + `skills`），用于时间轴 `data/mjs_adjustments.json` 持久化；"修改前/修改后"两行配对捕获同一技能前后描述。
- `hero_content_hash(hero)` — 官网字段（name/faction/position/max_hp/max_hand/gender/skills/icon_url）NFKC+去标签规范化后 md5，不含本地扩展字段。
- `build_hero_snapshot()` / `diff_heroes()` — 与 `data/baike_snapshot.json` 逐武将比对，输出 `{added, modified, removed}`。
- `fetch_baike_heroes()` — 复用 `fetch_all_raw() → transform() → validate_heroes()` 获取清洗后百科武将，失败返回 `None` 不中断。
- `hero_field_diff_summary(local, official)` — 字段级差异摘要（势力/定位/性别/体力/手牌/技能描述与结算，含新增/移除技能），中文、每行限长截断。
- `format_hero_full_text(hero)` — 武将只读全文（用于确认对话框的本地 vs 官网对比）。
- `build_update_candidates(announcements, local_heroes, official_heroes, diff)` — 组装"更新武将数据"确认候选：ready 公告解析武将与 diff `added`/`modified` 并集、按名称去重（后续来源补充缺失 ID）、附带摘要与本地/官网全文；`official_heroes=None` 时跳过摘要计算（降级）。

**公告状态机修复（避免本地已采集武将误报新增 + 滞后窗口期公告被永久吞掉）：**

- `AnnouncementManager.merge_new()`（`src/data/announcement_manager.py`）：`baseline=True` 首次运行时只记录不提醒（状态置 `APPLIED`）；非武将相关公告同样置 `APPLIED`，避免长期滞留 `PENDING`。
- `AnnouncementManager.mark_ready_if_updated(diff, current_names)`：`PENDING` → `READY` 的推进条件收紧为两个之一——① 公告提及的武将名命中 `diff.added|modified|removed` 的 changed 集合；② 公告新增武将（`change=="新增"`）的名字**已全部**出现在当前百科 `current_names` 中（即 `new_names <= current_names`）。兜底场景：基线快照若用本地已手动采集的数据初始化，`diff` 检测不到该"新增"，此时用百科全量名集兜底推进。
- `AnnouncementManager.mark_applied()`：只推进 `READY` → `APPLIED`；`PENDING` 公告保留。采集子进程成功不代表数据已落地百科（滞后窗口期），此时推进为终态会**永久吞掉公告**，故仅在百科已确认（`READY`）时才推进终态。
- `build_update_candidates()` 对 `diff.added` 武将先**回查本地数据**（先按 ID、再按名称兜底）：若本地已收录且与官网内容一致（`hero_field_diff_summary` 为空），直接剔除，不报"新增"，避免本地已采集武将被误报为新增。

### 3.6 实战配队导入（`src/scripts/import_combos.py`，2026-08 新增）

`src/business/maintenance/combo_import_service.run_import()` 是 CLI 与 UI 导入对话框共用的业务层入口：

1. 武将名 → 角色 ID 映射（heroes.json），未匹配项进报告，不静默丢弃
2. note 座次解析（`combo_seats.parse_seats`），解析失败/部分成功的条目照常导入（座次留空）并列入报告供人工复核
3. 解析结果与 `position` 字段交叉校验（以 note 为准），不一致清单进报告
4. **合并语义**：源导出记录 upsert；`manual=True` 手工记录保留（同 key 冲突时手工优先）；非手工记录若源中已不存在则移除并计数
5. 重复执行输出稳定（幂等）

报告字段：`total` / `imported` / `unmatched` / `duplicates` / `invalid` / `seat_stats` / `seat_review` / `position_mismatch` / `manual_kept` / `manual_collisions` / `removed_stale`

---

## 四、字段清洗与默认值代码节选

```python
# src/scraper/official_source/crawler.py :: transform()
def transform(raw: dict) -> dict | None:
    hero_id = raw.get("id")
    if hero_id is None:
        logger.warning("跳过: 缺少 id 字段 — %s", raw.get("name", "?"))
        return None

    name = clean_html(raw.get("name", ""))
    if not name:
        logger.warning("跳过 id=%s: 名称字段为空", hero_id)
        return None

    gender = GENDER_MAP.get(raw.get("gender"), "男")
    try:
        max_hp = int(raw.get("p_blood_max", 4))
    except (ValueError, TypeError):
        max_hp = 4
    try:
        max_hand = int(raw.get("p_card_max", 4))
    except (ValueError, TypeError):
        max_hand = 4
    # ...技能清洗略...
    hero = {
        "id": hero_id, "name": name, "title": "",
        "faction": clean_html(raw.get("dynasty", "")),
        "position": clean_html(raw.get("p_positioning", "")),
        "max_hp": max_hp, "max_hand": max_hand, "gender": gender,
        "skills": skills, "icon_url": str(raw.get("icon_url", "")),
        "difficulty": 2, "mode_viability": {},
        "last_updated": date.today().isoformat(),
    }
    return hero
```

> **设计思路：** `id` 和 `name` 缺失时拒绝整条数据（因为后续所有关联操作都依赖于它们）。`max_hp` / `max_hand` 转型失败使用默认值 4（绝大多数武将的体力和手牌就是 4），容忍偶发的格式不一致。

---

## 五、接口说明

本模块主要供内部业务层（抓取服务、公告服务、UI 主窗口）调用，也通过 `-m` 提供 CLI 入口：

| CLI 命令 | 功能 | 主要参数 |
|----------|------|----------|
| `python -m src.scraper.official` | 全量采集 | `--dry-run` 预览，`--output/-o` 指定路径，`--skip-images` 跳过头像，`--verbose/-v` 详细日志 |
| `python -m src.scraper.incremental` | 增量/指定采集 | `--incremental` / `--hero/-n` / `--hero-id` / `--output/-o` / `--dry-run` / `--skip-images` / `--verbose/-v` |

公共函数：

| 函数 | 文件 | 说明 |
|------|------|------|
| `fetch(url, binary=False)` | `crawler.py` | HTTP 请求，最多 3 次重试、`RETRY_DELAY=2s`；400/401/403/404 立即抛出 |
| `save_json_atomic(path, data)` | `crawler.py` | 临时文件写入后 `os.replace` 原子替换 |
| `clean_html(html_text)` | `crawler.py` | 去 HTML 标签、unescape、归一化空白 |
| `split_skill_desc(raw_desc)` | `crawler.py` | 按段落标题拆分技能描述/结算 |
| `transform(raw)` | `crawler.py` | 字段清洗与映射，返回 dict 或 None |
| `validate_heroes(heroes)` | `crawler.py` | Pydantic `Hero` 模型校验 |
| `fetch_all_raw()` | `crawler.py` | 端到端：HTML → chunk → JS 文本 → 原始 list |
| `download_hero_images(raw_list, image_dir=None, skip_existing=True)` | `crawler.py` | 头像下载，返回成功张数 |
| `find_chunk_url(html)` | `adapter.py` | 从 HTML 定位 JS chunk URL，失败带现场诊断 |
| `extract_js_array(js_text)` | `adapter.py` | 字符级状态机提取 `const e=[...]` 数组，忽略字符串内方括号 |
| `js_to_json(text)` | `adapter.py` | JS → JSON 字符级状态机（补引号键 / undefined→null / 去尾逗号） |
| `parse_heroes_chunk(js_text)` | `adapter.py` | 组合 `extract_js_array` + `js_to_json` |
| `fetch_latest_announcements()` | `announcement.py` | 公告 API，失败回退 HTML 解析 |
| `classify_hero_related(title, content_html, hero_names)` | `announcement.py` | 章节标题判定武将相关，返回 `(bool, list[dict])` |
| `extract_hero_changes(title, content_html)` | `announcement.py` | 提取武将变更事件（供时间轴持久化） |
| `build_timeline_events(announcements, cutoff_date=None)` | `announcement.py` | hero_related 公告 → 时间轴事件 |
| `hero_content_hash(hero)` | `announcement.py` | 官网字段内容哈希 |
| `build_hero_snapshot(heroes)` | `announcement.py` | `{id: {name, hash}}` 快照 |
| `diff_heroes(current, baseline)` | `announcement.py` | `{added, modified, removed}` 清单 |
| `fetch_baike_heroes()` | `announcement.py` | 获取并清洗百科武将，失败返回 None |
| `hero_field_diff_summary(local, official)` | `announcement.py` | 字段级差异中文摘要 |
| `format_hero_full_text(hero)` | `announcement.py` | 只读全文（用于确认对话框对比） |
| `build_update_candidates(announcements, local_heroes, official_heroes, diff)` | `announcement.py` | 组装"更新武将数据"确认候选 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.models.Hero` | Pydantic 模型，用于 `validate_heroes` 校验 |
| 依赖 | `src.config.env` | 读取 `PROJECT_ROOT` / `BUNDLE_ROOT` 与日志配置 |
| 依赖 | `src.config.logging_config` | 设置日志级别与输出 |
| 依赖 | `src.data.announcement_manager` | 公告持久化与 `AnnouncementStatus` 状态机 |
| 依赖 | `src.data.hero_timeline` | 时间轴事件追加与变更类型规范化 |
| 被调用方 | `src.business.fetching.hero_fetch_service` | 通过 QProcess 启动爬虫 CLI |
| 被调用方 | `src.business.announcement.announcement_service` | 公告检查 / 更新候选准备 |
| 被调用方 | `src.ui.app.main_window` | 菜单"数据 → 武将获取"触发爬虫 |