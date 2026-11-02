"""汇总各检查在各数据集上的命中率，用于标定判据。

命中率接近 100% 的检查多半是数据约定而非注入缺陷；同族 5 个子集之间命中率差异大的检查最有区分度。
用法: python3 calibration.py <输出目录>  > calibration.tsv
"""
import json
import sys
from pathlib import Path


def main() -> None:
    out = Path(sys.argv[1])
    print("dataset\tprofile\tverdict\tscore\tcheck\tstatus\tpass\tfail\tpending\tna\tfail_ratio\ttop_message")
    for rp in sorted(list(out.glob("*/report.json")) + list(out.glob("details/*/report.json"))):
        r = json.loads(rp.read_text(encoding="utf-8"))
        for c in r["checks"]:
            u = c["units"]
            judged = u["pass"] + u["fail"]
            msgs = {}
            for f in c["findings"]:
                if f["kind"] == "defect":
                    msgs[f["message"]] = msgs.get(f["message"], 0) + 1
            top = max(msgs, key=msgs.get) if msgs else ""
            print("\t".join(str(x) for x in (
                r["dataset_id"], r["profile"], r["verdict"]["id"], r["quality"]["score"], c["check_id"], c["status"],
                u["pass"], u["fail"], u["pending"], u["not_applicable"],
                f"{u['fail'] / judged:.4f}" if judged else "", top)))


if __name__ == "__main__":
    main()
