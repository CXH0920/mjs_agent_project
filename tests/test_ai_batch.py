"""名将杀 Agent - AI 批量生成工具单元测试"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest
import src.config.env as config_env
from src.config.env import get_api_config, get_runtime_params, parse_env_file
from src.scraper.ai import prompt_utils
from src.scraper.ai.api_generator import AIBatchGenerator
from src.scraper.ai.batch import (
    _load_existing_guides,
    _load_existing_synergies,
    estimate_cost,
    estimate_cost_by_tokens,
    load_heroes,
)
from src.scraper.ai.json_extract import extract_json
from src.scraper.ai.prompt_utils import (
    build_guide_prompt,
    build_synergy_prompt,
    estimate_item_cost,
    load_prompt,
)
from src.scraper.ai.utils import (
    _save_json,
    convert_ids_to_int,
    safe_url_origin,
    validate_guide,
    validate_synergy,
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

    def test_synergy_item_cost(self) -> None:
        """按实际相性对数量估算成本"""
        result = estimate_item_cost(3, "synergy")
        assert result["items"] == 3
        assert result["estimated_tokens"] > 0

    def test_item_cost_use_rag(self) -> None:
        """RAG 增强版输入 token 应高于经典模式"""
        rag = estimate_item_cost(3, "synergy", use_rag=True)
        classic = estimate_item_cost(3, "synergy", use_rag=False)
        assert rag["estimated_input_tokens"] > classic["estimated_input_tokens"]
        guide_rag = estimate_item_cost(3, "guide", use_rag=True)
        guide_classic = estimate_item_cost(3, "guide", use_rag=False)
        assert guide_rag["estimated_input_tokens"] > guide_classic["estimated_input_tokens"]

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
    @pytest.fixture(autouse=True)
    def _fixed_pricing(self, monkeypatch):
        """钉住模型单价：本组用例验证计算公式，不随 model_pricing.json 数据更新漂移。"""
        def _pricing(model):
            # 未知模型返回 None（与生产一致），已知模型返回固定单价
            if model == "unknown-model":
                return None
            return {"input_per_million": 3.0, "output_per_million": 6.0}

        monkeypatch.setattr(prompt_utils, "get_model_pricing", _pricing)

    def test_basic_calculation(self) -> None:
        """estimate_cost_by_tokens 基本计算"""
        cost = estimate_cost_by_tokens(1_000_000, 500_000)
        # 1M input @ CNY3/M = 3; 500K output @ CNY6/M = 3; total = 6
        assert cost == 6.0

    def test_zero_tokens(self) -> None:
        """0 token 费用为 0"""
        cost = estimate_cost_by_tokens(0, 0)
        assert cost == 0.0

    def test_small_values(self) -> None:
        """小数量 token 费用正确"""
        cost = estimate_cost_by_tokens(1000, 500)
        # 1000 * 3 / 1_000_000 + 500 * 6 / 1_000_000 = 0.003 + 0.003 = 0.006
        assert cost == 0.006

    def test_unknown_model_returns_none(self) -> None:
        assert estimate_cost_by_tokens(1000, 500, "unknown-model") is None

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
        secret_response = "SECRET_RESPONSE_BODY"
        with pytest.raises(ValueError) as exc_info:
            extract_json(secret_response)
        assert secret_response not in str(exc_info.value)

    def test_safe_url_origin_removes_credentials_and_request_details(self) -> None:
        assert safe_url_origin(
            "https://user:password@example.com:8443/chat?token=secret#message"
        ) == "https://example.com:8443"

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

    def test_build_synergy_prompt(self, monkeypatch) -> None:
        """构建相性 prompt 包含双方武将信息（经典模式无 RAG 区块）"""
        monkeypatch.setenv("RAG_ENABLED", "false")
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
        assert "RAG 官方规则语料" not in prompt

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

    def test_load_rejects_non_list_root(self, tmp_path: Path) -> None:
        """根节点不是列表时不得进入生成流程。"""
        path = tmp_path / "heroes.json"
        path.write_text('{"id": 1, "name": "曹操"}', encoding="utf-8")

        assert load_heroes(path) == []

    def test_load_rejects_file_with_invalid_record(self, tmp_path: Path) -> None:
        """存在非法记录时不得只加载部分武将。"""
        path = tmp_path / "heroes.json"
        path.write_text(
            json.dumps([{"id": 1, "name": "曹操"}, {"id": "bad", "name": "刘备"}], ensure_ascii=False),
            encoding="utf-8",
        )

        assert load_heroes(path) == []

    def test_load_rejects_invalid_utf8(self, tmp_path: Path) -> None:
        """编码损坏的武将文件应明确失败而不是抛出未处理异常。"""
        path = tmp_path / "heroes.json"
        path.write_bytes(b"\xff")

        assert load_heroes(path) == []

    def test_load_rejects_duplicate_ids(self, tmp_path: Path) -> None:
        """重复 ID 会导致断点和配对歧义，因此拒绝整个输入。"""
        path = tmp_path / "heroes.json"
        path.write_text(
            json.dumps([{"id": 1, "name": "曹操"}, {"id": 1, "name": "刘备"}], ensure_ascii=False),
            encoding="utf-8",
        )

        assert load_heroes(path) == []


class TestLoadExistingData:
    def test_corrupted_guide_file_is_backed_up_and_reset(self, tmp_path: Path) -> None:
        path = tmp_path / "guides.json"
        path.write_text("{", encoding="utf-8")

        assert _load_existing_guides(path) == {}
        backups = list(tmp_path.glob("guides.corrupt-*.json"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "{"
        assert json.loads(path.read_text(encoding="utf-8")) == []

    def test_invalid_guide_record_is_backed_up_while_valid_data_is_retained(self, tmp_path: Path) -> None:
        path = tmp_path / "guides.json"
        original = [
            {"hero_id": 1, "description": "有效攻略"},
            {"hero_id": "bad", "description": "无效攻略"},
        ]
        path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

        existing = _load_existing_guides(path)

        assert existing[1]["description"] == "有效攻略"
        backups = list(tmp_path.glob("guides.corrupt-*.json"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8")) == original
        repaired = json.loads(path.read_text(encoding="utf-8"))
        assert len(repaired) == 1
        assert repaired[0]["hero_id"] == 1

    def test_corrupted_synergy_file_is_backed_up_and_reset(self, tmp_path: Path) -> None:
        path = tmp_path / "synergies.json"
        path.write_text("[", encoding="utf-8")

        existing, keys = _load_existing_synergies(path)

        assert existing == {}
        assert keys == set()
        backups = list(tmp_path.glob("synergies.corrupt-*.json"))
        assert len(backups) == 1
        assert backups[0].read_text(encoding="utf-8") == "["
        assert json.loads(path.read_text(encoding="utf-8")) == []

    def test_invalid_synergy_record_is_backed_up_while_valid_data_is_retained(self, tmp_path: Path) -> None:
        path = tmp_path / "synergies.json"
        original = [
            {"hero_a_id": 1, "hero_b_id": 2, "score": 5},
            {"hero_a_id": 1, "hero_b_id": 3, "score": "bad"},
        ]
        path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

        existing, keys = _load_existing_synergies(path)

        assert keys == {(1, 2)}
        assert existing[(1, 2)]["score"] == 5
        backups = list(tmp_path.glob("synergies.corrupt-*.json"))
        assert len(backups) == 1
        assert json.loads(backups[0].read_text(encoding="utf-8")) == original
        repaired = json.loads(path.read_text(encoding="utf-8"))
        assert len(repaired) == 1
        assert repaired[0]["hero_b_id"] == 2

    def test_backup_failure_keeps_original_file(self, tmp_path: Path, monkeypatch) -> None:
        path = tmp_path / "guides.json"
        path.write_text("{", encoding="utf-8")

        def fail_replace(_self, _target):
            raise OSError("backup denied")

        monkeypatch.setattr(Path, "replace", fail_replace)

        with pytest.raises(RuntimeError, match="无法备份损坏文件"):
            _load_existing_guides(path)
        assert path.read_text(encoding="utf-8") == "{"



class TestConfigLoading:
    @pytest.fixture(autouse=True)
    def _isolate_profiles_file(self, monkeypatch, tmp_path):
        """隔离 DEFAULT_PROFILES_FILE：本机 config/api_profiles.json 若含默认档案，
        会使 get_api_config 走档案分支而非 tmp env，导致断言读到档案 Key。
        指向不存在的 tmp 文件即可（A4 测试隔离缺口）。"""
        monkeypatch.setattr(
            config_env, "DEFAULT_PROFILES_FILE", tmp_path / "nonexistent_profiles.json"
        )

    def test_parse_env_file_nonexistent(self):
        """不存在的 .env 文件应返回空 dict"""
        result = parse_env_file("/nonexistent/config.env")
        assert result == {}

    def test_parse_env_file_valid(self):
        """解析有效的 .env 文件"""
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
        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "empty.env"
            env_path.write_text("", encoding="utf-8")

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
        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "empty.env"
            env_path.write_text("", encoding="utf-8")

            original = config_env.DEFAULT_ENV_FILE
            config_env.DEFAULT_ENV_FILE = env_path
            try:
                params = get_runtime_params()
                assert params["requests_per_minute"] == 30
                assert params["max_retries"] == 3
                assert params["max_output_tokens"] == 16_384
                assert params["http_timeout"] == 300
            finally:
                config_env.DEFAULT_ENV_FILE = original
        finally:
            shutil.rmtree(tmpdir)

    def test_get_runtime_params_custom(self):
        """get_runtime_params 应从 config.env 读取自定义值"""
        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
            env_path.write_text(
                "REQUESTS_PER_MINUTE=10\n"
                "MAX_RETRIES=5\n"
                "MAX_OUTPUT_TOKENS=32768\n"
                "HTTP_TIMEOUT=120\n",
                encoding="utf-8"
            )

            original = config_env.DEFAULT_ENV_FILE
            config_env.DEFAULT_ENV_FILE = env_path
            try:
                params = get_runtime_params()
                assert params["requests_per_minute"] == 10
                assert params["max_retries"] == 5
                assert params["max_output_tokens"] == 32_768
                assert params["http_timeout"] == 120
            finally:
                config_env.DEFAULT_ENV_FILE = original
        finally:
            shutil.rmtree(tmpdir)

    def test_get_runtime_params_converts_log_to_file_to_bool(self):
        """LOG_TO_FILE 应在配置加载阶段转换为布尔值。"""
        tmpdir = tempfile.mkdtemp()
        try:
            env_path = Path(tmpdir) / "config.env"
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


def test_aibatchgenerator_ollama_allows_empty_key():
    """BUG-1：ollama 本地服务 requires_key=False，允许空 Key 构造。"""
    gen = AIBatchGenerator(
        api_key="",
        api_url="http://localhost:11434/v1/chat/completions",
        model="llama3",
        provider="ollama",
    )
    assert gen.api_key == ""
    assert gen.provider == "ollama"


def test_aibatchgenerator_requires_key_provider_rejects_empty_key():
    """BUG-1：deepseek 等需 Key 供应商空 Key 抛 ValueError。"""
    with pytest.raises(ValueError):
        AIBatchGenerator(api_key="", provider="deepseek")


def test_call_api_payload_thinking_only_for_deepseek():
    """BUG-2：thinking 字段仅 deepseek 加，其他供应商不含（避免未知字段 400）。"""
    captured: dict = {}

    class _FakeResp:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}

    class _FakeClient:
        def post(self, url, headers, json):
            captured["payload"] = json
            return _FakeResp()

    gen = AIBatchGenerator(api_key="sk-x", provider="deepseek")
    gen._client = _FakeClient()
    gen._call_api([{"role": "user", "content": "x"}])
    assert captured["payload"].get("thinking") == {"type": "disabled"}

    gen2 = AIBatchGenerator(api_key="sk-x", provider="openai")
    gen2._client = _FakeClient()
    gen2._call_api([{"role": "user", "content": "x"}])
    assert "thinking" not in captured["payload"]
