# 模块：爬虫与数据采集

> 对应目录：`src/scraper/`（不含 `ai_*.py` 的 AI 部分）
> 职责：从官网解析武将数据、数据清洗与校验、头像下载

---

## 一、模块职责

本模块负责从[名将杀官网百科](https://mjs.ztgame.com/baike/)获取武将原始数据。官网不提供 REST API，数据通过 Nuxt.js 打包在 JS chunk 中。模块的核心工作是从 JS 文本中提取数据、清洗字段、映射到 Pydantic 模型并输出为 JSON。

功能包括：
- **全量采集** — 从官网下载全部 165 个武将数据
- **增量采集** — 只采集本地没有的新武将
- **指定采集** — 按武将名或 ID 采集特定武将
- **头像下载** — 从官网下载武将头像到 `images/` 目录

---

## 二、文件结构

```
src/scraper/
├── __init__.py
├── crawler.py          # 爬虫核心：网络请求、JS 解析、数据清洗、Pydantic 校验
├── official.py         # 全量采集 CLI 入口
└── incremental.py      # 增量/指定采集 CLI 入口
```

---

## 三、核心逻辑

### 3.1 JS chunk 解析管道

官网武将数据经过 5 步转换才能成为可用的 JSON：

```
官网首页 HTML
  ① fetch(BAIKE_URL) → HTML
  ② find_chunk_url(html) → JS chunk URL
  ③ fetch(chunk_url) → JS 文本（约 300KB）
  ④ extract_js_array(js_text) → JSON-like 字符串
  ⑤ js_to_json(text) → Python list[dict]
```

**难点：** JS 数组不是合法 JSON。`js_to_json()` 执行三步预处理：

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

---

## 四、关键代码片段

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
| `find_chunk_url(html)` | `crawler.py` | 从 HTML 定位 JS chunk |
| `extract_js_array(js_text)` | `crawler.py` | 括号深度计数器提取数组 |
| `js_to_json(text)` | `crawler.py` | JS → JSON 三步转换 |
| `transform(raw)` | `crawler.py` | 字段清洗与映射 |
| `validate_heroes(heroes)` | `crawler.py` | Pydantic 校验 |
| `download_hero_images(raw_list, image_dir)` | `crawler.py` | 头像下载 |

---

## 六、模块间关系

| 方向 | 模块 | 说明 |
|------|------|------|
| 依赖 | `src.data.models` | 使用 Hero 模型进行 Pydantic 校验 |
| 依赖 | `src.config.env` | 读取日志级别配置 |
| 被调用方 | `src.business.fetch_service` | 通过 QProcess 启动爬虫 CLI |
| 被调用方 | `src.ui.main_window` | 菜单"数据 → 武将获取"触发爬虫 |
