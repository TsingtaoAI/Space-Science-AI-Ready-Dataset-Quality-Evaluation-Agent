"""边界与位置偏差：几何越界、事件时段与观测时刻不相容。"""
from __future__ import annotations

from datetime import timedelta

from ..model import Location, defect, merge, ok, pending
from ..registry import check
from ._util import as_number, events, events_ptr, get_path, image_size, parse_time, shapes


@check("B01")
def inside_image(ctx, rec):
    path, lst = shapes(ctx, rec)
    if not any(s for s in lst):
        return ok(rec.key)
    size = image_size(ctx, rec)
    if size is None:
        return pending(rec.key, Location(rec.key), "既没有数据文件也没有声明尺寸，无法判断几何是否越界",
                       missing="图像文件或 imageWidth/imageHeight 声明")
    w, h, source = size
    outs = []
    for i, s in enumerate(lst):
        if not isinstance(s, dict):
            continue
        pts = [(as_number(x), as_number(y)) for x, y in s["points"]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > w or bbox[3] > h:
            outside = sum(1 for x, y in pts if x < 0 or y < 0 or x > w or y > h)
            outs.append(defect(rec.key, Location(rec.key, pointer=f"{path}[{i}]", region=bbox),
                               "几何标注越出图像范围", image_size=[w, h], size_source=source,
                               points_outside=outside, points_total=len(pts)))
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("B02")
def event_time_window(ctx, rec):
    doc = ctx.label(rec)
    cfg = ctx.profile_spec(rec)["events"]
    tol = timedelta(seconds=ctx.spec.raw.get("time_tolerance_s", 1))
    outs = []
    evs = events(ctx, rec)
    if ctx.profile_spec(rec)["modality"] == "timeseries":
        fs = parse_time(get_path(doc, "basic_annotation.startTime"))
        fe = parse_time(get_path(doc, "basic_annotation.endTime"))
        if fs is None or fe is None:
            return pending(rec.key, Location(rec.key), "文件时间窗缺失，无法核对事件时段",
                           missing="basic_annotation.startTime/endTime")
        # 事件可以跨越文件边界（公开集干净版本约 14% 的事件只与本文件部分重叠），只有与文件时间窗完全不相交才矛盾
        for i, e in enumerate(evs):
            a, b = parse_time(e.get(cfg["start_key"])), parse_time(e.get(cfg["end_key"]))
            if a and b and (b < fs - tol or a > fe + tol):
                outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i),
                                                     interval=(e.get(cfg["start_key"]), e.get(cfg["end_key"]))),
                                   "事件时段与文件时间窗完全不相交",
                                   file_window=[get_path(doc, "basic_annotation.startTime"),
                                                get_path(doc, "basic_annotation.endTime")]))
    else:
        ref_ptr = cfg.get("reference_time")
        ref = parse_time(get_path(doc, ref_ptr)) if ref_ptr else None
        match_tol = timedelta(seconds=cfg.get("match_tolerance_s", 0))
        if ref is None:
            return ok(rec.key)  # 无观测参考时刻（如字段缺失）时由结构检查负责
        for i, e in enumerate(evs):
            a, b = parse_time(e.get(cfg["start_key"])), parse_time(e.get(cfg["end_key"]))
            if a and b and not (a - match_tol <= ref <= b + match_tol):
                outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i),
                                                     interval=(e.get(cfg["start_key"]), e.get(cfg["end_key"]))),
                                   "图像观测时刻超出所匹配事件的起止时段（含匹配容差）",
                                   image_time=get_path(doc, ref_ptr), match_tolerance_s=match_tol.total_seconds()))
    return merge(rec.key, outs) if outs else ok(rec.key)
