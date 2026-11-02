"""判定记录：覆盖率、缺陷清单、三档结论、质量分与排序。

报告只含由输入与规格决定的内容（无时间戳、无耗时），保证同一规则版本下重跑字节一致。
运行耗时等非确定信息另写 run_log.json。
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath
from typing import Any

from .dataset import DatasetContext
from .model import STATUS_ZH, CheckResult, Status

REPORT_SCHEMA = "sqa-report/1"


class InvariantError(Exception):
    pass


def coverage(results: list[CheckResult]) -> dict[str, Any]:
    applicable = [r for r in results if r.status != Status.NOT_APPLICABLE]
    completed = [r for r in applicable if r.status in (Status.PASS, Status.FAIL, Status.PENDING)]
    defects = [r for r in completed if r.status == Status.FAIL]
    m, n, k = len(applicable), len(completed), len(defects)
    if not (k <= n <= m):
        raise InvariantError(f"覆盖率不变量被破坏: K={k} N={n} M={m}")
    return {
        "applicable": m,
        "completed": n,
        "defects_found": k,
        "pending": sum(r.status == Status.PENDING for r in applicable),
        "completion_rate": round(n / m, 6) if m else None,
        "statement": f"已检 {n} 项 / 适用 {m} 项 / 检出 {k} 项",
    }


def verdict(ctx: DatasetContext, results: list[CheckResult]) -> dict[str, Any]:
    sev = {c.id: c.severity for c in ctx.spec.checks}
    crit_fail = [r.check_id for r in results if r.status == Status.FAIL and sev[r.check_id] == "critical"]
    crit_pend = [r.check_id for r in results if r.status == Status.PENDING and sev[r.check_id] == "critical"]
    major_fail = [r.check_id for r in results if r.status == Status.FAIL and sev[r.check_id] == "major"]
    by_id = {v["id"]: v for v in ctx.spec.raw["verdicts"]}
    if crit_fail:
        vid, basis = "reject", crit_fail
    elif crit_pend or major_fail:
        vid, basis = "manual_review", crit_pend + major_fail
    else:
        vid, basis = "accept_with_marks", []
    v = by_id[vid]
    return {"id": vid, "zh": v["zh"], "action": v["action"], "basis_checks": basis}


def _unit_group(ctx: DatasetContext) -> dict[str, str]:
    """单元 → 标注族，用于分组统计缺陷比例（不同族的分母不混算）。"""
    m = {}
    for rec in ctx.records:
        prof = ctx.profile(rec) or "unknown"
        m[rec.key] = prof
        rel = ctx.data_file_of(rec)
        if rel:
            m.setdefault(rel, prof)
    return m


def _sample_of(unit: str) -> str:
    """把标签单元、数据文件单元、样本单元统一到样本名。"""
    from .variants import sample_key
    name = PurePosixPath(unit.split(":", 1)[-1]).name
    return sample_key(name[:-5] if name.endswith(".json") else name)


def _element_count(ctx: DatasetContext) -> tuple[int, int]:
    """(标注元素数, 其中带几何或时间的元素数)，无同源版本时作为比例分母。"""
    from .variants import elements
    total = geo = 0
    for rec in ctx.records:
        doc = ctx.label(rec)
        if isinstance(doc, dict) and ctx.profile(rec):
            _, els = elements(doc, ctx.profile_spec(rec))
            if els:
                total += len(els)
                geo += sum(1 for e in els if e.points or e.start is not None)
    return total, geo


def measures(ctx: DatasetContext, results: list[CheckResult]) -> dict[str, Any]:
    """缺陷程度：按规格 measures 逐项计算比例，分子分母与来源检查都写出，可由检查结果复算。"""
    by_id = {r.check_id: r for r in results}
    xv = ctx.cache.get("xv")
    totals = xv["totals"] if xv else {}
    n_labels = len(ctx.records)
    n_data = len(ctx.data_files)
    n_el, n_geo = _element_count(ctx)
    ref_el = totals.get("elements_reference", n_el) if xv else n_el
    act_el = totals.get("elements_actual", n_el) if xv else n_el
    ref_geo = totals.get("geometries", n_geo) if xv else n_geo

    def fail_units(cids):
        out = set()
        for cid in cids:
            r = by_id.get(cid)
            if r and r.status != Status.NOT_APPLICABLE:
                out |= {(cid, u) for u in r.units(Status.FAIL)}
        return out

    def findings(cids):
        out = []
        for cid in cids:
            r = by_id.get(cid)
            if r and r.status != Status.NOT_APPLICABLE:
                out += [(cid, f) for o in r.outcomes if o.status == Status.FAIL for f in o.findings if f.kind == "defect"]
        return out

    def applicable(cids):
        return any(by_id.get(c) and by_id[c].status != Status.NOT_APPLICABLE for c in cids)

    out = {}
    for key, m in ctx.spec.raw["measures"].items():
        cids = m["checks"]
        if key in ("missing_ratio",):
            num = len({_sample_of(u) for _, u in fail_units(cids)})
            den = totals.get("samples_reference", n_labels) if xv else n_labels
        elif key in ("corrupt_ratio", "duplicate_ratio", "structure_ratio", "doc_ratio"):
            num = len({_sample_of(u) for _, u in fail_units(cids)})
            den = n_data if key == "corrupt_ratio" else n_labels
        elif key == "mislabel_ratio":
            num, den = len(findings(cids)), ref_el
        elif key in ("omission_ratio", "perturbation_ratio"):
            num, den = len(findings(cids)), ref_el
        elif key == "spurious_ratio":
            num, den = len(findings(cids)), act_el
        elif key == "offset_ratio":
            num, den = len(findings(cids)), ref_geo
        elif key == "distribution_shift":
            d = ctx.cache.get("distribution_shift")
            num, den = (round(d, 6), 1) if d is not None else (0, 0)
        elif key == "usage_info":
            r = by_id.get("U01")
            st = r.status if r else None
            num, den = (1, 1) if st == Status.FAIL else ((0, 1) if st == Status.PASS else (0, 0))
        else:
            raise ValueError(f"未实现的程度指标: {key}")
        abstained = sum(1 for cid in cids if by_id.get(cid) for o in by_id[cid].outcomes for f in o.findings
                        if f.kind == "pending" and f.evidence.get("abstained"))
        computable = applicable(cids) and den > 0 and not (abstained and num == 0)
        entry = {"zh": m["zh"], "numerator": num if computable else None, "denominator": den if computable else None,
                 "ratio": round(min(1.0, num / den), 6) if computable else None, "source_checks": cids,
                 "definition": f"{m['numerator']} / {m['denominator']}"}
        if not computable:
            entry["not_computable_reason"] = ("相关检查均不适用" if not applicable(cids)
                                              else (f"不可重推项已弃报（{abstained} 条落待判定），不按 0 计" if abstained and num == 0
                                                    else ("证据不足（相关检查待判定）" if key == "usage_info" else "分母为 0")))
        if key == "offset_ratio" and computable:
            sev = [f.evidence["severity"]["score"] for _, f in findings(["X06"]) if "severity" in f.evidence]
            entry["mean_severity"] = round(sum(sev) / len(sev), 6) if sev else None
            px = sorted(f.evidence["offset_px"] for _, f in findings(["X06"]) if "offset_px" in f.evidence)
            ts = sorted(abs(f.evidence["time_offset_s"]) for _, f in findings(["X06"]) if "time_offset_s" in f.evidence)
            if px:
                entry["median_offset_px"] = px[len(px) // 2]
            if ts:
                entry["median_time_offset_s"] = ts[len(ts) // 2]
        out[key] = entry
    return out


def _grade(spec, score, key_ratio) -> dict[str, Any]:
    prov = bool(spec.raw.get("grades_provisional"))
    extra = {"provisional": True, "note": "暂定：未获取档位标签文件，阈值未经标定"} if prov else {}
    for g in spec.raw["grades"]:
        if score is not None and score >= g["min_score"] and key_ratio <= g.get("max_key_defect_ratio", 1.0):
            return {"id": g["id"], "zh": g["zh"], **extra}
    return {"id": "unknown", "zh": "无法评级", **extra}


def quality(ctx: DatasetContext, results: list[CheckResult]) -> dict[str, Any]:
    """程度指标 → 维度分 → 综合分 → 等级（规格 dimensions / scoring / grades）。"""
    spec = ctx.spec
    ms = measures(ctx, results)
    terms = []
    for key, m in ms.items():
        if m["ratio"] is None:
            continue
        f = 1.0
        if key == "offset_ratio" and m.get("mean_severity") is not None:
            f = 0.5 + 0.5 * m["mean_severity"]
        w = spec.raw["measures"][key]["weight"]
        terms.append({"measure": key, "weight": w, "ratio": m["ratio"], "severity_factor": round(f, 6),
                      "contribution": round(w * m["ratio"] * f, 8)})
    tmap = {t["measure"]: t for t in terms}

    dims = {}
    for d, cfg in spec.raw["dimensions"].items():
        dt = [tmap[k] for k in cfg["measures"] if k in tmap]
        den = sum(t["weight"] for t in dt)
        score = round(100 * (1 - sum(t["contribution"] for t in dt) / den), 6) if den else None
        dims[d] = {"zh": cfg["zh"], "weight": cfg["weight"], "score": score, "measures": cfg["measures"],
                   "status": "已评分" if score is not None else "证据不足",
                   "insufficient_measures": [k for k in cfg["measures"] if k not in tmap]}
    scored = [v for v in dims.values() if v["score"] is not None]
    wsum = sum(v["weight"] for v in scored)
    composite = round(sum(v["weight"] * v["score"] for v in scored) / wsum, 6) if wsum else None

    sev = {c.id: c.severity for c in spec.checks}
    key_defects = []
    key_samples = set()
    for r in results:
        if sev[r.check_id] == "critical" and r.status == Status.FAIL:
            units = r.units(Status.FAIL)
            key_samples |= {_sample_of(u) for u in units}
            key_defects.append({"check_id": r.check_id, "name": spec.check(r.check_id).name, "units": len(units)})
    n_samples = max(len(ctx.records), len({_sample_of(d) for d in ctx.data_files}), 1)
    key_ratio = round(len(key_samples) / n_samples, 6)

    # 分类别统计（只作参考，不进入得分）
    cat_of = {c.id: c.category for c in spec.checks}
    unit_grp = _unit_group(ctx)
    checked, defective = defaultdict(set), defaultdict(set)
    for r in results:
        if r.status == Status.NOT_APPLICABLE:
            continue
        for o in r.outcomes:
            k = (cat_of[r.check_id], unit_grp.get(o.unit, "unknown"))
            if o.status in (Status.PASS, Status.FAIL):
                checked[k].add(o.unit)
            if o.status == Status.FAIL:
                defective[k].add(o.unit)
    rows = [{"category": c, "category_zh": spec.categories[c], "profile": g, "checked_units": len(checked[(c, g)]),
             "defect_units": len(defective[(c, g)]),
             "defect_ratio": round(len(defective[(c, g)]) / len(checked[(c, g)]), 6) if checked[(c, g)] else None}
            for c, g in sorted(checked)]
    return {"score": composite,
            "formula": "维度分 = 100 × (1 − Σ w·r·f / Σ w)；综合分 = Σ W·维度分 / Σ W（证据不足的维度不计入）",
            "grade": _grade(spec, composite, key_ratio),
            "dimensions": dims, "terms": terms,
            "key_defects": {"ratio": key_ratio, "affected_samples": len(key_samples), "samples": n_samples,
                            "checks": key_defects,
                            "rule": "关键缺陷单独列示；关键缺陷比例超过等级上限时不得评为该等级，不能被其他维度高分抵消"},
            "critical_defect_units": sum(k["units"] for k in key_defects),
            "excluded_measures": [k for k, m in ms.items() if m["ratio"] is None],
            "measures": ms, "by_category": rows}


def scorecard(ctx: DatasetContext, results: list[CheckResult], q: dict[str, Any], cov: dict[str, Any]) -> dict[str, Any]:
    """评分卡：各维度得分、检查覆盖、主要缺陷及影响、整改建议、未完成事项。"""
    spec = ctx.spec
    issues = []
    for r in results:
        if r.status != Status.FAIL:
            continue
        c = spec.check(r.check_id)
        n = len(r.units(Status.FAIL))
        issues.append({"check_id": c.id, "name": c.name, "dimension": spec.raw["dimensions"][c.dimension]["zh"],
                       "severity": c.severity, "affected_units": n, "remediation": c.remediation})
    rank = {"critical": 0, "major": 1, "minor": 2}
    issues.sort(key=lambda x: (rank[x["severity"]], -x["affected_units"], x["check_id"]))
    review = [{"check_id": r.check_id, "name": spec.check(r.check_id).name, "units": len(r.units(Status.PENDING)),
               "missing_materials": r.missing_materials or spec.check(r.check_id).missing_materials,
               "reason": r.reason}
              for r in results if r.status == Status.PENDING]
    return {
        "dataset_id": ctx.dataset_id,
        "dataset_version": ctx.cache.get("inventory_sha256"),
        "purpose": ctx.purpose, "rule_version": spec.rule_version,
        "grade": q["grade"], "composite_score": q["score"],
        "dimension_scores": {v["zh"]: v["score"] if v["score"] is not None else "证据不足" for v in q["dimensions"].values()},
        "check_coverage": cov["statement"], "completion_rate": cov["completion_rate"],
        "key_defects": q["key_defects"],
        "main_issues": issues[:10],
        "review_and_supplement": review,
    }


def _finding_dict(ctx: DatasetContext, check_id: str, f) -> dict[str, Any]:
    from .codes import finding_code, track
    code = finding_code(ctx.spec, check_id, ctx.dominant_profile, f)
    d = {"check_id": check_id, "rule_version": ctx.spec.rule_version,
         "kind": f.kind, "location": f.location.to_dict(), "message": f.message,
         "defect_code": code, "defect_code_zh": ctx.spec.raw["defect_codes"][code]["zh"],
         "defect_track": track(ctx.spec, code, ctx.dominant_profile)}
    if f.evidence:
        d["evidence"] = f.evidence
    if f.missing_materials:
        d["missing_materials"] = f.missing_materials
    return d


def _type_counts(spec, defects: list, pending: list) -> list[dict[str, Any]]:
    from collections import Counter
    d = Counter(f["defect_type"] for f in defects)
    p = Counter(f.get("defect_type") for f in pending)
    return [{"defect_type": t, "zh": zh, "confirmed": d.get(t, 0), "pending": p.get(t, 0)}
            for t, zh in spec.raw["defect_types"].items()]


def build_report(ctx: DatasetContext, results: list[CheckResult]) -> dict[str, Any]:
    spec = ctx.spec
    checks = []
    defect_list = []
    review_queue = []
    sev_rank = {"critical": 0, "major": 1, "minor": 2}
    for r in results:
        c = spec.check(r.check_id)
        findings = [_finding_dict(ctx, r.check_id, f) for o in r.outcomes for f in o.findings]
        entry = {
            "check_id": c.id, "name": c.name, "rule_version": spec.rule_version,
            "category": c.category, "category_zh": spec.categories[c.category],
            "evidence_type": c.evidence_type, "severity": c.severity,
            "status": r.status.value, "status_zh": STATUS_ZH[r.status],
            "units": {s.value: len(r.units(s)) for s in Status},
            "findings": findings,
        }
        if r.reason:
            entry["reason"] = r.reason
        if r.missing_materials:
            entry["missing_materials"] = r.missing_materials
        checks.append(entry)
        for f in findings:
            item = {**f, "category": c.category, "severity": c.severity, "defect_type": c.defect_type,
                    "defect_type_zh": spec.raw["defect_types"][c.defect_type]}
            if isinstance(f.get("evidence"), dict) and "severity" in f["evidence"]:
                item["degree"] = f["evidence"]["severity"]
            (defect_list if f["kind"] == "defect" else review_queue).append(item)
        if r.status == Status.PENDING and not findings:
            review_queue.append({"check_id": c.id, "rule_version": spec.rule_version, "kind": "pending",
                                 "location": {"file": ctx.dataset_id}, "message": r.reason,
                                 "missing_materials": r.missing_materials,
                                 "category": c.category, "severity": c.severity, "defect_type": c.defect_type,
                                 "defect_type_zh": spec.raw["defect_types"][c.defect_type]})

    order = lambda f: (sev_rank[f["severity"]], f["check_id"], f["location"].get("file", ""), f["location"].get("pointer") or "")
    cov = coverage(results)
    q = quality(ctx, results)
    return {
        "schema": REPORT_SCHEMA,
        "dataset_id": ctx.dataset_id,
        "profile": ctx.dominant_profile,
        "profile_zh": spec.profiles[ctx.dominant_profile]["zh"] if ctx.dominant_profile else None,
        "purpose": ctx.purpose,
        "rule_version": spec.rule_version,
        "spec_sha256": spec.sha256,
        "records": len(ctx.records),
        "data_files": len(ctx.data_files),
        "variant_group": ctx.cache.get("xv", {}).get("group"),
        "verdict": verdict(ctx, results),
        "coverage": cov,
        "quality": q,
        "scorecard": scorecard(ctx, results, q, cov),
        "execution_failures": ctx.cache.get("failures", []),
        "defect_type_counts": _type_counts(spec, defect_list, review_queue),
        "defect_list": sorted(defect_list, key=order),
        "review_queue": sorted(review_queue, key=order),
        "checks": checks,
    }


def _rank_key(r: dict[str, Any]):
    q = r["quality"]
    score = q["score"] if q["score"] is not None else -1.0
    worst = max([t["ratio"] for t in q["terms"]] or [0.0])
    return (-score, q["critical_defect_units"], worst, r["dataset_id"])


def _main_factors(r: dict[str, Any], top: int = 3) -> list[dict[str, Any]]:
    terms = sorted(r["quality"]["terms"], key=lambda t: (-t["contribution"], t["measure"]))
    return [{"measure": t["measure"], "zh": r["quality"]["measures"][t["measure"]]["zh"], "ratio": t["ratio"],
             "contribution": t["contribution"]} for t in terms[:top] if t["contribution"] > 0]


def _difference(a: dict[str, Any], b: dict[str, Any]) -> str:
    """a 排在 b 前面的主要原因：贡献差最大的指标。"""
    ta = {t["measure"]: t for t in a["quality"]["terms"]}
    tb = {t["measure"]: t for t in b["quality"]["terms"]}
    diffs = sorted(((tb.get(k, {}).get("contribution", 0) - ta.get(k, {}).get("contribution", 0), k)
                    for k in set(ta) | set(tb)), reverse=True)
    if not diffs or diffs[0][0] <= 0:
        sa, sb = a["quality"]["score"], b["quality"]["score"]
        if sa == sb:
            return "质量分相同，按关键缺陷单元数、最大单项比例、数据集标识依次区分"
        return "质量分更高"
    k = diffs[0][1]
    zh = (a["quality"]["measures"].get(k) or b["quality"]["measures"][k])["zh"]
    ra = ta.get(k, {}).get("ratio", 0)
    rb = tb.get(k, {}).get("ratio", 0)
    return f"{zh}更低（{ra:.4f} vs {rb:.4f}）"


def ranking(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """全局排序（评分准则 §2.5：全部受评数据集之间，不输出并列，说明主要差异）+ 组内排序。"""
    ordered = sorted(reports, key=_rank_key)
    global_rank = []
    for i, r in enumerate(ordered, 1):
        nxt = ordered[i] if i < len(ordered) else None
        global_rank.append({
            "rank": i, "dataset_id": r["dataset_id"], "profile": r["profile"], "variant_group": r.get("variant_group"),
            "quality_score": r["quality"]["score"], "grade": r["quality"]["grade"]["zh"],
            "verdict": r["verdict"]["id"], "verdict_zh": r["verdict"]["zh"],
            "critical_defect_units": r["quality"]["critical_defect_units"],
            "main_factors": _main_factors(r),
            "ahead_of_next_because": _difference(r, nxt) if nxt else None,
            "difference_significant": (None if nxt is None or r["quality"]["score"] is None or nxt["quality"]["score"] is None
                                       else r["quality"]["score"] - nxt["quality"]["score"] >= 0.5),
            "coverage": r["coverage"]["statement"]})
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in reports:
        gid = "+".join(r["variant_group"]) if r.get("variant_group") else (r["profile"] or "unknown")
        groups[gid].append(r)
    group_rank = []
    for gid in sorted(groups):
        members = sorted(groups[gid], key=_rank_key)
        for i, r in enumerate(members, 1):
            group_rank.append({"group": gid, "rank_in_group": i, "group_size": len(members),
                               "dataset_id": r["dataset_id"], "quality_score": r["quality"]["score"],
                               "verdict": r["verdict"]["id"], "main_factors": _main_factors(r)})
    return {"global": global_rank, "within_group": group_rank,
            "rule": "综合分降序；同分依次比较关键缺陷单元数（少者优）、最大单项比例（低者优）、数据集标识"}
