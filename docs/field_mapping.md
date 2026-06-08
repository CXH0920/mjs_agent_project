# 名将杀 Agent - 官网字段映射表

> 基于 <https://mjs.ztgame.com/baike/> 官网 JS 数据源分析。
> 数据源格式：Nuxt 服务端渲染的 `const e=[...]` 数组。
> 探查日期：2026-06-06

---

## 原始数据源结构

官网数据来源于 JS chunk 中的硬编码数组 `const e=[...]`，每条数据为一个对象。

### Hero 武将字段映射

| # | 原始字段 | 原始类型 | 示例值 | 映射模型字段 | 模型类型 | 纯净度 | 说明 |
|---|---------|---------|--------|------------|---------|--------|------|
| 1 | `id` | int | `165` | `Hero.id` | int | ✅ 纯净 | 游戏内武将编号 |
| 2 | `name` | str | `"吕蒙"` | `Hero.name` | str | ✅ 纯净 | 武将名 |
| 3 | `dynasty` | str | `"孙吴"` | `Hero.faction` | str | ✅ 纯净 | 所属势力 |
| 4 | `p_positioning` | str | `"控制"` | `Hero.position` | str | ✅ 纯净 | 定位(输出/辅助/控制/防御/攻击) |
| 5 | `p_blood_max` | str | `"6"` | `Hero.max_hp` | int | ⚠️ 类型转换 | 字符串→整数 |
| 6 | `p_card_max` | str | `"3"` | `Hero.max_hand` | int | ⚠️ 类型转换 | 字符串→整数 |
| 7 | `gender` | int | `1` 或 `2` | `Hero.gender` | Gender(enum) | ⚠️ 枚举映射 | `1`→`男`, `2`→`女` |
| 8 | `icon_url` | str | URL | — | — | ❌ 丢弃 | 仅前端使用 |
| 9 | `img_url` | str | URL | — | — | ❌ 丢弃 | 仅前端使用 |
| 10 | `img_url_m` | str | URL | — | — | ❌ 丢弃 | 仅前端使用 |
| 11 | `display_priority` | int | `98` | — | — | ❌ 丢弃 | 排序优先级 |
| 12 | `status` | int | `1` | — | — | ❌ 丢弃 | 发布状态(1=已发布) |
| 13 | `ctime` | str | `"2026-06-04 16:00:10"` | — | — | ❌ 丢弃 | 创建时间戳 |

### Skill 技能字段映射

| # | 原始字段 | 原始类型 | 示例值 | 映射模型字段 | 模型类型 | 纯净度 | 说明 |
|---|---------|---------|--------|------------|---------|--------|------|
| 1 | `skill_name` | str | `"白衣渡江"` | `Skill.name` | str | ✅ 纯净 | 技能名 |
| 2 | skill_desc | str(HTML) | HTML全文 | Skill.description + Skill.settlement | str | ⚠️ **需拆分清洗** | HTML→纯文本，按段落拆分为描述/结算，丢弃典故/设计思路 |

#### skill_desc 内部段落结构

`skill_desc` 为 HTML 格式，内部通过 `<p><strong>段落标题</strong></p>` 分隔，共 4个段落：

| 段落 | HTML标记 | 内容说明 | 是否保留 | 说明 |
|------|---------|---------|---------|------|
| 技能描述 | `<p><strong>技能描述</strong></p>` | 技能核心效果描述 | ✅ 保留到 description | 纯文本清洗后保留 |
| 结算详情/结算详解 | <p><strong>结算详情</strong></p> | 规则结算细则 | ✅ Skill.settlement | 单独存入 settlement 字段 |
| 技能典故 | <p><strong>技能典故</strong></p> | 历史典故出处 | ❌ 丢弃 | 不存入模型 |
| 设计思路 | <p><strong>设计思路</strong></p> | 设计思路说明 | ❌ 丢弃 | 不存入模型 |

**清洗策略**：先按 <p><strong>段落标题</strong></p> 结构拆分原始 HTML，再逐段清洗标签。技能描述→description，结算详情/结算详解→settlement，技能典故和设计思路丢弃。若技能描述段落缺失，记录异常日志。

---

## 模型补充字段说明

| 字段 | 来源 | 默认值 | 说明 |
|------|------|--------|------|
| `Hero.title` | 官网无此字段 | `""` | 英雄称号，官网数据不含，留空待后续补充 |
| `Hero.difficulty` | 官网无此字段 | `Difficulty.MEDIUM(2)` | 难度评级，官网不含，默认中等 |
| `Hero.mode_viability` | 官网无此字段 | `{}` | 各模式强度梯队，需 AI 生成 |
| `Hero.last_updated` | 当前日期 | `date.today()` | 自动填入采集日期 |

---

## 官网数据局限

1. **无 difficulty 字段**：官网不提供难度评级，需后续 AI 评估补全
2. **无 title 字段**：官网不提供武将称号，留空
3. **无 mode_viability**：各模式强度需 AI 或人工评估
4. **gender 为数字编码**：需要映射表转换
5. **skill_desc 为 HTML**：需要 HTML→纯文本转换
6. **p_card_max / p_blood_max 为字符串**：需转型为 int
