"""按依赖顺序调度检查器，产出每个检查的四态结果；每个检查的开始、结束、重试、失败、降级都记入运行事件。"""
from __future__ import annotations

import resource
import sys
import time
from datetime import datetime, timezone

from .dataset import DatasetContext
from .model import CheckResult, Location, Outcome, Status, not_applicable, pending
from .registry import REGISTRY
from .spec import CheckSpec, execution_order


def _aggregate(outcomes: list[Outcome]) -> Status:
    statuses = {o.status for o in outcomes}
    if Status.FAIL in statuses:
        return Status.FAIL
    if Status.PENDING in statuses:
        return Status.PENDING
    if Status.PASS in statuses:
        return Status.PASS
    return Status.NOT_APPLICABLE


def _peak_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(rss * (1 if sys.platform == "darwin" else 1024) / 2**20, 1)


def _tokens(ctx: DatasetContext) -> int:
    client = ctx.cache.get("llm")
    return client.tokens if client is not None else 0


def log_event(ctx: DatasetContext, check_id: str, event: str, status: str, elapsed_ms: float | None = None,
              retry: int = 0, detail: str | None = None, tokens: int = 0) -> None:
    """运行事件（交付材料清单 §5.5）：时间戳为 UTC；资源峰值取当前进程的峰值常驻内存。"""
    e = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
         "datasetId": ctx.dataset_id, "event": event, "checkId": check_id, "status": status,
         "elapsedMs": None if elapsed_ms is None else round(elapsed_ms, 1),
         "resourcePeak": {"cpu": "单进程", "memoryMb": _peak_mb(), "accelerator": "未使用"},
         "tokenUsage": tokens, "retryCount": retry}
    if detail:
        e["detail"] = detail
    ctx.cache.setdefault("events", []).append(e)


def _safe(ctx: DatasetContext, c: CheckSpec, call, unit: str, single: bool = False):
    """执行检查器；异常时按 execution.retry_limit 重试，仍失败则降级为待判定并记入运行日志。"""
    limit = ctx.spec.raw["execution"]["retry_limit"]
    err = None
    for attempt in range(limit + 1):
        try:
            return call()
        except Exception as e:  # noqa: BLE001  检查器失败不得中断整次评估
            err = e
            retries = ctx.cache.setdefault("retries", {})
            if attempt < limit:
                retries[c.id] = retries.get(c.id, 0) + 1
                log_event(ctx, c.id, "重试", f"失败：{type(e).__name__}: {e}", retry=retries[c.id], detail=unit)
    log_event(ctx, c.id, "检查失败", f"失败：{type(err).__name__}: {err}", retry=ctx.cache.get("retries", {}).get(c.id, 0), detail=unit)
    log_event(ctx, c.id, "降级", "该单元落待判定", detail=unit)
    ctx.cache.setdefault("failures", []).append(
        {"check_id": c.id, "unit": unit, "attempts": limit + 1, "error": f"{type(err).__name__}: {err}"})
    o = pending(unit, Location(unit), f"检查器执行失败（已重试 {limit} 次）：{type(err).__name__}",
                missing="排查检查器错误后重跑")
    return o if single else [o]


def _check_level_na(ctx: DatasetContext, c: CheckSpec) -> str | None:
    """检查整体是否不适用；返回原因或 None。

    只在标注族都已识别、且没有一条记录属于适用族时判整体不适用；
    识别不出的记录会在逐条执行时落待判定。
    """
    if c.purposes is not None and ctx.purpose not in c.purposes:
        return f"用途 {ctx.purpose} 不要求此检查（适用用途: {', '.join(c.purposes)}）"
    if "any" in c.profiles or not ctx.records:
        return None
    profiles = set(ctx.profile_of.values())
    if None not in profiles and not profiles & set(c.profiles):
        zh = [ctx.spec.profiles[p]["zh"] for p in sorted(profiles) if p]
        return f"本数据集标注族（{'；'.join(zh)}）不适用此检查"
    return None


def _run_record_check(ctx: DatasetContext, c: CheckSpec, fn) -> list[Outcome]:
    if not ctx.records:
        return [pending(ctx.dataset_id, Location(ctx.dataset_id), "数据集中没有标签文件",
                        missing="标签文件")]
    outcomes = []
    for rec in ctx.records:
        loc = Location(rec.key)
        blocked = [d for d in c.depends_on if ctx.dep_status(d, rec.key) in (Status.FAIL, Status.PENDING)]
        if blocked:
            outcomes.append(pending(rec.key, loc, f"前置检查 {', '.join(blocked)} 未通过，本项无法判定",
                                    missing=f"先修复 {', '.join(blocked)} 指出的问题"))
            continue
        if "any" not in c.profiles:
            profile = ctx.profile(rec)
            if profile is None:
                outcomes.append(pending(rec.key, loc, "无法识别标注族，无法判断本检查是否适用",
                                        missing="可识别的事件/形状标注块"))
                continue
            if not c.applies_to(profile):
                outcomes.append(not_applicable(rec.key, f"标注族 {profile} 不适用"))
                continue
        outcomes.append(_safe(ctx, c, lambda: fn(ctx, rec), rec.key, single=True))
    return outcomes


def run_checks(ctx: DatasetContext) -> list[CheckResult]:
    results: list[CheckResult] = []
    for c in execution_order(ctx.spec):
        if c.implemented and REGISTRY[c.id].scope == "group":
            continue  # 跨数据集 / 跨样本的检查在 run_group_checks 中统一执行（届时标注族已识别）
        na_reason = _check_level_na(ctx, c)
        if na_reason:
            results.append(CheckResult(c.id, Status.NOT_APPLICABLE, reason=na_reason))
            log_event(ctx, c.id, "检查结束", "实际完成", 0.0, detail=f"不适用：{na_reason}")
            continue
        if not c.implemented:
            # 未实现的适用检查不得从分母删除：落「待判定」并写明缺什么
            results.append(CheckResult(c.id, Status.PENDING, reason="检查器尚未实现",
                                       missing_materials=c.missing_materials))
            log_event(ctx, c.id, "检查结束", "失败：检查器尚未实现（保留在分母中，结论落待判定）", 0.0)
            continue
        checker = REGISTRY[c.id]
        t0, tok0 = time.monotonic(), _tokens(ctx)
        log_event(ctx, c.id, "检查开始", "进行中")
        if checker.scope == "record":
            outcomes = _run_record_check(ctx, c, checker.fn)
        else:
            outcomes = _safe(ctx, c, lambda: checker.fn(ctx), ctx.dataset_id)
        ctx.outcomes[c.id] = {o.unit: o for o in outcomes}
        status = _aggregate(outcomes)
        reason = None
        if status == Status.NOT_APPLICABLE:
            reasons = sorted({o.reason for o in outcomes if o.reason})
            reason = "；".join(reasons) or "所有记录均不适用"
        results.append(CheckResult(c.id, status, sorted(outcomes, key=lambda o: o.unit), reason=reason))
        log_event(ctx, c.id, "检查结束", "实际完成", (time.monotonic() - t0) * 1000,
                  retry=ctx.cache.get("retries", {}).get(c.id, 0), detail=f"结论：{status.value}，单元 {len(outcomes)}",
                  tokens=_tokens(ctx) - tok0)
    order = {c.id: i for i, c in enumerate(ctx.spec.checks)}
    return sorted(results, key=lambda r: order[r.check_id])


def _downgrade_common(group: list[DatasetContext], results: dict[str, list[CheckResult]], min_agree: int) -> int:
    """同一样本在所有同源版本中都被同一检查判为不通过：属基础数据特性而非版本缺陷，降级为待判定。"""
    from collections import Counter

    from .model import Finding
    from .variants import sample_keys, unit_sample

    have = Counter(k for c in group for k in sample_keys(c))
    n = len(group)
    changed = 0
    check_ids = {r.check_id for c in group for r in results[c.dataset_id] if REGISTRY.get(r.check_id)
                 and REGISTRY[r.check_id].scope != "group" and r.status == Status.FAIL}
    for cid in sorted(check_ids):
        fails = Counter()
        for c in group:
            fails.update({unit_sample(u) for u, o in c.outcomes.get(cid, {}).items() if o.status == Status.FAIL})
        common = {k for k, m in fails.items() if m == have[k] == n and n >= min_agree}
        if not common:
            continue
        for c in group:
            res = next((r for r in results[c.dataset_id] if r.check_id == cid), None)
            if res is None:
                continue
            new = []
            for o in res.outcomes:
                if o.status == Status.FAIL and unit_sample(o.unit) in common:
                    fs = [Finding("pending", f.location, f"所有同源版本均存在：{f.message}", f.evidence,
                                  "确认是否属于数据约定；属缺陷时需在基础数据层面整改") for f in o.findings]
                    o = Outcome(o.unit, Status.PENDING, fs, o.reason)
                    changed += 1
                new.append(o)
            res.outcomes = new
            res.status = _aggregate(new)
            c.outcomes[cid] = {o.unit: o for o in new}
    return changed


def _downgrade_in_reference(group: list[DatasetContext], results: dict[str, list[CheckResult]], ref_id: str) -> int:
    """参考版本（组内质量序第一）上同一样本、同一位置、同一判据也不通过的结论：属来源固有问题而非本版本缺陷，
    降级为待判定。参考版本自身的这类结论同样降级（它是组内比较的基准）。"""
    from .model import Finding
    from .variants import unit_sample

    ref = next(c for c in group if c.dataset_id == ref_id)
    changed = 0
    for cid in sorted({r.check_id for c in group for r in results[c.dataset_id]
                       if REGISTRY.get(r.check_id) and REGISTRY[r.check_id].scope != "group"}):
        sig = {(unit_sample(u), f.location.pointer, f.message)
               for u, o in ref.outcomes.get(cid, {}).items() if o.status == Status.FAIL
               for f in o.findings if f.kind == "defect"}
        if not sig:
            continue
        for c in group:
            res = next((r for r in results[c.dataset_id] if r.check_id == cid), None)
            if res is None or res.status != Status.FAIL:
                continue
            new = []
            for o in res.outcomes:
                if o.status == Status.FAIL:
                    fs = []
                    for f in o.findings:
                        if f.kind == "defect" and (unit_sample(o.unit), f.location.pointer, f.message) in sig:
                            f = Finding("pending", f.location, f"参考版本同样存在（来源固有）：{f.message}", f.evidence,
                                        "确认是否属于源数据固有问题；属缺陷时需在基础数据层面整改")
                            changed += 1
                        fs.append(f)
                    if not any(f.kind == "defect" for f in fs):
                        o = Outcome(o.unit, Status.PENDING, fs, o.reason)
                    else:
                        o = Outcome(o.unit, Status.FAIL, fs, o.reason)
                new.append(o)
            res.outcomes = new
            res.status = _aggregate(new)
            c.outcomes[cid] = {o.unit: o for o in new}
    return changed


def apply_policies(ctx: DatasetContext, results: list[CheckResult]) -> int:
    """输出前的行为规则：不可重推项弃报（规格 non_reproducible）。返回弃报条数。"""
    from .codes import abstain_non_reproducible

    from .model import Finding

    total = 0
    for r in results:
        if r.status != Status.FAIL:
            continue
        # R02：证据位置无法确定的缺陷结论不得输出为不通过
        fixed = []
        for o in r.outcomes:
            if o.status == Status.FAIL and any(f.kind == "defect" and not f.location.file for f in o.findings):
                fs = [Finding("pending", f.location, f"证据位置无法确定，不输出不通过：{f.message}", f.evidence,
                              "补充可回查的文件或记录位置") if f.kind == "defect" and not f.location.file else f
                      for f in o.findings]
                o = Outcome(o.unit, Status.FAIL if any(f.kind == "defect" for f in fs) else Status.PENDING, fs, o.reason)
                log_event(ctx, r.check_id, "降级", "实际完成", detail="证据位置无法确定的结论改落待判定")
            fixed.append(o)
        r.outcomes = fixed
        r.status = _aggregate(fixed)
        if r.status != Status.FAIL:
            ctx.outcomes[r.check_id] = {o.unit: o for o in fixed}
            continue
        new, n = abstain_non_reproducible(ctx.spec, r.check_id, ctx.dominant_profile, r.outcomes)
        if n:
            log_event(ctx, r.check_id, "降级", "实际完成", detail=f"不可重推项弃报 {n} 条：不输出不通过，改落待判定")
            r.outcomes = new
            r.status = _aggregate(new)
            ctx.outcomes[r.check_id] = {o.unit: o for o in new}
            total += n
    ctx.cache["abstained"] = total
    return total


def _reference_inputs(group: list[DatasetContext], spec) -> tuple[dict, dict, dict]:
    """质量序（独立检查不通过单元数，越少越靠前）与各样本上未通过独立检查的版本。"""
    from collections import defaultdict

    from .variants import unit_sample

    ref_checks = spec.raw["variants"]["reference_checks"]
    data_checks = {"N02", "N03"}
    rank, inv_label, inv_data = {}, defaultdict(set), defaultdict(set)
    for c in group:
        total = 0
        for cid in ref_checks:
            for u, o in c.outcomes.get(cid, {}).items():
                if o.status == Status.FAIL:
                    total += 1
                    (inv_data if cid in data_checks else inv_label)[unit_sample(u)].add(c.dataset_id)
        rank[c.dataset_id] = total
    return rank, inv_label, inv_data


def run_group_checks(ctxs: list[DatasetContext], results: dict[str, list[CheckResult]]) -> list[dict]:
    """质量变体对照：分组 → 降级共有问题 → 排质量序 → 组内逐样本比较 → 按检查编号写回结果。返回分组摘要。"""
    from .variants import compare_group, group_variants

    if not ctxs:
        return []
    spec = ctxs[0].spec
    cfg = spec.raw["variants"]
    group_checks = [c for c in execution_order(spec) if c.implemented and REGISTRY[c.id].scope == "group"]
    summary = []
    for group in group_variants(ctxs, cfg["min_jaccard"]):
        compare_ms = 0.0
        if len(group) < 2:
            gr = None
        else:
            downgraded = _downgrade_common(group, results, cfg["min_agree"]) if cfg.get("downgrade_common_findings") else 0
            rank, inv_label, inv_data = _reference_inputs(group, spec)
            t0 = time.monotonic()
            gr = compare_group(group, cfg["min_agree"], rank, inv_label, inv_data)
            compare_ms = (time.monotonic() - t0) * 1000
            order = sorted(rank, key=lambda d: (rank[d], d))
            in_ref = (_downgrade_in_reference(group, results, order[0])
                      if cfg.get("downgrade_reference_findings") and len(group) >= cfg["min_agree"] else 0)
            summary.append({"members": gr.members, "samples": gr.samples, "confirmed_samples": gr.confirmed_samples,
                            "quality_order": [{"dataset_id": d, "independent_defect_units": rank[d]} for d in order],
                            "downgraded_common_findings": downgraded, "downgraded_reference_findings": in_ref})
        for ctx in group:
            if gr is not None:
                ctx.cache["xv"] = {"diffs": [d for d in gr.diffs if d.dataset_id == ctx.dataset_id],
                                   "compared": gr.compared[ctx.dataset_id], "totals": gr.totals[ctx.dataset_id],
                                   "hist": gr.hist[ctx.dataset_id], "group": gr.members}
            for c in group_checks:
                t0 = time.monotonic()
                standalone = c.run_when == "no_variant_group"
                if standalone and gr is not None:
                    res = CheckResult(c.id, Status.NOT_APPLICABLE,
                                      reason="有同源质量版本，由版本对照（X 类检查）覆盖")
                    log_event(ctx, c.id, "检查结束", "实际完成", 0.0, detail=f"不适用：{res.reason}")
                elif gr is None and not standalone:
                    res = CheckResult(c.id, Status.NOT_APPLICABLE,
                                      reason="未找到同源质量版本（同族且样本名重叠），无法做版本对照")
                    log_event(ctx, c.id, "检查结束", "实际完成", 0.0, detail=f"不适用：{res.reason}")
                elif standalone and (na := _check_level_na(ctx, c)):
                    res = CheckResult(c.id, Status.NOT_APPLICABLE, reason=na)
                    log_event(ctx, c.id, "检查结束", "实际完成", 0.0, detail=f"不适用：{na}")
                else:
                    log_event(ctx, c.id, "检查开始", "进行中", detail=f"组内比较耗时 {compare_ms:.0f} ms（组内共用）")
                    outcomes = _safe(ctx, c, lambda: REGISTRY[c.id].fn(ctx), ctx.dataset_id)
                    ctx.outcomes[c.id] = {o.unit: o for o in outcomes}
                    status = _aggregate(outcomes)
                    reason = None
                    if not standalone and len(group) < cfg["min_agree"] and status != Status.FAIL:
                        reason = f"同源版本仅 {len(group)} 个，少于确认所需的 {cfg['min_agree']} 个，差异只能落待判定"
                    res = CheckResult(c.id, status, sorted(outcomes, key=lambda o: o.unit), reason=reason)
                    log_event(ctx, c.id, "检查结束", "实际完成", (time.monotonic() - t0) * 1000,
                              retry=ctx.cache.get("retries", {}).get(c.id, 0), detail=f"结论：{status.value}，单元 {len(outcomes)}")
                results[ctx.dataset_id].append(res)
            order = {c.id: i for i, c in enumerate(spec.checks)}
            results[ctx.dataset_id].sort(key=lambda r: order[r.check_id])

    return summary
