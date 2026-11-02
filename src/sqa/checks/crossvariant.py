"""质量变体对照检查（X01 ~ X07）。比较由 variants.compare_group 一次完成，这里按检查编号取结果。"""
from __future__ import annotations

from collections import defaultdict

from ..model import Finding, Location, Outcome, Status, merge, ok
from ..registry import check


def _covered(ctx, unit: str) -> set[str]:
    """该记录已被单数据集检查判为缺陷的路径，避免同一缺陷以不同类别重复报告。"""
    paths = set()
    for per_unit in ctx.outcomes.values():
        o = per_unit.get(unit)
        if not o or o.status != Status.FAIL:
            continue
        for f in o.findings:
            if f.location.pointer:
                paths.add(f.location.pointer)
            for k in ("missing",):
                for p in f.evidence.get(k, []) if isinstance(f.evidence.get(k), list) else []:
                    paths.add(p)
            for item in f.evidence.get("fields", []) if isinstance(f.evidence.get("fields"), list) else []:
                if isinstance(item, dict) and "field" in item:
                    paths.add(item["field"])
    return paths


def _is_covered(pointer: str, covered: set[str]) -> bool:
    base = pointer.split("（")[0]
    return any(base == p or base in p.split("/") or p.startswith(base + ".") or base.startswith(p + "[")
               or base.startswith(p + ".") for p in covered)


def outcomes_for(ctx, check_id: str) -> list[Outcome]:
    xv = ctx.cache["xv"]
    diffs = [d for d in xv["diffs"] if d.check_id == check_id]
    by_unit = defaultdict(list)
    for d in diffs:
        by_unit[d.unit].append(d)
    out = []
    for unit in sorted(xv["compared"].get(check_id, set()) | set(by_unit)):
        ds = by_unit.get(unit, [])
        if not ds:
            out.append(ok(unit))
            continue
        covered = _covered(ctx, unit) if check_id in ("X03", "X07") else set()
        parts = []
        for d in ds:
            loc = d.location
            if check_id in ("X03", "X07") and loc.get("pointer") and _is_covered(loc["pointer"], covered):
                continue
            f = Finding("defect" if d.status == "fail" else "pending",
                        Location(loc["file"], pointer=loc.get("pointer"),
                                 interval=tuple(loc["interval"]) if "interval" in loc else None,
                                 region=tuple(loc["region"]) if "region" in loc else None),
                        d.message, d.evidence, d.missing)
            parts.append(Outcome(unit, Status.FAIL if d.status == "fail" else Status.PENDING, [f]))
        out.append(merge(unit, parts) if parts else ok(unit))
    return out


for _cid in ("X01", "X02", "X03", "X04", "X05", "X06", "X07", "X08"):
    check(_cid, scope="group")(lambda ctx, _c=_cid: outcomes_for(ctx, _c))
