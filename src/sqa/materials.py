"""文件清单（需求说明书「数据理解」：建立文件清单并记录版本与校验信息）与未完成事项说明（DEL-11）。

    details/<数据集>/inventory.json   文件清单：路径、字节数、校验值（标签全量 SHA-256，数据文件见指纹规则）
    09_未完成事项说明.md               未实现检查 / 未覆盖维度 / 不可计算项 / 不可重推检查项声明 / 已知局限
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .dataset import DatasetContext
from .variants import fingerprint

FINGERPRINT_RULE = "标签：全量 SHA-256；数据文件：<= 8MB 全量 SHA-256，否则 SHA-256(大小 + 首 1MB + 尾 1MB)"


def inventory(ctx: DatasetContext) -> dict[str, Any]:
    cache = ctx.cache.setdefault("fingerprints", {})
    files = []
    for rec in ctx.records:
        data = rec.label_path.read_bytes()
        files.append({"path": rec.key, "role": "label", "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    for rel in ctx.data_files:
        p = ctx.root / rel
        files.append({"path": rel, "role": "data", "bytes": p.stat().st_size, "fingerprint": fingerprint(p, cache)})
    files.sort(key=lambda f: f["path"])
    digest = hashlib.sha256("\n".join(
        f"{f['path']}\t{f['bytes']}\t{f.get('sha256') or f.get('fingerprint')}" for f in files).encode()).hexdigest()
    ctx.cache["inventory_sha256"] = digest
    return {"dataset_id": ctx.dataset_id, "dataset_version": digest, "fingerprint_rule": FINGERPRINT_RULE,
            "labels": len(ctx.records), "data_files": len(ctx.data_files), "files": files}


def unfinished_md(reports: list[dict[str, Any]], spec) -> str:
    unimpl = [c for c in spec.checks if not c.implemented]
    lines = ["# 未完成事项说明", "", f"规则版本 {spec.rule_version}", "",
             "## 一、未实现检查项清单（仍计入适用检查分母，结论落「待判定」）", ""]
    lines += [f"- {c.id} {c.name}：{c.missing_materials}" for c in unimpl] or ["- 无"]
    lines += ["", "## 二、未覆盖维度（证据不足，不按 0 分或满分计入综合分）", ""]
    any_dim = False
    for r in reports:
        ins = [d["zh"] for d in r["quality"]["dimensions"].values() if d["score"] is None]
        if ins:
            any_dim = True
            lines.append(f"- {r['dataset_id']}：{'、'.join(ins)}")
    lines += [] if any_dim else ["- 无"]
    lines += ["", "## 三、不可计算项标注", ""]
    nc = sorted({(k, m["zh"], m.get("not_computable_reason")) for r in reports for k, m in r["quality"]["measures"].items()
                 if m["ratio"] is None})
    for k, zh, why in nc:
        ds = [r["dataset_id"] for r in reports if r["quality"]["measures"][k]["ratio"] is None
              and r["quality"]["measures"][k].get("not_computable_reason") == why]
        lines.append(f"- {zh}（{k}）：{why}；涉及 {len(ds)} 个数据集")
    lines += [] if nc else ["- 无"]
    cfg = spec.raw["non_reproducible"]
    lines += ["", "## 四、不可重推检查项声明", "",
              f"下列缺陷类型需外部基准方可判定（评分标准附件一「非计分轨」）。本系统对其{cfg['statement']}："
              "判据给出的提示一律落「待判定」，不输出「不通过」，提示的数量与位置保留在证据中供人工复核。", "",
              "| 缺陷代码 | 名称 | 需补充的基准材料 | 涉及检查 | 本次弃报条数 |", "|---|---|---|---|---:|"]
    abst = {}
    for r in reports:
        for f in r["review_queue"]:
            if (f.get("evidence") or {}).get("abstained"):
                abst[f["defect_code"]] = abst.get(f["defect_code"], 0) + 1
    for code, d in spec.raw["defect_codes"].items():
        if d["track"] != "non_reproducible":
            continue
        checks = [c.id for c in spec.checks if c.defect_code == code or code in c.defect_code_by_profile.values()]
        scope = f"（{'、'.join(spec.profiles[p]['zh'].split(' ')[0] for p in d.get('scoring_profiles', []))}上可凭内部矛盾判定，按计分轨处理）" if d.get("scoring_profiles") else ""
        lines.append(f"| {code} | {d['zh']}{scope} | {cfg['materials'][code]} | {'、'.join(checks) or '按结论细分'} | {abst.get(code, 0)} |")
    lines += ["", "## 五、已知局限与改进计划", "",
              "- 无同源质量版本的独立数据集：漏标、虚标、类别置换只能依赖单数据集判据（汇总与明细矛盾、类别表、身份标识格式、"
              "物理一致性），召回低于有版本对照时；改进计划：接入外部事件目录（HEK、CDAW、台站事件表）作参照。",
              "- 质量等级阈值为暂定值：发榜方尚未给出档位与高 / 中 / 低等级的对应关系；评分卡另给出按档位锚点的质量预测。",
              "- 图像内容（像素）层面的缺陷：公开集验证未达可用水平，未纳入判定；图像被改动只列为待复核事项。"]
    lines += ["", "## 六、未获取的依据材料", ""]
    lines += [f"- {m['name']}" + (f"：{m['affects']}" if m.get("affects") else "")
              for m in spec.raw["reference_materials"] if m["status"] != "已获取"] or ["- 无"]
    lines += ["", "## 七、执行失败（已按重试上限处理并降级为待判定）", ""]
    fails = [(r["dataset_id"], f) for r in reports for f in r.get("execution_failures", [])]
    lines += [f"- {d} {f['check_id']} {f['unit']}：{f['error']}" for d, f in fails] or ["- 无"]
    return "\n".join(lines) + "\n"
