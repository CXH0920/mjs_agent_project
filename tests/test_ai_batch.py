"""名将杀 Agent - AI 批量生成工具单元测试"""

import json
import os
import tempfile
from pathlib import Path

import pytest
from src.scraper.ai_batch import (
    _estimate_cost,
    estimate_cost,
    load_heroes,
)
from src.scraper.ai_utils import _save_json
from src.scraper.prompt_utils import load_prompt
from src.scraper.json_extract import extract_json
from src.scraper.ai_utils import (
    convert_ids_to_int,
    validate_guide,
    validate_synergy,
)
from src.scraper.prompt_utils import (
    build_guide_prompt,
    build_synergy_prompt,
)

from src.config.env import parse_env_file, get_api_config, get_runtime_params


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

    def test_unknown_model_has_no_cost_estimate(self) -> None:
        result = estimate_cost(1, "guide", "unknown-model")
        assert result["estimated_cost_cny"] is None
        assert not result["pricing_available"]
        assert "无法自动估算" in result["message"]


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

    def test_unknown_model_returns_none(self) -> None:
        assert _estimate_cost(1000, 500, "unknown-model") is None

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
            assert b"\r\n" not in filepath.read_bytes()
            assert filepath.read_bytes().endswith(b"\n")

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
        result = extract_json('{"hero_id": 114, "name": "\u8bf8\u845b\u4eae"}')
        assert result["hero_id"] == 114

    def test_extract_json_from_code_block(self) -> None:
        """从 ```json 代码块提取 JSON"""
        text = "```json\n{\"hero_id\": 115}\n```"
        result = extract_json(text)
        assert result["hero_id"] == 115

    def test_extract_json_from_plain_block(self) -> None:
        """从 ``` 代码块提取 JSON"""
        text = "```\n{\"hero_id\": 116}\n```"
        result = extract_json(text)
        assert result["hero_id"] == 116

    def test_extract_json_invalid_raises(self) -> None:
        """无效 JSON 应抛出异常"""
        with pytest.raises(Exception):
            extract_json("not json at all")

    def test_extract_json_from_separator(self) -> None:
        """从 --- 分隔线后提取 JSON（代码块内）"""
        text = "## 攻略正文\n内容...\n\n---\n\n```json\n{\"hero_id\": 117}\n```"
        result = extract_json(text)
        assert result["hero_id"] == 117

    def test_extract_json_from_separator_no_codeblock(self) -> None:
        """从 --- 分隔线后提取 JSON（无代码块）"""
        text = "## 正文\n分析内容\n\n---\n\n{\"hero_id\": 118, \"score\": 5}"
        result = extract_json(text)
        assert result["hero_id"] == 118
        assert result["score"] == 5

    def test_convert_ids_to_int(self) -> None:
        """字符串 ID 转 int"""
        data = {"weak_against_type": ["高爆发型"], "synergizes_with": ["141"]}
        result = convert_ids_to_int(data, ["synergizes_with"])
        assert result["weak_against_type"] == ["高爆发型"]
        assert result["synergizes_with"] == [141]

    def test_convert_ids_int_already_int(self) -> None:
        """已经是 int 的 ID 不应改变"""
        data = {"synergizes_with": [129, 130]}
        result = convert_ids_to_int(data, ["synergizes_with"])
        assert result["synergizes_with"] == [129, 130]

    def test_convert_ids_empty_list(self) -> None:
        """空列表不应报错"""
        data = {"synergizes_with": []}
        result = convert_ids_to_int(data, ["synergizes_with"])
        assert result["synergizes_with"] == []

    def test_validate_guide_success(self) -> None:
        """Pydantic 攻略校验成功"""
        data = {
            "hero_id": 114,
            "key_points": ["要点1", "要点2"],
            "weak_against_type": ["高爆发型"],
            "strong_against_type": ["慢速防御型"],
            "synergizes_with": [141],
            "counter_strategy": "保留闪避",
            "description": "攻略正文",
            "tips_for_beginners": "新手提示",
            "last_updated": "2026-06-07",
        }
        result = validate_guide(data)
        assert result is not None
        assert result["hero_id"] == 114
        assert result["weak_against_type"] == ["高爆发型"]
        assert result["strong_against_type"] == ["慢速防御型"]
        assert result["synergizes_with"] == [141]

    def test_validate_guide_failure(self) -> None:
        """Pydantic 攻略校验失败应返回 None"""
        data = {"key_points": ["要点"]}
        result = validate_guide(data)
        assert result is None

    def test_validate_synergy_success(self) -> None:
        """Pydantic 相性校验成功"""
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
        result = validate_synergy(data)
        assert result is not None
        assert result["hero_a_id"] == 114
        assert result["hero_b_id"] == 115
        assert result["synergy_rating"] == "A"

    def test_validate_synergy_failure(self) -> None:
        """Pydantic 相性校验失败应返回 None"""
        data = {
            "hero_a_id": 114,
            "hero_b_id": 115,
            "score": 100,  # 超出 -10~10 范围
            "synergy_rating": "S",
            "combo_ceiling": 8,
            "combo_stability": 6,
            "adaptability": 7,
        }
        result = validate_synergy(data)
        assert result is None

    def test_build_guide_prompt(self) -> None:
        """构建攻略 prompt 包含武将信息"""
        hero = {
            "id": 114, "name": "诸葛亮", "title": "卧龙",
            "faction": "蜀", "position": "控制",
            "max_hp": 4, "max_hand": 4, "gender": "男",
            "difficulty": 3,
            "skills": [{"name": "观星", "description": "控制牌堆"}],
        }
        prompt = build_guide_prompt(hero)
        assert "诸葛亮" in prompt
        assert "观星" in prompt
        assert "定位" in prompt
        assert "体力" in prompt
        assert "手牌" in prompt
        assert "性别" in prompt

    def test_build_synergy_prompt(self) -> None:
        """构建相性 prompt 包含双方武将信息"""
        ha = {
            "id": 114, "name": "诸葛亮", "max_hp": 4,
            "position": "控制", "skills": [],
        }
        hb = {
            "id": 115, "name": "曹操", "max_hp": 5,
            "position": "防御", "skills": [],
        }
        prompt = build_synergy_prompt(ha, hb)
        assert "## 武将 A:" in prompt or "## 武将 A：" in prompt
        assert "## 武将 B:" in prompt or "## 武将 B：" in prompt
        assert "诸葛亮" in prompt
        assert "曹操" in prompt
        assert "控制" in prompt
        assert "防御" in prompt
        assert "体力/手牌" in prompt

    def test_combat_synergy_compatibility(self) -> None:
        """兼容旧 prompt 中的 combat_synergy 字段 — 验证 generate_synergy 中的转换逻辑"""
        text = json.dumps({
            "score": 5, "synergy_rating": "A",
            "combat_synergy": 7, "combo_stability": 6,
            "adaptability": 5, "description": "test"
        })
        data = extract_json(text)
        # 模拟 generate_synergy 中的兼容逻辑
        if "combat_synergy" in data and "combo_ceiling" not in data:
            data["combo_ceiling"] = data.pop("combat_synergy")
        data["hero_a_id"] = 1
        data["hero_b_id"] = 2
        # 通过 validate_synergy 走 Pydantic 校验完整路径（与生产代码一致）
        result = validate_synergy(data)
        assert result is not None
        assert "combat_synergy" not in result
        assert result["combo_ceiling"] == 7


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



class TestConfigLoading:
    def test_parse_env_file_nonexistent(self):
        """不存在的 .env 文件应返回空 dict"""
        from src.config.env import parse_env_file
        result = parse_env_file("/nonexistent/config.env")
        assert result == {}

    def test_parse_env_file_valid(self):
        """解析有效的 .env 文件"""
        import tempfile, os, shutil
        from src.config.env import parse_env_file

        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=sk-test\n"
                "DEEPSEEK_MODEL=deepseek-v4-pro\n",
                encoding="utf-8"
            )
            result = parse_env_file(env_path)
            assert result["DEEPSEEK_API_KEY"] == "sk-test"
            assert result["DEEPSEEK_MODEL"] == "deepseek-v4-pro"
        finally:
            shutil.rmtree(tmpdir)

    def test_parse_env_file_with_comments(self):
        """解析包含注释和空行的 .env 文件"""
        import tempfile, os, shutil
        from src.config.env import parse_env_file

        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text(
                "# This is a comment\n"
                "DEEPSEEK_API_KEY=sk-test\n"
                "\n"
                "# Another comment\n"
                "DEEPSEEK_MODEL=deepseek-v4-pro\n",
                encoding="utf-8"
            )
            result = parse_env_file(env_path)
            assert result["DEEPSEEK_API_KEY"] == "sk-test"
            assert result["DEEPSEEK_MODEL"] == "deepseek-v4-pro"
        finally:
            shutil.rmtree(tmpdir)

    def test_parse_env_file_quotes(self):
        """解析包含引号值的 .env 文件"""
        import tempfile, os, shutil
        from src.config.env import parse_env_file

        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text(
                'DEEPSEEK_API_KEY="sk-test"\n'
                "DEEPSEEK_API_URL='https://custom.url/api'\n",
                encoding="utf-8"
            )
            result = parse_env_file(env_path)
            assert result["DEEPSEEK_API_KEY"] == "sk-test"
            assert result["DEEPSEEK_API_URL"] == "https://custom.url/api"
        finally:
            shutil.rmtree(tmpdir)

    def test_parse_env_file_empty(self):
        """空文件应返回空 dict"""
        import tempfile, os, shutil
        from src.config.env import parse_env_file

        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text("", encoding="utf-8")
            result = parse_env_file(env_path)
            assert result == {}
        finally:
            shutil.rmtree(tmpdir)

    def test_get_api_config_from_env_file(self):
        """get_api_config 应从 config.env 读取值"""
        import tempfile, os, shutil
        from src.config.env import get_api_config

        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text(
                "DEEPSEEK_API_KEY=sk-from-env\n"
                "DEEPSEEK_API_URL=https://custom.api/chat\n"
                "DEEPSEEK_MODEL=custom-model\n",
                encoding="utf-8"
            )
            # Temporarily override DEFAULT_ENV_FILE
            import src.config.env as config_env
            original = config_env.DEFAULT_ENV_FILE
            config_env.DEFAULT_ENV_FILE = env_path
            try:
                result = get_api_config()
                assert result["api_key"] == "sk-from-env"
                assert result["api_url"] == "https://custom.api/chat"
                assert result["model"] == "custom-model"
            finally:
                config_env.DEFAULT_ENV_FILE = original
        finally:
            shutil.rmtree(tmpdir)

    def test_get_api_config_env_var_fallback(self):
        """环境变量作为 config.env 的回退"""
        from src.config.env import get_api_config

        import tempfile, os, shutil
        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "empty.env"
            env_path.write_text("", encoding="utf-8")

            import src.config.env as config_env
            original = config_env.DEFAULT_ENV_FILE
            config_env.DEFAULT_ENV_FILE = env_path

            old_deepseek = os.environ.get("DEEPSEEK_API_KEY", "")
            os.environ["DEEPSEEK_API_KEY"] = "sk-from-envvar"
            try:
                result = get_api_config()
                assert result["api_key"] == "sk-from-envvar"
            finally:
                config_env.DEFAULT_ENV_FILE = original
                if old_deepseek:
                    os.environ["DEEPSEEK_API_KEY"] = old_deepseek
                else:
                    del os.environ["DEEPSEEK_API_KEY"]
        finally:
            shutil.rmtree(tmpdir)

    def test_get_runtime_params_defaults(self):
        """get_runtime_params 应返回默认值"""
        from src.config.env import get_runtime_params

        import tempfile, os, shutil
        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "empty.env"
            env_path.write_text("", encoding="utf-8")

            import src.config.env as config_env
            original = config_env.DEFAULT_ENV_FILE
            config_env.DEFAULT_ENV_FILE = env_path
            try:
                params = get_runtime_params()
                assert params["requests_per_minute"] == 30
                assert params["max_retries"] == 3
                assert params["http_timeout"] == 300
            finally:
                config_env.DEFAULT_ENV_FILE = original
        finally:
            shutil.rmtree(tmpdir)

    def test_get_runtime_params_custom(self):
        """get_runtime_params 应从 config.env 读取自定义值"""
        from src.config.env import get_runtime_params

        import tempfile, os, shutil
        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text(
                "REQUESTS_PER_MINUTE=10\n"
                "MAX_RETRIES=5\n"
                "HTTP_TIMEOUT=120\n",
                encoding="utf-8"
            )

            import src.config.env as config_env
            original = config_env.DEFAULT_ENV_FILE
            config_env.DEFAULT_ENV_FILE = env_path
            try:
                params = get_runtime_params()
                assert params["requests_per_minute"] == 10
                assert params["max_retries"] == 5
                assert params["http_timeout"] == 120
            finally:
                config_env.DEFAULT_ENV_FILE = original
        finally:
            shutil.rmtree(tmpdir)

    def test_get_runtime_params_converts_log_to_file_to_bool(self):
        """LOG_TO_FILE 应在配置加载阶段转换为布尔值。"""
        import tempfile, shutil
        from src.config.env import get_runtime_params

        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            import src.config.env as config_env
            original = config_env.DEFAULT_ENV_FILE
            config_env.DEFAULT_ENV_FILE = env_path
            try:
                env_path.write_text("LOG_TO_FILE=true\n", encoding="utf-8")
                assert get_runtime_params()["log_to_file"] is True

                env_path.write_text("LOG_TO_FILE=false\n", encoding="utf-8")
                assert get_runtime_params()["log_to_file"] is False
            finally:
                config_env.DEFAULT_ENV_FILE = original
        finally:
            shutil.rmtree(tmpdir)
