"""单数据集模式的样本级检出率（开发期评估工具，只读公开集）。

公开集每个数据集的 5 个档位由同一批文件注入缺陷而来，因此「数据文件相对 clean 缺失，或标签与 clean 不同而数据文件逐字节相同」
的样本即计分轨注入的样本（真值）。数据文件本身也被改动的样本（图像与标注一并做几何变换、加噪，时序断裂）属反印证或数据质量类，
不进入分数，单独计数、不算作真值。各档位单独运行（没有同源版本可对照）后，取至少有一条计分轨「不通过」的样本为检出，统计：
    召回率 = 检出 ∩ 真值 / 真值        精确率 = 检出 ∩ 真值 / 检出
真值里也含非计分轨的注入（如数值扰动），因此召回率是下界。真值只用于评估，不进入任何判据。

用法: python3 single_mode_eval.py <公开集根目录> <逐目录单独运行的输出根目录> <档位标签 md>
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

TIERS = ["clean", "P1", "P2", "P3", "P4"]


def tiers(md: Path) -> dict[str, tuple[str, str]]:
    out = {}
    for line in md.read_text(encoding="utf-8").splitlines():
        p = [c.strip() for c in line.split("|")]
        if len(p) > 5 and p[2].startswith("`"):
            out[p[2].strip("`")] = (p[3], p[4])
    return out


def stem(name: str) -> str:
    name = os.path.basename(name)
    for ext in (".json", ".png", ".csv"):
        name = name[: -len(ext)] if name.endswith(ext) else name
    return name


def files(d: Path) -> tuple[dict[str, bytes], dict[str, Path]]:
    labels, data = {}, {}
    for root, _, fs in os.walk(d):
        for f in fs:
            p = Path(root) / f
            if f.endswith(".json"):
                labels[stem(f)] = p.read_bytes()
            else:
                data[stem(f)] = p
    return labels, data


def same_file(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            x, y = fa.read(1 << 20), fb.read(1 << 20)
            if x != y:
                return False
            if not x:
                return True


def detected(out: Path) -> set[str]:
    hits = set()
    with (out / "06_问题清单" / "findings.jsonl").open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["status"] == "不通过" and r.get("defectTrack") == "scoring":
                hits.add(stem((r.get("affectedScope") or {}).get("sample") or ""))
    return hits


def main() -> int:
    pub, runs, md = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    lab = tiers(md)
    by_ds = defaultdict(dict)
    for dir_id, (ds, tier) in lab.items():
        if (runs / dir_id / "06_问题清单").exists():
            by_ds[ds][tier] = dir_id
    print(f"{'数据集':16s} {'档位':5s} {'样本':>6s} {'真值':>6s} {'检出':>6s} {'命中':>6s} {'召回率':>7s} {'精确率':>7s} {'数据被改(不计)':>8s}")
    for ds in sorted(by_ds):
        if "clean" not in by_ds[ds]:
            continue
        cl, cd = files(pub / by_ds[ds]["clean"])
        for tier in TIERS:
            if tier not in by_ds[ds]:
                continue
            dir_id = by_ds[ds][tier]
            tl, td = files(pub / dir_id)
            missing = {k for k in cl if k in cd and k not in td}
            changed = {k for k in cl if tl.get(k) != cl[k] and k in cd and k in td}
            data_changed = {k for k in changed if not same_file(cd[k], td[k])}
            truth = missing | (changed - data_changed)
            hit = detected(runs / dir_id) - data_changed
            both = truth & hit
            rec = f"{len(both) / len(truth):.3f}" if truth else "—"
            prec = f"{len(both) / len(hit):.3f}" if hit else "—"
            print(f"{ds:16s} {tier:5s} {len(cl):6d} {len(truth):6d} {len(hit):6d} {len(both):6d} {rec:>7s} {prec:>7s} {len(data_changed):8d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
