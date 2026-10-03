# 数据来源登记

本文件登记项目使用或曾使用的第三方内容来源。采集均为公开渠道、非商业、本地学习用途。
抓取数据自 2026-10-03 起不入版本库，经 `src/scripts/pull_data.py` 从私有数据仓获取
（历史说明见 LICENSE「第三方内容说明」）。

| 内容 | 来源 | 采集时间 | 备注 |
|---|---|---|---|
| 武将/技能数据 heroes.json | 官网武将百科页 | 随公告增量更新（见文件内 last_updated） | crawler 自动采集 |
| 卡牌数据 cards.json / special_cards.json | 官网卡牌页 | 2026-08 | 文案为官方原文 |
| 武将头像 images/（186 张） | 官网静态资源 | 随武将采集 | — |
| 攻略整理 raw_guides/jinxia/ | B 站 UP 主「讲者」名将杀攻略系列，视频清单见同目录 bilibili_videos_weijiang.csv | 2026-06 ~ 08 | 对视频讲解的文字化整理，非原文转载 |
| RAG 语料 rag_corpus/ | 官方规则文本 + 自研精化（curated） | 持续 | 武将/卡牌两文件含人工精化成果 |
| 公告/百科快照 announcements.json、baike_snapshot.json | 官网公告页 | 随检查更新 | 公告 diff 基线 |
| 卡牌点数花色 card_points.json | 官方卡牌的点数/花色（部分自归档 xlsx 导入） | 2026-08 起维护 | 「卡牌点数维护」页维护 |
| 装备属性 equip_attrs.json | 官方装备的距离/范围修正 | 2026-08 起维护 | 「装备属性维护」页维护 |
| 武将分类 hero_classification.json | 基于官方武将技能文本的自研归类 | 2026-09-30 更新 | 「武将分类维护」页维护 |
| 武将调整时间轴 mjs_adjustments.json | 官方加强/削弱公告的事件化记录 | 随公告更新 | 公告驱动追加 |

不入仓说明：`guides.json` / `synergies.json` 为 AI 生成内容（见 TERMS 第 7 节免责），
可随时重新生成，不随任何仓库分发。

权利人如需下架任何条目，请联系版权持有人（见 LICENSE 附加条款），将立即处理
（含历史版本，见 LICENSE「第三方内容说明」）。
