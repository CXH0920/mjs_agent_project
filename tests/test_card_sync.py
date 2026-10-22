"""卡牌官网同步单元测试：chunk 提取、哈希 diff、快照/变更记录、写回与精化时效。"""

import json
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError
from src.business.card_sync import (
    CHECK_COOLDOWN_SECONDS,
    CardSyncService,
    card_field_diff_summary,
    format_card_full_text,
)
from src.business.rag.audit_service import collect_stale_card_curated
from src.data.baike_ignore_store import ignore_entry, remove_entry
from src.data.card_catalog import CardRepository
from src.data.card_sync_store import (
    CardChangeRecord,
    CardSnapshot,
    append_card_change,
    load_card_changes,
    load_card_snapshot,
    save_card_snapshot,
)
from src.scraper.official_source.adapter import (
    extract_js_array,
    find_card_chunk_url,
    parse_cards_chunk,
)
from src.scraper.official_source.card_baike import (
    build_card_snapshot,
    card_content_hash,
    clean_card_detail,
    diff_cards,
)

# 模仿手牌库 modern chunk 的真实形态：无 const e= 标记、噪声数组在前、
# card_detail 含 HTML 与转义、字符串内含冒号与花括号
CARD_CHUNK_SNIPPET = (
    'var noise=[{other:1,note:"ignore me"}];'
    'var t=[{id:1,name:"杀",card_type:"行动牌",card_desc:"出牌阶段:对一名角色使用",'
    'card_detail:"<p>1.打出的杀不计入次数 {x:1}</p>\\n\\n<p>2.所有打出杀都是打出杀</p>",'
    'story_source:"s",design_idea:"d",img_url:"u",display_priority:1,status:1},'
    '{id:4,name:"闪",card_type:"行动牌",card_desc:"抵消一次杀",'
    'card_detail:"<p>所有打出闪都是打出闪</p>",'
    'story_source:"s",design_idea:"d",img_url:"u",display_priority:2,status:1}];'
    'var after=[{tail:true}]'
)


def _make_card(card_id: str, name: str, detail: str = "详解", amount: int = 2) -> dict:
    return {
        "id": card_id, "name": name, "card_type": "行动牌",
        "card_desc": "旧描述", "card_detail": detail, "card_amount": amount,
    }


def _make_repo(tmp_path: Path, cards: list[dict]) -> CardRepository:
    (tmp_path / "cards.json").write_text(
        json.dumps(cards, ensure_ascii=False), encoding="utf-8"
    )
    repo = CardRepository(tmp_path / "cards.json")
    repo.load()
    return repo


# ---------------------------------------------------------------------------
# 爬虫层
# ---------------------------------------------------------------------------


def test_parse_cards_chunk_skips_noise_and_self_validates() -> None:
    records = parse_cards_chunk(CARD_CHUNK_SNIPPET)

    assert [card["id"] for card in records] == [1, 4]
    assert records[0]["card_detail"].startswith("<p>")


def test_parse_cards_chunk_raises_when_no_card_array() -> None:
    with pytest.raises(RuntimeError, match="卡牌数据数组"):
        parse_cards_chunk("var a=[{foo:1}];var b=[2,3];")


def test_find_card_chunk_url_excludes_legacy_prefetch() -> None:
    html = '<script src="/_nuxt/spk-legacy.9110511d.js"></script>'
    with pytest.raises(RuntimeError, match="卡牌数据 JS chunk"):
        find_card_chunk_url(html)
    assert find_card_chunk_url('src="/_nuxt/spk.3cde8b9e.js"') == (
        "https://mjs.ztgame.com/_nuxt/spk.3cde8b9e.js"
    )


def test_extract_js_array_keeps_hero_default_marker() -> None:
    assert extract_js_array("const e=[1,2]") == "[1,2]"


# ---------------------------------------------------------------------------
# diff 基元
# ---------------------------------------------------------------------------


def test_card_content_hash_ignores_non_game_fields_and_html_noise() -> None:
    official = {"id": 1, "name": "杀", "card_type": "行动牌",
                "card_desc": "对一名角色使用", "card_detail": "<p>1.a</p>\\n<p>2.b</p>",
                "status": 1, "display_priority": 1, "img_url": "u"}
    local = {"id": "1", "name": "杀", "card_type": "行动牌",
             "card_desc": "对一名角色使用", "card_detail": "1.a\\n2.b", "card_amount": 14}

    assert card_content_hash(official) == card_content_hash(local)
    changed = dict(local, card_desc="对一名其他角色使用")
    assert card_content_hash(changed) != card_content_hash(local)


def test_diff_cards_three_states_sorted_by_numeric_id() -> None:
    current = build_card_snapshot([
        {"id": 10, "name": "甲", "card_type": "行动牌", "card_desc": "a", "card_detail": "d"},
        {"id": 4, "name": "乙", "card_type": "行动牌", "card_desc": "b已修改", "card_detail": "d"},
    ])
    baseline = build_card_snapshot([
        {"id": 4, "name": "乙", "card_type": "行动牌", "card_desc": "b", "card_detail": "d"},
        {"id": 7, "name": "丙", "card_type": "行动牌", "card_desc": "旧", "card_detail": "d"},
    ])

    diff = diff_cards(current, baseline)

    assert [entry["id"] for entry in diff["added"]] == ["10"]
    assert [entry["id"] for entry in diff["removed"]] == ["7"]
    assert [entry["id"] for entry in diff["modified"]] == ["4"]


def test_clean_card_detail_keeps_line_structure() -> None:
    assert clean_card_detail("<p>1.打出的杀</p>\n\n<p>2.不计次数</p>") == "1.打出的杀\n2.不计次数"


def test_card_field_diff_summary_labels_changed_fields() -> None:
    summary = card_field_diff_summary(
        {"name": "杀", "card_type": "行动牌", "card_desc": "旧描述", "card_detail": "详解"},
        {"name": "杀", "card_type": "行动牌", "card_desc": "新描述", "card_detail": "详解"},
    )

    assert summary == ["描述：本地「旧描述」→ 官网「新描述」"]


def test_format_card_full_text_contains_four_fields() -> None:
    text = format_card_full_text({"name": "闪", "card_type": "行动牌", "card_desc": "抵消", "card_detail": ""})

    assert "名称：闪" in text and "类型：行动牌" in text and "描述：抵消" in text and "结算详解：" in text


# ---------------------------------------------------------------------------
# 数据层：快照与变更记录
# ---------------------------------------------------------------------------


def test_card_snapshot_round_trip(tmp_path) -> None:
    path = tmp_path / "card_snapshot.json"
    snapshot = CardSnapshot(checked_at="2026-09-14T10:00:00", cards={
        "1": {"name": "杀", "hash": "aaa"},
        "4": {"name": "闪", "hash": "bbb"},
    })
    save_card_snapshot(snapshot, path)

    loaded = load_card_snapshot(path)

    assert loaded.cards["1"].name == "杀" and loaded.cards["4"].hash == "bbb"


def test_load_card_snapshot_missing_or_corrupted_returns_empty(tmp_path) -> None:
    assert load_card_snapshot(tmp_path / "missing.json").cards == {}
    (tmp_path / "bad.json").write_text("{invalid", encoding="utf-8")
    assert load_card_snapshot(tmp_path / "bad.json").cards == {}


def test_load_card_snapshot_corrupted_preserves_backup(tmp_path) -> None:
    """回归（TD-A5-002）：快照损坏时按原字节留底——调用方把空快照当首次
    启用重建基线并覆盖写回，没有副本损坏前的官网基线不可逆丢失。"""
    path = tmp_path / "card_snapshot.json"
    path.write_text("{invalid", encoding="utf-8")

    snapshot = load_card_snapshot(path)

    assert snapshot.cards == {}
    backups = sorted((tmp_path / "backups").glob("card_snapshot-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{invalid"


def test_append_card_change_is_idempotent(tmp_path) -> None:
    path = tmp_path / "card_changes.json"
    record = CardChangeRecord(date="2026-09-14", applied_ids=["4", "1"], added_ids=[])

    assert append_card_change(record, path) is True
    # 集合相同（顺序不同）视为重复
    assert append_card_change(
        CardChangeRecord(date="2026-09-14", applied_ids=["1", "4"], added_ids=[]), path
    ) is False

    records = load_card_changes(path)
    assert len(records) == 1 and records[0].applied_ids == ["4", "1"]


def test_append_card_change_corrupt_file_backed_up(tmp_path) -> None:
    """损坏的变更记录被覆盖写之前须按原字节留底（官网同步历史不可重建）。"""
    path = tmp_path / "card_changes.json"
    corrupt = '{"date": "2026-09-14", "applied_ids": ["4"'  # 截断 JSON
    path.write_text(corrupt, encoding="utf-8")

    record = CardChangeRecord(date="2026-09-15", applied_ids=["1"], added_ids=[])
    assert append_card_change(record, path) is True

    backups = list((tmp_path / "backups").glob("card_changes-*.json"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == corrupt
    assert [r.date for r in load_card_changes(path)] == ["2026-09-15"]


# ---------------------------------------------------------------------------
# 写回通道：CardRepository.apply_official_updates
# ---------------------------------------------------------------------------


def test_apply_official_updates_preserves_amount_and_appends_new(tmp_path) -> None:
    repo = _make_repo(tmp_path, [_make_card("4", "闪")])

    applied = repo.apply_official_updates(
        {"4": {"name": "闪", "card_type": "行动牌", "card_desc": "抵消一次杀", "card_detail": "新详解"}},
        [{"id": "99", "name": "新牌", "card_type": "战法牌", "card_desc": "", "card_detail": ""}],
    )

    assert applied == ["4", "99"]
    flash = repo.get_card("4")
    assert flash.card_desc == "抵消一次杀" and flash.card_detail == "新详解"
    assert flash.card_amount == 2  # 官网无数量字段，本地值保留
    new_card = repo.get_card("99")
    assert new_card.card_amount == 1  # 新增卡数量默认 1，待人工核对
    # 落盘校验：重读文件可解析且包含两张卡
    assert len(json.loads((tmp_path / "cards.json").read_text(encoding="utf-8"))) == 2


def test_apply_official_updates_skips_unknown_modified_id(tmp_path) -> None:
    repo = _make_repo(tmp_path, [_make_card("4", "闪")])

    applied = repo.apply_official_updates({"404": {"name": "幽灵"}}, [])

    assert applied == []
    assert repo.get_card("4").card_desc == "旧描述"


def test_apply_official_updates_atomic_on_invalid_record(tmp_path) -> None:
    """官网数据畸形抛错时不得留下半更新状态：内存与磁盘都保持原样（先验后写）。"""
    repo = _make_repo(tmp_path, [_make_card("4", "闪"), _make_card("5", "桃")])

    with pytest.raises(ValidationError):
        repo.apply_official_updates(
            {
                "4": {"name": "闪", "card_desc": "抵消一次杀", "card_detail": "新详解"},
                "5": {"name": "   "},  # 卡名空白 → Card.name_not_empty 拒绝
            },
            [],
        )

    flash = repo.get_card("4")
    peach = repo.get_card("5")
    assert flash is not None and flash.card_desc == "旧描述"  # 好卡不得被应用
    assert peach is not None and peach.name == "桃"
    disk = json.loads((tmp_path / "cards.json").read_text(encoding="utf-8"))
    assert [card["card_detail"] for card in disk] == ["详解", "详解"]  # 磁盘未被半更新


def test_apply_official_updates_rolls_back_memory_when_save_fails(tmp_path, monkeypatch) -> None:
    """回归（数据域 VUL-B4-002）：写盘失败必须回滚内存。

    磁盘满/文件被占用时调用方报"应用失败"而内存已变，下次任意保存
    会固化未经确认的半更新；回滚后重试仍然干净（幂等）。
    """
    repo = _make_repo(tmp_path, [_make_card("4", "闪")])
    original_save = repo._save
    save_calls = {"count": 0}

    def flaky_save() -> None:
        save_calls["count"] += 1
        if save_calls["count"] == 1:
            raise OSError("文件被另一进程占用")
        original_save()

    monkeypatch.setattr(repo, "_save", flaky_save)

    with pytest.raises(OSError):
        repo.apply_official_updates(
            {"4": {"name": "闪", "card_desc": "抵消一次杀", "card_detail": "新详解"}}, [],
        )

    assert repo.get_card("4").card_desc == "旧描述"  # 内存已回滚
    disk = json.loads((tmp_path / "cards.json").read_text(encoding="utf-8"))
    assert disk[0]["card_desc"] == "旧描述"  # 磁盘未被写入

    # 重试（占用解除后）成功，不残留首次的半更新
    applied = repo.apply_official_updates(
        {"4": {"name": "闪", "card_desc": "抵消一次杀", "card_detail": "新详解"}}, [],
    )
    assert applied == ["4"]
    assert repo.get_card("4").card_desc == "抵消一次杀"


# ---------------------------------------------------------------------------
# 业务层：CardSyncService 检查编排与增量基线
# ---------------------------------------------------------------------------


def _official_record(card_id: int, desc: str) -> dict:
    return {"id": card_id, "name": f"卡{card_id}", "card_type": "行动牌",
            "card_desc": desc, "card_detail": "<p>详解</p>"}


def test_service_first_check_initializes_baseline_from_local(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "旧描述")])

    result = service._do_check()

    assert result.official_ok and result.diff == {"added": [], "modified": [], "removed": []}
    assert len(result.pending_saves) == 1
    service._finalize_check(result)
    assert load_card_snapshot(tmp_path / "snap.json").cards["1"].name == "卡1"


def test_service_detects_change_and_partial_apply_keeps_rest_pending(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path, [_make_card("1", "卡1"), _make_card("2", "卡2")])
    snap_path, changes_path = tmp_path / "snap.json", tmp_path / "changes.json"
    service = CardSyncService(repo, snapshot_path=snap_path, changes_path=changes_path)
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "旧描述"), _official_record(2, "旧描述")])
    service._finalize_check(service._do_check())

    # 官网第 2 张卡描述变化
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "旧描述"), _official_record(2, "新描述")])
    result = service._do_check()
    service._finalize_check(result)
    assert [entry["id"] for entry in result.diff["modified"]] == ["2"]

    # 只应用卡 2：基线仅推进卡 2；卡 1 与官网一致，下次检查无任何提示
    summary = service.apply_updates(modified_ids=["2"], added_ids=[])
    assert summary == {"applied": 1, "modified": 1, "added": 0}
    assert repo.get_card("2").card_desc == "新描述"

    next_result = service._do_check()
    assert not any(next_result.diff.values())


def test_service_apply_writes_change_record_and_skips_without_check(tmp_path) -> None:
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    changes_path = tmp_path / "changes.json"
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json", changes_path=changes_path)

    with pytest.raises(RuntimeError, match="尚未检查"):
        service.apply_updates(["1"], [])

    service._last_official_cards = [_official_record(1, "新描述")]
    service._last_snapshot = None  # 无快照时应用后基线不推进，但写回与记录照常
    service.apply_updates(["1"], [])

    records = load_card_changes(changes_path)
    assert len(records) == 1 and records[0].applied_ids == ["1"]


def test_service_apply_added_card_inserts_with_default_amount(monkeypatch, tmp_path) -> None:
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    service._last_official_cards = [_official_record(1, "旧描述"), _official_record(50, "全新描述")]
    service.apply_updates([], ["50"])

    assert repo.get_card("50") is not None
    assert repo.get_card("50").card_amount == 1


def test_service_apply_rejects_while_check_in_flight(monkeypatch, tmp_path) -> None:
    """检查进行中应用必须被拒：防 GUI 写 cards 与 worker 检查并发。"""
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    started = threading.Event()
    release = threading.Event()

    def slow_fetch():
        started.set()
        release.wait(2)
        return [_official_record(1, "旧描述")]

    monkeypatch.setattr("src.business.card_sync.fetch_official_cards", slow_fetch)
    assert service.check_now() is True
    assert started.wait(2)

    with pytest.raises(RuntimeError, match="检查正在进行"):
        service.apply_updates(["1"], [])

    release.set()
    service._thread.join(2)


def test_service_apply_rejects_when_cards_repo_has_load_errors(tmp_path) -> None:
    """cards.json 加载有 error（损坏/坏记录）时拒绝应用：防空库被官网 added 覆盖（VUL-B4-002）。"""
    cards_path = tmp_path / "cards.json"
    corrupt = '[{"id": "1", "name": "卡1"'  # 截断 JSON
    cards_path.write_text(corrupt, encoding="utf-8")
    repo = CardRepository(cards_path)
    repo.load()
    assert any(issue.severity == "error" for issue in repo.load_issues)

    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    service._last_official_cards = [_official_record(1, "旧描述"), _official_record(50, "全新描述")]

    with pytest.raises(RuntimeError, match="加载异常"):
        service.apply_updates([], ["50"])

    # 坏文件保持原字节（data 层已另存保底副本），不得被残缺库覆盖
    assert cards_path.read_text(encoding="utf-8") == corrupt


def test_service_apply_change_record_failure_keeps_diff_revisible(monkeypatch, tmp_path) -> None:
    """变更记录写入失败时基线不得推进：diff 仍在，重新应用可幂等补齐（VUL-B4-001）。"""
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    snap_path, changes_path = tmp_path / "snap.json", tmp_path / "changes.json"
    service = CardSyncService(repo, snapshot_path=snap_path, changes_path=changes_path)
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "旧描述")])
    service._finalize_check(service._do_check())  # 首跑：本地卡初始化基线，diff 为空

    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "新描述")])
    service._finalize_check(service._do_check())  # 检出卡 1 差异

    def broken_append(record, path=None):
        raise OSError("磁盘已满")

    monkeypatch.setattr("src.business.card_sync.append_card_change", broken_append)
    with pytest.raises(RuntimeError, match="变更记录"):
        service.apply_updates(["1"], [])

    # 卡牌已写回；基线未推进（hash 仍是旧描述口径）、变更记录未落盘——
    # 下次检查差异仍在，重新应用即可补齐（应用链路幂等）
    updated = repo.get_card("1")
    assert updated is not None and updated.card_desc == "新描述"
    entry = load_card_snapshot(snap_path).cards.get("1")
    assert entry is not None
    assert entry.hash == card_content_hash(_official_record(1, "旧描述"))
    assert not changes_path.exists()


def test_service_finalize_check_reports_baseline_save_failure(monkeypatch, tmp_path) -> None:
    """基线落盘失败不得静默：须写入 result.warning 并在 check_finished 之前可见。

    否则磁盘基线恒空 → 每次检查都重走首跑初始化、用当时本地库重建基线 →
    diff 恒空，官网真实变更被逐次就地吸收（TD-A5-003）。
    """
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "旧描述")])

    def broken_save(snapshot, path=None):
        raise OSError("磁盘已满")

    monkeypatch.setattr("src.business.card_sync.save_card_snapshot", broken_save)
    finished = []
    service.check_finished.connect(finished.append)

    result = service._do_check()
    assert result.pending_saves  # 首跑确有基线要落盘
    service._finalize_check(result)

    assert result.warning is not None and "基线保存失败" in result.warning
    # 结果仍然广播（不能因收尾失败让进度条永久停留）
    assert finished == [result]
    # 内存基线已更新，磁盘没有
    assert service._last_snapshot is result.snapshot
    assert not (tmp_path / "snap.json").exists()


def test_service_finalize_check_no_warning_when_save_succeeds(monkeypatch, tmp_path) -> None:
    """正常路径不得产生 warning，避免对话框常态显示警示行。"""
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    service = CardSyncService(repo, snapshot_path=tmp_path / "snap.json",
                              changes_path=tmp_path / "changes.json")
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "旧描述")])

    result = service._do_check()
    service._finalize_check(result)

    assert result.warning is None
    assert (tmp_path / "snap.json").exists()


# ---------------------------------------------------------------------------
# 忙碌 / 冷却 / 忽略名单（TD-A7-001：此前七个公开方法零覆盖）
# ---------------------------------------------------------------------------


def _fake_thread(monkeypatch, *, alive: bool) -> dict:
    """把 card_sync 的 threading.Thread 换成可控替身，记录是否真的 start。"""
    record: dict = {"started": 0, "target": None}

    class _Thread:
        def __init__(self, target=None, daemon=None):
            record["target"] = target
            record["daemon"] = daemon

        def is_alive(self) -> bool:
            return alive

        def start(self) -> None:
            record["started"] += 1

    monkeypatch.setattr("src.business.card_sync.threading.Thread", _Thread)
    return record


def _service(tmp_path, **kwargs) -> CardSyncService:
    repo = _make_repo(tmp_path, [_make_card("1", "卡1")])
    return CardSyncService(
        repo,
        snapshot_path=kwargs.get("snapshot_path", tmp_path / "snap.json"),
        changes_path=kwargs.get("changes_path", tmp_path / "changes.json"),
        ignore_path=kwargs.get("ignore_path", tmp_path / "ignore.json"),
    )


def test_check_now_blocked_while_busy(monkeypatch, tmp_path) -> None:
    """检查在途时拒绝重复启动，且不发 check_started。"""
    service = _service(tmp_path)
    record = _fake_thread(monkeypatch, alive=True)
    started = []
    service.check_started.connect(lambda: started.append(1))
    service._thread = threading.Thread(target=lambda: None)

    assert service.is_busy is True
    assert service.check_now() is False
    assert started == []
    assert record["started"] == 0


def test_check_now_blocked_during_cooldown(monkeypatch, tmp_path) -> None:
    """冷却期内拒绝启动；冷却到期后放行（CHECK_COOLDOWN_SECONDS 边界）。"""
    service = _service(tmp_path)
    record = _fake_thread(monkeypatch, alive=False)
    started = []
    service.check_started.connect(lambda: started.append(1))

    service._gate.try_acquire()
    assert service.cooldown_remaining > 0
    assert service.check_now() is False
    assert started == []
    assert record["started"] == 0

    service._gate.backdate(CHECK_COOLDOWN_SECONDS + 1)
    assert service.cooldown_remaining == 0
    assert service.check_now() is True
    assert started == [1]
    assert record["started"] == 1


def test_cooldown_remaining_never_negative(tmp_path) -> None:
    """冷却剩余量下限为 0，不会把负值传给 UI（UI 用 int(remaining)+1 拼文案）。"""
    service = _service(tmp_path)
    assert service.cooldown_remaining == 0.0  # 从未检查
    service._gate.try_acquire()
    service._gate.backdate(10_000)
    assert service.cooldown_remaining == 0.0


def test_ignore_card_restore_and_count_round_trip(tmp_path) -> None:
    """忽略→计数/列出→按 id 恢复→全部恢复，忽略名单增删改闭环。"""
    service = _service(tmp_path)
    service._last_official_cards = [_official_record(2, "新描述"), _official_record(3, "另一张")]

    service.ignore_card("2", "卡2", "modified")
    service.ignore_card("3", "卡3", "added")

    assert service.ignored_card_count() == 2
    entries = service.list_ignored_cards()
    assert set(entries) == {"2", "3"}
    assert entries["2"].state == "modified"
    # 哈希取最近一次检查的官网数据（与快照同源）
    assert entries["2"].hash == card_content_hash(_official_record(2, "新描述"))

    service.restore_cards(["2"])
    assert service.ignored_card_count() == 1
    assert set(service.list_ignored_cards()) == {"3"}

    service.restore_cards()
    assert service.ignored_card_count() == 0
    assert service.list_ignored_cards() == {}


def test_ignore_card_without_official_cache_uses_empty_hash(tmp_path) -> None:
    """未检查过（无官网缓存）时忽略：不抛错，哈希退化为空卡内容哈希。

    注意：这是现状语义而非理想语义——空卡哈希是常量，且 removed 态在
    _matches 里根本不比对哈希，故该忽略会对"任意内容的同 id 卡"永久生效。
    本轮不改行为，只以用例锁定现状。
    """
    service = _service(tmp_path)
    assert service._last_official_cards == []

    service.ignore_card("9", "卡9", "removed")

    entry = service.list_ignored_cards()["9"]
    assert entry.hash == card_content_hash({})
    assert entry.state == "removed"


def test_restore_cards_unknown_id_is_noop(tmp_path) -> None:
    """恢复不存在的条目不得抛错（管理对话框可能持有过期 id）。"""
    service = _service(tmp_path)
    service.restore_cards(["999"])
    assert service.ignored_card_count() == 0


# ---------------------------------------------------------------------------
# 精化时效：collect_stale_card_curated
# ---------------------------------------------------------------------------


def _write_corpus(tmp_path: Path, block_id: str, updated_at: str) -> None:
    corpus_dir = tmp_path / "data" / "rag_corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "卡牌RAG语料.json").write_text(json.dumps([
        {"block_id": block_id, "effect": "效果", "curated": {"updated_at": updated_at}},
    ], ensure_ascii=False), encoding="utf-8")


def test_card_curated_stale_detects_sync_after_refinement(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "card_changes.json").write_text(json.dumps([
        {"date": "2026-09-14", "applied_ids": ["3"], "added_ids": []},
    ]), encoding="utf-8")
    _write_corpus(tmp_path, "card_3_多多益善", "2026-08-15")

    hits = collect_stale_card_curated(tmp_path)

    assert hits == [{"card": "多多益善", "curated_at": "2026-08-15", "changed_at": "2026-09-14"}]


def test_card_curated_stale_ignores_fresh_or_uncurated_blocks(tmp_path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "card_changes.json").write_text(json.dumps([
        {"date": "2026-09-14", "applied_ids": ["3", "5"], "added_ids": []},
    ]), encoding="utf-8")
    corpus_dir = tmp_path / "data" / "rag_corpus"
    corpus_dir.mkdir(parents=True)
    (corpus_dir / "卡牌RAG语料.json").write_text(json.dumps([
        # 精化晚于同步：不提示
        {"block_id": "card_3_多多益善", "curated": {"updated_at": "2026-09-15"}},
        # 无 curated：不参与
        {"block_id": "card_5_无中生有"},
        # 非 card 块：忽略
        {"block_id": "hero_x_武将", "curated": {"updated_at": "2026-01-01"}},
    ], ensure_ascii=False), encoding="utf-8")

    assert collect_stale_card_curated(tmp_path) == []


def test_service_filters_ignored_cards_in_check(monkeypatch, tmp_path) -> None:
    """忽略名单过滤：被忽略差异视为不存在；恢复后下次检查重现。"""
    repo = _make_repo(tmp_path, [_make_card("1", "卡1"), _make_card("2", "卡2")])
    snap_path = tmp_path / "snap.json"
    ignore_path = tmp_path / "ignore.json"
    service = CardSyncService(repo, snapshot_path=snap_path,
                              changes_path=tmp_path / "changes.json",
                              ignore_path=ignore_path)
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "旧描述"), _official_record(2, "旧描述")])
    service._finalize_check(service._do_check())

    # 官网两张卡的描述都变化 → 忽略卡 2 后仅剩卡 1
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "新描述"), _official_record(2, "新描述")])
    ignore_entry("cards", "2", "卡2", "modified",
                 card_content_hash(_official_record(2, "新描述")), ignore_path)
    result = service._do_check()

    assert [entry["id"] for entry in result.diff["modified"]] == ["1"]
    assert result.ignored_count == 1

    # 官网卡 2 再变化（哈希不匹配）→ 忽略失效自动重现
    monkeypatch.setattr("src.business.card_sync.fetch_official_cards",
                        lambda: [_official_record(1, "新描述"), _official_record(2, "更新描述")])
    result = service._do_check()
    assert sorted(entry["id"] for entry in result.diff["modified"]) == ["1", "2"]

    # 恢复忽略后全部重现
    remove_entry("cards", "2", ignore_path)
    result = service._do_check()
    assert sorted(entry["id"] for entry in result.diff["modified"]) == ["1", "2"]
    assert result.ignored_count == 0
