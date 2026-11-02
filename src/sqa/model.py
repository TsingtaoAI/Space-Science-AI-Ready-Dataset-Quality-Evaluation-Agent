"""判定记录的数据结构：四种状态、证据位置、单元判定、检查结果。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    NOT_APPLICABLE = "not_applicable"


STATUS_ZH = {
    Status.PASS: "通过",
    Status.FAIL: "不通过",
    Status.PENDING: "待判定",
    Status.NOT_APPLICABLE: "不适用",
}


@dataclass(frozen=True)
class Location:
    """证据位置：可回查到文件 / 样本 / JSON 路径 / 时间区间 / 图像区域（外接框 x1,y1,x2,y2）。"""

    file: str
    sample: str | None = None
    pointer: str | None = None
    interval: tuple | None = None
    region: tuple | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"file": self.file}
        if self.sample is not None:
            d["sample"] = self.sample
        if self.pointer is not None:
            d["pointer"] = self.pointer
        if self.interval is not None:
            d["interval"] = list(self.interval)
        if self.region is not None:
            d["region"] = list(self.region)
        return d


@dataclass
class Finding:
    kind: str  # "defect" | "pending"
    location: Location
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    missing_materials: str | None = None


@dataclass
class Outcome:
    """一个检查在一个单元（一条记录或一个文件）上的判定。"""

    unit: str
    status: Status
    findings: list[Finding] = field(default_factory=list)
    reason: str | None = None


def ok(unit: str) -> Outcome:
    return Outcome(unit, Status.PASS)


def defect(unit: str, loc: Location, message: str, /, **evidence: Any) -> Outcome:
    return Outcome(unit, Status.FAIL, [Finding("defect", loc, message, evidence)])


def pending(unit: str, loc: Location, message: str, missing: str) -> Outcome:
    return Outcome(unit, Status.PENDING, [Finding("pending", loc, message, missing_materials=missing)])


def not_applicable(unit: str, reason: str) -> Outcome:
    return Outcome(unit, Status.NOT_APPLICABLE, reason=reason)


def merge(unit: str, outcomes: list[Outcome]) -> Outcome:
    """同一单元上多个子判定合并：有缺陷即不通过，否则有待判定即待判定。"""
    findings = [f for o in outcomes for f in o.findings]
    statuses = {o.status for o in outcomes}
    if Status.FAIL in statuses:
        return Outcome(unit, Status.FAIL, findings)
    if Status.PENDING in statuses:
        return Outcome(unit, Status.PENDING, findings)
    return Outcome(unit, Status.PASS, findings)


@dataclass
class CheckResult:
    check_id: str
    status: Status
    outcomes: list[Outcome] = field(default_factory=list)
    reason: str | None = None
    missing_materials: str | None = None

    def units(self, status: Status) -> list[str]:
        return [o.unit for o in self.outcomes if o.status == status]
