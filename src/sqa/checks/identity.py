"""疑似虚构标注（虚标）：事件身份标识与同批格式不符（M03）；同一标注内出现平移缩放得到的复制多边形（M04）。"""
from __future__ import annotations

import re
from collections import Counter

from ..model import Location, defect, merge, not_applicable, ok
from ..registry import check
from ._util import events, events_ptr, region_of, shapes

_NULLISH = {"", "none", "null", "nan"}


def shape(v) -> str | None:
    """取值的格式型：字母记 a、数字记 9；空值与字符串 None/null 记为 None。"""
    if v is None or (isinstance(v, str) and v.strip().lower() in _NULLISH):
        return None
    return re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", str(v)))


def _dominant(ctx, keys: tuple, tol: dict) -> dict[str, str] | None:
    """各身份字段的主流格式型；同批事件太少或没有主流格式时返回 None（不适用）。"""
    cache = ctx.cache.setdefault("identity_dominant", {})
    if keys in cache:
        return cache[keys]
    counts = {k: Counter() for k in keys}
    n = 0
    for rec in ctx.records:
        if ctx.profile(rec) is None or not ctx.profile_spec(rec).get("events"):
            continue
        for e in events(ctx, rec):
            n += 1
            for k in keys:
                counts[k][shape(e.get(k))] += 1
    dom = {}
    if n >= tol["min_elements"]:
        for k, c in counts.items():
            s, m = c.most_common(1)[0]
            if s is not None and m / n >= tol["dominant_share"]:
                dom[k] = s
    cache[keys] = dom or None
    return cache[keys]


@check("M03")
def identity_format(ctx, rec):
    keys = tuple(ctx.profile_spec(rec)["events"].get("identity_keys", ()))
    if not keys:
        return not_applicable(rec.key, "该标注族未声明事件身份字段")
    tol = ctx.spec.check("M03").tolerance
    dom = _dominant(ctx, keys, tol)
    if not dom:
        return not_applicable(rec.key, f"同批事件少于 {tol['min_elements']} 个或身份字段没有占比 ≥ {tol['dominant_share']} 的主流格式")
    outs = []
    for i, e in enumerate(events(ctx, rec)):
        bad = {k: e.get(k) for k, s in dom.items() if shape(e.get(k)) != s}
        if bad:
            gk = ctx.profile_spec(rec)["events"].get("polygon_key")
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i),
                                                 region=region_of(e.get(gk) or []) if gk else None),
                               "事件身份标识缺失或格式与同批不符（疑似虚构标注）",
                               identity=bad, dominant_format=dom))
    return merge(rec.key, outs) if outs else ok(rec.key)


def _normalized(pts) -> tuple | None:
    """顶点按自身外接框归一到 [0, 1]²；坐标非数值或外接框退化时返回 None。"""
    try:
        xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
    except (TypeError, ValueError, IndexError):
        return None
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return None
    return tuple(((x - min(xs)) / w, (y - min(ys)) / h) for x, y in zip(xs, ys))


@check("M04")
def copied_shapes(ctx, rec):
    """同一标注内两个多边形顶点数相同、按各自外接框归一后逐点重合，而位置或大小不同：后一个是平移缩放得到的复制品（虚标）。"""
    tol = ctx.spec.check("M04").tolerance
    path, lst = shapes(ctx, rec)
    polys = [(i, s["points"], _normalized(s["points"])) for i, s in enumerate(lst)
             if isinstance(s, dict) and isinstance(s.get("points"), list) and len(s["points"]) >= tol["min_vertices"]]
    polys = [p for p in polys if p[2] is not None]
    if not polys:
        return not_applicable(rec.key, f"没有顶点数 ≥ {tol['min_vertices']} 的多边形")
    outs = []
    for k, (j, pj, nj) in enumerate(polys):
        for i, pi, ni in polys[:k]:
            if len(ni) != len(nj) or pi == pj:
                continue
            dev = max(max(abs(a[0] - b[0]), abs(a[1] - b[1])) for a, b in zip(ni, nj))
            if dev <= tol["max_normalized_deviation"]:
                xs, ys = [float(p[0]) for p in pj], [float(p[1]) for p in pj]
                outs.append(defect(rec.key, Location(rec.key, pointer=f"{path}[{j}]", region=(min(xs), min(ys), max(xs), max(ys))),
                                   f"多边形 [{j}] 是多边形 [{i}] 平移缩放后的复制品（疑似虚构标注）",
                                   copied_from=f"{path}[{i}]", vertices=len(nj), normalized_deviation=round(dev, 5)))
                break
    return merge(rec.key, outs) if outs else ok(rec.key)
