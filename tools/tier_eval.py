"""用发榜方公开的档位标签（评分标准附件一 A4）检验一次全量运行的结果。

标签只给出每个包的档位、注入覆盖率与污染项目类型，不给缺陷位置，因此本脚本检验三件事：
  ① 误报迹象：clean 包上的「不通过」结论；结论的缺陷代码不在该包污染项目内（计分轨）
  ② 覆盖率估计：至少有一条计分轨「不通过」结论的样本占比，对照官方注入覆盖率（5% / 20% / 40% / 60%）
  ③ 排序：组内名次是否与档位顺序一致

用法：python3 tools/tier_eval.py <输出目录> [标签文件.md|.csv]
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

TIERS = ["clean", "P1", "P2", "P3", "P4"]


def load_tiers(path: Path) -> dict[str, dict]:
    out = {}
    if path.suffix == ".csv":
        for r in csv.DictReader(path.open(encoding="utf-8-sig")):
            codes = [c for c in re.split(r"[;|, ]+", r.get("defect_class_ids", "")) if c]
            out[r["randomized_dir"]] = {"tier": r["tier"], "codes": set(codes)}
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\|\s*\d+\s*\|\s*`(\S+)`\s*\|\s*\S+\s*\|\s*(\S+)\s*\|\s*(\d+)%\s*\|(.*)\|", line)
        if m:
            out[m.group(1)] = {"tier": m.group(2), "coverage": int(m.group(3)) / 100,
                               "codes": set(re.findall(r"`([^`]+)`", m.group(4)))}
    return out


def sample_of(file: str) -> str:
    name = Path(file).name
    name = name[:-5] if name.endswith(".json") else name
    return Path(name).stem if Path(name).suffix else name


def main(out_dir: Path, label_file: Path) -> int:
    tiers = load_tiers(label_file)
    spec_codes = None
    rows, fam_rank = [], defaultdict(list)
    summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
    within = {x["dataset_id"]: x for x in summary["ranking"]["within_group"]}
    for d, t in sorted(tiers.items()):
        rp = next((p for p in (out_dir / d / "report.json", out_dir / "details" / d / "report.json") if p.exists()), None)
        if rp is None:
            continue
        r = json.loads(rp.read_text(encoding="utf-8"))
        n = max(r["records"], 1)
        by_code = defaultdict(set)
        unexpected = Counter()
        for f in r["defect_list"]:
            code, trk = f.get("defect_code"), f.get("defect_track")
            s = sample_of(f["location"].get("file", ""))
            by_code[code].add(s)
            if trk == "scoring" and code not in t["codes"]:
                unexpected[(f["check_id"], code)] += 1
        scoring_samples = set().union(*[v for k, v in by_code.items() if k in t["codes"]] or [set()])
        est = len(scoring_samples) / n
        rows.append({"dataset": d, "tier": t["tier"], "official": t.get("coverage"), "estimated": round(est, 4),
                     "confirmed": len(r["defect_list"]), "pending": len(r["review_queue"]),
                     "by_code": {k: len(v) for k, v in sorted(by_code.items())},
                     "unexpected": dict(unexpected), "rank_in_group": within.get(d, {}).get("rank_in_group")})
        fam_rank[d.rsplit("_", 1)[0]].append((TIERS.index(t["tier"]), within.get(d, {}).get("rank_in_group")))
    print(f"{'数据集':32s} {'档位':5s} {'官方覆盖':>8s} {'估计':>7s} {'名次':>4s}  计分轨样本数（按缺陷代码）   ｜ 不在污染项目内的结论")
    for x in rows:
        codes = " ".join(f"{k}:{v}" for k, v in x["by_code"].items())
        un = " ".join(f"{c}/{k}:{v}" for (c, k), v in x["unexpected"].items())
        off = f"{x['official']:.0%}" if x["official"] is not None else "-"
        print(f"{x['dataset']:32s} {x['tier']:5s} {off:>8s} {x['estimated']:7.1%} {x['rank_in_group'] or '-':>4}  {codes} ｜ {un}")
    ok = all([r for _, r in sorted(v)] == list(range(1, len(v) + 1)) for v in fam_rank.values())
    clean_fp = sum(x["confirmed"] for x in rows if x["tier"] == "clean")
    unexpected_total = sum(sum(x["unexpected"].values()) for x in rows)
    print(f"\n组内排序与档位一致：{'是' if ok else '否'}；clean 包上的不通过结论 {clean_fp} 条；"
          f"不在污染项目内的计分轨结论 {unexpected_total} 条")
    return 0


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    lab = Path(sys.argv[2]) if len(sys.argv) > 2 else root / "评分项目与准则-附数据集质量等级标签-v1.1.md"
    sys.exit(main(Path(sys.argv[1]), lab))
