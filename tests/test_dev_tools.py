"""开发辅助工具：标注族草稿生成（启发式部分）与文档审阅（用模拟模型服务）。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _gen(ds: Path) -> dict:
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "gen_profile.py"), str(ds), "--name", "x"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return yaml.safe_load(r.stdout)["x"]


def test_profile_draft_matches_existing_profiles(publish):
    from sqa.spec import load_spec
    spec = load_spec()
    dmsp = _gen(publish / "DMSP_SSUSI_dddd")
    assert dmsp["shapes"]["path"] == spec.profiles["aurora_shapes"]["shapes"]["path"]
    assert dmsp["image_size"]["width"] == spec.profiles["aurora_shapes"]["image_size"]["width"]
    ulf = _gen(publish / "ULF_sample_cccc")
    ref = spec.profiles["ulf_timeseries"]["events"]
    assert {k: ulf["events"][k] for k in ("block", "list", "type_key", "start_key", "end_key")} == \
           {k: ref[k] for k in ("block", "list", "type_key", "start_key", "end_key")}
    assert ulf["modality"] == "timeseries"


def test_doc_review_writes_report(tmp_path, llm_server):
    llm_server([{"claims": []}])
    doc = tmp_path / "规范.md"
    doc.write_text("# 规范\n\n## 1. 指标\n\n缺陷比例 = 缺陷样本 / 样本数。\n", encoding="utf-8")
    out = tmp_path / "review.md"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "llm_review.py"), str(doc), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    text = out.read_text(encoding="utf-8")
    assert "审阅意见" in text and "规范.md" in text
