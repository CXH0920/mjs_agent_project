"""攻略/相性生成质量评估：样本采样 + 基线/改造后统计对照。

用法：
  python -m src.scripts.eval_guide_quality --pick-sample
  python -m src.scripts.eval_guide_quality --stats --guides data/guides.json --synergies data/synergies.json [--attempts N]

--pick-sample：分层采样 20 武将（覆盖势力/定位）+ 10 对相性，输出清单并写
  data/sample_heroes.json（供 --heroes-file）/ data/sample_pairs.json。
--stats：统计攻略技能联动覆盖率、相性 score 分布/漂移、JSON 抛错率（需 --attempts）。
"""
import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from src.config.env import PROJECT_ROOT as ROOT

HEROES_FILE = ROOT / "data" / "heroes.json"


def pick_sample(n: int = 20, seed: int = 42):
    import random
    heroes = json.loads(HEROES_FILE.read_text(encoding="utf-8"))
    random.seed(seed)

    # 分层：每势力按定位各取，保证势力+定位覆盖
    by_faction = defaultdict(list)
    for h in heroes:
        by_faction[h.get("faction", "")].append(h)
    sample = []
    per = max(1, n // len(by_faction))
    for f, hs in by_faction.items():
        random.shuffle(hs)
        sample.extend(hs[:per])
    # 补足/裁剪到 n
    if len(sample) < n:
        rest = [h for h in heroes if h["id"] not in {s["id"] for s in sample}]
        random.shuffle(rest)
        sample.extend(rest[: n - len(sample)])
    random.shuffle(sample)
    sample = sample[:n]

    # 10 对：从样本内随机配对，互不重复
    ids = [h["id"] for h in sample]
    random.shuffle(ids)
    pairs = [(ids[i], ids[i + 1]) for i in range(0, 2 * 10, 2)]

    print(f"## {n} 武将样本（覆盖 {len(set(h['faction'] for h in sample))} 势力 / "
          f"{len(set(h['position'] for h in sample))} 定位）")
    for h in sample:
        print(f"- id={h['id']} {h['name']} ({h.get('faction','')}/{h.get('position','')})")
    print("\n## 10 对相性样本")
    for a, b in pairs:
        print(f"- {a} <-> {b}")

    (ROOT / "data" / "sample_heroes.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "data" / "sample_pairs.json").write_text(
        json.dumps([{"hero_a_id": a, "hero_b_id": b} for a, b in pairs],
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写 data/sample_heroes.json / data/sample_pairs.json")


def stats(guides_path, synergies_paths, attempts=None):
    print("=" * 55)
    print("  生成质量统计")
    print("=" * 55)

    # 攻略：技能联动覆盖率
    if guides_path and Path(guides_path).exists():
        guides = json.loads(Path(guides_path).read_text(encoding="utf-8"))
        total = len(guides)
        link_hits = 0
        for g in guides:
            desc = g.get("description", "")
            if "技能联动" in desc or "无显著联动" in desc or "无联动" in desc:
                link_hits += 1
        rate = link_hits / total * 100 if total else 0
        print(f"\n[攻略] 共 {total} 条")
        print(f"  技能联动覆盖率: {link_hits}/{total} = {rate:.1f}%")
        if attempts:
            err_rate = (attempts - total) / attempts * 100 if attempts else 0
            print(f"  JSON 抛错率（尝试 {attempts}）: {attempts - total}/{attempts} = {err_rate:.1f}%")

    # 相性：单文件分布 / 多文件漂移对比
    if synergies_paths:
        loaded = [(p, json.loads(Path(p).read_text(encoding="utf-8")))
                  for p in synergies_paths if Path(p).exists()]
        if len(loaded) == 1:
            syn = loaded[0][1]
            scores = [s.get("score") for s in syn if s.get("score") is not None]
            ratings = Counter(s.get("synergy_rating", "?") for s in syn)
            print(f"\n[相性] 共 {len(syn)} 条（{loaded[0][0]}）")
            if scores:
                print(f"  score: min={min(scores)} max={max(scores)} "
                      f"mean={statistics.mean(scores):.2f} std={statistics.pstdev(scores):.2f}")
                print(f"  评级分布: {dict(ratings)}")
        elif len(loaded) >= 2:
            by_key = {}
            for _p, syn in loaded:
                for s in syn:
                    key = tuple(sorted((s.get("hero_a_id", 0), s.get("hero_b_id", 0))))
                    sc = s.get("score")
                    if sc is not None:
                        by_key.setdefault(key, []).append(sc)
            total = sum(1 for scs in by_key.values() if len(scs) >= 2)
            drift = sum(1 for scs in by_key.values()
                        if len(scs) >= 2 and max(scs) - min(scs) >= 3)
            print(f"\n[相性漂移] {len(loaded)} 文件对比，同对 {total} 个")
            if total:
                print(f"  |Δscore|≥3 比例: {drift}/{total} = {drift/total*100:.1f}%")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="攻略/相性生成质量评估")
    p.add_argument("--pick-sample", action="store_true", help="采样 20 武将 + 10 对")
    p.add_argument("--stats", action="store_true", help="统计生成结果")
    p.add_argument("--guides", default="data/guides.json")
    p.add_argument("--synergies", nargs="+", default=["data/synergies.json"])
    p.add_argument("--attempts", type=int, default=None, help="尝试生成数（算抛错率用）")
    args = p.parse_args()
    if args.pick_sample:
        pick_sample()
    if args.stats:
        stats(args.guides, args.synergies, args.attempts)
