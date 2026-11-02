"""估算「单数据集模式」的召回率（开发期分析工具）。

隐藏集若逐个数据集单独运行，就没有同源版本可对照。本脚本把全量运行中版本对照（X 类检查）判出的缺陷当作参考，
统计只靠单数据集检查能命中其中多少（按需求说明书缺陷类型、样本粒度）。
这是内部估计：参考本身来自本系统，不能替代评测端的真实召回率。

用法: python3 single_mode_recall.py <输出目录>
"""
import json
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath


def sample_of(path: str) -> str:
    name = PurePosixPath(path).name
    name = name[:-5] if name.endswith(".json") else name
    p = PurePosixPath(name)
    return p.stem if p.suffix else name


def main() -> None:
    out = Path(sys.argv[1])
    ref = defaultdict(set)      # 缺陷类型 -> {(数据集, 样本)}
    single = defaultdict(set)
    zh = {}
    for rp in sorted(list(out.glob("*/report.json")) + list(out.glob("details/*/report.json"))):
        r = json.loads(rp.read_text(encoding="utf-8"))
        for f in r["defect_list"]:
            t = f.get("defect_code") or f["defect_type"]
            zh[t] = f.get("defect_code_zh") or f["defect_type_zh"]
            key = (r["dataset_id"], sample_of(f["location"]["file"]))
            (ref if f["check_id"].startswith("X") else single)[t].add(key)
    print(f"{'缺陷类型':24s} {'参考(版本对照)':>12s} {'单数据集命中':>12s} {'召回率':>8s}")
    tot_r = tot_h = 0
    for t in sorted(ref):
        hit = len(ref[t] & single[t])
        tot_r += len(ref[t])
        tot_h += hit
        print(f"{t + ' ' + zh[t]:24s} {len(ref[t]):12d} {hit:12d} {hit / len(ref[t]):8.3f}")
    print(f"{'合计':24s} {tot_r:12d} {tot_h:12d} {tot_h / max(tot_r, 1):8.3f}")


if __name__ == "__main__":
    main()
