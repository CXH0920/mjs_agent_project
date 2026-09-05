"""10 对 × 3 次相性漂移采样：直接调 AIBatchGenerator.generate_synergy。

用法：
  改造后（当前代码，temperature=0.3）：
    python -m src.scripts.run_synergy_drift --out-prefix syn_after
  基线（先 git stash 回退到 HEAD，temperature=0.7）：
    git stash
    python -m src.scripts.run_synergy_drift --out-prefix syn_base
    git stash pop
  对比漂移：
    python -m src.scripts.eval_guide_quality --stats --synergies data/syn_base_run1.json data/syn_base_run2.json data/syn_base_run3.json
    python -m src.scripts.eval_guide_quality --stats --synergies data/syn_after_run1.json data/syn_after_run2.json data/syn_after_run3.json
"""
import argparse
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
from src.config.env import PROJECT_ROOT as ROOT
from src.config.env import get_api_config, get_runtime_params
from src.scraper.ai.api_generator import AIBatchGenerator
from src.scraper.ai.utils import load_heroes


def main():
    p = argparse.ArgumentParser(description="10对×3次相性漂移采样")
    p.add_argument("--out-prefix", default="syn_drift", help="输出文件前缀")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--pairs", default="data/sample_pairs.json")
    p.add_argument("--heroes", default="data/sample_heroes.json")
    args = p.parse_args()

    heroes = load_heroes(ROOT / args.heroes)
    id2hero = {h["id"]: h for h in heroes}
    pairs = json.loads((ROOT / args.pairs).read_text(encoding="utf-8"))

    api = get_api_config()
    if not api["api_key"]:
        print("错误：未配置 API Key")
        sys.exit(1)
    rp = get_runtime_params()
    gen = AIBatchGenerator(
        api_key=api["api_key"], api_url=api["api_url"], model=api["model"],
        requests_per_minute=rp["requests_per_minute"],
        max_retries=rp["max_retries"], http_timeout=rp["http_timeout"],
    )

    for r in range(1, args.rounds + 1):
        print(f"\n===== 第 {r}/{args.rounds} 轮 =====", flush=True)
        results = []
        for i, pair in enumerate(pairs, 1):
            a = id2hero[pair["hero_a_id"]]
            b = id2hero[pair["hero_b_id"]]
            res, _usage = gen.generate_synergy(a, b)
            if res is None:
                print(f"  [{i}/{len(pairs)}] {a['name']} <-> {b['name']}: FAIL", flush=True)
                results.append({"hero_a_id": a["id"], "hero_b_id": b["id"], "score": None})
            else:
                print(f"  [{i}/{len(pairs)}] {a['name']} <-> {b['name']}: score={res.get('score')}", flush=True)
                results.append({
                    "hero_a_id": a["id"], "hero_b_id": b["id"],
                    "score": res.get("score"), "synergy_rating": res.get("synergy_rating"),
                    "description": res.get("description", ""),
                })
        out = ROOT / "data" / f"{args.out_prefix}_run{r}.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  已存 {out}", flush=True)

    gen.close()
    print("\n完成。对比漂移：")
    runs = " ".join(f"data/{args.out_prefix}_run{r}.json" for r in range(1, args.rounds + 1))
    print(f"  python -m src.scripts.eval_guide_quality --stats --synergies {runs}")


if __name__ == "__main__":
    main()
