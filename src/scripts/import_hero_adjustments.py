# -*- coding: utf-8 -*-
"""初始化导入武将变更时间轴（data/mjs_adjustments.json）。

一次性将外部抓取的 A 类全量快照归一化入库（.tmp_test 仅作临时读取源，
正式数据一律落在 data/ 下），并回填 announcements.json 中快照截止日
（lastUpdated）之后公告的武将变更（覆盖快照缺批次，如 8月27日停服更新）。
幂等可重跑：每次完整重建时间轴文件（公告事件可由 announcements.json 重新推导）。

用法：
    python -m src.scripts.import_hero_adjustments [--input PATH]
"""
import argparse
import sys
from datetime import date

from src.data.hero_timeline import (
    CORPUS_BASE_DATE,
    DEFAULT_TIMELINE_FILE,
    append_announcement_events,
    hero_last_change,
    load_timeline,
    normalize_change_type,
    parse_skill_entry,
    save_timeline,
)
from src.scraper.official_source.announcement import build_timeline_events
from src.scripts.rag_common import load_json, project_path, setup_stdout

setup_stdout()

DEFAULT_SNAPSHOT = ".tmp_test/mjs_adjustments.json"


def collect_snapshot_events(snapshot: dict) -> list[dict]:
    """A 类快照 → init 事件；同 (date, hero) 多条合并（如韩信 03-26 两条）。"""
    raw_events = []
    for entry in snapshot.get("adjustments") or []:
        for general in entry.get("generals") or []:
            skills = []
            for item in general.get("skills") or []:
                name, change = parse_skill_entry(item)
                skills.append({"skill": name, "change": change})
            raw_events.append({
                "date": str(entry.get("date") or ""),
                "hero": str(general.get("name") or ""),
                "change_type": normalize_change_type(general.get("type")),
                "skills": skills,
                "source": "init",
            })
    for entry in snapshot.get("newGenerals") or []:
        for hero in entry.get("generals") or []:
            raw_events.append({
                "date": str(entry.get("date") or ""),
                "hero": str(hero or ""),
                "change_type": "新增",
                "skills": [],
                "source": "init",
            })
    merged: dict[tuple[str, str], dict] = {}
    ordered: list[dict] = []
    for event in raw_events:
        key = (event["date"], event["hero"])
        if key in merged:
            merged[key]["skills"].extend(event["skills"])
        else:
            merged[key] = event
            ordered.append(event)
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化导入武将变更时间轴")
    parser.add_argument("--input", default=DEFAULT_SNAPSHOT, help="A 类全量快照路径")
    args = parser.parse_args()

    snapshot = load_json(project_path(*args.input.split("/")))
    cutoff = str(snapshot.get("lastUpdated") or "")
    init_events = collect_snapshot_events(snapshot)
    if not init_events:
        print("❌ 快照未解析到任何事件，中止")
        sys.exit(1)

    save_timeline({
        "init_imported_at": date.today().isoformat(),
        "init_source_last_updated": cutoff,
        "corpus_base_date": CORPUS_BASE_DATE,
        "events": init_events,
    })

    # 回填公告：截止日之后的 hero_related 公告（快照缺批次），build_timeline_events
    # 的缺省 cutoff 即刚写入的 init_source_last_updated
    announcements = load_json(project_path("data", "announcements.json"), required=False) or []
    backfilled = append_announcement_events(build_timeline_events(announcements))

    timeline = load_timeline()
    events = timeline["events"]
    adjust_events = [e for e in events if e["change_type"] != "新增"]
    new_events = [e for e in events if e["change_type"] == "新增"]
    heroes = load_json(project_path("data", "heroes.json"))
    hero_skill_names = {s["name"] for h in heroes for s in h.get("skills", [])}
    skill_entries = [s for e in adjust_events for s in e.get("skills") or []]
    matched = sum(1 for s in skill_entries if s.get("skill") in hero_skill_names)
    unsynced = [
        h["name"] for h in heroes
        if (hero_last_change(h["name"], timeline) or "") > (h.get("last_updated") or "")
    ]
    print(f"✅ 时间轴已写入: {DEFAULT_TIMELINE_FILE}")
    print(f"  A 类快照: 调整事件 {sum(1 for e in init_events if e['change_type'] != '新增')} 条 /"
          f" 新增武将 {sum(1 for e in init_events if e['change_type'] == '新增')} 名（截止 {cutoff}）")
    print(f"  公告回填: 新增 {backfilled} 条（候选公告 {len(announcements)} 条）")
    print(f"  事件总数 {len(events)} = 调整 {len(adjust_events)} + 新增 {len(new_events)}")
    print(f"  技能级条目 {len(skill_entries)}，命中 heroes.json 技能名 {matched}"
          f"（未命中 {len(skill_entries) - matched} 条降级为 hero 级）")
    print(f"  heroes.json 同步风险武将 {len(unsynced)}: {unsynced[:8]}")


if __name__ == "__main__":
    main()
