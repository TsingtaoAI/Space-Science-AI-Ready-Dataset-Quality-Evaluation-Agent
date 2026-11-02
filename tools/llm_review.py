"""开发辅助：让模型（如 DeepSeek）对照评分要求审阅交付文档，输出审阅意见供人工处理。

不参与评测运行，也不改动任何文件，只写一份审阅意见。文档按二级标题分段送审，每段对照同一份审阅要点。

用法：
  python3 tools/llm_review.py docs/评价规范.md [docs/方案规划.md ...] [--checklist 要点.md] [--out 审阅意见.md]
使用 DeepSeek 时先设置：LLM_BASE_URL=https://api.deepseek.com  LLM_API_KEY=<密钥>  LLM_MODEL=deepseek-chat

注意：只送审自己写的文档（规范、方案、说明）；不要送审含比赛样本内容的文件（问题清单、证据、报告），比赛数据受保密承诺约束。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqa.llm import LLMCallError, LLMClient  # noqa: E402

DEFAULT_CHECKLIST = """\
1. 规范覆盖六个评价维度：完整性、标注质量、样本对应关系、覆盖与分布、文档与来源追溯、使用条件
2. 指标定义、统计分母、容差均可明确表述
3. 给出样本结果到维度得分的汇总方法，且可复算
4. 关键缺陷单独列示规则明确，说明其可否被其他维度抵消
5. 给出可比性说明与综合排序条件，含保留分项比较或并列的条件
6. 四类检查状态与证据不足的处理规则明确，含未实现检查能否从分母移除
7. 每条检查写明适用范围与受限边界，边界可机械判定，不以「数据质量较差」作为适用条件
8. 表述前后一致：同一数字、阈值、版本号在不同段落中相同
9. 没有无法兑现的承诺（如「全部」「零误报」须有依据）
"""

SYSTEM = ("你是数据质量评价规范的评审专家。对照给出的审阅要点，指出这段文档中缺失、含糊、前后矛盾或无依据的地方。"
          "每条意见写明对应的要点编号、文档中的位置（引用原文不超过 20 字）和修改建议。没有问题就回答「本段无意见」。不要改写全文。")


def sections(text: str, limit: int = 12000) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^(?=## )", text)
    out = []
    for p in parts:
        if not p.strip():
            continue
        title = p.strip().splitlines()[0].lstrip("# ").strip()
        for i in range(0, len(p), limit):
            out.append((title + (f"（续 {i // limit}）" if i else ""), p[i:i + limit]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("docs", nargs="+", type=Path)
    ap.add_argument("--checklist", type=Path, help="审阅要点（默认：评分标准第二段最低质量线与通用要求）")
    ap.add_argument("--out", type=Path, default=Path("审阅意见.md"))
    a = ap.parse_args()
    for d in a.docs:
        if d.suffix.lower() in (".jsonl", ".json", ".csv"):
            print(f"拒绝送审 {d}：结构化结果文件可能含比赛样本内容（保密承诺）", file=sys.stderr)
            return 2
    checklist = a.checklist.read_text(encoding="utf-8") if a.checklist else DEFAULT_CHECKLIST
    client = LLMClient.from_env()
    lines = ["# 审阅意见", "", f"审阅模型：{client.model}。意见仅供参考，修改前请对照原文与评分标准核实。", "",
             "## 审阅要点", "", checklist.strip(), ""]
    for doc in a.docs:
        lines += [f"## {doc.name}", ""]
        for title, body in sections(doc.read_text(encoding="utf-8")):
            try:
                reply = client.chat([{"role": "system", "content": SYSTEM},
                                     {"role": "user", "content": f"审阅要点：\n{checklist}\n\n文档段落「{title}」：\n{body}"}])
            except LLMCallError as e:
                reply = f"（调用失败：{e}）"
            lines += [f"### {title}", "", reply.strip(), ""]
    lines += [f"模型调用 {client.usage['calls']} 次，消耗 {client.tokens} tokens。"]
    a.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
