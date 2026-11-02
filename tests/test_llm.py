"""模型子代理：用本地模拟的 OpenAI 兼容服务验证 D04 与整改建议改写，以及默认关闭时的行为。"""
from __future__ import annotations

import json

from sqa.cli import main

README = "# DMSP 极光样本\n\n本数据集共 10 个样本，包含 PCA 与 TPA 两类极光。\n图像为 39×35 像素。\n"

GOOD = {"claims": [
    {"field": "sample_count", "value": 10, "quote": "本数据集共 10 个样本"},
    {"field": "categories", "value": ["PCA", "TPA"], "quote": "包含 PCA 与 TPA 两类极光"},
    {"field": "image_size", "value": [39, 35], "quote": "图像为 39×35 像素"},
    {"field": "modality", "value": "timeseries", "quote": "这是一份时序数据"},      # 原文中没有：须被丢弃
]}


def _d04(out, ds):
    r = json.loads((out / "details" / ds / "report.json").read_text(encoding="utf-8"))
    return next(c for c in r["checks"] if c["check_id"] == "D04")


def test_without_model_doc_claims_are_pending(publish, tmp_path):
    (publish / "DMSP_SSUSI_dddd" / "README.md").write_text(README, encoding="utf-8")
    o = tmp_path / "o"
    assert main(["run", "--input", str(publish), "--output", str(o), "--workers", "1"]) == 0
    assert _d04(o, "DMSP_SSUSI_dddd")["status"] == "pending"
    assert _d04(o, "HMI_AR_sample_aaaa")["status"] == "not_applicable"      # 没有说明文档
    summary = json.loads((o / "08_运行日志" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["llm_usage"]["calls"] == 0


def test_doc_claims_are_checked_by_rules(publish, tmp_path, llm_server):
    (publish / "DMSP_SSUSI_dddd" / "README.md").write_text(README, encoding="utf-8")
    llm_server([GOOD])
    o = tmp_path / "o"
    assert main(["run", "--input", str(publish), "--output", str(o), "--workers", "1", "--use-llm"]) == 0
    c = _d04(o, "DMSP_SSUSI_dddd")
    assert c["status"] == "fail"
    msgs = {f["location"]["pointer"]: f for f in c["findings"] if f["kind"] == "defect"}
    assert set(msgs) == {"声明.sample_count", "声明.categories"}               # 尺寸相符；虚构摘录的模态声明被丢弃
    assert "TPA" in msgs["声明.categories"]["message"] and msgs["声明.sample_count"]["evidence"]["quote"] in README
    assert "PCA" not in msgs["声明.categories"]["message"]                  # 数据中有 PCA：须在标注族识别之后比对
    log = [json.loads(x) for x in (o / "08_运行日志" / "run.log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["checkId"] == "D04" and e["datasetId"] == "DMSP_SSUSI_dddd" and e["tokenUsage"] > 0 for e in log)
    summary = json.loads((o / "08_运行日志" / "run_summary.json").read_text(encoding="utf-8"))
    assert summary["llm_usage"]["calls"] >= 3                                 # 抽取 2 次 + 整改建议改写
    card = json.loads(next((o / "04_评分卡").glob("DMSP_SSUSI_dddd_*.json")).read_text(encoding="utf-8"))
    assert any(t.get("remediationDetail") for t in card["topDefects"])
    assert all(isinstance(d["score"], (int, float)) or d["score"] is None for d in card["dimensions"])


def test_disagreeing_extractions_fall_back_to_pending(publish, tmp_path, llm_server):
    (publish / "DMSP_SSUSI_dddd" / "README.md").write_text(README, encoding="utf-8")
    other = {"claims": [{"field": "sample_count", "value": 4, "quote": "本数据集共 10 个样本"}]}
    llm_server([GOOD, other])
    o = tmp_path / "o"
    assert main(["run", "--input", str(publish), "--output", str(o), "--workers", "1", "--use-llm"]) == 0
    assert _d04(o, "DMSP_SSUSI_dddd")["status"] == "pending"


def test_use_llm_without_env_exits_5(publish, tmp_path, monkeypatch):
    for v in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)
    assert main(["run", "--input", str(publish), "--output", str(tmp_path / "o"), "--use-llm"]) == 5
