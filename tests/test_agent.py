from __future__ import annotations

import json

import pytest

from sqa import checks  # noqa: F401
from sqa.cli import main
from sqa.registry import verify_correspondence
from sqa.spec import load_spec


def run(input_dir, out, *extra):
    return main(["run", "--input", str(input_dir), "--output", str(out), *extra])


@pytest.fixture
def out(publish, tmp_path):
    o = tmp_path / "out"
    assert run(publish, o) == 0
    return o


def load(out, ds):
    return json.loads((out / "details" / ds / "report.json").read_text(encoding="utf-8"))


def failed(report, cid):
    c = next(c for c in report["checks"] if c["check_id"] == cid)
    return {f["location"]["file"] for f in c["findings"] if f["kind"] == "defect"}


# 只有这三个文件含运行时刻或耗时；其余输出由输入与规格确定，重跑逐字节一致
NONDETERMINISTIC = {"run.log.jsonl", "run_summary.json", "00_MANIFEST.yaml"}


def abstained(report, cid):
    """不可重推项弃报后落待判定的单元（证据中带 abstained 标记）。"""
    c = next(c for c in report["checks"] if c["check_id"] == cid)
    return {f["location"]["file"] for f in c["findings"]
            if f["kind"] == "pending" and f.get("evidence", {}).get("abstained")}


def status(report, cid):
    return next(c for c in report["checks"] if c["check_id"] == cid)["status"]


def test_spec_matches_implementation():
    verify_correspondence(load_spec())


def test_discovers_all_datasets_and_profiles(out):
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    groups = {(x["dataset_id"], x["profile"]) for x in summary["ranking"]["global"]}
    assert groups == {("HMI_AR_sample_aaaa", "hek_image"), ("HMI_AR_sample_bbbb", "hek_image"),
                      ("ULF_sample_cccc", "ulf_timeseries"), ("DMSP_SSUSI_dddd", "aurora_shapes"),
                      ("EPB_seg_sample_eeee", "epb_segmentation")}


def test_hek_injected_defects_hit_exactly(out):
    r = load(out, "HMI_AR_sample_aaaa")
    L = "label/{}.json".format
    assert failed(r, "S01") == {L("broken.png")}
    assert failed(r, "S02") == {L("array.png")}
    assert failed(r, "S03") == {L("fields.png")}
    assert failed(r, "P01") == {L("nodata.png")}
    assert failed(r, "P02") == {L("misref.png")}
    assert failed(r, "P03") == {"data/orphan.png"}
    assert failed(r, "S05") == {L("count.png")}
    assert failed(r, "S06") == {L("types.png")}
    assert failed(r, "C02") == {L("types.png")}
    assert failed(r, "V03") == set() and abstained(r, "V03") == {L("rsun.png")}   # 标量数值：不可重推，弃报
    assert failed(r, "B01") == {L("outside.png")}
    assert failed(r, "N03") == {"data/trunc.png"}
    for cid in ("V01", "V02", "B02", "D03", "M01", "R01"):
        assert failed(r, cid) == set(), cid
    assert r["verdict"]["id"] == "reject"


def test_clean_subset_has_no_defects(out):
    r = load(out, "HMI_AR_sample_bbbb")
    assert r["defect_list"] == []
    assert r["verdict"]["id"] == "accept_with_marks"


def test_ulf_defects(out):
    r = load(out, "ULF_sample_cccc")
    assert failed(r, "B02") == {"late.csv.json"}
    assert failed(r, "D01") == set() and abstained(r, "D01") == {"short.csv"}    # 标签自洽、行数不符：时序断裂，弃报
    assert failed(r, "N02") == {"nan.csv"}
    assert failed(r, "M01") == set() and status(r, "M01") == "pending"       # 完全相同的重复标注只列待复核
    assert status(r, "V02") == "not_applicable"
    assert failed(r, "C01") == {"oov.csv.json"}     # 词表外事件类型


def test_shape_profiles(out):
    d = load(out, "DMSP_SSUSI_dddd")
    assert failed(d, "C01") == {"a2.json"}
    assert failed(d, "D02") == {"a3.json"}
    assert failed(d, "R01") >= {"a4.png"}
    assert failed(d, "P01") == set()      # <id>.json 与 <id>.png 按 stem 配对
    e = load(out, "EPB_seg_sample_eeee")
    assert failed(e, "C01") == {"e2.json"}
    assert failed(e, "D02") == {"e2.json"}  # imgSize/mask 声明 32x32，文件 32x33
    assert failed(e, "M04") == {"e3.json"}  # 第二个多边形是第一个平移缩放的复制品；第三个形状不同，不判


def test_every_conclusion_is_traceable(out):
    for ds in ("HMI_AR_sample_aaaa", "ULF_sample_cccc", "DMSP_SSUSI_dddd"):
        r = load(out, ds)
        for f in r["defect_list"] + r["review_queue"]:
            assert f["check_id"] and f["rule_version"] == r["rule_version"] and f["location"]["file"]
        for f in r["review_queue"]:
            assert f.get("missing_materials"), f
        cov = r["coverage"]
        assert cov["defects_found"] <= cov["completed"] <= cov["applicable"]


def test_unimplemented_checks_stay_in_denominator(out):
    r = load(out, "HMI_AR_sample_bbbb")
    c = {c["check_id"]: c for c in r["checks"]}
    assert c["C03"]["status"] == "pending" and c["C03"]["missing_materials"]
    assert c["M02"]["status"] == "pending"
    assert r["coverage"]["applicable"] >= r["coverage"]["completed"]


def test_global_ranking_has_no_ties_and_explains(out):
    ranking = json.loads((out / "summary.json").read_text(encoding="utf-8"))["ranking"]
    g = ranking["global"]
    assert [x["rank"] for x in g] == list(range(1, len(g) + 1))
    assert g[0]["dataset_id"] == "HMI_AR_sample_bbbb"          # 唯一无缺陷的数据集排第一
    assert all(x["ahead_of_next_because"] for x in g[:-1])
    scores = [x["quality_score"] for x in g]
    assert scores == sorted(scores, reverse=True)
    hek = [x for x in ranking["within_group"] if x["group"] == "hek_image"]
    assert [x["dataset_id"] for x in hek] == ["HMI_AR_sample_bbbb", "HMI_AR_sample_aaaa"]


def test_scores_are_recomputable_from_measures_and_spec(out):
    """评分准则 AUD-2：得分可由检查结果与所提交规范复算。"""
    spec = load_spec().raw
    for ds in ("ULF_sample_cccc", "HMI_AR_sample_aaaa", "DMSP_SSUSI_dddd"):
        q = load(out, ds)["quality"]
        for key, m in q["measures"].items():
            if m["ratio"] is not None and key not in ("distribution_shift", "usage_info"):
                assert m["ratio"] == round(min(1.0, m["numerator"] / m["denominator"]), 6), key
        terms = {t["measure"]: t for t in q["terms"]}
        total, wsum = 0.0, 0
        for d, cfg in spec["dimensions"].items():
            dt = [terms[k] for k in cfg["measures"] if k in terms]
            if not dt:
                assert q["dimensions"][d]["score"] is None and q["dimensions"][d]["status"] == "证据不足"
                continue
            sd = 100 * (1 - sum(t["weight"] * t["ratio"] * t["severity_factor"] for t in dt) / sum(t["weight"] for t in dt))
            assert abs(q["dimensions"][d]["score"] - sd) < 1e-4, (ds, d)
            total += cfg["weight"] * sd
            wsum += cfg["weight"]
        assert abs(q["score"] - total / wsum) < 1e-4


def test_scorecard_has_dimensions_grade_and_remediation(out):
    r = load(out, "HMI_AR_sample_aaaa")
    card = r["scorecard"]
    assert set(card["dimension_scores"]) == {"完整性", "标注质量", "样本对应关系", "覆盖与分布", "文档与来源追溯", "使用条件"}
    assert card["dimension_scores"]["使用条件"] == "证据不足"      # 没有权限/用途说明文件
    assert card["grade"]["zh"] in ("高", "中", "低")
    assert card["key_defects"]["ratio"] > 0 and card["grade"]["id"] != "high"
    assert all(i["remediation"] for i in card["main_issues"])
    assert any(x["check_id"] == "U01" for x in card["review_and_supplement"])


def test_rerun_is_byte_identical(publish, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    run(publish, a)
    run(publish, b)
    files = [p for p in sorted(a.rglob("*")) if p.is_file() and p.name not in NONDETERMINISTIC]
    assert len(files) > 20
    for p in files:
        assert p.read_bytes() == (b / p.relative_to(a)).read_bytes(), p


def test_single_dataset_input(publish, tmp_path):
    o = tmp_path / "single"
    assert run(publish / "ULF_sample_cccc", o) == 0
    assert (o / "details" / "ULF_sample_cccc" / "report.json").exists()


def test_llm_env_missing_is_explicit_error(publish, tmp_path, monkeypatch):
    for v in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.delenv(v, raising=False)
    assert run(publish, tmp_path / "o", "--use-llm") == 5


@pytest.mark.parametrize("bad", ["missing_dir", "empty_dir"])
def test_bad_input_exit_code(tmp_path, bad):
    d = tmp_path / bad
    if bad == "empty_dir":
        d.mkdir()
    assert run(d, tmp_path / "out") == 3


# ---------------------------------------------------------------------------
# 质量变体对照
# ---------------------------------------------------------------------------

def test_variant_comparison(variants, tmp_path):
    o = tmp_path / "vout"
    assert run(variants, o) == 0
    summary = json.loads((o / "summary.json").read_text(encoding="utf-8"))
    assert summary["variant_groups"][0]["members"] == ["DMSP_SSUSI_v1", "DMSP_SSUSI_v2", "DMSP_SSUSI_v3", "DMSP_SSUSI_v4"]
    clean = load(o, "DMSP_SSUSI_v1")
    assert [f for f in clean["defect_list"] if f["check_id"].startswith("X")] == []
    bad = load(o, "DMSP_SSUSI_v4")
    hits = {(f["check_id"], f["location"]["file"]) for f in bad["defect_list"] if f["check_id"].startswith("X")}
    assert ("X01", "s5") in hits                       # 样本整体缺失
    assert ("X02", "s1.png") not in hits               # 图像被改：不在已知污染类型内，只列待复核
    assert any(f["check_id"] == "X02" and f["location"]["file"] == "s1.png" for f in bad["review_queue"])
    assert ("X03", "s2.json") in hits                  # 类别错标
    assert ("X04", "s3.json") in hits                  # 漏标
    assert ("X05", "s4.json") in hits                  # 虚标
    assert ("X06", "s0.json") in hits                  # 偏移
    shift = next(f for f in bad["defect_list"] if f["check_id"] == "X06")
    assert shift["evidence"]["offset_px"] == 10.0 and shift["degree"]["form"] == "image"
    assert "region" in shift["location"]
    # imageWidth 被改：D02 已按 PNG 判出，X07 不重复报告
    assert ("D02", "s6.json") in {(f["check_id"], f["location"]["file"]) for f in bad["defect_list"]}
    assert not any(f["check_id"] == "X07" and f["location"]["file"] == "s6.json" for f in bad["defect_list"])
    m = bad["quality"]["measures"]
    assert m["omission_ratio"]["numerator"] == 1 and m["spurious_ratio"]["numerator"] >= 1
    assert m["missing_ratio"]["denominator"] == 7
    g = json.loads((o / "summary.json").read_text(encoding="utf-8"))["ranking"]["global"]
    assert g[-1]["dataset_id"] == "DMSP_SSUSI_v4"


def test_two_versions_only_pending(variants, tmp_path):
    import shutil
    two = tmp_path / "two"
    for v in ("DMSP_SSUSI_v1", "DMSP_SSUSI_v4"):
        shutil.copytree(variants / v, two / v)
    o = tmp_path / "tout"
    assert run(two, o) == 0
    bad = load(o, "DMSP_SSUSI_v4")
    assert not any(f["check_id"].startswith("X") for f in bad["defect_list"])
    assert any(f["check_id"].startswith("X") for f in bad["review_queue"])


def test_delivery_layout(out):
    """交付材料清单 §三 / §五：目录结构与必填字段。"""
    from sqa.spec import load_spec
    spec = load_spec()
    for rel in ("00_MANIFEST.yaml", "03_评价规范.yaml", "05_排序表/ranking_global.csv", "05_排序表/ranking_explanation.md",
                "06_问题清单/findings.jsonl", "06_问题清单/findings_report.md", "07_证据索引/evidence_index.csv",
                "08_运行日志/run.log.jsonl", "08_运行日志/system_env.txt", "08_运行日志/seeds.yaml", "09_未完成事项说明.md"):
        assert (out / rel).exists(), rel
    cards = sorted((out / "04_评分卡").glob("HMI_AR_sample_aaaa_*.json"))
    assert len(cards) == 1 and cards[0].with_suffix(".html").exists()
    card = json.loads(cards[0].read_text(encoding="utf-8"))
    for k in ("datasetId", "datasetVersion", "declaredUsage", "ruleVersion", "specVersion", "runId", "dimensions",
              "coverage", "checkStatusSummary", "topDefects", "evidenceRefs"):
        assert card[k] not in (None, ""), k
    assert {d["dimId"] for d in card["dimensions"]} == set(spec.raw["dimensions"])
    assert all({"dimId", "score", "range", "grade", "meaning"} <= set(d) for d in card["dimensions"])
    assert all(t["findingIds"] for t in card["topDefects"])
    import csv
    rows = list(csv.DictReader((out / "05_排序表" / "ranking_global.csv").open(encoding="utf-8")))
    assert [int(r["rank"]) for r in rows] == list(range(1, len(rows) + 1))
    assert all(r["datasetId"] and r["totalScore"] and r["comparisonBasis"] for r in rows)
    log = [json.loads(x) for x in (out / "08_运行日志" / "run.log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log and all({"ts", "runId", "datasetId", "event", "checkId", "status", "elapsedMs", "resourcePeak",
                        "tokenUsage", "retryCount"} <= set(e) and e["ts"].endswith("Z") for e in log)
    assert {e["checkId"] for e in log} >= {c.id for c in spec.checks if c.implemented} - {"M03"}
    inv = json.loads((out / "details" / "HMI_AR_sample_aaaa" / "inventory.json").read_text(encoding="utf-8"))
    assert inv["dataset_version"] == card["datasetVersion"]
    manifest = (out / "00_MANIFEST.yaml").read_text(encoding="utf-8")
    assert "06_问题清单/findings.jsonl" in manifest and card["runId"] in manifest
    unfinished = (out / "09_未完成事项说明.md").read_text(encoding="utf-8")
    assert "不可重推检查项声明" in unfinished and "L2-B" in unfinished


def test_checker_failure_degrades_to_pending(publish, tmp_path, monkeypatch):
    from sqa import registry
    real = registry.REGISTRY["V03"]

    def boom(ctx, rec):
        raise RuntimeError("模拟检查器故障")
    monkeypatch.setitem(registry.REGISTRY, "V03", registry.Checker("V03", "record", boom))
    o = tmp_path / "fail"
    assert run(publish, o, "--workers", "1") == 0      # 替换只在本进程生效，单进程运行
    r = load(o, "HMI_AR_sample_bbbb")
    c = next(c for c in r["checks"] if c["check_id"] == "V03")
    assert c["status"] == "pending"
    assert r["execution_failures"] and r["execution_failures"][0]["attempts"] == 2
    assert real.fn  # 未影响其他检查


def test_identity_matching_handles_mirrored_and_shuffled_events(hek_variants, tmp_path):
    o = tmp_path / "hout"
    assert run(hek_variants, o) == 0
    r = load(o, "AIA_AR_sample_v4")
    xv = [f for f in r["defect_list"] if f["check_id"].startswith("X")]
    kinds = sorted(f["check_id"] for f in xv)
    assert kinds == ["X06"], kinds                 # 不应出现 X04/X05；数值扰动（X08）不可重推，弃报为待判定
    shift = next(f for f in xv if f["check_id"] == "X06")
    assert shift["evidence"]["offset_px"] > 100 and abs(shift["evidence"]["area_ratio"] - 1) < 1e-6
    pert = next(f for f in r["review_queue"] if f["check_id"] == "X08")
    assert pert["evidence"]["abstained"] and pert["evidence"]["defect_code"] == "L2-A"
    assert "area_atdiskcenter" in pert["evidence"]["fields"]
    assert abs(pert["evidence"]["max_relative_deviation"] - 0.2345) < 1e-6


def test_nested_defects_do_not_blame_the_clean_version(nested_variants, tmp_path):
    o = tmp_path / "nout"
    assert run(nested_variants, o) == 0
    order = json.loads((o / "summary.json").read_text(encoding="utf-8"))["variant_groups"][0]["quality_order"]
    assert [x["dataset_id"][-2:] for x in order][:2] == ["v1", "v2"]
    for v in ("v1", "v2"):
        assert not [f for f in load(o, f"DMSP_SSUSI_{v}")["defect_list"] if f["check_id"] == "X03"], v
    for v in ("v3", "v4", "v5"):
        hits = [f for f in load(o, f"DMSP_SSUSI_{v}")["defect_list"] if f["check_id"] == "X03"]
        assert [h["location"]["file"] for h in hits] == ["s0.json"], v
        assert hits[0]["evidence"]["reference_version"] == "DMSP_SSUSI_v1"


def test_findings_common_to_all_versions_are_downgraded(nested_variants, tmp_path):
    o = tmp_path / "nout2"
    assert run(nested_variants, o) == 0
    r = load(o, "DMSP_SSUSI_v1")
    assert not any(f["check_id"] == "P01" for f in r["defect_list"])          # s9 所有版本都缺
    q = [f for f in r["review_queue"] if f["check_id"] == "P01"]
    assert q and q[0]["message"].startswith("所有同源版本均存在")
    r4 = load(o, "DMSP_SSUSI_v4")
    assert {f["location"]["file"] for f in r4["defect_list"] if f["check_id"] == "P01"} == {"s7.json", "s8.json"}


def test_physics_checks_work_without_variants(physics_ds, publish, tmp_path):
    o = tmp_path / "pout"
    assert run(physics_ds, o) == 0
    cme = load(o, "CME_C2_sample_pppp")
    assert failed(cme, "V04") == set() and abstained(cme, "V04") == {"label/bad.png.json"}   # 结构参数失配：弃报
    hmi = load(o, "HMI_AR_sample_qqqq")
    assert failed(hmi, "B03") == {"label/off.png.json"}
    assert failed(hmi, "B04") == {"label/scaled.png.json"}
    o2 = tmp_path / "pout2"
    assert run(publish, o2) == 0
    ulf = load(o2, "ULF_sample_cccc")
    assert failed(ulf, "C04") == {"oov.csv.json"}      # 来源为 Pi2 目录，类型却是 ULF-X9


def test_findings_have_required_fields(out):
    """问题清单 §5.1：四类状态全量输出；每条带检查编号、规则版本、证据位置、缺陷类别、程度。"""
    import csv
    from sqa.spec import load_spec
    spec = load_spec()
    recs = [json.loads(x) for x in (out / "06_问题清单" / "findings.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {r["status"] for r in recs} == {"通过", "不通过", "待判定", "不适用"}
    ids = {c.id for c in spec.checks}
    for r in recs:
        assert r["checkId"] in ids and r["ruleVersion"] == spec.rule_version and r["defectType"] in spec.raw["defect_codes"]
        assert r["findingId"].startswith(f"{r['checkId']}-{r['datasetId']}-") and r["evidenceRef"] == f"EV-{r['findingId']}"
        assert isinstance(r["severity"], dict) and "metric" in r["severity"]
        if r["status"] == "不适用":
            assert r["notApplicableReason"]
        if r["status"] == "不通过":
            assert r["confirmedOrPending"] == "已确认错误" and r["criticality"] in ("关键", "一般") and r["basis"]
        if r["status"] == "待判定":
            assert r["confirmedOrPending"] == "待复核事项" and "缺少的材料" in r["basis"]
    index = {row["evidenceId"]: row for row in csv.DictReader((out / "07_证据索引" / "evidence_index.csv").open(encoding="utf-8"))}
    assert set(index) == {r["evidenceRef"] for r in recs}
    fails = [r for r in recs if r["status"] == "不通过"]
    assert fails and all(index[r["evidenceRef"]]["locator"] and index[r["evidenceRef"]]["contentHash"] for r in fails)
    assert {"L0-01", "L2-P"} <= {r["defectType"] for r in fails if r["datasetId"] == "HMI_AR_sample_aaaa"}


def test_parallel_output_is_identical_to_serial(publish, variants, tmp_path):
    import shutil
    both = tmp_path / "both"
    shutil.copytree(publish, both)
    for d in variants.iterdir():
        shutil.copytree(d, both / d.name)
    a, b = tmp_path / "serial", tmp_path / "par"
    assert main(["run", "--input", str(both), "--output", str(a), "--workers", "1"]) == 0
    assert main(["run", "--input", str(both), "--output", str(b), "--workers", "4"]) == 0
    files = sorted(p.relative_to(a) for p in a.rglob("*") if p.is_file() and p.name not in NONDETERMINISTIC)
    assert files and files == sorted(p.relative_to(b) for p in b.rglob("*") if p.is_file() and p.name not in NONDETERMINISTIC)
    for f in files:
        assert (a / f).read_bytes() == (b / f).read_bytes(), f
    log = json.loads((b / "08_运行日志" / "run_summary.json").read_text(encoding="utf-8"))
    assert log["workers"] >= 2 and log["available_cpus"] >= 1


def test_roadshow_export(out, tmp_path):
    """路演两张 CSV：编号全局唯一且为「检查编号-数据集简称-序号」，四类状态取值合法，分数随缺陷密度单调、名次不并列。"""
    import csv
    import re
    import subprocess
    import sys
    from pathlib import Path
    tool = Path(__file__).resolve().parents[1] / "tools" / "roadshow_export.py"
    dst = tmp_path / "rs"
    subprocess.run([sys.executable, str(tool), str(out), "T", str(dst)], check=True, capture_output=True)
    res = list(csv.DictReader((dst / "路演结果_T.csv").open(encoding="utf-8")))
    ev = list(csv.DictReader((dst / "证据表_T.csv").open(encoding="utf-8")))
    assert sorted(int(r["rankGlobal"]) for r in res) == list(range(1, len(res) + 1))
    by_rank = sorted(res, key=lambda r: int(r["rankGlobal"]))
    assert all(float(a["totalScore"]) >= float(b["totalScore"]) for a, b in zip(by_rank, by_rank[1:]))
    ids = [r["findingId"] for r in ev]
    assert len(ids) == len(set(ids)) and all(re.fullmatch(r"[A-Z]\d{2}-[A-Z]+-\d{6}", i) for i in ids)
    assert {r["verdict"] for r in ev} <= {"pass", "fail", "undetermined", "not_applicable"}
    for name in ("缺陷类型结论_T.csv", "六维度结论_T.csv", "场景结论_T.csv", "缺陷编号对照_T.csv"):
        assert (dst / name).exists()
