"""评价规范文档必须与当前规格同步（评分准则 §3.3：规范与实现不一致者本项计 0）。"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_spec_doc_in_sync():
    gen = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_spec_doc.py")], capture_output=True, text=True, check=True)
    doc = (ROOT / "docs" / "评价规范.md").read_text(encoding="utf-8")
    assert doc == gen.stdout, "docs/评价规范.md 与 spec 不同步：运行 python3 tools/gen_spec_doc.py > docs/评价规范.md"
