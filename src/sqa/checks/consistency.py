"""同一事件跨样本一致（C05 类别、B05 时刻），只在没有同源质量版本时运行。

依据发榜方参考代码：LASCO C2 的三帧以 hek_event_annotation.image_time 成组，相邻帧的事件标注与中心帧一致；
AIA 同一 HEK 事件（kb_archivid）出现在多张图像中，其类别与起止时刻应一致。注入只改个别样本，
因此同一事件在多数样本中的取值可作参考，取值与严格多数不同的样本即判缺陷；没有严格多数时不判。
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from ..dataset import get_path
from ..model import Location, defect, merge, not_applicable, ok
from ..registry import check
from ._util import parse_time


def _occurrences(ctx) -> list[tuple[dict, dict]]:
    """返回 [(分组配置, {事件键: [(记录, 字段路径 → 取值, 区域)]})]，结果缓存供 C05 / B05 共用。"""
    if "cross_sample" in ctx.cache:
        return ctx.cache["cross_sample"]
    cs = ctx.spec.raw["cross_sample"]
    out = []
    for g in cs["groups"].get(ctx.dominant_profile or "", []):
        occ = defaultdict(list)
        for rec in ctx.records:
            doc = ctx.label(rec)
            prof = ctx.profile_spec(rec)
            if not isinstance(doc, dict) or ctx.profile(rec) != ctx.dominant_profile:
                continue
            ev = prof.get("events") or {}
            lst_path = f"{ev.get('block')}.{ev.get('list')}"
            if g["by"] == "element_identity":
                for i, e in enumerate(get_path(doc, lst_path) or []):
                    k = e.get(g["key"]) if isinstance(e, dict) else None
                    if k in (None, "", "None"):
                        continue
                    base = f"{lst_path}[{i}]"
                    vals = {f"{base}.{f}": e.get(f) for f in g["category_fields"] + g["time_fields"] if f in e}
                    occ[k].append((rec, vals, _bbox(e.get(ev.get("polygon_key") or ""))))
            else:
                k = get_path(doc, g["key"])
                if k in (None, ""):
                    continue
                block = g["key"].rsplit(".", 1)[0]
                vals = {f"{block}.{f}": get_path(doc, f"{block}.{f}") for f in g["category_fields"]}
                for j, e in enumerate(get_path(doc, g["element_list"]) or []):
                    if isinstance(e, dict):
                        vals.update({f"{g['element_list']}[{j}].{f}": e.get(f) for f in g["time_fields"] if f in e})
                occ[k].append((rec, vals, None))
        out.append((g, occ))
    ctx.cache["cross_sample"] = out
    return out


def _bbox(pts):
    try:
        xs, ys = [float(p[0]) for p in pts], [float(p[1]) for p in pts]
        return (round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2))
    except (TypeError, ValueError, IndexError):
        return None


def _field_majority(ctx) -> dict[str, str]:
    """全数据集各类别字段（按字段名）的严格多数取值：同一事件在多数样本中都被改写时，跨样本多数会与数据集定位相反。"""
    if "field_majority" in ctx.cache:
        return ctx.cache["field_majority"]
    counts = defaultdict(Counter)
    for g, occ in _occurrences(ctx):
        for items in occ.values():
            for _, vals, _ in items:
                for path, v in vals.items():
                    leaf = path.rsplit(".", 1)[-1]
                    if leaf in g["category_fields"]:
                        counts[leaf][json.dumps(v, sort_keys=True, ensure_ascii=False)] += 1
    out = {}
    for leaf, c in counts.items():
        top, n = c.most_common(1)[0]
        if n * 2 > sum(c.values()):
            out[leaf] = top
    ctx.cache["field_majority"] = out
    return out


def _judge(ctx, kind: str) -> list:
    """kind = category | time。返回逐记录的结论。"""
    min_n = ctx.spec.raw["cross_sample"]["min_occurrences"]
    majority = _field_majority(ctx) if kind == "category" else {}
    findings = defaultdict(list)
    seen = set()
    for g, occ in _occurrences(ctx):
        fields = g["category_fields"] if kind == "category" else g["time_fields"]
        for key, items in occ.items():
            if len(items) < min_n:
                continue
            for rec, _, _ in items:
                seen.add(rec.key)
            # 同一事件的同一字段归为一组比较：按身份分组时元素序号因样本而异，按字段名归组；按帧组分组时路径本身一致
            by_field = defaultdict(list)
            for rec, vals, region in items:
                for path, v in vals.items():
                    leaf = path.rsplit(".", 1)[-1]
                    if leaf in fields:
                        by_field[leaf if g["by"] == "element_identity" else path].append((rec, path, v, region))
            for f, rows in by_field.items():
                c = Counter(json.dumps(v, sort_keys=True, ensure_ascii=False) for _, _, v, _ in rows)
                top, n = c.most_common(1)[0]
                if n * 2 <= len(rows):
                    continue
                ref = json.loads(top)
                leaf = f.rsplit(".", 1)[-1]
                for rec, path, v, region in rows:
                    cur = json.dumps(v, sort_keys=True, ensure_ascii=False)
                    if cur == top:
                        continue
                    if majority.get(leaf) == cur:
                        continue   # 本样本与数据集定位一致，是同一事件的多数样本被改写，不判本样本
                    ev = {"event_key": str(key), "reference": ref, "actual": v, "agreeing_samples": n,
                          "samples": len(rows), "grouping": g["by"]}
                    loc = Location(rec.key, pointer=path, region=region)
                    if kind == "time":
                        a, b = parse_time(str(ref)), parse_time(str(v))
                        if a and b:
                            off = abs((b - a).total_seconds())
                            ev.update(time_offset_s=off, severity={"form": "time", "axes": {"time_offset": round(min(1.0, off / 3600), 6),
                                                                   "relative_deviation": round(min(1.0, off / 3600), 6)},
                                                                   "score": round(min(1.0, off / 3600), 4)})
                        findings[rec.key].append(defect(rec.key, loc, "事件时刻与同一事件在其他样本中的多数取值不一致", **ev))
                    else:
                        findings[rec.key].append(defect(rec.key, loc, "事件类别与同一事件在其他样本中的多数取值不一致", **ev))
    out = []
    for rec in ctx.records:
        if rec.key in findings:
            out.append(merge(rec.key, findings[rec.key]))
        elif rec.key in seen:
            out.append(ok(rec.key))
        else:
            out.append(not_applicable(rec.key, f"该样本的事件未在 ≥ {min_n} 个样本中出现"))
    return out


@check("C05", scope="group")
def category_consistency(ctx):
    return _judge(ctx, "category")


@check("B05", scope="group")
def time_consistency(ctx):
    return _judge(ctx, "time")


def _class_items(ctx, cfg) -> list[tuple]:
    """[(记录, 字段路径, 类别, 区域)]：事件级类别字段优先，事件没有类别字段时取帧级类别列表。"""
    items = []
    for rec in ctx.records:
        doc = ctx.label(rec)
        if not isinstance(doc, dict) or ctx.profile(rec) != ctx.dominant_profile:
            continue
        ev = ctx.profile_spec(rec).get("events") or {}
        lst_path = f"{ev.get('block')}.{ev.get('list')}"
        found = False
        for i, e in enumerate(get_path(doc, lst_path) or []):
            if not isinstance(e, dict):
                continue
            for f in cfg["element_fields"]:
                if e.get(f) not in (None, ""):
                    items.append((rec, f"{lst_path}[{i}].{f}", str(e[f]), _bbox(e.get(ev.get("polygon_key") or ""))))
                    found = True
                    break
        if not found:
            block = get_path(doc, cfg["block_field"])
            if isinstance(block, list):
                items.extend((rec, f"{cfg['block_field']}[{j}]", str(v), None) for j, v in enumerate(block) if v not in (None, ""))
    return items


@check("C06", scope="group")
def positioning(ctx):
    """类别与数据集定位相符：数据集内占严格多数的类别即数据集定位，其余类别判错标（不依赖目录名）。"""
    cfg = ctx.spec.raw["positioning"]
    items = _class_items(ctx, cfg)
    counts = Counter(c for _, _, c, _ in items)
    total = sum(counts.values())
    seen = {rec.key for rec, _, _, _ in items}
    if total < cfg["min_elements"] or counts.most_common(1)[0][1] <= cfg["min_share"] * total:
        why = (f"类别标注不足 {cfg['min_elements']} 个" if total < cfg["min_elements"]
               else "没有占严格多数的类别，无法确定数据集定位")
        return [not_applicable(rec.key, why) for rec in ctx.records]
    top, n = counts.most_common(1)[0]
    findings = defaultdict(list)
    for rec, path, c, region in items:
        if c != top:
            findings[rec.key].append(defect(rec.key, Location(rec.key, pointer=path, region=region),
                                            f"类别为 {c}，与数据集定位（{top}）不符",
                                            reference=top, actual=c, positioning_share=round(n / total, 4),
                                            class_counts=dict(counts.most_common())))
    return [merge(rec.key, findings[rec.key]) if rec.key in findings
            else ok(rec.key) if rec.key in seen
            else not_applicable(rec.key, "该样本没有类别标注") for rec in ctx.records]
