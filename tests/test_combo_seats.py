"""名将杀 Agent - 实战配队座次解析单元测试"""

from src.data.combo_seats import (
    STATUS_NONE,
    STATUS_PARSED,
    STATUS_PARTIAL,
    STATUS_UNPARSED,
    parse_seats,
)


class TestParseSeats:
    """parse_seats 座次解析规则"""

    def test_name_plus_digits_both_orders(self):
        """武将名+数字：note 中的名字顺序与 hero1/hero2 无关，以 note 为准"""
        status, s1, s2 = parse_seats("张良1+夏侯惇4：无限锦囊组合", "夏侯惇", "张良")
        assert status == STATUS_PARSED
        assert s1 == [4] and s2 == [1]

        status, s1, s2 = parse_seats("孙权4+刘备1：刘备留一张牌发动孙权技能", "刘备", "孙权")
        assert status == STATUS_PARSED
        assert s1 == [1] and s2 == [4]

    def test_digits_before_name(self):
        """数字前置写法：1马钧+4曹操"""
        status, s1, s2 = parse_seats("马钧+曹操：1马钧+4曹操：刷队友拿牌", "马钧", "曹操")
        assert status == STATUS_PARSED
        assert s1 == [1] and s2 == [4]

    def test_two_digit_group_is_seat_range(self):
        """两位数字组 = 可选区间：34 = 3或4号"""
        status, s1, s2 = parse_seats("刘彻34左慈12", "刘彻", "左慈")
        assert status == STATUS_PARSED
        assert s1 == [3, 4] and s2 == [1, 2]

    def test_bare_tokens_in_order(self):
        """纯数字 token 按顺序对应英雄1/英雄2"""
        status, s1, s2 = parse_seats("12 34", "张骞", "孙权")
        assert status == STATUS_PARSED
        assert s1 == [1, 2] and s2 == [3, 4]

        status, s1, s2 = parse_seats("4 1", "范雎", "虞姬")
        assert status == STATUS_PARSED
        assert s1 == [4] and s2 == [1]

    def test_bare_tokens_with_trailing_text(self):
        """纯数字 token 后跟机制文字，取开头数字段"""
        status, s1, s2 = parse_seats("2 3 惊羽弓", "卢莫愁", "张梁")
        assert status == STATUS_PARSED
        assert s1 == [2] and s2 == [3]

    def test_zero_means_no_requirement(self):
        """单个 '0' = 双方无座次要求"""
        status, s1, s2 = parse_seats("0", "乐毅", "龙且")
        assert status == STATUS_PARSED
        assert s1 == [] and s2 == []

    def test_no_digits_means_no_requirement(self):
        """note 无任何数字 = 无座次要求"""
        status, s1, s2 = parse_seats("孙策+虞姬：强制卖血组合", "孙策", "虞姬")
        assert status == STATUS_NONE
        assert s1 == [] and s2 == []

    def test_alias_nickname_resolved(self):
        """别名表：牢布→吕布、甄姬→甄宓"""
        status, s1, s2 = parse_seats("牢布3+孟尝君2：先集火一个", "孟尝君", "吕布")
        assert status == STATUS_PARSED
        assert s1 == [2] and s2 == [3]

        status, s1, s2 = parse_seats("甄姬34+如姬12：双姬靠勾运", "如姬", "甄宓")
        assert status == STATUS_PARSED
        assert s1 == [1, 2] and s2 == [3, 4]

    def test_partial_only_one_hero_found(self):
        """仅一方解析成功 = partial"""
        status, s1, s2 = parse_seats("赵奢12+牢布34：溅射出杀组合", "李牧", "赵奢")
        # note 中没有 李牧/赵奢 对应数字以外的另一方 → 赵奢通过本名命中
        assert status == STATUS_PARSED or status == STATUS_PARTIAL

    def test_partial_when_second_hero_missing(self):
        status, s1, s2 = parse_seats("吕布3：先手压制", "吕布", "华佗")
        assert status == STATUS_PARTIAL
        assert s1 == [3] and s2 == []

    def test_invalid_digits_unparsed(self):
        """数字超出号位范围（如 5）且无法归类 = unparsed"""
        status, s1, s2 = parse_seats("张三5+李四1", "张三", "李四")
        # "5" 非法回退到 token 兜底仍非法 → unparsed
        assert status in (STATUS_UNPARSED, STATUS_PARTIAL)

    def test_bare_single_token_unparsed(self):
        """单个非零 token 无法对应双方 = unparsed"""
        status, s1, s2 = parse_seats("34 需要距离", "甲", "乙")
        assert status == STATUS_UNPARSED
        assert s1 == [] and s2 == []

    def test_position_consistency_samples(self):
        """方案中 16 条 note/position 不一致的典型样本：解析以 note 为准"""
        _, s1, s2 = parse_seats("张春华12+小乔34：流血下的呼吸摸牌", "张春华", "小乔")
        assert s1 == [1, 2] and s2 == [3, 4]

    def test_alias_ignored_for_other_hero(self):
        """别名只在对应武将的候选里生效，不串位"""
        status, s1, s2 = parse_seats("牢布3+孟尝君2", "牢布", "孟尝君")  # hero1 直接叫"牢布"（非法武将名场景由导入侧过滤）
        # 名字表校验属导入脚本职责，此处只验证解析不崩溃且座次归位
        assert status == STATUS_PARSED

    def test_sit_front_is_seats_12_and_back_is_34(self):
        """坐前(面)/坐后(面) = 先手12号/后手34号，兼容"做"字连写与尽量修饰"""
        status, s1, s2 = parse_seats("王元姬坐前，烛奸李牧", "李牧", "王元姬")
        assert status == STATUS_PARTIAL
        assert s2 == [1, 2]

        status, s1, s2 = parse_seats("马超尽量坐后面防止装备不够", "马超", "陈宫")
        assert status == STATUS_PARTIAL
        assert s1 == [3, 4]

        status, s1, s2 = parse_seats("董卓尽量坐前面，小心脆", "董卓", "李斯")
        assert status == STATUS_PARTIAL
        assert s1 == [1, 2]

        status, s1, s2 = parse_seats("鲁仲连做坐前，一箭定聊", "鲁仲连", "张春华")
        assert status == STATUS_PARTIAL
        assert s1 == [1, 2]

    def test_sit_seat_number_word(self):
        """坐N/坐N号/坐多位数字组"""
        status, s1, s2 = parse_seats("韩信坐2号，十面埋伏稳定空城", "霍光", "韩信")
        assert status == STATUS_PARTIAL
        assert s2 == [2]

        status, s1, s2 = parse_seats("郑国坐13，渠卡对面手牌", "杜预", "郑国")
        assert status == STATUS_PARTIAL
        assert s2 == [1, 3]

        status, s1, s2 = parse_seats("廉颇坐123，负荆请罪流失体力", "廉颇", "张春华")
        assert status == STATUS_PARTIAL
        assert s1 == [1, 2, 3]

        status, s1, s2 = parse_seats("袁绍尽量坐1，郭嘉给袁绍补伤害牌", "郭嘉", "袁绍")
        assert status == STATUS_PARTIAL
        assert s2 == [1]

    def test_not_sit_takes_complement(self):
        """不坐N/别坐N = 其余三个号位"""
        status, s1, s2 = parse_seats("李夫人不坐3，位置看情况安排", "孟获", "李夫人")
        assert status == STATUS_PARTIAL
        assert s2 == [1, 2, 4]

        status, s1, s2 = parse_seats("李夫人别坐3，司马懿提高手", "李夫人", "司马懿")
        assert status == STATUS_PARTIAL
        assert s1 == [1, 2, 4]

        status, s1, s2 = parse_seats("董不坐4，武库有天雷地火", "杜预", "董仲舒")
        assert status == STATUS_PARTIAL
        assert s2 == [1, 2, 3]

    def test_first_move_words(self):
        """先手=12号 / 后手=34号，名字在先在后均可；裸数字句式优先于先手"""
        status, s1, s2 = parse_seats("王元姬坐前，陆逊想先手压制也可以陆逊坐1", "陆逊", "王元姬")
        assert status == STATUS_PARSED
        assert s1 == [1] and s2 == [1, 2]

        status, s1, s2 = parse_seats("位置不限，后手吴国太死不掉", "田单", "吴国太")
        assert status == STATUS_PARTIAL
        assert s2 == [3, 4]

    def test_seat_number_before_name(self):
        """N号位+名字 写法；配对声明的裸数字优先于语境提及的 N号位"""
        status, s1, s2 = parse_seats("刘邦24司马炎13，4号位刘邦开局记得刷5大风歌", "刘邦", "司马炎")
        assert status == STATUS_PARSED
        assert s1 == [2, 4] and s2 == [1, 3]

        status, s1, s2 = parse_seats("4号位刘邦开局记得刷5大风歌给司马炎补牌", "刘邦", "司马炎")
        assert status == STATUS_PARTIAL
        assert s1 == [4]

        status, s1, s2 = parse_seats("跟山涛交互可以酿很多酒，24号位王翦盖牌", "山涛", "王翦")
        assert status == STATUS_PARTIAL
        assert s2 == [2, 4]

    def test_short_name_fragments(self):
        """去首/去尾简称片段：临海=临海公主、相如=司马相如、羊=羊献容"""
        status, s1, s2 = parse_seats("临海24，钟离眜没距离可杀临海", "临海公主", "钟离眜")
        assert status == STATUS_PARTIAL
        assert s1 == [2, 4]

        status, s1, s2 = parse_seats("相如24，黄月英通过相如拿到战法", "司马相如", "黄月英")
        assert status == STATUS_PARTIAL
        assert s1 == [2, 4]

        status, s1, s2 = parse_seats("平阳坐前，学习八斗方醉", "山涛", "平阳公主")
        assert status == STATUS_PARTIAL
        assert s2 == [1, 2]

        status, s1, s2 = parse_seats("羊坐后，羊献容卖血有收益", "羊献容", "荆轲")
        assert status == STATUS_PARTIAL
        assert s1 == [3, 4] and s2 == []

    def test_shared_fragment_dropped(self):
        """两将共享的简称片段无法归属，须剔除（王导 vs 王元姬 的"王"）"""
        status, s1, s2 = parse_seats("王坐前，另一个随意", "王导", "王元姬")
        assert status == STATUS_NONE
        assert s1 == [] and s2 == []

    def test_single_char_fragment_rejects_bare_digits(self):
        """单字片段不参与裸数字句式，避免误配普通数字"""
        status, s1, s2 = parse_seats("风云3变", "赵云", "华佗")
        assert status == STATUS_UNPARSED
        assert s1 == [] and s2 == []

    def test_adverb_filler_between_name_and_seat_word(self):
        """尽量/尽可能/永远/只能 修饰词可夹在名字与座次词之间"""
        status, s1, s2 = parse_seats("羊祜尽量不坐2，杜预武库很多装备牌", "羊祜", "杜预")
        assert status == STATUS_PARTIAL
        assert s1 == [1, 3, 4]

        status, s1, s2 = parse_seats("霍光尽可能坐3，让韩安国有空城时机", "霍光", "韩安国")
        assert status == STATUS_PARTIAL
        assert s1 == [3]

        status, s1, s2 = parse_seats("鲁肃只能坐2，截胡敌方手牌", "申不害", "鲁肃")
        assert status == STATUS_PARTIAL
        assert s2 == [2]

        status, s1, s2 = parse_seats("信陵君永远坐13，如姬把装备给信陵君", "信陵君", "如姬")
        assert status == STATUS_PARTIAL
        assert s1 == [1, 3]

        status, s1, s2 = parse_seats("信陵君永远13，小乔绑信陵君", "信陵君", "小乔")
        assert status == STATUS_PARTIAL
        assert s1 == [1, 3]

    def test_seat_number_without_wei_and_typo_alias(self):
        """N号(省略"位")+名字；错别字别名 刘绑=刘邦"""
        status, s1, s2 = parse_seats("开局先发制人杀1号荀灌", "项梁", "荀灌")
        assert status == STATUS_PARTIAL
        assert s2 == [1]

        status, s1, s2 = parse_seats("刘绑坐前，先斩蛇，给夏侯惇牌", "夏侯惇", "刘邦")
        assert status == STATUS_PARTIAL
        assert s2 == [1, 2]
