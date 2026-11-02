"""把一次运行的输出转成闭门路演要求的两张 CSV（《选手展示和自测要求》第六章），并附六维度与缺陷类型结论表。

分数口径（第四章）：只有计分轨缺陷（标注质量类、文件配对类）进入分数；
    缺陷密度 d = 至少有一条计分轨「不通过」结论的样本数 / 样本数（评分卡 qualityPrediction.estimatedDefectCoverage）
    totalScore = 100 × (1 − d)，保留一位小数
分数与缺陷密度严格单调；密度相同时依次比较计分轨缺陷元素数 / 样本数、综合分、目录名，保证名次不并列。
密度是比例，与目录的样本规模无关，因此可以跨数据集直接比较，全局排序不是数据集内排序的拼接。

缺陷编号改为路演格式「检查编号-数据集简称-序号」（如 C01-AIA-000123），序号在同一数据集的 4 个目录间连续，全局唯一；
与引擎原编号的对照另存一张表，回查时可从路演编号找到问题清单与证据索引里的原记录。

结果表中的「N 处」按（样本, 事件）去重：同一事件被多项检查报出只计一处；证据表保留每项检查各自的结论。

用法: python3 roadshow_export.py <输出目录> <队名> <目标目录> [--dataset-version v34]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import yaml

VERDICT = {"通过": "pass", "不通过": "fail", "待判定": "undetermined", "不适用": "not_applicable"}
SUFFIX = re.compile(r"_[0-9A-Za-z]{10}$")
PAIRING = {"L0-01", "L0-03"}
ELEMENT = re.compile(r"^(.*\])")     # 字段路径截到最后一个元素下标：同一事件被多项检查报出时只计一处


def scoring(f: dict) -> bool:
    """计分轨以结论自带的 defectTrack 为准（如 L2-M 只在 ULF 上属计分轨）。"""
    return f.get("defectTrack") == "scoring"


def dataset_of(dir_id: str) -> str:
    return SUFFIX.sub("", dir_id)


def short_of(dataset_id: str) -> str:
    return dataset_id.split("_")[0]


def code_tracks(out: Path) -> tuple[dict[str, str], dict[str, set[str]], dict[str, str], dict[str, str]]:
    """从运行输出中的规格读出各缺陷代码的轨道、按标注族计分的例外、标注族中文名 → 标注族、标注族 → 模态。"""
    spec = yaml.safe_load((out / "03_评价规范.yaml").read_text(encoding="utf-8"))
    codes = spec["defect_codes"]
    return ({c: v["track"] for c, v in codes.items()}, {c: set(v.get("scoring_profiles") or []) for c, v in codes.items()},
            {v["zh"]: k for k, v in spec["profiles"].items()}, {k: v.get("modality") for k, v in spec["profiles"].items()})


def no_region_reason(ltype: str, pointer: str) -> str:
    """图像类「不通过」没有区域坐标时写明原因（《要求》自查第 5 项）。"""
    if ltype == "file":
        return "文件级缺陷（数据文件缺失或重复），没有可标的图像区域"
    if re.search(r"\]$|\]\.[A-Za-z_]+$", pointer or "") and "matched_events" in (pointer or ""):
        return "该事件没有坐标框（bbox_polygon_pixel 为空），按标注记录定位"
    return "元数据或汇总字段的缺陷，作用于整幅图像，没有单独的区域"


def load(out: Path):
    cards = {}
    for p in sorted((out / "04_评分卡").glob("*.json")):
        c = json.loads(p.read_text(encoding="utf-8"))
        cards[c["datasetId"]] = c
    findings = [json.loads(l) for l in (out / "06_问题清单" / "findings.jsonl").open(encoding="utf-8") if l.strip()]
    with (out / "07_证据索引" / "evidence_index.csv").open(encoding="utf-8") as f:
        index = {row["findingId"]: row for row in csv.DictReader(f)}
    payload = {}
    for p in sorted((out / "07_证据索引" / "evidence").glob("*.jsonl")):
        with p.open(encoding="utf-8") as f:
            for line in f:
                e = json.loads(line)
                if e.get("evidence"):
                    payload[e["findingId"]] = e["evidence"]
    return cards, findings, index, payload


def rank(cards: dict, findings: list[dict]) -> list[dict]:
    elem = Counter(f["datasetId"] for f in findings if f["status"] == "不通过" and scoring(f))
    rows = []
    for did, c in cards.items():
        q = c["qualityPrediction"]
        d = q["estimatedDefectCoverage"]
        rows.append({"dirId": did, "datasetId": dataset_of(did), "density": d, "samples": q["samples"],
                     "affected": q["affectedSamples"], "elemDensity": elem[did] / max(q["samples"], 1),
                     "composite": c["compositeScore"], "score": round(100 * (1 - d), 1)})
    rows.sort(key=lambda r: (r["density"], r["elemDensity"], -r["composite"], r["dirId"]))
    for i, r in enumerate(rows, 1):
        r["rankGlobal"] = i
    per = defaultdict(list)
    for r in rows:
        per[r["datasetId"]].append(r)
    for lst in per.values():
        for i, r in enumerate(lst, 1):
            r["rankInDataset"] = i
    return rows


def object_id(f: dict) -> str:
    s = (f.get("affectedScope") or {}).get("sample") or ""
    for ext in (".json", ".png", ".csv"):
        s = s[: -len(ext)] if s.endswith(ext) else s
    return s


KEY_ZH = {"actual": "实际值", "declared": "声明值", "reference": "参考值", "vocabulary": "类别表", "expected": "期望值",
          "copied_from": "复制自", "positioning_share": "定位类别占比", "file_window": "文件时间窗", "image_size": "实际图像尺寸"}
SKIP_KEYS = {"defect_code", "severity", "class_counts", "first_rows"}


def evidence_text(evd: dict | None, sev: dict) -> str:
    """证据中的标量取值（实际值、声明值等），让第三方不打开文件也能看到数值；与程度字段重复的不再列出。"""
    items = []
    for k, v in (evd or {}).items():
        if k in SKIP_KEYS or (k in ("reference", "actual") and k in sev):
            continue
        if isinstance(v, list) and len(v) <= 8 and all(isinstance(x, (str, int, float)) for x in v):
            v = "[" + ", ".join(str(x) for x in v) + "]"
        if isinstance(v, (str, int, float, bool)):
            items.append(f"{KEY_ZH.get(k, k)} {v}")
        if len(items) >= 6:
            break
    return "；".join(items)


def detail(f: dict, ev: dict | None, evd: dict | None = None, image: bool = False) -> str:
    parts = [f["message"]]
    sev = f.get("severity") or {}
    if f["status"] == "不通过" and sev.get("value") is not None and sev.get("metric") not in (None, "出现次数"):
        parts.append(f"{sev['metric']} = {sev['value']}{'' if sev.get('unit') in (None, '个', '处') else ' ' + sev['unit']}"
                     f"（容差 {sev.get('tolerance')}）")
    if "reference" in sev or "actual" in sev:
        parts.append(f"参考值 {sev.get('reference')}，实际值 {sev.get('actual')}")
    if f["status"] in ("不通过", "待判定"):
        parts.append(evidence_text(evd, sev))
    scope = f.get("affectedScope") or {}
    if scope.get("region"):
        parts.append("区域坐标 [x1,y1,x2,y2] = [" + ", ".join(str(x) for x in scope["region"]) + "] 像素")
    if scope.get("interval"):
        parts.append(f"时间区间 {scope['interval'][0]} 至 {scope['interval'][1]}")
    if image and f["status"] == "不通过" and not scope.get("region"):
        parts.append(no_region_reason((ev or {}).get("locatorType") or "", (ev or {}).get("pointer") or ""))
    if f["status"] == "不通过" and not scoring(f):
        parts.append("非计分轨，不计入分数")
    if f["status"] == "待判定":
        parts.append(f.get("basis") or "")
    if f["status"] == "不适用":
        parts.append(f.get("notApplicableReason") or "")
    if scope.get("units") is not None and f["status"] in ("通过", "不适用"):
        parts.append(f"检查范围：{scope['units']} 个单元")
    return "；".join(p for p in parts if p)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("team")
    ap.add_argument("dst", type=Path)
    ap.add_argument("--dataset-version", default="v34")
    a = ap.parse_args()
    cards, findings, index, payload = load(a.out)
    rows = rank(cards, findings)
    track_of, scoring_profiles, profile_of_zh, modality_of = code_tracks(a.out)
    image_dir = {d: modality_of.get(profile_of_zh.get(c.get("profile") or "")) == "image" for d, c in cards.items()}
    spec_codes = {}
    for f in findings:
        spec_codes[f["defectType"]] = f["defectTypeZh"]
    a.dst.mkdir(parents=True, exist_ok=True)

    # ---- 证据表 ----
    seq = Counter()
    id_map = []
    fails = defaultdict(lambda: defaultdict(set))    # dirId -> defectType -> {(样本, 事件)} 计分轨不通过的去重位置
    ev_rows = []
    for f in sorted(findings, key=lambda f: (dataset_of(f["datasetId"]), f["datasetId"], f["checkId"], f["findingId"])):
        did = f["datasetId"]
        key = (f["checkId"], short_of(dataset_of(did)))
        seq[key] += 1
        rid = f"{key[0]}-{key[1]}-{seq[key]:06d}"
        ev = index.get(f["findingId"]) or {}
        ltype = ev.get("locatorType") or "file"
        obj = object_id(f) if f["status"] in ("不通过", "待判定") and ltype != "sampleId" else ""
        ev_rows.append([rid, f["checkId"], f["ruleVersion"], did, obj, ev.get("pointer") or "", ltype,
                        VERDICT[f["status"]], detail(f, ev, payload.get(f["findingId"]), image_dir.get(did, False))])
        id_map.append([rid, f["findingId"], f.get("evidenceRef") or "", ev.get("file") or ""])
        if f["status"] == "不通过" and scoring(f):
            m = ELEMENT.match(ev.get("pointer") or "")
            fails[did][f["defectType"]].add((object_id(f) or ev.get("file") or f["findingId"], m.group(1) if m else ev.get("pointer") or ""))
    _write_csv(a.dst / f"证据表_{a.team}.csv",
               ["findingId", "checkId", "ruleVersion", "dirId", "objectId", "fieldPath", "locatorType", "verdict", "detail"],
               ev_rows)
    _write_csv(a.dst / f"缺陷编号对照_{a.team}.csv", ["findingId", "engineFindingId", "evidenceId", "file"], id_map)

    # ---- 结果表 ----
    res = []
    for r in sorted(rows, key=lambda r: (r["datasetId"], r["rankInDataset"])):
        c = Counter({t: len(locs) for t, locs in fails[r["dirId"]].items()})
        main_types = [f"{spec_codes[t]}" for t, _ in c.most_common(3)] or ["无计分轨缺陷"]
        pair = sum(n for t, n in c.items() if t in PAIRING)
        ev_txt = (f"计分轨缺陷样本 {r['affected']}/{r['samples']}（{100 * r['density']:.2f}%）；配对差异 {pair} 处；"
                  + "；".join(f"{spec_codes[t]} {n} 处" for t, n in c.most_common() if t not in PAIRING))
        res.append([r["datasetId"], a.dataset_version, r["dirId"], r["rankInDataset"], r["rankGlobal"], r["score"],
                    "；".join(main_types), ev_txt.rstrip("；")])
    _write_csv(a.dst / f"路演结果_{a.team}.csv",
               ["datasetId", "datasetVersion", "dirId", "rankInDataset", "rankGlobal", "totalScore", "mainDefectTypes", "evidence"],
               res)

    # ---- 缺陷类型结论（公开口径逐类：有 N 处 / 无 / 无法判定）与六维度 ----
    by = defaultdict(lambda: defaultdict(Counter))      # dirId -> code -> status 计数
    for f in findings:
        by[f["datasetId"]][f["defectType"]][f["status"]] += 1
    type_rows = []
    for r in sorted(rows, key=lambda r: r["rankGlobal"]):
        prof = profile_of_zh.get(cards[r["dirId"]].get("profile") or "")
        for code in sorted(by[r["dirId"]]):
            s = by[r["dirId"]][code]
            if s["不通过"]:
                concl = f"有，{s['不通过']} 处"
            elif s["待判定"] and not s["通过"]:
                concl = "无法判定"
            elif s["通过"]:
                concl = "无" + (f"（另有 {s['待判定']} 处待复核）" if s["待判定"] else "")
            else:
                concl = "不适用"
            on_score = track_of.get(code) == "scoring" or prof in scoring_profiles.get(code, set())
            type_rows.append([r["dirId"], code, spec_codes[code], "计分轨" if on_score else "非计分轨 / 补充",
                              concl, s["通过"], s["不通过"], s["待判定"], s["不适用"]])
    _write_csv(a.dst / f"缺陷类型结论_{a.team}.csv",
               ["dirId", "defectType", "name", "track", "conclusion", "passRecords", "failRecords", "undeterminedRecords",
                "notApplicableRecords"], type_rows)
    dim_rows = []
    for r in sorted(rows, key=lambda r: r["rankGlobal"]):
        for d in cards[r["dirId"]]["dimensions"]:
            dim_rows.append([r["dirId"], d["name"], d["score"] if d["score"] is not None else "undetermined",
                             d.get("grade") or "", d.get("status") or ""])
    _write_csv(a.dst / f"六维度结论_{a.team}.csv", ["dirId", "dimension", "score", "grade", "status"], dim_rows)

    # ---- 使用条件：验收、入库、选数分别给结论（《要求》3.6）----
    scene_rows = []
    for r in sorted(rows, key=lambda r: r["rankGlobal"]):
        card = cards[r["dirId"]]
        c = Counter({t: len(locs) for t, locs in fails[r["dirId"]].items()})
        top = "；".join(f"{spec_codes[t]} {n} 处" for t, n in c.most_common(3))
        usable = r["samples"] - r["affected"]
        if r["affected"] == 0:
            scene_rows.append([r["dirId"], "验收", "通过", f"全部 {r['samples']} 个样本", "无计分轨缺陷；非计分轨提示见证据表，按需复核"])
        else:
            scene_rows.append([r["dirId"], "验收", "退回整改", f"{r['affected']} 个样本（{100 * r['density']:.2f}%）",
                               f"按证据表逐条整改后重新提交：{top}"])
        verdict = card["verdict"]
        crit_types = list(dict.fromkeys(d["defectType"] for d in card.get("criticalDefects", [])))
        crit = "；".join(f"{spec_codes[t]} {c[t]} 处" for t in crit_types if c[t])
        scene_rows.append([r["dirId"], "入库", verdict["zh"], f"整个目录（{r['samples']} 个样本）",
                           verdict.get("action", "") + (f"；关键缺陷：{crit}" if crit else "")])
        scene_rows.append([r["dirId"], "选数", f"可选 {usable} / {r['samples']} 个样本",
                           f"剔除 {r['affected']} 个有计分轨缺陷的样本",
                           "剔除清单为证据表中 verdict=fail 且计入分数的 objectId；待判定样本按用途决定是否保留"])
    _write_csv(a.dst / f"场景结论_{a.team}.csv", ["dirId", "scenario", "conclusion", "scope", "recommendation"], scene_rows)
    print(f"{len(res)} 个目录，{len(ev_rows)} 条结论 → {a.dst}")
    for r in sorted(rows, key=lambda r: r["rankGlobal"]):
        print(f"{r['rankGlobal']:>2} {r['dirId']:28s} {r['score']:6.1f}  密度 {r['density']:.4f}  集内 {r['rankInDataset']}")
    return 0


def _write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
