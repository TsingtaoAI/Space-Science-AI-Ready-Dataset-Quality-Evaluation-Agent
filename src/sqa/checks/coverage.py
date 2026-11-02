"""覆盖与分布、使用条件。"""
from __future__ import annotations

import fnmatch

from ..model import Location, defect, not_applicable, ok, pending
from ..registry import check


def tvd(a, b) -> float | None:
    """两个类别直方图的总变差距离，取值 [0,1]。"""
    na, nb = sum(a.values()), sum(b.values())
    if not na or not nb:
        return None
    return 0.5 * sum(abs(a.get(k, 0) / na - b.get(k, 0) / nb) for k in set(a) | set(b))


@check("G01", scope="group")
def class_distribution(ctx):
    hist = ctx.cache["xv"]["hist"]
    d = tvd(hist["actual"], hist["reference"])
    unit = f"dataset:{ctx.dataset_id}"
    if d is None:
        return [not_applicable(unit, "参考或本版本没有带类别的标注元素")]
    ctx.cache["distribution_shift"] = d
    tol = ctx.spec.raw["coverage"]["max_tvd"]
    loc = Location(ctx.dataset_id, pointer="类别分布")
    ev = {"total_variation_distance": round(d, 6), "tolerance": tol,
          "reference": dict(sorted(hist["reference"].items())), "actual": dict(sorted(hist["actual"].items()))}
    return [defect(unit, loc, "类别分布与同源版本参考分布偏离", **ev) if d > tol else ok(unit)]


@check("U01", scope="dataset")
def usage_declaration(ctx):
    globs = ctx.spec.raw["usage"]["doc_globs"]
    found = sorted(p.relative_to(ctx.root).as_posix() for p in ctx.root.rglob("*")
                   if p.is_file() and any(fnmatch.fnmatch(p.name, g) for g in globs))
    unit = f"dataset:{ctx.dataset_id}"
    if found:
        ctx.cache["usage_docs"] = found
        return [ok(unit)]
    return [pending(unit, Location(ctx.dataset_id), "数据集中没有使用权限、许可或用途说明文件",
                    missing="使用权限信息与版本/用途说明（需求说明书第四节所列交付内容）")]
