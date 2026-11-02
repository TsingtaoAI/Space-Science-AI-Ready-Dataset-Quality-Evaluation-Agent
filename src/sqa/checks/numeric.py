"""数值扰动：标注数值的统计离群（只落待判定）、时序 CSV 与 PNG 本体的完好性。"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

from ..model import Location, defect, merge, ok, pending
from ..registry import check
from ._util import as_number, csv_scan, events, events_ptr, png_info

_SKIP_EVENT_KEYS = {"eventtype", "ar_noaanum", "SOL_standard", "kb_archivid", "solar_object_locator"}


def _numeric_items(ctx, rec):
    """(字段名, JSON 路径, 数值)。只取正数，按对数尺度比较量级。"""
    doc = ctx.label(rec)
    sci = doc.get("science_annotation") if isinstance(doc, dict) else None
    if isinstance(sci, dict):
        for k, v in sci.items():
            x = as_number(v)
            if x is not None and x > 0 and math.isfinite(x):
                yield f"science_annotation.{k}", f"science_annotation.{k}", x
    for i, e in enumerate(events(ctx, rec)):
        for k, v in e.items():
            if k in _SKIP_EVENT_KEYS:
                continue
            x = as_number(v)
            if x is not None and x > 0 and math.isfinite(x):
                yield f"event.{k}", events_ptr(ctx, rec, i, k), x


def _baseline(ctx):
    """每个数值字段在本数据集中的对数中位数与 MAD（同一标注族内）。"""
    if "num_baseline" in ctx.cache:
        return ctx.cache["num_baseline"]
    vals = defaultdict(list)
    for rec in ctx.records:
        if ctx.profile(rec) and isinstance(ctx.label(rec), dict):
            for field, _, x in _numeric_items(ctx, rec):
                vals[(ctx.profile(rec), field)].append(math.log10(x))
    base = {}
    min_n = ctx.spec.raw["outlier"]["min_samples"]
    for key, xs in vals.items():
        if len(xs) >= min_n:
            med = statistics.median(xs)
            mad = statistics.median(abs(x - med) for x in xs)
            if mad > 0:
                base[key] = (med, mad)
    ctx.cache["num_baseline"] = base
    return base


@check("N01")
def outliers(ctx, rec):
    base = _baseline(ctx)
    zmax = ctx.spec.raw["outlier"]["robust_z"]
    outs = []
    for field, ptr, x in _numeric_items(ctx, rec):
        b = base.get((ctx.profile(rec), field))
        if not b:
            continue
        med, mad = b
        z = abs(math.log10(x) - med) / (1.4826 * mad)
        if z > zmax:
            outs.append(pending(rec.key, Location(rec.key, pointer=ptr),
                                "数值量级与同批数据显著偏离，疑似单位换算或数值扰动",
                                missing="该字段的单位与量程声明，或独立观测对照"))
            outs[-1].findings[0].evidence = {"value": x, "batch_median": round(10 ** med, 6),
                                             "orders_of_magnitude_off": round(math.log10(x) - med, 2),
                                             "robust_z": round(z, 1)}
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("N02")
def timeseries_values(ctx, rec):
    rel = ctx.data_file_of(rec)
    if not rel.lower().endswith(".csv"):
        return pending(rec.key, Location(rel), "时序数据不是 CSV，暂无读取器", missing="该格式的读入程序")
    scan = csv_scan(ctx, rel)
    if scan["error"]:
        return defect(rec.key, Location(rel), "CSV 无法读取", error=scan["error"])
    outs = []
    ranges = ctx.spec.raw.get("physical_ranges", {})
    for kind, rows in sorted(scan["bad"].items()):
        r = ranges.get(f"csv.{kind.split(' ')[0]}", {}) if kind.endswith("超出量程") else {}
        if r.get("on_violation") == "pending":
            outs.append(pending(rec.key, Location(rel, interval=(rows[0], rows[-1])), f"时序数值{kind}：{r.get('reason', '')}",
                                missing=r.get("missing")))
            continue
        outs.append(defect(rec.key, Location(rel, interval=(rows[0], rows[-1])), f"时序数值异常：{kind}",
                           rows=len(rows), first_rows=rows[:5]))
    # 填充值的长连续段：缺失被静默填充
    for c, (value, run) in sorted(scan["max_run"].items()):
        outs.append(defect(rec.key, Location(rel), f"时序数值异常：{c} 存在填充值 {value} 的长连续段（缺失被填充）",
                           fill_value=value, longest_run=run, threshold=scan["run_threshold"], total_points=scan["rows"]))
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("N03")
def image_integrity(ctx, rec):
    rel = ctx.data_file_of(rec)
    if not rel.lower().endswith(".png"):
        return pending(rec.key, Location(rel), "图像不是 PNG，暂无读取器", missing="该格式的读入程序")
    info = png_info(ctx, rel)
    if "error" in info:
        return defect(rec.key, Location(rel), "图像文件无法读取", error=info["error"])
    if not info["valid_header"]:
        return defect(rec.key, Location(rel), "不是合法的 PNG 文件（签名或 IHDR 错误）", bytes=info["bytes"])
    if not info["has_iend"]:
        return defect(rec.key, Location(rel), "PNG 文件被截断（缺少 IEND 结束块）", bytes=info["bytes"])
    return ok(rec.key)
