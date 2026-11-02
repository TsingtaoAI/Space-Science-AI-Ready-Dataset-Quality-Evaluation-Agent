"""按质量变体组并行评估。

每个变体组（无同源版本的数据集各自一组）在一个进程里完成：单数据集检查 → 版本对照 → 报告。
组与组之间没有数据依赖，所以并行结果与串行逐字节一致；排序、汇总表由主进程在全部组完成后统一生成。
进程数取「容器实际可用的 CPU 数」与组数中较小者，不假设能拿到整机资源。
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from typing import Any

from .dataset import get_path, is_missing, load_dataset
from .spec import Spec, load_spec
from .variants import group_variants

PROFILE_PROBE = 50   # 预分组时每个数据集最多解析的标签数


def available_cpus() -> int:
    """容器内实际可用的 CPU 数：CPU 亲和性与 cgroup 配额中较小者。"""
    try:
        n = len(os.sched_getaffinity(0))
    except AttributeError:
        n = os.cpu_count() or 1
    for path in ("/sys/fs/cgroup/cpu.max",):              # cgroup v2：如 "200000 100000" 表示 2 核
        try:
            quota, period = Path(path).read_text().split()[:2]
            if quota != "max":
                n = min(n, max(1, int(int(quota) / int(period))))
        except (OSError, ValueError):
            pass
    try:                                                     # cgroup v1
        q = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        p = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if q > 0 and p > 0:
            n = min(n, max(1, q // p))
    except (OSError, ValueError):
        pass
    return max(1, n)


def pregroup(roots: list[Path], spec: Spec, purpose: str) -> list[list[Path]]:
    """只看文件名与少量标签，按与正式分组相同的规则预先分组。"""
    light = []
    for root in roots:
        ctx = load_dataset(root, spec, purpose)
        for rec in ctx.records[:PROFILE_PROBE]:
            try:
                doc = json.loads(rec.label_path.read_bytes())
            except (OSError, ValueError, UnicodeDecodeError):
                continue
            if isinstance(doc, dict):
                for name, prof in spec.profiles.items():
                    if not is_missing(get_path(doc, prof["detect"])):
                        ctx.profile_of[rec.key] = name
                        break
        light.append(ctx)
    groups = group_variants(light, spec.raw["variants"]["min_jaccard"])
    return [[c.root for c in g] for g in groups]


def run_group(roots: list[str], spec_path: str | None, purpose: str, output: str, use_llm: bool = False) -> dict[str, Any]:
    """一个进程处理一个变体组；返回该组的报告、分组摘要与耗时。启用模型时每个进程各建一个客户端。"""
    from . import checks  # noqa: F401  子进程里注册检查器
    from .materials import inventory
    from .report import build_report
    from .runner import apply_policies, run_checks, run_group_checks

    from .llm import LLMClient
    spec = load_spec(Path(spec_path) if spec_path else None)
    out = Path(output)
    client = LLMClient.from_env() if use_llm else None
    ctxs, results, timings = [], {}, {}
    for root in roots:
        t = time.monotonic()
        ctx = load_dataset(Path(root), spec, purpose)
        if client is not None:
            ctx.cache["llm"] = client
        results[ctx.dataset_id] = run_checks(ctx)
        ctxs.append(ctx)
        timings[ctx.dataset_id] = round(time.monotonic() - t, 3)
        print(f"[{ctx.dataset_id}] 单数据集检查完成（{timings[ctx.dataset_id]}s）", file=sys.stderr, flush=True)
    t = time.monotonic()
    groups = run_group_checks(ctxs, results)
    variant_s = round(time.monotonic() - t, 3)
    reports, hashes, events = [], {}, {}
    for ctx in ctxs:
        apply_policies(ctx, results[ctx.dataset_id])
        inv = inventory(ctx)
        _dump(out / "details" / ctx.dataset_id / "inventory.json", inv)
        hashes[ctx.dataset_id] = {f["path"]: f.get("sha256") or f.get("fingerprint") for f in inv["files"]}
        report = build_report(ctx, results[ctx.dataset_id])
        _dump(out / "details" / ctx.dataset_id / "report.json", report)
        reports.append(report)
        events[ctx.dataset_id] = ctx.cache.get("events", [])
        print(f"[{ctx.dataset_id}] {report['verdict']['zh']} · {report['coverage']['statement']} "
              f"· 质量分 {report['quality']['score']}", file=sys.stderr, flush=True)
    return {"reports": reports, "groups": groups, "timings": timings, "variant_s": variant_s,
            "hashes": hashes, "events": events, "llm_usage": dict(client.usage) if client else None}


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_all(roots: list[Path], spec: Spec, spec_path: Path | None, purpose: str, output: Path,
            workers: int, use_llm: bool = False) -> tuple[list[dict], list[dict], dict, int, dict, dict, dict]:
    """返回 (按数据集顺序排列的报告, 变体组摘要, 耗时, 实际进程数, 文件校验值, 运行事件, 模型消耗合计)。"""
    tasks = pregroup(roots, spec, purpose)
    # 大组先跑，减少尾部等待
    tasks.sort(key=lambda g: -sum(len(list(Path(r).rglob("*.json"))) for r in g))
    n = workers if workers > 0 else min(len(tasks), available_cpus())
    n = max(1, min(n, len(tasks)))
    args = [([str(r) for r in g], str(spec_path) if spec_path else None, purpose, str(output), use_llm) for g in tasks]
    if n == 1:
        outs = [run_group(*a) for a in args]
    else:
        ctx = get_context("fork" if sys.platform.startswith("linux") else "spawn")
        with ProcessPoolExecutor(max_workers=n, mp_context=ctx) as pool:
            outs = list(pool.map(run_group, *zip(*args)))
    reports, groups, timings, variant_total, hashes, events = [], [], {}, 0.0, {}, {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
    for o in outs:
        for k in usage:
            usage[k] += (o["llm_usage"] or {}).get(k, 0)
        reports += o["reports"]
        groups += o["groups"]
        timings.update(o["timings"])
        variant_total += o["variant_s"]
        hashes.update(o["hashes"])
        events.update(o["events"])
    reports.sort(key=lambda r: _root_rank(r["dataset_id"], roots))
    groups.sort(key=lambda g: min(g["members"]))
    timings["_variant_comparison"] = round(variant_total, 3)
    return reports, groups, timings, n, hashes, events, usage


def _root_rank(dataset_id: str, roots: list[Path]) -> int:
    for i, r in enumerate(roots):
        if r.name == dataset_id:
            return i
    return len(roots)
