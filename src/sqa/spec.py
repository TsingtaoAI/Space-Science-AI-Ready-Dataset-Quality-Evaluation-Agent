"""加载并校验声明式判定标准。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SEVERITIES = ("critical", "major", "minor")
EVIDENCE_TYPES = ("computable", "reference", "undeterminable")
TOP_LEVEL = ("rule_version", "statuses", "verdicts", "purposes", "layout", "profiles",
             "required_fields", "physical_ranges", "defect_categories", "checks", "variants", "measures",
             "coverage", "usage", "execution", "dimensions", "grades", "scoring", "defect_types",
             "reference_materials", "defect_codes", "non_reproducible", "field_defect_codes", "llm", "cross_sample")
TRACKS = ("scoring", "non_reproducible", "supplementary")


class SpecError(Exception):
    pass


@dataclass(frozen=True)
class CheckSpec:
    id: str
    name: str
    category: str
    evidence_type: str
    severity: str
    profiles: tuple[str, ...]
    dimension: str = ""
    remediation: str = ""
    defect_type: str = ""
    defect_code: str = ""
    defect_code_by_profile: dict[str, str] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    purposes: tuple[str, ...] | None = None
    implemented: bool = True
    missing_materials: str | None = None
    tolerance: dict[str, Any] = field(default_factory=dict)
    method: str | None = None
    run_when: str | None = None   # no_variant_group：只在没有同源质量版本时运行

    def applies_to(self, profile: str | None) -> bool:
        return "any" in self.profiles or profile in self.profiles


@dataclass
class Spec:
    raw: dict[str, Any]
    sha256: str
    checks: list[CheckSpec]

    @property
    def rule_version(self) -> str:
        return self.raw["rule_version"]

    @property
    def categories(self) -> dict[str, str]:
        return {c["id"]: c["zh"] for c in self.raw["defect_categories"]}

    @property
    def profiles(self) -> dict[str, dict[str, Any]]:
        return self.raw["profiles"]

    def check(self, check_id: str) -> CheckSpec:
        for c in self.checks:
            if c.id == check_id:
                return c
        raise KeyError(check_id)


DEFAULT_SPEC = Path(__file__).resolve().parents[2] / "spec" / "quality_spec.yaml"


def load_spec(path: Path | None = None) -> Spec:
    path = path or DEFAULT_SPEC
    data = path.read_bytes()
    try:
        raw = yaml.safe_load(data)
    except yaml.YAMLError as e:
        raise SpecError(f"规格无法解析: {e}") from e
    for key in TOP_LEVEL:
        if key not in raw:
            raise SpecError(f"规格缺少顶层字段: {key}")

    categories = {c["id"] for c in raw["defect_categories"]}
    profiles = set(raw["profiles"]) | {"any"}
    for field_name, r in raw["physical_ranges"].items():
        for k in ("min", "max", "min_exclusive"):
            if k in r and (isinstance(r[k], bool) or not isinstance(r[k], (int, float))):
                raise SpecError(f"physical_ranges.{field_name}.{k} 不是数字: {r[k]!r}（YAML 科学计数法需写成 1.0e+11）")
    for name, p in raw["profiles"].items():
        if "detect" not in p or "modality" not in p:
            raise SpecError(f"标注族 {name} 缺少 detect 或 modality")
    checks: list[CheckSpec] = []
    seen: set[str] = set()
    for c in raw["checks"]:
        cid = c.get("id")
        if not cid or cid in seen:
            raise SpecError(f"检查编号缺失或重复: {cid}")
        seen.add(cid)
        if c["category"] not in categories:
            raise SpecError(f"{cid}: 未声明的缺陷类别 {c['category']}")
        if c["severity"] not in SEVERITIES:
            raise SpecError(f"{cid}: 非法严重度 {c['severity']}")
        if c["evidence_type"] not in EVIDENCE_TYPES:
            raise SpecError(f"{cid}: 非法证据类型 {c['evidence_type']}")
        if not set(c["profiles"]) <= profiles:
            raise SpecError(f"{cid}: 未声明的标注族 {c['profiles']}")
        if c.get("dimension") not in raw["dimensions"]:
            raise SpecError(f"{cid}: 未声明的评价维度 {c.get('dimension')}")
        if not c.get("remediation"):
            raise SpecError(f"{cid}: 缺少整改建议 remediation")
        if c.get("defect_type") not in raw["defect_types"]:
            raise SpecError(f"{cid}: 未声明的缺陷类型 {c.get('defect_type')}")
        codes = [c.get("defect_code"), *(c.get("defect_code_by_profile") or {}).values()]
        if any(k not in raw["defect_codes"] for k in codes):
            raise SpecError(f"{cid}: 未声明的缺陷代码 {codes}")
        implemented = c.get("implemented", True)
        if not implemented and not c.get("missing_materials"):
            raise SpecError(f"{cid}: 未实现的检查必须写明 missing_materials")
        checks.append(CheckSpec(
            id=cid,
            name=c["name"],
            category=c["category"],
            evidence_type=c["evidence_type"],
            severity=c["severity"],
            profiles=tuple(c["profiles"]),
            dimension=c["dimension"],
            remediation=c["remediation"],
            defect_type=c["defect_type"],
            defect_code=c["defect_code"],
            defect_code_by_profile=dict(c.get("defect_code_by_profile") or {}),
            depends_on=tuple(c.get("depends_on", [])),
            purposes=tuple(c["purposes"]) if "purposes" in c else None,
            implemented=implemented,
            missing_materials=c.get("missing_materials"),
            tolerance=c.get("tolerance", {}),
            method=c.get("method"),
            run_when=c.get("run_when"),
        ))
    for code, d in raw["defect_codes"].items():
        if d.get("track") not in TRACKS:
            raise SpecError(f"缺陷代码 {code}: 非法 track {d.get('track')}")
        if d.get("requirement") not in raw["defect_types"]:
            raise SpecError(f"缺陷代码 {code}: 未声明的需求说明书缺陷类型 {d.get('requirement')}")
        if d["track"] == "non_reproducible" and code not in raw["non_reproducible"]["materials"]:
            raise SpecError(f"不可重推缺陷代码 {code} 未写明需补充的基准材料")
    for r in raw["field_defect_codes"]:
        if r["code"] not in raw["defect_codes"]:
            raise SpecError(f"field_defect_codes 引用了未声明的缺陷代码 {r['code']}")
    for c in checks:
        for dep in c.depends_on:
            if dep not in seen:
                raise SpecError(f"{c.id}: 依赖了不存在的检查 {dep}")
    for key, m in raw["measures"].items():
        for cid in m["checks"]:
            if cid not in seen:
                raise SpecError(f"程度指标 {key} 引用了不存在的检查 {cid}")
    owner = {}
    for d, cfg in raw["dimensions"].items():
        for k in cfg["measures"]:
            if k not in raw["measures"]:
                raise SpecError(f"维度 {d} 引用了不存在的程度指标 {k}")
            if k in owner:
                raise SpecError(f"程度指标 {k} 同时属于 {owner[k]} 与 {d}")
            owner[k] = d
    if set(raw["measures"]) - set(owner):
        raise SpecError(f"程度指标未归入任何维度: {sorted(set(raw['measures']) - set(owner))}")
    return Spec(raw=raw, sha256=hashlib.sha256(data).hexdigest(), checks=checks)


def execution_order(spec: Spec) -> list[CheckSpec]:
    """按依赖拓扑排序；同层保持规格中的书写顺序，保证确定性。"""
    order: list[CheckSpec] = []
    done: set[str] = set()
    remaining = list(spec.checks)
    while remaining:
        ready = [c for c in remaining if all(d in done for d in c.depends_on)]
        if not ready:
            raise SpecError(f"检查依赖存在环: {[c.id for c in remaining]}")
        for c in ready:
            order.append(c)
            done.add(c.id)
            remaining.remove(c)
    return order
