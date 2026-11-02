"""按《交付材料清单》（§三 提交包结构、§四 格式与命名、§五 必填字段）输出评估结果。

    00_MANIFEST.yaml                       本次输出的文件清单与校验值
    03_评价规范.yaml                        本次运行所依据的规格（与镜像内 spec 逐字节相同）
    04_评分卡/<datasetId>_<version>.json   逐数据集评分卡（DEL-5），另有同名 .html 可读版
    05_排序表/ranking_global.csv            全局排序（DEL-6），ranking_explanation.md 为可读说明
    06_问题清单/findings.jsonl              问题清单（DEL-7）：四类状态全量输出，结论以此为准
    06_问题清单/findings_report.md          问题清单可读版
    07_证据索引/evidence_index.csv          证据索引（DEL-8）；evidence/<datasetId>.jsonl 为证据文件
    08_运行日志/run.log.jsonl               运行日志（DEL-9）：一行一事件，UTC 时间戳
    08_运行日志/system_env.txt、seeds.yaml  系统版本与随机种子（DEL-10）
    09_未完成事项说明.md                    未完成事项（DEL-11）
    details/<datasetId>/report.json        完整判定记录（含全部证据，供复算）

字段名小驼峰；CSV 逗号分隔、UTF-8、含表头。除运行日志与 MANIFEST 中的时间外，内容由输入与规格确定，重跑一致。
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import platform
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml

from .variants import unit_sample

STATUS_ZH = {"pass": "通过", "fail": "不通过", "pending": "待判定", "not_applicable": "不适用"}
USAGE_ZH = {"ingest": "入库", "training": "选数", "acceptance": "验收"}


def run_id(agent_version: str, spec_sha: str, reports: list[dict]) -> str:
    """运行编号：由系统版本、规格指纹与全部输入数据集版本确定，同一输入重跑得到同一编号。"""
    h = hashlib.sha256(f"{agent_version}\n{spec_sha}\n".encode())
    for r in sorted(reports, key=lambda r: r["dataset_id"]):
        h.update(f"{r['dataset_id']}\t{r['scorecard']['dataset_version']}\n".encode())
    return "run-" + h.hexdigest()[:16]


def _ver(r: dict) -> str:
    return (r["scorecard"]["dataset_version"] or "")[:12]


def _csv_text(header: list[str], rows: list[list[Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for row in rows:
        w.writerow(["" if x is None else (json.dumps(x, ensure_ascii=False, sort_keys=True) if isinstance(x, (dict, list)) else x)
                    for x in row])
    return buf.getvalue()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# 程度计量（severity 对象）
# ---------------------------------------------------------------------------

def severity_of(spec, check_id: str, f: dict) -> dict[str, Any]:
    """结论的程度计量：度量名、数值、单位、容差；有两轴严重度时一并给出（评分准则 §2.4）。"""
    ev = f.get("evidence") or {}
    tol = spec.check(check_id).tolerance or {}
    sev = ev.get("severity") if isinstance(ev.get("severity"), dict) else None
    if sev and "score" in sev:
        out = {"metric": f"{sev['form']} 严重度（两轴归一后 1:1）", "value": sev["score"], "unit": "[0,1]",
               "tolerance": 0, "form": sev["form"], "axes": sev["axes"]}
        for k, u in (("offset_px", "px"), ("time_offset_s", "s"), ("area_ratio", "倍"), ("relative_deviation", "比例")):
            if ev.get(k) is not None:
                out[k] = ev[k]
        return out
    if "time_offset_s" in ev:
        return {"metric": "时间偏移", "value": ev["time_offset_s"], "unit": "s", "tolerance": spec.raw.get("time_tolerance_s", 1)}
    if "center_distance_px" in ev and ev.get("solar_radius_px"):
        return {"metric": "目标中心到日心距离 / 太阳半径", "value": round(ev["center_distance_px"] / ev["solar_radius_px"], 4),
                "unit": "R☉", "tolerance": spec.raw["physics"]["limb_margin"]}
    if "area_ratio" in ev:
        return {"metric": "面积比", "value": ev["area_ratio"], "unit": "倍", "tolerance": spec.raw["physics"]["area_ratio_range"]}
    if "ratio" in ev:
        return {"metric": "实测与理论比值", "value": ev["ratio"], "unit": "倍", "tolerance": ev.get("tolerance", tol)}
    if ev.get("max_relative_deviation") is not None:
        return {"metric": "最大相对偏差", "value": ev["max_relative_deviation"], "unit": "比例", "tolerance": 0}
    if ev.get("relative_deviation") is not None:
        return {"metric": "相对偏差", "value": ev["relative_deviation"], "unit": "比例", "tolerance": 0}
    if ev.get("points_change_ratio") is not None:
        return {"metric": "采样点变化比例", "value": ev["points_change_ratio"], "unit": "比例", "tolerance": 0}
    if ev.get("agreeing_samples") and ev.get("samples"):
        return {"metric": "同一事件取多数值的样本占比（本样本与之不同）", "value": round(ev["agreeing_samples"] / ev["samples"], 4),
                "unit": "比例", "tolerance": "> 0.5 才判", "reference": ev.get("reference"), "actual": ev.get("actual")}
    if "declared" in ev and "actual" in ev:
        return {"metric": "声明值与实际值不符", "value": {"declared": ev["declared"], "actual": ev["actual"]},
                "unit": None, "tolerance": 0}
    return {"metric": "出现次数", "value": 1, "unit": "处", "tolerance": 0}


# ---------------------------------------------------------------------------
# 问题清单 + 证据索引
# ---------------------------------------------------------------------------

def _locator(dataset_id: str, loc: dict) -> tuple[str, str]:
    path = f"{dataset_id}/{loc.get('file', '')}".rstrip("/")
    if loc.get("region"):
        return "imageRegion", f"{path}#{loc.get('pointer') or ''}@bbox={','.join(str(x) for x in loc['region'])}"
    if loc.get("interval"):
        return "timeInterval", f"{path}#{loc.get('pointer') or ''}@interval={loc['interval'][0]}/{loc['interval'][1]}"
    if loc.get("pointer"):
        return "annotationRecord", f"{path}#{loc['pointer']}"
    if loc.get("file") and loc["file"] != dataset_id:
        return "file", path
    return "sampleId", dataset_id


def build_findings(spec, r: dict, rid: str, hashes: dict[str, str]) -> tuple[list[dict], list[list], list[dict]]:
    """返回 (问题清单记录, 证据索引行, 证据文件记录)。

    不通过、待判定逐条输出；通过与单元级不适用按检查汇总为一条（含单元数）；整项不适用一条并写明原因。
    """
    did, dver = r["dataset_id"], r["scorecard"]["dataset_version"]
    sev_of = {c.id: c.severity for c in spec.checks}
    name_of = {c.id: c.name for c in spec.checks}
    method_of = {c.id: c.method for c in spec.checks}
    records, index, payload = [], [], []
    for c in r["checks"]:
        cid = c["check_id"]
        seq = 0

        def emit(status: str, code: str, loc: dict, message: str, extra: dict, ev: dict | None, content_hash: str):
            nonlocal seq
            seq += 1
            fid = f"{cid}-{did}-{seq:06d}"
            eid = f"EV-{fid}"
            ltype, locator = _locator(did, loc)
            rec = {"findingId": fid, "checkId": cid, "ruleVersion": spec.rule_version,
                   "datasetId": did, "datasetVersion": dver, "defectType": code,
                   "defectTypeZh": spec.raw["defect_codes"][code]["zh"],
                   "status": status, "evidenceRef": eid, "message": message, **extra}
            records.append(rec)
            index.append([eid, fid, ltype, locator, content_hash, rid, cid, loc.get("file"), loc.get("pointer"),
                          loc.get("region"), loc.get("interval")])
            payload.append({"evidenceId": eid, "findingId": fid, "checkId": cid, "location": loc, "evidence": ev or {}})

        default_code = spec.check(cid).defect_code_by_profile.get(r["profile"] or "", spec.check(cid).defect_code)
        units = c["units"]
        dataset_loc = {"file": did}
        if c["status"] == "not_applicable" and not c["findings"]:
            emit("不适用", default_code, dataset_loc, f"{name_of[cid]}：不适用",
                 {"notApplicableReason": c.get("reason") or "不适用", "affectedScope": {"units": 0},
                  "confirmedOrPending": None, "criticality": None,
                  "severity": {"metric": "不适用", "value": None, "unit": None, "tolerance": None},
                  "basis": method_of[cid]}, None, dver)
            continue
        for f in c["findings"]:
            loc = f["location"]
            code = f.get("defect_code") or default_code
            st = "不通过" if f["kind"] == "defect" else "待判定"
            scope = {"samples": 1, "sample": unit_sample(loc.get("file", "")), "category": code}
            for k in ("region", "interval"):
                if loc.get(k):
                    scope[k] = loc[k]
            extra = {"severity": severity_of(spec, cid, f), "affectedScope": scope,
                     "criticality": ("关键" if sev_of[cid] == "critical" else "一般") if st == "不通过" else None,
                     "basis": (f"{name_of[cid]}：{method_of[cid] or ''}" if st == "不通过"
                               else f"缺少的材料与补查要求：{f.get('missing_materials') or c.get('missing_materials') or '见说明'}"),
                     "confirmedOrPending": "已确认错误" if st == "不通过" else "待复核事项",
                     "defectTrack": f.get("defect_track")}
            emit(st, code, loc, f["message"], extra, f.get("evidence"), hashes.get(loc.get("file", ""), dver))
        if c["status"] == "pending" and not c["findings"]:
            emit("待判定", default_code, dataset_loc, c.get("reason") or "待判定",
                 {"severity": {"metric": "无法计量", "value": None, "unit": None, "tolerance": None},
                  "affectedScope": {"units": None}, "criticality": None, "confirmedOrPending": "待复核事项",
                  "basis": f"缺少的材料与补查要求：{c.get('missing_materials') or '见说明'}"}, None, dver)
        if units.get("pass"):
            emit("通过", default_code, dataset_loc, f"{name_of[cid]}：{units['pass']} 个单元通过",
                 {"severity": {"metric": "不通过单元数", "value": 0, "unit": "个", "tolerance": 0},
                  "affectedScope": {"units": units["pass"]}, "criticality": None, "confirmedOrPending": None,
                  "basis": method_of[cid]}, None, dver)
        if units.get("not_applicable") and c["status"] != "not_applicable":
            emit("不适用", default_code, dataset_loc, f"{name_of[cid]}：{units['not_applicable']} 个单元不适用",
                 {"notApplicableReason": c.get("reason") or "部分单元的标注族或内容不满足本检查的适用前提",
                  "affectedScope": {"units": units["not_applicable"]}, "criticality": None, "confirmedOrPending": None,
                  "severity": {"metric": "不适用", "value": None, "unit": None, "tolerance": None},
                  "basis": method_of[cid]}, None, dver)
    return records, index, payload


# ---------------------------------------------------------------------------
# 评分卡
# ---------------------------------------------------------------------------

DIM_MEANING = {
    "completeness": "文件存在性、数据完好：缺失、损坏与内容改动越少得分越高",
    "annotation": "类别正确、无漏标虚标、边界与位置准确、数值未被扰动",
    "correspondence": "标签与数据一一对应、结构自洽、无重复样本",
    "coverage": "类别分布与同源版本参考分布一致",
    "provenance": "声明的格式、尺寸、时刻、参数与数据内容一致",
    "usage": "具备使用权限、许可与用途说明",
}


def _grade_of(spec, score) -> str | None:
    if score is None:
        return None
    for g in spec.raw["grades"]:
        if score >= g["min_score"]:
            return g["zh"]
    return None


def quality_prediction(spec, r: dict, within: dict | None = None) -> dict[str, Any]:
    """质量预测：估计注入覆盖率并给出档位。

    同源版本组的版本数与档位数相同时，按组内名次一一对应档位（附件一：同组各版本分属不同档位）；
    否则取估计覆盖率最接近的档位锚点。
    """
    n = max(r["records"], 1)
    samples = {unit_sample(f["location"].get("file", "")) for f in r["defect_list"] if f.get("defect_track") == "scoring"}
    est = len(samples) / n
    anchors = spec.raw.get("tier_anchors") or {}
    order = sorted(anchors, key=anchors.get)
    if within and within.get("group_size") == len(order):
        tier, how = order[within["rank_in_group"] - 1], "组内名次对应档位（同源版本数与档位数相同）"
    else:
        tier = min(anchors, key=lambda k: (abs(anchors[k] - est), anchors[k])) if anchors else None
        how = "估计覆盖率最接近的档位锚点"
    return {"estimatedDefectCoverage": round(est, 6), "affectedSamples": len(samples), "samples": n,
            "predictedTier": tier, "tierAnchors": anchors, "method": how,
            "coverageDefinition": "至少有一条计分轨「不通过」结论的样本数 / 样本数（评分标准附件一 A2 的档位锚点）"}


def scorecard_json(spec, r: dict, rid: str, finding_ids: dict[str, list[str]], evidence_ids: list[str],
                   within: dict | None = None, remedies: dict | None = None) -> dict:
    q = r["quality"]
    cs = Counter()
    for c in r["checks"]:
        cs[STATUS_ZH[c["status"]]] += 1
    dims = [{"dimId": d, "name": v["zh"], "score": v["score"], "range": "0-100",
             "grade": _grade_of(spec, v["score"]) if v["score"] is not None else "证据不足",
             "meaning": DIM_MEANING.get(d, ""), "weight": v["weight"],
             "measures": v["measures"], "status": v["status"]} for d, v in q["dimensions"].items()]
    groups = Counter((f["check_id"], f.get("defect_code")) for f in r["defect_list"])
    top = []
    for (cid, code), n in sorted(groups.items(), key=lambda x: (-x[1], x[0])):
        ids = finding_ids.get(f"{cid}|{code}", [])
        top.append({"checkId": cid, "defectType": code, "defectTypeZh": spec.raw["defect_codes"][code]["zh"],
                    "count": n, "findingIds": ids[:5],
                    "impact": f"{n} 处，涉及 {spec.raw['dimensions'][spec.check(cid).dimension]['zh']}维度",
                    "remediation": spec.check(cid).remediation,
                    **({"remediationDetail": remedies[f"{cid}|{code}"],
                        "remediationDetailBy": "子代理 remediation_writer（模型改写表述，不参与判定与计分）"}
                       if remedies and f"{cid}|{code}" in remedies else {})})
    crit = [t for t in top if spec.check(t["checkId"]).severity == "critical"]
    return {
        "datasetId": r["dataset_id"], "datasetVersion": r["scorecard"]["dataset_version"],
        "declaredUsage": USAGE_ZH.get(r["purpose"], r["purpose"]), "ruleVersion": spec.rule_version,
        "specVersion": f"{spec.raw['spec_name']} {spec.rule_version}（sha256 {spec.sha256[:16]}）", "runId": rid,
        "profile": r["profile_zh"],
        "compositeScore": q["score"], "grade": q["grade"], "verdict": r["verdict"],
        "qualityPrediction": quality_prediction(spec, r, within),
        "dimensions": dims,
        "coverage": {"completed": r["coverage"]["completed"], "applicable": r["coverage"]["applicable"],
                     "ratio": r["coverage"]["completion_rate"], "statement": r["coverage"]["statement"]},
        "checkStatusSummary": {k: cs.get(k, 0) for k in ("通过", "不通过", "待判定", "不适用")},
        "defectMeasures": {k: {"name": m["zh"], "numerator": m["numerator"], "denominator": m["denominator"],
                               "ratio": m["ratio"], "definition": m["definition"],
                               **({"notComputableReason": m["not_computable_reason"]} if "not_computable_reason" in m else {})}
                           for k, m in q["measures"].items()},
        "topDefects": top[:10],
        "criticalDefects": crit,
        "keyDefectRule": q["key_defects"]["rule"],
        "uncomputableDimensions": [{"dimId": d["dimId"], "name": d["name"], "reason": "相关程度指标均不可计算（证据不足或检查不适用）"}
                                   for d in dims if d["score"] is None],
        "reviewAndSupplement": r["scorecard"]["review_and_supplement"],
        "evidenceRefs": evidence_ids,
    }


def scorecard_html(card: dict) -> str:
    e = html.escape

    def row(*cells):
        return "<tr>" + "".join(f"<td>{e(str(c)) if c is not None else '—'}</td>" for c in cells) + "</tr>"
    qp = card["qualityPrediction"]
    parts = [f"<!doctype html><html lang='zh'><head><meta charset='utf-8'><title>评分卡 {e(card['datasetId'])}</title>",
             "<style>body{font-family:system-ui,'PingFang SC','Noto Sans SC',sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;color:#14213D}"
             "table{border-collapse:collapse;width:100%;margin:8px 0 20px}td,th{border:1px solid #ccc;padding:4px 8px;font-size:14px;text-align:left}"
             "th{background:#f0ede4}h1{font-size:22px}h2{font-size:17px;margin-top:24px}</style></head><body>",
             f"<h1>评分卡 · {e(card['datasetId'])}</h1>",
             "<table>", row("数据集版本", card["datasetVersion"]), row("评价用途", card["declaredUsage"]),
             row("规则版本 / 规范", f"{card['ruleVersion']} / {card['specVersion']}"), row("运行编号", card["runId"]),
             row("综合分 / 等级", f"{card['compositeScore']} / {card['grade']['zh']}{'（暂定）' if card['grade'].get('provisional') else ''}"),
             row("入库结论", card["verdict"]["zh"]),
             row("质量预测", f"估计缺陷覆盖率 {qp['estimatedDefectCoverage']:.2%}，最接近档位 {qp['predictedTier']}"),
             row("检查覆盖", card["coverage"]["statement"]),
             row("四类状态", "，".join(f"{k} {v}" for k, v in card["checkStatusSummary"].items())), "</table>",
             "<h2>各维度得分</h2><table><tr><th>维度</th><th>得分</th><th>等级</th><th>含义</th></tr>"]
    parts += [row(d["name"], d["score"], d["grade"], d["meaning"]) for d in card["dimensions"]]
    parts += ["</table><h2>缺陷程度</h2><table><tr><th>指标</th><th>分子</th><th>分母</th><th>比例</th><th>定义</th></tr>"]
    parts += [row(m["name"], m["numerator"], m["denominator"], m["ratio"] if m["ratio"] is not None else m.get("notComputableReason"),
                  m["definition"]) for m in card["defectMeasures"].values()]
    parts += ["</table><h2>主要缺陷及影响</h2><table><tr><th>检查</th><th>缺陷类型</th><th>数量</th><th>示例结论</th><th>整改建议</th></tr>"]
    parts += [row(t["checkId"], f"{t['defectType']} {t['defectTypeZh']}", t["count"], " ".join(t["findingIds"][:2]),
                  t.get("remediationDetail") or t["remediation"])
              for t in card["topDefects"]] or [row("无", "", "", "", "")]
    parts += ["</table><h2>待复核与补充材料</h2><ul>"]
    parts += [f"<li>{e(x['check_id'])} {e(x['name'])}（{x['units']} 处）：{e(str(x['missing_materials'] or x['reason']))}</li>"
              for x in card["reviewAndSupplement"]] or ["<li>无</li>"]
    parts += ["</ul></body></html>\n"]
    return "".join(parts)


# ---------------------------------------------------------------------------
# 总入口
# ---------------------------------------------------------------------------

def write_all(out: Path, spec, spec_path: Path, reports: list[dict], ranking: dict, hashes: dict[str, dict],
              events: dict[str, list[dict]], meta: dict, remedies: dict | None = None) -> dict[str, Any]:
    rid = meta["runId"]
    all_findings, all_index = [], []
    per_ds_ids: dict[str, dict[str, list[str]]] = {}
    per_ds_ev: dict[str, list[str]] = {}
    for r in reports:
        recs, idx, payload = build_findings(spec, r, rid, hashes.get(r["dataset_id"], {}))
        all_findings += recs
        all_index += idx
        ids = defaultdict(list)
        for x in recs:
            if x["status"] == "不通过":
                ids[f"{x['checkId']}|{x['defectType']}"].append(x["findingId"])
        per_ds_ids[r["dataset_id"]] = ids
        per_ds_ev[r["dataset_id"]] = [x["evidenceRef"] for x in recs]
        _write(out / "07_证据索引" / "evidence" / f"{r['dataset_id']}.jsonl",
               "".join(json.dumps(p, ensure_ascii=False, sort_keys=True) + "\n" for p in payload))

    _write(out / "06_问题清单" / "findings.jsonl", "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in all_findings))
    _write(out / "07_证据索引" / "evidence_index.csv", _csv_text(
        ["evidenceId", "findingId", "locatorType", "locator", "contentHash", "capturedBy",
         "checkId", "file", "pointer", "region", "interval"], all_index))

    # 评分卡
    within_by = {x["dataset_id"]: x for x in ranking["within_group"]}
    for r in reports:
        card = scorecard_json(spec, r, rid, per_ds_ids[r["dataset_id"]], per_ds_ev[r["dataset_id"]],
                              within_by.get(r["dataset_id"]), remedies)
        name = f"{r['dataset_id']}_{_ver(r)}"
        _write(out / "04_评分卡" / f"{name}.json", _json(card))
        _write(out / "04_评分卡" / f"{name}.html", scorecard_html(card))

    # 排序表
    by_id = {r["dataset_id"]: r for r in reports}
    within = {x["dataset_id"]: x for x in ranking["within_group"]}
    rows = []
    for g in ranking["global"]:
        r = by_id[g["dataset_id"]]
        dims = {v["zh"]: v["score"] if v["score"] is not None else "证据不足" for v in r["quality"]["dimensions"].values()}
        w = within[g["dataset_id"]]
        retain = ("否" if g["difference_significant"] in (True, None)
                  else "是：与下一名分差 < 0.5，建议同时保留分项比较（排序仍不并列）")
        rows.append([g["rank"], g["dataset_id"], r["scorecard"]["dataset_version"], g["quality_score"], dims,
                     w["group"], f"组内第 {w['rank_in_group']}/{w['group_size']}；" + (g["ahead_of_next_because"] or "末位"),
                     retain, g["grade"], quality_prediction(spec, r, w)["predictedTier"]])
    _write(out / "05_排序表" / "ranking_global.csv", _csv_text(
        ["rank", "datasetId", "datasetVersion", "totalScore", "dimensionScores", "groupId", "comparisonBasis",
         "retainSubRanking", "grade", "predictedTier"], rows))
    _write(out / "05_排序表" / "ranking_explanation.md", ranking_md(spec, reports, ranking))
    _write(out / "06_问题清单" / "findings_report.md", findings_md(spec, reports, all_findings))

    # 运行日志、系统版本、种子
    log_lines = []
    for did in sorted(events):
        for e in events[did]:
            log_lines.append(json.dumps({"ts": e["ts"], "runId": rid, **{k: v for k, v in e.items() if k != "ts"},
                                         "level": "ERROR" if e["event"] == "检查失败" else ("WARN" if e["event"] in ("重试", "降级") else "INFO")},
                                        ensure_ascii=False))
    _write(out / "08_运行日志" / "run.log.jsonl", "\n".join(log_lines) + "\n")
    _write(out / "08_运行日志" / "seeds.yaml", yaml.safe_dump(
        {"runId": rid, "globalSeed": meta["random_seed"], "samplingSeed": None,
         "note": "判定全程确定性，不使用随机数；不抽样（证据链全量保留）。种子仍按审计要求声明"}, allow_unicode=True, sort_keys=False))
    _write(out / "08_运行日志" / "system_env.txt", "\n".join([
        f"agentVersion: {meta['agent_version']}", f"ruleVersion: {spec.rule_version}", f"specSha256: {spec.sha256}",
        f"runId: {rid}", f"python: {platform.python_version()}", f"platform: {platform.platform()}",
        f"pyyaml: {yaml.__version__}", "parallelism: 见 run_summary.json（workers、availableCpus）",
        f"llm: {'启用（模型名见环境变量 LLM_MODEL，消耗见 run_summary.json）' if meta['llm_usage'].get('calls') else '未启用（模型服务消耗 0）'}", ""]))
    _write(out / "08_运行日志" / "run_summary.json", _json(meta))
    _write(out / "03_评价规范.yaml", spec_path.read_text(encoding="utf-8"))
    return {"runId": rid, "findings": len(all_findings)}


def ranking_md(spec, reports: list[dict], ranking: dict) -> str:
    by_id = {r["dataset_id"]: r for r in reports}
    within = {x["dataset_id"]: x for x in ranking["within_group"]}
    lines = ["# 全局排序说明", "", f"规则版本 {spec.rule_version}。排序覆盖全部受评数据集，不输出并列。", "",
             f"排序规则：{ranking['rule']}。", "",
             "| 名次 | 数据集 | 综合分 | 等级 | 预测档位 | 主要扣分因素 | 领先下一名的原因 |", "|---:|---|---:|---|---|---|---|"]
    for g in ranking["global"]:
        tier = quality_prediction(spec, by_id[g["dataset_id"]], within.get(g["dataset_id"]))["predictedTier"]
        factors = "；".join(f"{m['zh']} {m['ratio']}" for m in g["main_factors"]) or "无"
        lines.append(f"| {g['rank']} | {g['dataset_id']} | {g['quality_score']} | {g['grade']} | {tier} | {factors} | "
                     f"{g['ahead_of_next_because'] or '—'} |")
    return "\n".join(lines) + "\n"


def findings_md(spec, reports: list[dict], findings: list[dict]) -> str:
    cnt = Counter((x["datasetId"], x["status"]) for x in findings)
    codes = Counter((x["datasetId"], x["defectType"]) for x in findings if x["status"] == "不通过")
    lines = ["# 问题清单（可读版）", "", "权威版本为 findings.jsonl；本文件只做汇总。", "",
             "| 数据集 | 通过 | 不通过 | 待判定 | 不适用 | 不通过按缺陷类型 |", "|---|---:|---:|---:|---:|---|"]
    for r in reports:
        d = r["dataset_id"]
        by = "；".join(f"{k} {spec.raw['defect_codes'][k]['zh']} {v}" for (dd, k), v in sorted(codes.items()) if dd == d) or "无"
        lines.append(f"| {d} | {cnt[(d, '通过')]} | {cnt[(d, '不通过')]} | {cnt[(d, '待判定')]} | {cnt[(d, '不适用')]} | {by} |")
    return "\n".join(lines) + "\n"


def write_manifest(out: Path, meta: dict) -> None:
    """最后生成：列出本次输出的每个文件、字节数与 SHA-256。"""
    files = []
    for p in sorted(out.rglob("*")):
        if p.is_file() and p.name != "00_MANIFEST.yaml":
            data = p.read_bytes()
            files.append({"path": p.relative_to(out).as_posix(), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    _write(out / "00_MANIFEST.yaml", yaml.safe_dump({
        "agent": "空间科学智能就绪数据集质量评价智能体", "agentVersion": meta["agent_version"], "ruleVersion": meta["rule_version"],
        "runId": meta["runId"], "datasets": meta["datasets"], "files": files}, allow_unicode=True, sort_keys=False))
