"""命令行入口。

退出码（确定性，与入库结论无关——「拒绝入库」是正常判定结果）：
    0 运行完成
    2 规格错误或规格与实现不对应
    3 输入目录错误（不存在 / 无可识别数据集）
    4 覆盖率不变量被破坏（K ≤ N ≤ M）
    5 启用了大模型但环境变量缺失
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

from . import __version__, checks  # noqa: F401  导入即注册检查器
from .dataset import discover
from .llm import LLMClient, LLMConfigError
from .deliver import run_id, write_all, write_manifest
from .materials import unfinished_md
from .registry import verify_correspondence
from .parallel import available_cpus, run_all
from .report import InvariantError, ranking
from .spec import DEFAULT_SPEC, SpecError, load_spec


# 判定全程确定性，不使用随机数；仍按审计要求声明种子
RANDOM_SEED = 0


def _resources() -> dict:
    """运行期资源占用（评分准则 §2.7）：进程峰值内存与 CPU 时间。"""
    import resource
    me, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    scale = 1 if sys.platform == "darwin" else 1024  # macOS 以字节计，Linux 以 KB 计
    return {"peak_rss_mb": round(me.ru_maxrss * scale / 2**20, 1),
            "peak_rss_mb_largest_worker": round(kids.ru_maxrss * scale / 2**20, 1),
            "cpu_user_s": round(me.ru_utime + kids.ru_utime, 1),
            "cpu_system_s": round(me.ru_stime + kids.ru_stime, 1),
            "accelerator": "未使用"}


def _dump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_run(args) -> int:
    t0 = time.monotonic()
    spec = load_spec(args.spec)
    verify_correspondence(spec)
    if args.purpose not in spec.raw["purposes"]:
        raise SpecError(f"未声明的用途: {args.purpose}（可选: {', '.join(spec.raw['purposes'])}）")
    llm = LLMClient.from_env() if args.use_llm else None

    if not args.input.is_dir():
        print(f"输入目录不存在: {args.input}", file=sys.stderr)
        return 3
    roots = discover(args.input, spec)
    if not roots:
        print(f"输入目录下没有可识别的数据集（需含标签 JSON 与数据文件）: {args.input}", file=sys.stderr)
        return 3

    # 按质量变体组并行：每组在一个进程里完成单数据集检查、版本对照与报告
    reports, groups, timings, n_workers, hashes, events, usage = run_all(
        roots, spec, args.spec, args.purpose, args.output, args.workers, use_llm=llm is not None)
    remedies = {}
    if llm is not None:
        from .assist import remediation_texts
        remedies = remediation_texts(llm, spec, reports)   # 子代理 remediation_writer：只改写整改建议的表述
        for k in usage:
            usage[k] += llm.usage.get(k, 0)
    for g in groups:
        print(f"[变体组] {' / '.join(g['members'])}：样本 {g['samples']}，可确认参考 {g['confirmed_samples']}",
              file=sys.stderr)

    rk = ranking(reports)
    _dump(args.output / "summary.json", {
        "rule_version": spec.rule_version, "spec_sha256": spec.sha256, "purpose": args.purpose,
        "variant_groups": groups, "ranking": rk,
    })
    (args.output / "09_未完成事项说明.md").write_text(unfinished_md(reports, spec), encoding="utf-8")
    meta = {
        "agent_version": __version__, "rule_version": spec.rule_version, "runId": run_id(__version__, spec.sha256, reports),
        "python": platform.python_version(), "platform": platform.platform(), "random_seed": RANDOM_SEED,
        "datasets": [r["dataset_id"] for r in reports],
        "seconds_by_dataset": timings,
        "execution_failures": sum(len(r["execution_failures"]) for r in reports),
        "workers": n_workers, "available_cpus": available_cpus(),
        "llm_usage": usage,
    }
    write_all(args.output, spec, args.spec or DEFAULT_SPEC, reports, rk, hashes, events, meta, remedies)
    meta.update(seconds_total=round(time.monotonic() - t0, 3), resources=_resources())
    _dump(args.output / "08_运行日志" / "run_summary.json", meta)
    write_manifest(args.output, meta)
    return 0


def cmd_validate_spec(args) -> int:
    spec = load_spec(args.spec)
    verify_correspondence(spec)
    impl = sum(c.implemented for c in spec.checks)
    print(f"规格 {spec.rule_version} 有效：{len(spec.checks)} 项检查，已实现 {impl} 项，"
          f"待实现 {len(spec.checks) - impl} 项（运行时落「待判定」）")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="sqa", description="空间科学智能就绪数据集质量评价智能体")
    p.add_argument("--spec", type=Path, default=None, help="判定标准 YAML（默认 spec/quality_spec.yaml）")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="评估数据集并输出判定记录")
    r.add_argument("--input", type=Path, required=True)
    r.add_argument("--output", type=Path, required=True)
    r.add_argument("--purpose", default="ingest", help="用途：ingest（入库，默认）| training（模型训练）")
    r.add_argument("--use-llm", action="store_true",
                   help="启用模型服务：读说明文档（D04）、改写整改建议（需 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL）")
    r.add_argument("--workers", type=int, default=int(os.environ.get("SQA_WORKERS", "0")),
                   help="并行进程数；0 = 自动（容器可用 CPU 数与质量变体组数中较小者）")
    r.set_defaults(fn=cmd_run)

    v = sub.add_parser("validate-spec", help="校验规格及其与实现的逐条对应")
    v.set_defaults(fn=cmd_validate_spec)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except SpecError as e:
        print(f"规格错误: {e}", file=sys.stderr)
        return 2
    except InvariantError as e:
        print(f"不变量错误: {e}", file=sys.stderr)
        return 4
    except LLMConfigError as e:
        print(f"大模型配置错误: {e}", file=sys.stderr)
        return 5
