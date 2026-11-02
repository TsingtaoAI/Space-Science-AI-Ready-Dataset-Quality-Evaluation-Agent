"""说明文档的声明与数据内容一致（D04）。

声明由子代理 doc_claim_extractor 从文档中抽取（仅 --use-llm），比对全部由本模块的确定性程序完成。
"""
from __future__ import annotations

from ..assist import extract_claims, find_docs, read_text
from ..model import Location, defect, merge, not_applicable, ok, pending
from ..registry import check
from ._util import get_path, image_size, parse_time


def _facts(ctx) -> dict:
    """数据实际情况：样本数、出现过的类别（含概念名）、时间覆盖、图像尺寸、模态。"""
    from ..variants import elements
    cats, sizes, times, mods = set(), set(), [], set()
    for rec in ctx.records:
        doc = ctx.label(rec)
        prof = ctx.profile_spec(rec)
        if not isinstance(doc, dict) or not prof:
            continue
        mods.add(prof.get("modality"))
        _, els = elements(doc, prof)
        for e in els or []:
            if e.label is not None:
                cats.add(str(e.label).lower())
                concept = (prof.get("concept_of") or {}).get(e.label)
                if concept:
                    cats.add(concept.lower())
        for ptr in ("basic_annotation.startTime", "basic_annotation.endTime", "hek_event_annotation.image_time"):
            t = parse_time(get_path(doc, ptr))
            if t:
                times.append(t)
        if prof.get("modality") == "image":
            s = image_size(ctx, rec)
            if s:
                sizes.add((s[0], s[1]))
    return {"sample_count": len(ctx.records), "categories": cats, "time": (min(times), max(times)) if times else None,
            "sizes": sizes, "modalities": mods - {None}}


def _judge(ctx, facts: dict, c: dict) -> str | None:
    """声明与数据不符时返回说明，相符或无法比较时返回 None。"""
    tol = ctx.spec.raw["llm"]["tolerances"]
    f, v = c["field"], c["value"]
    if f == "sample_count":
        n = facts["sample_count"]
        if n and abs(v - n) / n > tol["sample_count_ratio"]:
            return f"文档声称样本数 {v}，实际标签数 {n}"
    elif f == "categories":
        missing = [x for x in v if str(x).strip().lower() not in facts["categories"]]
        if missing:
            return f"文档声称包含的类别在全部标注中没有出现：{'、'.join(map(str, missing))}"
    elif f == "time_range" and facts["time"]:
        a, b = parse_time(str(v[0])), parse_time(str(v[1]))
        lo, hi = facts["time"]
        if a and b:
            gaps = []
            if (lo - a).total_seconds() > tol["time_range_s"]:
                gaps.append(f"{v[0]} 至 {lo.isoformat()}")
            if (b - hi).total_seconds() > tol["time_range_s"]:
                gaps.append(f"{hi.isoformat()} 至 {v[1]}")
            if gaps:
                return f"文档声称覆盖的时段在样本中缺失：{'；'.join(gaps)}"
    elif f == "image_size" and facts["sizes"]:
        try:
            w, h = int(v[0]), int(v[1])
        except (TypeError, ValueError):
            return None
        if (w, h) not in facts["sizes"]:
            return f"文档声称图像尺寸 {w}×{h}，实际尺寸为 {'、'.join(f'{a}×{b}' for a, b in sorted(facts['sizes']))}"
    elif f == "modality" and facts["modalities"]:
        if str(v).strip().lower() not in facts["modalities"]:
            return f"文档声称模态 {v}，标注内容为 {'、'.join(sorted(facts['modalities']))}"
    return None


@check("D04", scope="dataset")
def doc_claims(ctx):
    docs = find_docs(ctx)
    if not docs:
        return [not_applicable(f"dataset:{ctx.dataset_id}", "数据集中没有说明文档")]
    client = ctx.cache.get("llm")
    if client is None:
        return [pending(d, Location(d), "说明文档中的声明需要模型服务读取后才能与数据比对",
                        missing="启用 --use-llm（评测平台注入模型服务），或人工核对文档声明") for d in docs]
    facts = None
    out = []
    for d in docs:
        res = extract_claims(client, ctx.spec, read_text(ctx.root / d))
        if res["status"] == "error":
            out.append(pending(d, Location(d), f"模型服务调用失败：{res['detail']}", missing="恢复模型服务后重跑，或人工核对"))
            continue
        if res["status"] == "disagree":
            out.append(pending(d, Location(d), "多次抽取的文档声明不一致，不采用", missing="人工核对文档声明"))
            continue
        if not res["claims"]:
            out.append(not_applicable(d, "文档中没有可核对的声明（样本数、类别、时段、尺寸、模态）"))
            continue
        facts = facts or _facts(ctx)
        parts = []
        for c in res["claims"]:
            why = _judge(ctx, facts, c)
            if why:
                parts.append(defect(d, Location(d, pointer=f"声明.{c['field']}"), why, quote=c["quote"],
                                    declared=c["value"], truncated=res["truncated"], extracted_by="doc_claim_extractor"))
        out.append(merge(d, parts) if parts else ok(d))
    return out
