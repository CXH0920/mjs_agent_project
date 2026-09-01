# 模块：爬虫与数据采集

> 对应目录：`src/scraper/official_source/` + 根 CLI 入口
> 职责：从官网解析武将数据、数据清洗与校验、头像下载

---

## 一、模块职责

本模块负责从[名将杀官网百科](https://mjs.ztgame.com/baike/)获取武将原始数据。官网不提供 REST API，数据通过 Nuxt.js 打包在 JS chunk 中。模块的核心工作是从 JS 文本中提取数据、清洗字段、映射到 Pydantic 模型并输出为 JSON。

功能包括：
- **全量采集** — 从官网下载全部 165 个武将数据
- **增量采集** — 只采集本地没有的新武将
- **指定采集** — 按武将名或 ID 采集特定武将
- **头像下载** — 从官网下载武将头像到 `images/` 目录
- **公告监控** — 拉取官方公告 API，仅对 `【新增武将】/【武将调整】` 章节相关公告提醒；百科逐武将哈希 diff 确认“什么真的变了”

---

## 二、文件结构

```
src/scraper/
├── __init__.py
├── official.py         # 全量采集兼容 CLI 入口
├── incremental.py      # 增量/指定采集兼容 CLI 入口
└── official_source/
    ├── adapter.py      # 官网 HTML/JS chunk 解析适配器
    ├── crawler.py      # 网络请求、数据清洗、校验与头像下载
    ├── full.py         # 全量采集实现
    ├── incremental.py  # 增量/指定采集实现
    └── announcement.py # 公告 API/回退解析、武将相关判定、百科逐武将 diff
```

---

## 三、核心逻辑

### 3.1 JS chunk 解析管道

官网武将数据经过 5 步转换才能成为可用的 JSON：

```
官网首页 HTML
  ① fetch(BAIKE_URL) → HTML
  ② adapter.find_chunk_url(html) → JS chunk URL
  ③ fetch(chunk_url) → JS 文本（约 300KB）
  ④ adapter.parse_heroes_chunk(js_text) → Python list[dict]
```

**适配器边界：** `official_source/adapter.py` 集中承载官网页面与 chunk 的格式假设；官网改版时只需修改该文件。JS 数组不是合法 JSON，适配器内部的 `js_to_json()` 执行三步预处理：

1. **key 加引号** — `a:1` → `"a":1`
2. **`undefined` → `null`** — 这是合法 JS 值但 JSON 不认识
3. **移除尾部多余逗号** — `[1,2,]` → `[1,2]`

三步顺序不可调换——例如先移除逗号会破坏 `undefined,` 中的逗号定位。

### 3.2 数据清洗（transform）

每条原始数据经过 `transform()` 映射为模型字段：

| 原始字段 | 目标字段 | 处理方式 |
|----------|----------|----------|
| `id` | `hero.id` | 直接取，缺失则跳过整条 |
| `name` | `hero.name` | `clean_html()` 去标签解码，缺失则跳过整条 |
| `dynasty` | `hero.faction` | `clean_html()` |
| `p_positioning` | `hero.position` | `clean_html()` |
| `p_blood_max` | `hero.max_hp` | str→int，失败默认 4 |
| `p_card_max` | `hero.max_hand` | str→int，失败默认 4 |
| `gender` | `hero.gender` | 1→男、2→女，默认男 |
| `skill`（遍历） | `hero.skills[]` | `split_skill_desc()` 拆分 |
| `icon_url` | `hero.icon_url` | 直接取 |

**技能拆分逻辑（`split_skill_desc`）：**
按 HTML 的 `<p><strong>段落标题</strong></p>` 结构拆分：
- 保留「技能描述」→ `description`
- 保留「结算详情/结算详解/技能详解/技能详情」→ `settlement`
- 丢弃「技能典故」「设计思路」（lore 文本，对游戏策略无帮助）

### 3.3 增量采集

`incremental.py` 的三种模式：

```bash
python -m src.scraper.incremental --incremental        # 只追加本地没有的新武将
python -m src.scraper.incremental --hero 诸葛亮,关羽    # 按名称采集
python -m src.scraper.incremental --hero-id 52,114      # 按 ID 采集
```

增量去重：`load_existing_ids(path)` → 读取本地 JSON 的 ID 集合 → `incremental_collect()` 差集筛选。

### 3.4 头像下载

`download_hero_images()` 遍历官网原始数据，取 `icon_url` 和 `name`：
- 文件路径固定为 `images/{武将名}.png`；角色名仅允许中文、字母、数字、`_`、`-`，并拒绝 Windows 保留名
- 仅允许从 `https://siteres.ztgame.com` 下载，逐跳校验至多 3 次重定向
- 响应以 64 KiB 分块下载，最大 5 MiB；仅接受并用 Pillow 解码验证 PNG，像素数最大 4,000,000
- 临时文件通过验证后原子替换正式头像；下载失败只打 warning 并保留已有头像，不中断流程
- `skip_existing=True` 时跳过已存在的文件

### 3.5 公告监控

公告列表页是 Nuxt 对公开 JSON API 的 SSR 展示，接口为 `https://ucmsv2api.ztgame.com/api/news/list`（`site=mjs&type=notice&page=1&per_page=5`），单次返回 5 条公告全文。

- `fetch_latest_announcements()` — 请求公告 API；失败回退解析 `notice-1.html` 的 `<li>`（仅 title/date/url，`content_missing=True`）。
- `classify_hero_related(title, content_html, hero_names)` — 仅按 `【新增武将】/【武将调整】`（含加强/削弱/修改变体）章节标题判定；在章节内解析 `名称（增强|削弱|调整）` 或已知武将名；正文其他位置提及武将名（修复、副本内容）不判相关；不在本地名单的名字标“未收录”。
- `hero_content_hash(hero)` — 官网字段（name/faction/position/max_hp/max_hand/gender/skills/icon_url）NFKC+去标签规范化后 md5，不含本地扩展字段。
- `fetch_baike_heroes()` — 复用 `fetch_all_raw() → transform() → validate_heroes()` 获取清洗后百科武将，失败返回 None 不中断。
- `build_hero_snapshot()` / `diff_heroes()` — 与 `data/baike_snapshot.json` 逐武将比对，输出 `{added, modified, removed}`。
- `hero_field_diff_summary(local, official)` — 字段级差异摘要（势力/定位/性别/体力/手牌/技能描述与结算，含新增/移除技能），中文、每行限长截断。
- `format_hero_full_text(hero)` — 武将只读全文（用于确认对话框的本地 vs 官网对比）。
- `build_update_candidates(announcements, local_heroes, official_heroes, diff)` — 组装“更新武将数据”确认候选：ready 公告解析武将与 diff added/modified 并集、按名称去重（后续来源补充缺失 ID）、附带摘要与本地/官网全文；`official_heroes=None` 时跳过摘要计算（降级）。

### 3.6 实战配队导入（`src/scripts/import_combos.py`，2026-08 新增）

`src/business/maintenance/combo_import_service.run_import()` 是 CLI 与 UI 导入对话框共用的业务层入口：

1. 武将名 → 角色 ID 映射（heroes.json），未匹配项进报告，不静默丢弃
2. note 座次解析（`combo_seats.parse_seats`），解析失败/部分成功的条目照常导入（座次留空）并列入报告供人工复核
3. 解析结果与 `position` 字段交叉校验（以 note 为准），不一致清单进报告
4. **合并语义**：源导出记录 upsert；`manual=True` 手工记录保留（同 key 冲突时手工优先）；非手工记录若源中已不存在则移除并计数
5. 重复执行输出稳定（幂等）

报告字段：`total` / `imported` / `unmatched` / `duplicates` / `invalid` / `seat_stats` / `seat_review` / `position_mismatch` / `manual_kept` / `manual_collisions` / `removed_stale`

### 4.1 括号深度计数器提取 JS 数组

```python
def extract_js_array(js_text: str) -> str:
    start = js_text.find("const e=[")
    if start == -1:
        raise ValueError("未找到 const e= 数组")
    start += len("const e=")
    depth = 0
    for i in range(start, len(js_text)):
        if js_text[i] == '[':
            depth += 1
        elif js_text[i] == ']':
            depth -= 1
            if depth == 0:
                return js_text[start:i+1]
    raise ValueError("未找到匹配的数组结束位置")
```

> **设计思路：** 不使用正则 `r"\[.*\]"` 是因为 JS 数组嵌套对象，内部可能包含方括号字符。深度计数器精确匹配外层数组边界，不会被嵌套结构干扰。

### 4.2 字段清洗与默认值

```python
def transform(raw: dict) -> dict | None:
    hero_id = raw.get("id")
    name = clean_html(raw.get("name", ""))
    if hero_id is None or not name:
        return None  # 关键字段缺失，跳过整条

    return {
        "id": hero_id,
        "name": name,
        "max_hp": _safe_int(raw.get("p_blood_max"), 4),  # 失败默认 4
        "max_hand": _safe_int(raw.get("p_card_max"), 4),  # 失败默认 4
        # ...
    }
```

> **设计思路：** `id` 和 `name` 缺失时拒绝整条数据（因为后续所有关联操作都依赖于它们）。`max_hp` 转型失败使用默认值 4（90% 武将的体力和手牌就是 4），容忍偶发的格式不一致。

---

## 五、接口说明

本模块提供 CLI 入口，不提供 Python API：

| CLI 命令 | 功能 | 主要参数 |
|----------|------|----------|
| `python -m src.scraper.official` | 全量采集 | `--dry-run` 预览，`--skip-images` 跳过头像 |
| `python -m src.scraper.incremental` | 增量采集 | `--incremental` / `--hero` / `--hero-id` |

公共函数：

| 函数 | 文件 | 说明 |
|------|------|------|
| `fetch(url, binary)` | `crawler.py` | HTTP 请求，3 次重试 |
| `find_chunk_url(html)` | `adapter.py` | 从 HTML 定位 JS chunk |
| `parse_heroes_chunk(js_text)` | `adapter.py` | 提取官网数组并转为 Python 数据 |
| `extract_js_array(js_text)` | `adapter.py` | 忽略字符串中方括号的深度扫描 |
| `js_to_json(text)` | `adapter.py` | JS → JSON 三步转换 |
| `transform(raw)` | `crawler.py` | 字段清洗与映射 |
| `validate_heroes(heroes)` | `crawler.py` | Pydantic 校验 |
| `save_json_atomic(path, data)` | `crawler.py` | 临时文件写入后原子替换，供全量和增量采集共用 |
| `download_hero_images(raw_list, image_dir)` | `crawler.py` | 头像下载 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.models` | 使用 Hero 模型进行 Pydantic 校验 |
| 依赖 | `src.config.env` | 读取日志级别配置 |
| 被调用方 | `src.business.fetching.hero_fetch_service` | 通过 QProcess 启动爬虫 CLI |
| 被调用方 | `src.ui.app.main_window` | 菜单"数据 → 武将获取"触发爬虫 |
