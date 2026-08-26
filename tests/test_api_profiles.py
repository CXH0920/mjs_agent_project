"""多 API 档案（api_profiles.json）接口层测试。

覆盖 docs/design/api_multi_config_design.md §4.3 / §5.1 / §5.2 / §八 相关行为：
加载容错、默认唯一、名称去重、Key 掩码、任务解析、旧配置迁移、向后兼容。
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import env as config_env
from src.config.env import (
    get_api_config,
    get_api_profile,
    list_api_profiles,
    load_api_profiles,
    migrate_legacy_api_config,
    resolve_api_config,
    save_api_profiles,
)

LEGACY = {
    "DEEPSEEK_API_KEY": "sk-legacy",
    "DEEPSEEK_API_URL": "https://legacy.example.com/v1/chat/completions",
    "DEEPSEEK_MODEL": "legacy-model",
}


def _profile(name="deepseek-main", **overrides) -> dict:
    base = {
        "name": name,
        "provider": "deepseek",
        "api_key": "sk-secret",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-pro",
        "enabled": True,
        "is_default": True,
        "note": "",
    }
    base.update(overrides)
    return base


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------
# 加载 / 保存
# ---------------------------------------------------------------

class TestLoadSave:
    def test_load_missing_file_returns_empty(self, tmp_path: Path):
        result = load_api_profiles(tmp_path / "nope.json")
        assert result == {"version": 1, "profiles": []}

    def test_save_then_load_roundtrip(self, tmp_path: Path):
        path = tmp_path / "api_profiles.json"
        save_api_profiles({"version": 1, "profiles": [_profile()]}, path)
        result = load_api_profiles(path)
        assert result["version"] == 1
        assert result["profiles"][0]["name"] == "deepseek-main"
        assert result["profiles"][0]["api_key"] == "sk-secret"

    def test_load_corrupt_file_returns_empty_and_keeps_file(self, tmp_path: Path, caplog):
        path = tmp_path / "api_profiles.json"
        path.write_text("{broken json", encoding="utf-8")
        import logging
        with caplog.at_level(logging.WARNING, logger="src.config.env"):
            result = load_api_profiles(path)
        assert result == {"version": 1, "profiles": []}
        # 损坏文件不被覆盖（用户数据不可因容错而丢失）
        assert path.read_text(encoding="utf-8") == "{broken json"
        assert any("API 档案配置不可用" in r.message for r in caplog.records)

    def test_load_structure_invalid_returns_empty(self, tmp_path: Path):
        path = tmp_path / "api_profiles.json"
        path.write_text('{"version": 1}', encoding="utf-8")  # 缺 profiles 列表
        assert load_api_profiles(path)["profiles"] == []

    def test_load_skips_non_dict_items(self, tmp_path: Path):
        path = tmp_path / "api_profiles.json"
        _write(path, {"version": 1, "profiles": [_profile("good"), "junk", 42]})
        names = [p["name"] for p in load_api_profiles(path)["profiles"]]
        assert names == ["good"]

    def test_load_drops_deprecated_is_default(self, tmp_path: Path):
        """is_default 字段已废弃：加载时被静默丢弃，不参与逻辑。"""
        path = tmp_path / "api_profiles.json"
        _write(path, {
            "version": 1,
            "profiles": [
                _profile("a", is_default=True),
                _profile("b", is_default=True),
            ],
        })
        profiles = load_api_profiles(path)["profiles"]
        assert all("is_default" not in p for p in profiles)
        assert [p["name"] for p in profiles] == ["a", "b"]

    def test_normalize_multiple_enabled_keeps_first(self, tmp_path: Path):
        """启用互斥：多个 enabled 时只保留第一个，其余停用。"""
        path = tmp_path / "api_profiles.json"
        _write(path, {
            "version": 1,
            "profiles": [
                _profile("a", enabled=True),
                _profile("b", enabled=True),
                _profile("c", enabled=True),
            ],
        })
        profiles = load_api_profiles(path)["profiles"]
        enabled = [p["name"] for p in profiles if p["enabled"]]
        assert enabled == ["a"]

    def test_load_duplicate_names_suffixed(self, tmp_path: Path):
        path = tmp_path / "api_profiles.json"
        _write(path, {"version": 1, "profiles": [_profile("dup"), _profile("dup")]})
        names = [p["name"] for p in load_api_profiles(path)["profiles"]]
        assert names == ["dup", "dup-2"]

    def test_load_empty_name_filled(self, tmp_path: Path):
        path = tmp_path / "api_profiles.json"
        _write(path, {"version": 1, "profiles": [{"name": "", "provider": "deepseek"}]})
        profiles = load_api_profiles(path)["profiles"]
        assert profiles[0]["name"] == "profile-1"

    def test_save_drops_deprecated_is_default(self, tmp_path: Path):
        path = tmp_path / "api_profiles.json"
        save_api_profiles({
            "version": 1,
            "profiles": [_profile("a", is_default=True), _profile("b", is_default=True)],
        }, path)
        profiles = load_api_profiles(path)["profiles"]
        assert all("is_default" not in p for p in profiles)

    def test_save_writes_atomic_tmp_removed(self, tmp_path: Path):
        path = tmp_path / "api_profiles.json"
        save_api_profiles({"version": 1, "profiles": [_profile()]}, path)
        assert not path.with_suffix(".json.tmp").exists()

    def test_save_empty_profiles_removes_existing_file(self, tmp_path: Path):
        """BUG-4：空 profiles 不写空文件（已存在则删），让 get_api_config 走旧链兜底。"""
        path = tmp_path / "api_profiles.json"
        save_api_profiles({"version": 1, "profiles": [_profile("a")]}, path)
        assert path.exists()
        save_api_profiles({"version": 1, "profiles": []}, path)
        assert not path.exists()  # 空不写，已存在则删


# ---------------------------------------------------------------
# 列表展示（Key 掩码）
# ---------------------------------------------------------------

class TestListProfiles:
    def test_list_masks_api_key(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            config_env, "DEFAULT_PROFILES_FILE",
            tmp_path / "api_profiles.json",
        )
        save_api_profiles({"version": 1, "profiles": [_profile(api_key="sk-very-secret")]})
        result = list_api_profiles()
        assert result[0]["name"] == "deepseek-main"
        assert "api_key" not in result[0]
        assert result[0]["has_key"] is True

    def test_list_has_key_false_when_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(
            config_env, "DEFAULT_PROFILES_FILE",
            tmp_path / "api_profiles.json",
        )
        save_api_profiles({"version": 1, "profiles": [_profile(api_key="")]})
        assert list_api_profiles()[0]["has_key"] is False


# ---------------------------------------------------------------
# 按名取档案 / 任务解析
# ---------------------------------------------------------------

class TestResolve:
    def test_get_profile_by_name(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", tmp_path / "p.json")
        save_api_profiles({"version": 1, "profiles": [_profile("relay", is_default=False)]})
        profile = get_api_profile("relay")
        assert profile is not None
        assert profile["api_key"] == "sk-secret"  # 解析路径可取明文

    def test_get_profile_missing_returns_none(self):
        assert get_api_profile("nope") is None
        assert get_api_profile("") is None

    def test_resolve_by_name(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", tmp_path / "p.json")
        save_api_profiles({
            "version": 1,
            "profiles": [
                _profile("main", enabled=False),
                _profile("relay", api_url="https://relay.example.com/v1", model="gpt-4o-mini"),
            ],
        })
        resolved = resolve_api_config("relay")
        assert resolved["api_url"] == "https://relay.example.com/v1"
        assert resolved["model"] == "gpt-4o-mini"
        assert resolved["api_key"] == "sk-secret"

    def test_resolve_disabled_falls_back_to_default(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", tmp_path / "p.json")
        save_api_profiles({
            "version": 1,
            "profiles": [
                _profile("main", api_key="sk-main"),
                _profile("off", is_default=False, enabled=False, api_key="sk-off"),
            ],
        })
        resolved = resolve_api_config("off")
        assert resolved["api_key"] == "sk-main"

    def test_resolve_missing_name_falls_back(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", tmp_path / "p.json")
        save_api_profiles({"version": 1, "profiles": [_profile("main", api_key="sk-main")]})
        resolved = resolve_api_config("ghost")
        assert resolved["api_key"] == "sk-main"

    def test_resolve_none_uses_default_profile(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", tmp_path / "p.json")
        save_api_profiles({
            "version": 1,
            "profiles": [
                _profile("main", api_key="sk-main"),
                _profile("backup", is_default=False, api_key="sk-backup"),
            ],
        })
        resolved = resolve_api_config()
        assert resolved["api_key"] == "sk-main"

    def test_resolve_without_any_profile_falls_back_legacy(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", tmp_path / "nope.json")
        monkeypatch.setattr(config_env, "load_api_profiles", lambda: {"version": 1, "profiles": []})
        monkeypatch.setattr(
            config_env, "_legacy_api_config",
            lambda: {"api_key": "sk-legacy", "api_url": "", "model": ""},
        )
        assert resolve_api_config()["api_key"] == "sk-legacy"


# ---------------------------------------------------------------
# get_api_config 向后兼容
# ---------------------------------------------------------------

class TestGetApiConfigCompatibility:
    def test_prefers_default_profile(self, monkeypatch):
        monkeypatch.setattr(config_env, "load_api_profiles", lambda: {
            "version": 1,
            "profiles": [
                _profile("main", api_key="sk-main"),
                _profile("backup", is_default=False, api_key="sk-backup"),
            ],
        })
        result = get_api_config()
        assert result["api_key"] == "sk-main"

    def test_falls_back_to_legacy_when_no_profiles_file(self, monkeypatch, tmp_path):
        """从未配置档案（文件不存在）时走旧链，保持历史语义。"""
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", tmp_path / "nope.json")
        monkeypatch.setattr(config_env, "load_api_profiles", lambda: {"version": 1, "profiles": []})
        monkeypatch.setattr(
            config_env, "_legacy_api_config",
            lambda: {"api_key": "sk-legacy", "api_url": "", "model": ""},
        )
        assert get_api_config()["api_key"] == "sk-legacy"

    def test_skips_disabled_takes_first_enabled(self, monkeypatch):
        """停用档案被跳过，取第一个 enabled 档案（启用互斥语义）。"""
        monkeypatch.setattr(config_env, "load_api_profiles", lambda: {
            "version": 1,
            "profiles": [
                _profile("off", enabled=False, api_key="sk-off"),
                _profile("other", api_key="sk-other"),
            ],
        })
        assert get_api_config()["api_key"] == "sk-other"

    def test_no_legacy_env_key_when_profiles_file_exists(self, tmp_path, monkeypatch):
        """A1：档案文件存在但无可用默认时，不读 config.env 旧键，只走环境变量。"""
        profiles_path = tmp_path / "api_profiles.json"
        profiles_path.write_text(json.dumps({"version": 1, "profiles": [
            _profile("off", is_default=True, enabled=False, api_key=""),
        ]}), encoding="utf-8")
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", profiles_path)
        env_path = tmp_path / "config.env"
        env_path.write_text("DEEPSEEK_API_KEY=sk-old-envfile\n", encoding="utf-8")
        monkeypatch.setattr(config_env, "DEFAULT_ENV_FILE", env_path)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        cfg = get_api_config()
        assert cfg["api_key"] == ""  # 不读 config.env 旧键；无环境变量 → 空
        assert cfg["api_url"] == config_env.DEFAULT_API_URL
        assert cfg["model"] == config_env.DEFAULT_MODEL

    def test_as_bool_none_uses_default(self):
        """A3：_as_bool(None, default) 返回 default（旧实现返回 bool(None)=False）。"""
        assert config_env._as_bool(None, True) is True
        assert config_env._as_bool(None, False) is False
        assert config_env._as_bool(True, False) is True
        assert config_env._as_bool(False, True) is False
        assert config_env._as_bool("true", False) is True
        assert config_env._as_bool("false", True) is False

    def test_get_api_config_skips_empty_url_profile(self, monkeypatch):
        """BUG-5：空 URL 的 enabled 档案被跳过，不回退 DeepSeek 默认 URL。"""
        monkeypatch.setattr(config_env, "load_api_profiles", lambda: {"version": 1, "profiles": [
            _profile("bad", api_url="", api_key="sk-x"),
            _profile("good", api_url="https://good.example.com/v1/chat/completions", api_key="sk-y"),
        ]})
        cfg = get_api_config()
        assert cfg["api_url"] == "https://good.example.com/v1/chat/completions"
        assert cfg["api_key"] == "sk-y"

    def test_env_var_fallback_reads_url_and_model(self, monkeypatch, tmp_path):
        """BUG-8：_env_var_fallback 回读 DEEPSEEK_API_URL/MODEL 环境变量（CI/脚本注入完整配置）。"""
        profiles_path = tmp_path / "api_profiles.json"
        profiles_path.write_text('{"version": 1, "profiles": []}', encoding="utf-8")
        monkeypatch.setattr(config_env, "DEFAULT_PROFILES_FILE", profiles_path)
        monkeypatch.setattr(config_env, "load_api_profiles", lambda: {"version": 1, "profiles": []})
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ci")
        monkeypatch.setenv("DEEPSEEK_API_URL", "https://ci.example.com/v1/chat/completions")
        monkeypatch.setenv("DEEPSEEK_MODEL", "ci-model")
        cfg = get_api_config()
        assert cfg["api_url"] == "https://ci.example.com/v1/chat/completions"
        assert cfg["model"] == "ci-model"


# ---------------------------------------------------------------
# 旧配置迁移
# ---------------------------------------------------------------

class TestMigration:
    def _env(self, tmp_path: Path, values: dict) -> Path:
        path = tmp_path / "config.env"
        path.write_text(
            "".join(f"{k}={v}\n" for k, v in values.items()),
            encoding="utf-8",
        )
        return path

    def test_migrate_creates_default_profile(self, tmp_path: Path):
        env_path = self._env(tmp_path, LEGACY)
        profiles_path = tmp_path / "api_profiles.json"
        assert migrate_legacy_api_config(env_path, profiles_path) is True
        result = load_api_profiles(profiles_path)
        profiles = result["profiles"]
        assert len(profiles) == 1
        assert profiles[0]["name"] == "deepseek-main"
        assert profiles[0]["enabled"] is True
        assert profiles[0]["provider"] == "deepseek"
        assert profiles[0]["api_key"] == "sk-legacy"
        assert profiles[0]["api_url"] == "https://legacy.example.com/v1/chat/completions"
        assert profiles[0]["model"] == "legacy-model"

    def test_migrate_fills_missing_url_model_defaults(self, tmp_path: Path):
        env_path = self._env(tmp_path, {"DEEPSEEK_API_KEY": "sk-only-key"})
        profiles_path = tmp_path / "api_profiles.json"
        assert migrate_legacy_api_config(env_path, profiles_path) is True
        profile = load_api_profiles(profiles_path)["profiles"][0]
        assert profile["api_url"] == config_env.DEFAULT_API_URL
        assert profile["model"] == config_env.DEFAULT_MODEL

    def test_migrate_idempotent_when_file_exists(self, tmp_path: Path):
        env_path = self._env(tmp_path, LEGACY)
        profiles_path = tmp_path / "api_profiles.json"
        save_api_profiles({"version": 1, "profiles": [_profile("existing")]}, profiles_path)
        assert migrate_legacy_api_config(env_path, profiles_path) is False
        # 已有档案不被覆盖
        assert load_api_profiles(profiles_path)["profiles"][0]["name"] == "existing"

    def test_migrate_empty_legacy_no_profile(self, tmp_path: Path):
        env_path = self._env(tmp_path, {})
        profiles_path = tmp_path / "api_profiles.json"
        assert migrate_legacy_api_config(env_path, profiles_path) is False
        assert not profiles_path.exists()