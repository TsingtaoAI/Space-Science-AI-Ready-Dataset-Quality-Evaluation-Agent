"""标注结构自洽：语法层 → 类型层/标注族 → 字段层 → 事件层。"""
from __future__ import annotations

import json
from collections import Counter

from ..model import Location, defect, merge, not_applicable, ok, pending
from ..registry import check
from ._util import TYPE_CHECKS, events_ptr, get_path, is_missing


class _DuplicateKey(ValueError):
    pass


def _no_duplicates(pairs):
    seen = {}
    for k, v in pairs:
        if k in seen:
            raise _DuplicateKey(f"重复键 {k!r}")
        seen[k] = v
    return seen


def _reject_constant(name):
    raise ValueError(f"非标准 JSON 常量 {name}")


@check("S01")
def strict_parse(ctx, rec):
    loc = Location(rec.key)
    raw = rec.label_path.read_bytes()
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except UnicodeDecodeError as e:
        return defect(rec.key, loc, "标签文件不是合法 UTF-8", error=str(e))
    except (json.JSONDecodeError, ValueError) as e:
        ev = {"error": str(e)}
        if isinstance(e, json.JSONDecodeError):
            ev.update(line=e.lineno, column=e.colno)
        return defect(rec.key, loc, "标签 JSON 无法严格解析", **ev)
    ctx.parsed[rec.key] = doc
    return ok(rec.key)


@check("S02")
def top_level_and_profile(ctx, rec):
    doc = ctx.label(rec)
    loc = Location(rec.key, pointer="$")
    if not isinstance(doc, dict):
        return defect(rec.key, loc, "顶层不是 JSON 对象，读得出内容但取不到任何字段",
                      expected="object", actual=type(doc).__name__)
    for name, prof in ctx.spec.profiles.items():
        if not is_missing(get_path(doc, prof["detect"])):
            ctx.profile_of[rec.key] = name
            return ok(rec.key)
    ctx.profile_of[rec.key] = None
    return pending(rec.key, loc, "标签中没有任何已知标注族的标注块，无法确定适用的判据",
                   missing="标注块定义（hek/ulf 事件、specialShapes、mask 之一）")


def _type_ok(v, types) -> bool:
    types = types if isinstance(types, list) else [types]
    return any(TYPE_CHECKS[t](v) for t in types)


@check("S03")
def required_fields(ctx, rec):
    doc = ctx.label(rec)
    missing, wrong = [], []
    for r in ctx.spec.raw["required_fields"]:
        v = get_path(doc, r["path"])
        if is_missing(v):
            missing.append(r["path"])
        elif not _type_ok(v, r["type"]):
            wrong.append({"field": r["path"], "expected": r["type"],
                          "actual": "null" if v is None else type(v).__name__})
    outs = []
    if missing:
        outs.append(defect(rec.key, Location(rec.key), f"缺少 {len(missing)} 个必填字段", missing=missing))
    if wrong:
        outs.append(defect(rec.key, Location(rec.key), f"{len(wrong)} 个必填字段类型不符", fields=wrong))
    return merge(rec.key, outs) if outs else ok(rec.key)


def _key_paths(doc: dict) -> set[str]:
    keys = set()
    for k, v in doc.items():
        keys.add(k)
        if isinstance(v, dict):
            keys.update(f"{k}.{kk}" for kk in v)
    return keys


def _common_keys(ctx) -> tuple[set[str], int]:
    if "common_keys" not in ctx.cache:
        docs = [d for d in ctx.parsed.values() if isinstance(d, dict)]
        counts = Counter(k for d in docs for k in _key_paths(d))
        ratio = ctx.spec.raw.get("schema_conformity_ratio", 0.95)
        common = {k for k, n in counts.items() if docs and n / len(docs) >= ratio}
        ctx.cache["common_keys"] = (common, len(docs))
    return ctx.cache["common_keys"]


@check("S04")
def schema_conformity(ctx, rec):
    common, n = _common_keys(ctx)
    missing = sorted(common - _key_paths(ctx.label(rec)))
    if missing:
        return defect(rec.key, Location(rec.key),
                      f"缺少 {len(missing)} 个同批标签普遍具有的字段",
                      missing=missing, baseline_labels=n,
                      ratio=ctx.spec.raw.get("schema_conformity_ratio", 0.95))
    return ok(rec.key)


def _usual_summary(ctx, cfg) -> tuple | None:
    """同批中「计数与列表一致且列表非空」的标签里，占比 ≥ 90% 的事件类型汇总；没有则 None。"""
    key = ("usual_summary", cfg["block"])
    if key not in ctx.cache:
        c = Counter()
        for doc in ctx.parsed.values():
            blk = doc.get(cfg["block"]) if isinstance(doc, dict) else None
            lst = blk.get(cfg["list"]) if isinstance(blk, dict) else None
            if isinstance(lst, list) and lst and blk.get(cfg["count"]) == len(lst) and isinstance(blk.get(cfg["types"]), list):
                c[tuple(sorted(map(str, blk[cfg["types"]])))] += 1
        n = sum(c.values())
        top = c.most_common(1)
        ctx.cache[key] = top[0][0] if n and top[0][1] / n >= 0.9 else None
    return ctx.cache[key]


@check("S05")
def event_count(ctx, rec):
    cfg = ctx.profile_spec(rec)["events"]
    doc = ctx.label(rec)
    block = get_path(doc, cfg["block"])
    lst = get_path(doc, f"{cfg['block']}.{cfg['list']}")
    if not isinstance(block, dict):
        return defect(rec.key, Location(rec.key, pointer=cfg["block"]), "事件标注块不是对象")
    if not isinstance(lst, list):
        return defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec)), "事件列表缺失或不是数组",
                      actual="缺失" if is_missing(lst) else type(lst).__name__)
    outs = []
    loc = Location(rec.key, pointer=cfg["block"])
    count = block.get(cfg["count"])
    if count != len(lst):
        # 汇总的事件类型也与明细不符：汇总被改写成别的类别（公开集 CME 的类别错标即此形态）
        summary = block.get(cfg.get("types", ""))
        detail = {e.get(cfg.get("type_key")) for e in lst if isinstance(e, dict)} - {None}
        if detail:
            relabeled = isinstance(summary, list) and set(summary) != detail
        else:  # 事件明细不带类型字段（如 CME）：与同批计数一致的标签的主流汇总比较
            usual = _usual_summary(ctx, cfg)
            relabeled = isinstance(summary, list) and usual is not None and bool(lst) and tuple(sorted(map(str, summary))) != usual
        code = {"defect_code": "L2-P"} if relabeled else {}
        outs.append(defect(rec.key, loc, "事件计数与事件列表长度不一致", declared=count, actual=len(lst),
                           declared_types=summary, **code))
    has = block.get(cfg["has"])
    if has is not None and bool(has) != (len(lst) > 0):
        outs.append(defect(rec.key, loc, "has_events 与事件列表是否为空矛盾", has_events=has, events=len(lst)))
    for i, e in enumerate(lst):
        if not isinstance(e, dict):
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i)),
                               "事件元素不是对象", actual=type(e).__name__))
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("S06")
def event_types_summary(ctx, rec):
    cfg = ctx.profile_spec(rec)["events"]
    doc = ctx.label(rec)
    lst = get_path(doc, f"{cfg['block']}.{cfg['list']}")
    detail = sorted({e[cfg["type_key"]] for e in lst if isinstance(e, dict) and e.get(cfg["type_key"])})
    if not detail:
        return not_applicable(rec.key, "事件明细中没有类型字段，无法对照")
    summary = get_path(doc, f"{cfg['block']}.{cfg['types']}")
    if not isinstance(summary, list):
        return defect(rec.key, Location(rec.key, pointer=f"{cfg['block']}.{cfg['types']}"),
                      "事件类型汇总缺失或不是数组", detail_types=detail)
    if sorted(set(summary)) != detail:
        return defect(rec.key, Location(rec.key, pointer=f"{cfg['block']}.{cfg['types']}"),
                      "事件类型汇总与事件明细不一致", declared=summary, from_events=detail)
    return ok(rec.key)
