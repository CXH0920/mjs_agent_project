"""名将杀 Agent - AI 批量生成工具单元测试"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest
from scraper.ai_batch import (
    AIBatchGenerator,
    _estimate_cost,
    _save_json,
    estimate_cost,
    load_heroes,
    load_prompt,
)


class TestLoadPrompt:
    def test_load_existing_file(self) -> None:
        """加载已存在的 prompt 文件"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("# Test Prompt\n内容")
            tmp_path = f.name
        try:
            result = load_prompt(Path(tmp_path))
            assert result == "# Test Prompt\n内容"
        finally:
            os.unlink(tmp_path)

    def test_load_nonexistent_file(self) -> None:
        """加载不存在的 prompt 文件应返回空字符串"""
        result = load_prompt(Path("/nonexistent/prompt.md"))
        assert result == ""


class TestEstimateCost:
    def test_guide_cost(self) -> None:
        """估算攻略生成成本"""
        result = estimate_cost(149, "guide")
        assert result["mode"] == "guide"
        assert result["items"] == 149
        assert result["estimated_tokens"] > 0
        assert result["estimated_cost_cny"] > 0

    def test_synergy_cost(self) -> None:
        """估算相性评分生成成本"""
        result = estimate_cost(10, "synergy")
        assert result["mode"] == "synergy"
        assert result["items"] == 45  # 10*9/2
        assert result["estimated_tokens"] > 0

    def test_zero_heroes(self) -> None:
        """0 武将的成本应为 0"""
        result = estimate_cost(0, "guide")
        assert result["items"] == 0
        assert result["estimated_tokens"] == 0

    def test_cost_unit(self) -> None:
        """费用单位为 CNY"""
        result = estimate_cost(1, "guide")
        assert "estimated_cost_cny" in result
        assert isinstance(result["estimated_cost_cny"], float)


class TestInternalEstimateCost:
    def test_basic_calculation(self) -> None:
        """_estimate_cost 基本计算"""
        cost = _estimate_cost(1_000_000, 500_000)
        # 1M input @ CNY3/M = 3; 500K output @ CNY6/M = 3; total = 6
        assert cost == 6.0

    def test_zero_tokens(self) -> None:
        """0 token 费用为 0"""
        cost = _estimate_cost(0, 0)
        assert cost == 0.0

    def test_small_values(self) -> None:
        """小数量 token 费用正确"""
        cost = _estimate_cost(1000, 500)
        # 1000 * 3 / 1_000_000 + 500 * 6 / 1_000_000 = 0.003 + 0.003 = 0.006
        assert cost == 0.006

class TestSaveJson:
    def test_atomic_write(self) -> None:
        """_save_json 应原子写入 JSON 文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            data = [{"id": 1, "name": "test"}]
            _save_json(filepath, data)
            assert filepath.exists()
            # .tmp 文件不应存在（已被 rename）
            assert not filepath.with_suffix(".tmp").exists()
            loaded = json.loads(filepath.read_text(encoding="utf-8"))
            assert loaded == data

    def test_overwrite(self) -> None:
        """覆盖已有文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "test.json"
            _save_json(filepath, [{"v": 1}])
            _save_json(filepath, [{"v": 2}])
            loaded = json.loads(filepath.read_text(encoding="utf-8"))
            assert loaded == [{"v": 2}]


class TestAIBatchGenerator:
    def test_extract_json_direct(self) -> None:
        """直接解析 JSON"""
        result = AIBatchGenerator._extract_json('{"hero_id": 114, "name": "\u8bf8\u845b\u4eae"}')
        assert result["hero_id"] == 114

    def test_extract_json_from_code_block(self) -> None:
        """从 ```json 代码块提取 JSON"""
        text = "```json\n{\"hero_id\": 115}\n```"
        result = AIBatchGenerator._extract_json(text)
        assert result["hero_id"] == 115

    def test_extract_json_from_plain_block(self) -> None:
        """从 ``` 代码块提取 JSON"""
        text = "```\n{\"hero_id\": 116}\n```"
        result = AIBatchGenerator._extract_json(text)
        assert result["hero_id"] == 116

    def test_extract_json_invalid_raises(self) -> None:
        """无效 JSON 应抛出异常"""
        with pytest.raises(Exception):
            AIBatchGenerator._extract_json("not json at all")

    def test_extract_json_from_separator(self) -> None:
        """从 --- 分隔线后提取 JSON（代码块内）"""
        text = "## 攻略正文\n内容...\n\n---\n\n```json\n{\"hero_id\": 117}\n```"
        result = AIBatchGenerator._extract_json(text)
        assert result["hero_id"] == 117

    def test_extract_json_from_separator_no_codeblock(self) -> None:
        """从 --- 分隔线后提取 JSON（无代码块）"""
        text = "## 正文\n分析内容\n\n---\n\n{\"hero_id\": 118, \"score\": 5}"
        result = AIBatchGenerator._extract_json(text)
        assert result["hero_id"] == 118
        assert result["score"] == 5

    def test_convert_ids_to_int(self) -> None:
        """字符串 ID 转 int"""
        data = {"counters": ["129", "130"], "synergizes_with": ["141"]}
        result = AIBatchGenerator._convert_ids_to_int(data, ["counters", "synergizes_with"])
        assert result["counters"] == [129, 130]
        assert result["synergizes_with"] == [141]

    def test_convert_ids_int_already_int(self) -> None:
        """已经是 int 的 ID 不应改变"""
        data = {"counters": [129, 130]}
        result = AIBatchGenerator._convert_ids_to_int(data, ["counters"])
        assert result["counters"] == [129, 130]

    def test_convert_ids_empty_list(self) -> None:
        """空列表不应报错"""
        data = {"counters": []}
        result = AIBatchGenerator._convert_ids_to_int(data, ["counters"])
        assert result["counters"] == []

    def test_validate_guide_success(self) -> None:
        """Pydantic 攻略校验成功"""
        gen = AIBatchGenerator(api_key="test")
        data = {
            "hero_id": 114,
            "key_points": ["要点1", "要点2"],
            "counters": [129],
            "synergizes_with": [141],
            "description": "攻略正文",
            "tips_for_beginners": "新手提示",
            "last_updated": "2026-06-07",
        }
        result = gen._validate_guide(data)
        assert result is not None
        assert result["hero_id"] == 114
        assert result["counters"] == [129]
        assert result["synergizes_with"] == [141]

    def test_validate_guide_failure(self) -> None:
        """Pydantic 攻略校验失败应返回 None"""
        gen = AIBatchGenerator(api_key="test")
        data = {"key_points": ["要点"]}
        result = gen._validate_guide(data)
        assert result is None

    def test_validate_synergy_success(self) -> None:
        """Pydantic 相性校验成功"""
        gen = AIBatchGenerator(api_key="test")
        data = {
            "hero_a_id": 114,
            "hero_b_id": 115,
            "score": 7,
            "synergy_rating": "S",
            "combo_ceiling": 8,
            "combo_stability": 6,
            "adaptability": 7,
            "description": "测试相性",
        }
        result = gen._validate_synergy(data)
        assert result is not None
        assert result["hero_a_id"] == 114
        assert result["hero_b_id"] == 115
        assert result["synergy_rating"] == "S"

    def test_validate_synergy_failure(self) -> None:
        """Pydantic 相性校验失败应返回 None"""
        gen = AIBatchGenerator(api_key="test")
        data = {
            "hero_a_id": 114,
            "hero_b_id": 115,
            "score": 100,  # 超出 -10~10 范围
            "synergy_rating": "S",
            "combo_ceiling": 8,
            "combo_stability": 6,
            "adaptability": 7,
        }
        result = gen._validate_synergy(data)
        assert result is None

    def test_build_guide_prompt(self) -> None:
        """构建攻略 prompt 包含武将信息"""
        gen = AIBatchGenerator(api_key="test")
        hero = {
            "id": 114, "name": "诸葛亮", "title": "卧龙",
            "faction": "蜀", "position": "控制",
            "max_hp": 4, "max_hand": 4, "gender": "男",
            "difficulty": 3,
            "skills": [{"name": "观星", "description": "控制牌堆"}],
        }
        prompt = gen._build_guide_prompt(hero)
        assert "诸葛亮" in prompt
        assert "观星" in prompt
        assert "114" in prompt
        assert "体力上限" in prompt
        assert "手牌上限" in prompt
        assert "性别" in prompt

    def test_build_synergy_prompt(self) -> None:
        """构建相性 prompt 包含双方武将（管道分隔格式）"""
        gen = AIBatchGenerator(api_key="test")
        ha = {
            "id": 114, "name": "诸葛亮", "max_hp": 4,
            "position": "控制", "skills": [],
        }
        hb = {
            "id": 115, "name": "曹操", "max_hp": 5,
            "position": "防御", "skills": [],
        }
        prompt = gen._build_synergy_prompt(ha, hb)
        assert "=== 武将 A ===" in prompt
        assert "=== 武将 B ===" in prompt
        assert "诸葛亮" in prompt
        assert "曹操" in prompt
        # 验证管道分隔格式
        assert " | " in prompt
        assert "114" in prompt
        assert "115" in prompt
        assert "无技能" in prompt

    def test_combat_synergy_compatibility(self) -> None:
        """兼容旧 prompt 中的 combat_synergy 字段"""
        gen = AIBatchGenerator(api_key="test")
        text = json.dumps({
            "hero_a_id": 1, "hero_b_id": 2, "score": 5,
            "synergy_rating": "A", "combat_synergy": 7,
            "combo_stability": 6, "adaptability": 5, "description": "test"
        })
        data = gen._extract_json(text)
        # 模拟 generate_synergy 中的兼容逻辑
        if "combat_synergy" in data and "combo_ceiling" not in data:
            data["combo_ceiling"] = data.pop("combat_synergy")
        assert "combat_synergy" not in data
        assert data["combo_ceiling"] == 7


class TestLoadHeroes:
    def test_load_valid_file(self) -> None:
        """加载有效的武将 JSON 文件"""
        heroes = [{"id": 1, "name": "曹操"}, {"id": 2, "name": "诸葛亮"}]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as f:
            json.dump(heroes, f)
            tmp_path = f.name
        try:
            result = load_heroes(tmp_path)
            assert len(result) == 2
            assert result[0]["name"] == "曹操"
        finally:
            os.unlink(tmp_path)

    def test_load_nonexistent_file(self) -> None:
        """加载不存在的文件应返回空列表"""
        result = load_heroes("/nonexistent/heroes.json")
        assert result == []
