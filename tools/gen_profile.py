"""开发辅助：为新数据类型生成标注族（profiles）草稿，人工审核后再写入规格。

不参与评测运行。步骤：
  ① 启发式分析：读若干标签，找出标注元素列表、类别键、几何键、时间键、身份键、图像尺寸字段与类别词表；
  ② 可选 --llm：把草稿与样本交给模型（如 DeepSeek，OpenAI 兼容接口）补全中文说明、数据集类型并纠错；
  ③ 机械校验：草稿中的每个路径与键都在样本上实测命中率，不达标的标出，供人工判断。

用法：
  python3 tools/gen_profile.py <数据集目录> [--name 名称] [--samples 50] [--llm] [--out 草稿.yaml]
使用 DeepSeek 时先设置：LLM_BASE_URL=https://api.deepseek.com  LLM_API_KEY=<密钥>  LLM_MODEL=deepseek-chat

注意：--llm 会把 3 份样本标签发给模型服务。比赛数据受保密承诺约束，不得发给外部模型——对比赛数据只用启发式
（不加 --llm），或改用内网 / 评测平台提供的模型；--llm 需同时加 --i-confirm-data-may-leave，确认数据允许外发。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqa.checks._util import parse_time  # noqa: E402
from sqa.dataset import get_path, is_missing  # noqa: E402

TYPE_HINT = re.compile(r"(label|type|kind|class|category)$", re.I)
START_HINT = re.compile(r"(start|begin)", re.I)
END_HINT = re.compile(r"(end|stop)", re.I)
ID_HINT = re.compile(r"(^id$|_id$|identifier|archiv|sol_|locator)", re.I)


def load_samples(d: Path, n: int) -> tuple[list[dict], set[str]]:
    docs, exts = [], Counter()
    for p in sorted(d.rglob("*.json"))[:n]:
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            docs.append(doc)
    for p in d.rglob("*"):
        if p.is_file() and p.suffix and p.suffix != ".json":
            exts[p.suffix.lstrip(".").lower()] += 1
    return docs, {e for e, _ in exts.most_common(2)}


def _paths(doc: dict, prefix: str = "", depth: int = 3):
    for k, v in doc.items():
        p = f"{prefix}.{k}" if prefix else k
        yield p, v
        if isinstance(v, dict) and depth > 1:
            yield from _paths(v, p, depth - 1)


def _is_points(v) -> bool:
    return (isinstance(v, list) and len(v) >= 2 and all(isinstance(x, list) and len(x) >= 2 and
                                                         all(isinstance(c, (int, float)) for c in x[:2]) for x in v))


def analyse(docs: list[dict], exts: set[str]) -> dict:
    """启发式草稿：返回规格 profiles 中一项的结构。"""
    lists = Counter()
    for doc in docs:
        for p, v in _paths(doc):
            if isinstance(v, list) and v and all(isinstance(e, dict) for e in v):
                lists[p] += 1
    if not lists:
        raise SystemExit("样本中没有「对象列表」形式的标注元素，无法生成草稿")
    path = lists.most_common(1)[0][0]
    els = [e for doc in docs for e in (get_path(doc, path) or []) if isinstance(e, dict)]
    keys = Counter(k for e in els for k in e)
    n = len(els)

    def share(pred, k):
        return sum(1 for e in els if pred(e.get(k))) / n if n else 0

    type_key = next((k for k, _ in keys.most_common() if TYPE_HINT.search(k)
                     and share(lambda v: isinstance(v, str), k) > 0.9), None)
    geo_key = next((k for k, _ in keys.most_common() if share(_is_points, k) > 0.5), None)
    times = [k for k, _ in keys.most_common() if share(lambda v: parse_time(v) is not None, k) > 0.5]
    start = next((k for k in times if START_HINT.search(k)), None)
    end = next((k for k in times if END_HINT.search(k)), None)
    point_time = times[0] if times and not (start and end) else None
    ids = [k for k, _ in keys.most_common() if ID_HINT.search(k)
           and share(lambda v: isinstance(v, str) and v.strip().lower() not in ("", "none", "null"), k) > 0.5]
    vocab = sorted(Counter(str(e.get(type_key)) for e in els if e.get(type_key) is not None)) if type_key else []
    block, _, lst = path.rpartition(".")
    image = "png" in exts or "jpg" in exts or "fits" in exts
    prof = {"zh": "（待填写：数据来源与标注内容）", "data_type": "图像" if image else "时序",
            "dataset_type": "（待填写）", "modality": "image" if image else "timeseries"}
    if start or end or point_time:   # 有时间字段的元素按事件处理，否则按形状处理
        ev = {"block": block, "list": lst}
        blocks = [get_path(d, block) for d in docs if isinstance(get_path(d, block), dict)]
        for b in blocks[:1]:
            for k, v in b.items():
                if isinstance(v, bool):
                    ev.setdefault("has", k)
                elif isinstance(v, int) and sum(b2.get(k) == len(b2.get(lst) or []) for b2 in blocks) / len(blocks) > 0.9:
                    ev.setdefault("count", k)
                elif isinstance(v, list) and k != lst and all(isinstance(x, str) for x in v):
                    ev.setdefault("types", k)
        if type_key:
            ev["type_key"] = type_key
        if geo_key:
            ev["polygon_key"] = geo_key
        if start and end:
            ev.update(start_key=start, end_key=end)
        elif point_time:
            ev["point_time_key"] = point_time
        if ids:
            ev["identity_keys"] = ids[:3]
        prof.update(detect=block or path, events=ev)
    else:
        prof.update(detect=path, shapes={"path": path})
    if vocab and len(vocab) <= 50:
        prof["vocabulary"] = vocab
    size = {}
    for doc in docs[:1]:
        for p, v in _paths(doc):
            leaf = p.rsplit(".", 1)[-1].lower()
            if leaf in ("imagewidth", "width") and isinstance(v, (int, float)):
                size["width"] = p
            elif leaf in ("imageheight", "height") and isinstance(v, (int, float)):
                size["height"] = p
            elif leaf in ("imgsize", "image_size") and isinstance(v, str):
                size["text"] = p
    if image and size:
        prof["image_size"] = size
    return prof


def validate(prof: dict, docs: list[dict]) -> list[tuple[str, float, bool]]:
    """逐项实测命中率：(检查项, 命中率, 是否达标)。"""
    rows = []
    n = len(docs) or 1
    rows.append((f"detect `{prof.get('detect')}` 在标签中存在", sum(not is_missing(get_path(d, prof.get("detect", ""))) for d in docs) / n, None))
    path = (f"{prof['events']['block']}.{prof['events']['list']}" if "events" in prof else prof.get("shapes", {}).get("path"))
    rows.append((f"元素列表 `{path}` 为数组", sum(isinstance(get_path(d, path or ""), list) for d in docs) / n, None))
    els = [e for d in docs for e in (get_path(d, path or "") or []) if isinstance(e, dict)]
    m = len(els) or 1
    ev = prof.get("events") or {}
    for k in ("type_key", "polygon_key", "start_key", "end_key", "point_time_key"):
        if ev.get(k):
            rows.append((f"{k} `{ev[k]}` 在元素中存在", sum(not is_missing(e.get(ev[k])) for e in els) / m, None))
    tk = ev.get("type_key") or ("label" if "shapes" in prof else None)
    if tk and prof.get("vocabulary"):
        rows.append(("元素类别都在词表内", sum(str(e.get(tk)) in prof["vocabulary"] for e in els) / m, None))
    for k, p in (prof.get("image_size") or {}).items():
        rows.append((f"image_size.{k} `{p}` 存在", sum(not is_missing(get_path(d, p)) for d in docs) / n, None))
    return [(name, round(r, 4), r >= 0.9) for name, r, _ in rows]


def refine_with_llm(prof: dict, docs: list[dict]) -> dict:
    from sqa.llm import LLMClient
    client = LLMClient.from_env()
    samples = json.dumps(docs[:3], ensure_ascii=False)[:12000]
    system = ("你是空间科学数据工程师。给定一个标注族配置草稿（YAML 语义的 JSON）和几份样本标签，"
              "请修正明显错误的键名、补全 zh（中文说明）与 dataset_type（如「图像 · 事件标注型」），保持原有字段结构，"
              "不要新增草稿中没有的字段类型。只输出 JSON。")
    out = client.chat_json(system, f"草稿：{json.dumps(prof, ensure_ascii=False)}\n样本：{samples}")
    print(f"模型调用 {client.usage['calls']} 次，消耗 {client.tokens} tokens", file=sys.stderr)
    return out if isinstance(out, dict) else prof


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--name", default="new_profile")
    ap.add_argument("--samples", type=int, default=200, help="读取的标签数；类别词表只来自这些样本，需人工补全")
    ap.add_argument("--llm", action="store_true", help="用模型补全与纠错（需 LLM_* 环境变量；会把样本发给模型服务）")
    ap.add_argument("--i-confirm-data-may-leave", action="store_true",
                    help="确认这批样本允许发给 LLM_BASE_URL 指向的模型服务（比赛数据不允许外发）")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    docs, exts = load_samples(a.dataset, a.samples)
    if not docs:
        print("没有可解析的标签 JSON", file=sys.stderr)
        return 1
    prof = analyse(docs, exts)
    if a.llm:
        if not a.i_confirm_data_may_leave:
            print("--llm 会把样本标签发给模型服务。比赛数据受保密承诺约束，不得发给外部模型；"
                  "确认数据允许外发后再加 --i-confirm-data-may-leave。本次只输出启发式草稿。", file=sys.stderr)
        else:
            prof = refine_with_llm(prof, docs)
    report = validate(prof, docs)
    text = yaml.safe_dump({a.name: prof}, allow_unicode=True, sort_keys=False)
    (a.out.write_text(text, encoding="utf-8") if a.out else print(text))
    print(f"# 校验（{len(docs)} 份样本）", file=sys.stderr)
    for name, r, good in report:
        print(f"  {'✓' if good else '✗'} {name}：{r:.0%}", file=sys.stderr)
    print("草稿须人工审核后再写入 spec/quality_spec.yaml 的 profiles，并重新生成规范文档与构件登记。", file=sys.stderr)
    return 0 if all(g for _, _, g in report) else 2


if __name__ == "__main__":
    sys.exit(main())
