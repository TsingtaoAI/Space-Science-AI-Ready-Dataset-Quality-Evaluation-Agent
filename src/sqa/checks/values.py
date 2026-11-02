"""标注数值合法性：时间、几何、物理量范围。"""
from __future__ import annotations

import math

from ..model import Location, defect, merge, ok
from ..registry import check
from ._util import as_number, events, events_ptr, get_path, is_missing, parse_time, polygon_area, region_of, shapes

FILE_TIMES = ("basic_annotation.startTime", "basic_annotation.endTime")


@check("V01")
def times(ctx, rec):
    doc = ctx.label(rec)
    outs = []
    parsed = {}
    for ptr in FILE_TIMES:
        v = get_path(doc, ptr)
        if is_missing(v):
            continue  # 是否必填由 S03/S04 判断
        t = parse_time(v)
        if t is None:
            outs.append(defect(rec.key, Location(rec.key, pointer=ptr), "时刻无法解析", actual=v))
        parsed[ptr] = t
    s, e = parsed.get(FILE_TIMES[0]), parsed.get(FILE_TIMES[1])
    if s and e and s > e:
        outs.append(defect(rec.key, Location(rec.key, pointer="basic_annotation"), "起始时刻晚于结束时刻",
                           startTime=get_path(doc, FILE_TIMES[0]), endTime=get_path(doc, FILE_TIMES[1])))
    cfg = ctx.profile_spec(rec).get("events") or {}
    if cfg.get("start_key"):
        for i, ev in enumerate(events(ctx, rec)):
            a, b = ev.get(cfg["start_key"]), ev.get(cfg["end_key"])
            if a is None and b is None:
                continue
            ta, tb = parse_time(a), parse_time(b)
            loc = Location(rec.key, pointer=events_ptr(ctx, rec, i), interval=(a, b))
            if ta is None or tb is None:
                outs.append(defect(rec.key, loc, "事件起止时刻无法解析", start=a, end=b))
            elif ta > tb:
                outs.append(defect(rec.key, loc, "事件起始时刻晚于结束时刻", start=a, end=b))
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("V02")
def geometry(ctx, rec):
    path, lst = shapes(ctx, rec)
    outs = []
    for i, s in enumerate(lst):
        if s is None:
            continue  # HEK 事件没有多边形（如 CME）
        ptr = f"{path}[{i}]"
        if not isinstance(s, dict):
            outs.append(defect(rec.key, Location(rec.key, pointer=ptr), "形状元素不是对象"))
            continue
        pts = s.get("points")
        if not isinstance(pts, list) or not all(
                isinstance(p, list) and len(p) == 2 and all(as_number(c) is not None and math.isfinite(as_number(c))
                                                            for c in p) for p in pts):
            outs.append(defect(rec.key, Location(rec.key, pointer=f"{ptr}.points"), "点坐标不是有限数对",
                               sample=pts[:3] if isinstance(pts, list) else pts))
            continue
        pts = [[as_number(x), as_number(y)] for x, y in pts]
        kind = s.get("shape_type", "polygon")
        if kind == "rectangle":
            if len(pts) != 2:
                outs.append(defect(rec.key, Location(rec.key, pointer=f"{ptr}.points"), "矩形必须由 2 个角点给出",
                                   points=len(pts)))
            elif pts[0][0] == pts[1][0] or pts[0][1] == pts[1][1]:
                outs.append(defect(rec.key, Location(rec.key, pointer=f"{ptr}.points"), "矩形宽或高为 0",
                                   points=pts))
        else:
            if len(pts) < 3:
                outs.append(defect(rec.key, Location(rec.key, pointer=f"{ptr}.points"), "多边形少于 3 个点",
                                   points=len(pts)))
            elif polygon_area(pts) <= 0:
                outs.append(defect(rec.key, Location(rec.key, pointer=f"{ptr}.points", region=region_of(pts)), "多边形面积为 0"))
    return merge(rec.key, outs) if outs else ok(rec.key)


def _range_violation(v: float, r: dict) -> str | None:
    if "allowed" in r:
        return None if v in r["allowed"] else f"不在允许取值 {r['allowed']} 中"
    if "min" in r and v < r["min"]:
        return f"小于下限 {r['min']}"
    if "max" in r and v > r["max"]:
        return f"大于上限 {r['max']}"
    if "min_exclusive" in r and v <= r["min_exclusive"]:
        return f"应大于 {r['min_exclusive']}"
    return None


@check("V03")
def physical_ranges(ctx, rec):
    doc = ctx.label(rec)
    ranges = ctx.spec.raw["physical_ranges"]
    outs = []

    def judge(ptr, raw, r):
        if raw is None or raw == "":
            return  # 空值是否允许由结构检查判断
        v = as_number(raw)
        if v is None or not math.isfinite(v):
            outs.append(defect(rec.key, Location(rec.key, pointer=ptr), "数值字段不是有限数", actual=raw))
            return
        why = _range_violation(v, r)
        if why:
            outs.append(defect(rec.key, Location(rec.key, pointer=ptr), f"物理量{why}",
                               value=v, unit=r.get("unit"), rule=r,
                               defect_code="L2-C" if r.get("unit") == "deg" else "L2-A"))

    for field, r in ranges.items():
        if field.startswith("science_annotation."):
            raw = get_path(doc, field)
            if not is_missing(raw):
                judge(field, raw, r)
    for i, ev in enumerate(events(ctx, rec)):
        for field, r in ranges.items():
            if field.startswith("event."):
                key = field[len("event."):]
                if key in ev:
                    judge(events_ptr(ctx, rec, i, key), ev[key], r)
    return merge(rec.key, outs) if outs else ok(rec.key)
