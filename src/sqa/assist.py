"""模型子代理（仅 --use-llm 时启用）。

doc_claim_extractor   读说明文档，抽取可核对的声明；摘录须逐字出现在文档中，多次抽取一致才采用
remediation_writer    把评分卡主要缺陷的整改建议改写得更具体；只写入说明字段，不影响结论与分数

两个子代理都不做计量、判定、计分与排序。声明与数据的比对在 checks/docclaims.py 中由确定性程序完成。
"""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from .llm import LLMCallError

CLAIM_TYPES = {"sample_count": int, "categories": list, "time_range": list, "image_size": list, "modality": str}


def find_docs(ctx) -> list[str]:
    """数据集目录中的说明文档（相对路径，排序）。"""
    globs = ctx.spec.raw["llm"]["doc_globs"]
    return sorted(p.relative_to(ctx.root).as_posix() for p in ctx.root.rglob("*")
                  if p.is_file() and any(fnmatch.fnmatch(p.name, g) for g in globs))


def _norm(s: str) -> str:
    return "".join(str(s).split())


def _valid(c: Any, fields: list[str]) -> bool:
    if not isinstance(c, dict) or c.get("field") not in fields or not isinstance(c.get("quote"), str):
        return False
    v, typ = c.get("value"), CLAIM_TYPES[c["field"]]
    if typ is int:
        return isinstance(v, int) and not isinstance(v, bool)
    if c["field"] in ("time_range", "image_size"):
        return isinstance(v, list) and len(v) == 2
    return isinstance(v, typ) and bool(v)


def extract_claims(client, spec, text: str) -> dict[str, Any]:
    """返回 {"claims": [...], "rejected": [...], "status": "ok" | "disagree" | "error", "detail": str, "truncated": bool}。

    ① 调用 agreement_calls 次；② 每次只保留结构合法、且摘录逐字出现在文档中的声明；③ 只采用各次完全一致的声明。
    """
    cfg = spec.raw["llm"]
    truncated = len(text) > cfg["max_doc_chars"]
    body = text[:cfg["max_doc_chars"]]
    runs, rejected = [], []
    for _ in range(cfg["agreement_calls"]):
        try:
            out = client.chat_json(cfg["prompts"]["doc_claim_extractor"], body, retry=spec.raw["execution"]["retry_limit"],
                                   temperature=cfg["temperature"])
        except LLMCallError as e:
            return {"claims": [], "rejected": [], "status": "error", "detail": str(e), "truncated": truncated}
        kept = set()
        for c in out.get("claims") or []:
            if _valid(c, cfg["claim_fields"]) and _norm(c["quote"]) and _norm(c["quote"]) in _norm(body):
                kept.add(json.dumps(c, ensure_ascii=False, sort_keys=True))
            else:
                rejected.append(c)
        runs.append(kept)
    agreed = set.intersection(*runs) if runs else set()
    status = "ok" if agreed or not any(runs) else "disagree"
    return {"claims": [json.loads(x) for x in sorted(agreed)], "rejected": rejected, "status": status,
            "detail": "" if status == "ok" else "各次抽取结果不一致", "truncated": truncated}


def remediation_texts(client, spec, reports: list[dict]) -> dict[str, str]:
    """按（检查编号, 缺陷代码）各改写一次整改建议；调用失败的项不写（评分卡保留规格中的通用建议）。"""
    cfg = spec.raw["llm"]
    groups: dict[str, dict] = {}
    for r in reports:
        for f in r["defect_list"]:
            key = f"{f['check_id']}|{f.get('defect_code')}"
            g = groups.setdefault(key, {"count": 0, "examples": []})
            g["count"] += 1
            if len(g["examples"]) < 3 and f["message"] not in g["examples"]:
                g["examples"].append(f["message"])
    out = {}
    for key in sorted(groups):
        cid, code = key.split("|")
        c = spec.check(cid)
        prompt = (f"检查：{cid} {c.name}\n缺陷类型：{code} {spec.raw['defect_codes'][code]['zh']}\n"
                  f"全部数据集合计：{groups[key]['count']} 处\n示例说明：{'；'.join(groups[key]['examples'])}\n"
                  f"通用整改建议：{c.remediation}")
        try:
            text = client.chat([{"role": "system", "content": cfg["prompts"]["remediation_writer"]},
                                {"role": "user", "content": prompt}],
                               retry=spec.raw["execution"]["retry_limit"], temperature=cfg["temperature"])
        except LLMCallError:
            continue
        text = text.strip()
        if text:
            out[key] = text[:200]
    return out


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")
