"""检查器注册表：规格中的每个检查编号对应一个实现函数。

scope="record"  函数签名 (ctx, rec) -> Outcome，逐条记录调用
scope="dataset" 函数签名 (ctx) -> list[Outcome]，整个数据集调用一次
scope="group"   函数签名 (ctx) -> list[Outcome]，在全部数据集跑完后、按质量变体组统一比较后调用
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .spec import Spec, SpecError


@dataclass(frozen=True)
class Checker:
    check_id: str
    scope: str
    fn: Callable


REGISTRY: dict[str, Checker] = {}


def check(check_id: str, scope: str = "record"):
    def deco(fn: Callable) -> Callable:
        if check_id in REGISTRY:
            raise RuntimeError(f"检查器重复注册: {check_id}")
        REGISTRY[check_id] = Checker(check_id, scope, fn)
        return fn
    return deco


def verify_correspondence(spec: Spec) -> None:
    """规则与实现逐条对应：声明已实现的必须有实现，有实现的必须在规格中声明。"""
    declared = {c.id for c in spec.checks if c.implemented}
    unimplemented = {c.id for c in spec.checks if not c.implemented}
    registered = set(REGISTRY)
    errors = []
    if missing := declared - registered:
        errors.append(f"规格声明已实现但无实现: {sorted(missing)}")
    if extra := registered - declared - unimplemented:
        errors.append(f"有实现但规格未声明: {sorted(extra)}")
    if stale := registered & unimplemented:
        errors.append(f"已有实现但规格仍标 implemented: false: {sorted(stale)}")
    if errors:
        raise SpecError("；".join(errors))
