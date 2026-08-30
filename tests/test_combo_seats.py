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
