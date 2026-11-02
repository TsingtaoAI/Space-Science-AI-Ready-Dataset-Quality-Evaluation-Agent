"""类别标注错误与重复标注（虚标）。"""
from __future__ import annotations

import json

from ..model import Location, defect, merge, ok, pending
from ..registry import check
from ._util import events, events_ptr, region_of, shapes


@check("C01")
def vocabulary(ctx, rec):
    prof = ctx.profile_spec(rec)
    vocab = prof.get("vocabulary") or []
    if "shapes" in prof:
        path, lst = shapes(ctx, rec)
        items = [(f"{path}[{i}].label", s.get("label"), region_of(s.get("points") or []))
                 for i, s in enumerate(lst) if isinstance(s, dict)]
    else:
        key, gk = prof["events"]["type_key"], prof["events"].get("polygon_key")
        items = [(events_ptr(ctx, rec, i, key), e.get(key), region_of(e.get(gk) or []) if gk else None)
                 for i, e in enumerate(events(ctx, rec))]
    outs = [defect(rec.key, Location(rec.key, pointer=ptr, region=region), "类别不在声明的类别表内", actual=v, vocabulary=vocab)
            for ptr, v, region in items if v not in vocab]
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("C02")
def code_matches_concept(ctx, rec):
    concept_of = ctx.profile_spec(rec).get("concept_of") or {}
    outs = []
    for i, e in enumerate(events(ctx, rec)):
        code, concept = e.get("event_type"), e.get("concept")
        if code is None or concept is None:
            continue
        expected = concept_of.get(code)
        if expected is None:
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i, "event_type")),
                               "事件类型代码不在 HEK 定义中", actual=code))
        elif expected.lower() != str(concept).strip().lower():
            outs.append(defect(rec.key, Location(rec.key, pointer=events_ptr(ctx, rec, i)),
                               "事件类型代码与概念名不对应", event_type=code, concept=concept, expected=expected))
    return merge(rec.key, outs) if outs else ok(rec.key)


def _canon(x) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


@check("M01")
def duplicate_annotations(ctx, rec):
    outs = []
    items = []
    path, lst = shapes(ctx, rec)
    if "shapes" in ctx.profile_spec(rec):
        items = [(f"{path}[{i}]", s) for i, s in enumerate(lst) if s is not None]
    else:
        items = [(events_ptr(ctx, rec, i), e) for i, e in enumerate(events(ctx, rec))]
    first = {}
    for ptr, item in items:
        k = _canon(item)
        if k in first:
            # 公开集实测：完全相同的重复事件在各档位中数量不随污染程度增加（clean 也有），属源事件目录重复收录，
            # 不是注入的虚标，故只列待复核（附件一 clean 档覆盖 0%）
            outs.append(pending(rec.key, Location(rec.key, pointer=ptr), "重复标注：与前一条完全相同（疑似源目录重复收录）",
                                missing="核对源事件目录是否重复收录；确属多余时删除"))
        else:
            first[k] = ptr
    return merge(rec.key, outs) if outs else ok(rec.key)
