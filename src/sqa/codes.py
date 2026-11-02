"""缺陷代码（与发榜方附件一的污染项目类型对齐）及不可重推项的弃报。

每条结论的缺陷代码按以下顺序确定：结论证据中的 defect_code（检查器按子情形细分）→
检查在该标注族上的 defect_code_by_profile → 检查的 defect_code。
"""
from __future__ import annotations

import re
from functools import lru_cache

from .model import Finding, Outcome, Status


def finding_code(spec, check_id: str, profile: str | None, finding: Finding | None = None) -> str:
    if finding is not None and isinstance(finding.evidence, dict) and finding.evidence.get("defect_code"):
        return finding.evidence["defect_code"]
    c = spec.check(check_id)
    return c.defect_code_by_profile.get(profile or "", c.defect_code)


def track(spec, code: str, profile: str | None) -> str:
    d = spec.raw["defect_codes"][code]
    if d["track"] == "non_reproducible" and profile in d.get("scoring_profiles", ()):
        return "scoring"
    return d["track"]


@lru_cache(maxsize=None)
def _field_rules(rules: tuple) -> list:
    return [(re.compile(m), c) for m, c in rules]


def field_code(spec, pointer: str | None, default: str) -> str:
    """按 field_defect_codes 把字段路径映射为缺陷代码；无匹配时取 default。"""
    if not pointer:
        return default
    for rx, code in _field_rules(tuple((r["match"], r["code"]) for r in spec.raw["field_defect_codes"])):
        if rx.search(pointer):
            return code
    return default


def abstain_non_reproducible(spec, check_id: str, profile: str | None, outcomes: list[Outcome]) -> tuple[list[Outcome], int]:
    """不可重推项弃报：此类缺陷结论改为「待判定」并写明需补充的基准材料。返回 (新结论, 弃报条数)。"""
    cfg = spec.raw["non_reproducible"]
    out, n = [], 0
    for o in outcomes:
        if o.status != Status.FAIL:
            out.append(o)
            continue
        kept, abstained = [], []
        for f in o.findings:
            code = finding_code(spec, check_id, profile, f) if f.kind == "defect" else None
            if code and track(spec, code, profile) == "non_reproducible":
                ev = {**f.evidence, "defect_code": code, "abstained": True}
                abstained.append(Finding("pending", f.location, f"{cfg['statement']}（{spec.raw['defect_codes'][code]['zh']}）："
                                                                f"判据提示 {f.message}", ev, cfg["materials"][code]))
                n += 1
            else:
                kept.append(f)
        if not abstained:
            out.append(o)
        elif any(f.kind == "defect" for f in kept):
            out.append(Outcome(o.unit, Status.FAIL, kept + abstained, o.reason))
        else:
            out.append(Outcome(o.unit, Status.PENDING, kept + abstained, o.reason))
    return out, n
