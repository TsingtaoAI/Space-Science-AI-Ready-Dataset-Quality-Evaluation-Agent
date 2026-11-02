"""物理与逻辑一致性：单个数据集即可判定的内容性缺陷（不依赖同源版本）。"""
from __future__ import annotations

import math
import re

from ..model import Location, defect, merge, not_applicable, ok, pending
from ..registry import check
from ._util import as_number, events, events_ptr, get_path, image_size, is_missing, polygon_area


@check("V04")
def cme_kinetic_energy(ctx, rec):
    tol = ctx.spec.raw["physics"]["kinetic_energy_tolerance"]
    outs, seen = [], False
    for i, e in enumerate(events(ctx, rec)):
        m, v, k = (as_number(e.get(x)) for x in ("mass", "linear_speed", "kinetic_energy"))
        if not (m and v and k) or m <= 0 or v <= 0 or k <= 0:
            continue
        seen = True
        expected = 0.5 * m * (v * 1e5) ** 2          # g · (cm/s)² = erg
        ratio = k / expected
        if abs(math.log(ratio)) > math.log(1 + tol):
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i)),
                               "CME 动能与质量、速度不自洽（数值扰动）",
                               kinetic_energy=k, mass_g=m, linear_speed_kms=v, expected_kinetic_energy=expected,
                               ratio=round(ratio, 4), tolerance=tol))
    if not seen:
        return not_applicable(rec.key, "事件没有同时给出质量、线速度与动能")
    return merge(rec.key, outs) if outs else ok(rec.key)


def _polygons(ctx, rec):
    key = ctx.profile_spec(rec)["events"].get("polygon_key")
    for i, e in enumerate(events(ctx, rec)):
        pts = e.get(key) if key else None
        if isinstance(pts, list) and len(pts) >= 3 and all(
                isinstance(p, list) and len(p) >= 2 and as_number(p[0]) is not None and as_number(p[1]) is not None
                for p in pts):
            yield i, e, [[as_number(p[0]), as_number(p[1])] for p in pts]


@check("B03")
def center_on_disk(ctx, rec):
    cfg = ctx.spec.raw["physics"]
    polys = list(_polygons(ctx, rec))
    if not polys:
        return not_applicable(rec.key, "没有多边形标注")
    doc = ctx.label(rec)
    rsun = as_number(get_path(doc, "science_annotation.rsunObs"))
    if not rsun:
        return pending(rec.key, Location(rec.key, pointer="science_annotation.rsunObs"),
                       "缺少太阳视半径，无法判断目标是否位于日面内", missing="science_annotation.rsunObs")
    wl = get_path(doc, "science_annotation.wavelnth")
    base = cfg["plate_scale_arcsec"]["with_wavelength" if not is_missing(wl) and wl not in (None, "") else "without_wavelength"]
    size = image_size(ctx, rec)
    w, h = (size[0], size[1]) if size else (cfg["reference_width_px"], cfg["reference_width_px"])
    scale = base * cfg["reference_width_px"] / w      # 降采样图像的像素尺度按比例放大
    r_px = rsun / scale
    cx0, cy0 = w / 2, h / 2
    outs = []
    for i, e, pts in polys:
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        d = math.hypot(cx - cx0, cy - cy0)
        if d > cfg["limb_margin"] * r_px:
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i),
                                                 region=(min(xs), min(ys), max(xs), max(ys))),
                               "目标中心落在日面以外（位置偏移）",
                               center_distance_px=round(d, 1), solar_radius_px=round(r_px, 1),
                               radius_ratio=round(d / r_px, 4), plate_scale_arcsec=round(scale, 4),
                               image_size=[w, h]))
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("B04")
def area_matches_pixels(ctx, rec):
    lo, hi = ctx.spec.raw["physics"]["area_ratio_range"]
    outs, seen = [], False
    for i, e, pts in _polygons(ctx, rec):
        n = as_number(e.get("event_npixels"))
        a = polygon_area(pts)
        if not n or n <= 0 or a <= 0:
            continue
        seen = True
        r = a / n
        if not lo <= r <= hi:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i),
                                                 region=(min(xs), min(ys), max(xs), max(ys))),
                               "目标框面积与事件像素数不符（边界被缩放）",
                               polygon_area_px=round(a, 1), event_npixels=n, ratio=round(r, 4), allowed=[lo, hi]))
    if not seen:
        return not_applicable(rec.key, "没有同时给出多边形与 event_npixels 的事件")
    return merge(rec.key, outs) if outs else ok(rec.key)


_KIND = re.compile(r"(pi1|pi2|pi3|pc1|pc2|pc3|pc4|pc5)", re.IGNORECASE)


@check("C04")
def kind_matches_source(ctx, rec):
    cfg = ctx.profile_spec(rec)["events"]
    outs, seen = [], False
    for i, e in enumerate(events(ctx, rec)):
        src = e.get("source")
        if not isinstance(src, str):
            continue
        name = src.rsplit("/", 1)[-1]
        declared = {m.capitalize() for m in _KIND.findall(name)}
        if not declared:
            continue
        seen = True
        kind = e.get(cfg["type_key"])
        if str(kind).capitalize() not in declared:
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i, cfg["type_key"]),
                                                 interval=(e.get(cfg["start_key"]), e.get(cfg["end_key"]))),
                               "事件类型与来源目录不一致（类别错标）", actual=kind, source=src, expected=sorted(declared)))
    if not seen:
        return not_applicable(rec.key, "来源目录名中没有可识别的事件类型")
    return merge(rec.key, outs) if outs else ok(rec.key)
