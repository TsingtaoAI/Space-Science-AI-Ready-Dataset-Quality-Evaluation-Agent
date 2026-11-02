"""借鉴发榜方参考代码（OPC-code）的检查：同一事件跨样本一致（C05 / B05）、文件清单（P04）、掩膜图像名（P02）。"""
from __future__ import annotations

import json

from conftest import epb_label, hek_label, png_bytes, write
from sqa.cli import main


def _run(inp, out):
    assert main(["run", "--input", str(inp), "--output", str(out), "--workers", "1"]) == 0


def _check(out, ds, cid):
    r = json.loads((out / "details" / ds / "report.json").read_text(encoding="utf-8"))
    return next(c for c in r["checks"] if c["check_id"] == cid)


def _failed(c):
    return {(f["location"]["file"], f["location"].get("pointer")) for f in c["findings"] if f["kind"] == "defect"}


def _event(name, etype="AR", start="2012-04-29 20:00:00", kb="id-000001"):
    d = hek_label(name)
    e = d["hek_event_annotation"]["matched_events"][0]
    e.update(event_type=etype, concept={"AR": "Active Region", "CH": "Coronal Hole"}[etype], event_starttime=start,
             kb_archivid=kb)
    d["hek_event_annotation"]["event_types"] = [etype]
    d["hek_event_annotation"]["image_time"] = f"2012-04-29T2{name[1]}:00:00"     # 各样本图像时刻不同
    return d


def test_same_event_must_agree_across_samples(tmp_path):
    root = tmp_path / "in" / "AIA_AR_sample_solo"
    for i, (etype, start) in enumerate([("AR", "2012-04-29 20:00:00"), ("AR", "2012-04-29 20:00:00"),
                                        ("CH", "2012-04-29 20:00:00"), ("AR", "2012-04-29 23:30:00")]):
        name = f"s{i}.png"
        write(root, f"label/{name}.json", _event(name, etype, start))
        write(root, f"data/{name}", png_bytes(64, 64, tag=name))
    o = tmp_path / "o"
    _run(root, o)
    assert sum(c["check_id"] == "C05" for c in json.loads((o / "details" / "AIA_AR_sample_solo" / "report.json")
               .read_text(encoding="utf-8"))["checks"]) == 1                    # 每个检查只出现一次
    assert _failed(_check(o, "AIA_AR_sample_solo", "C05")) == {
        ("label/s2.png.json", "hek_event_annotation.matched_events[0].event_type"),
        ("label/s2.png.json", "hek_event_annotation.matched_events[0].concept")}
    b05 = _check(o, "AIA_AR_sample_solo", "B05")
    assert _failed(b05) == {("label/s3.png.json", "hek_event_annotation.matched_events[0].event_starttime")}
    f = next(f for f in b05["findings"] if f["kind"] == "defect")
    assert f["evidence"]["time_offset_s"] == 12600 and f["defect_code"] == "L2-T"


def test_cme_frame_group_must_agree(tmp_path):
    root = tmp_path / "in" / "CME_C2_sample_solo"
    for i in range(3):
        name = f"f{i}.png"
        d = hek_label(name)
        h = d["hek_event_annotation"]
        h["image_time"] = "2012-04-29T21:00:00"                                       # 三帧同组
        h["matched_events"] = [{"cme_time": "2012-04-29T20:36:05" if i != 1 else "2012-04-29T19:10:00", "width": 60}]
        h["event_types"] = ["CME"] if i != 2 else ["FL"]
        write(root, f"label/{name}.json", d)
        write(root, f"data/{name}", png_bytes(64, 64, tag=name))
    o = tmp_path / "o"
    _run(root, o)
    assert _failed(_check(o, "CME_C2_sample_solo", "C05")) == {("label/f2.png.json", "hek_event_annotation.event_types")}
    assert _failed(_check(o, "CME_C2_sample_solo", "B05")) == {
        ("label/f1.png.json", "hek_event_annotation.matched_events[0].cme_time")}


def test_cross_sample_checks_defer_to_variant_comparison(variants, tmp_path):
    o = tmp_path / "o"
    _run(variants, o)
    c = _check(o, "DMSP_SSUSI_v1", "C05")
    assert c["status"] == "not_applicable"


def test_file_list_and_dataset_summary(tmp_path):
    root = tmp_path / "in" / "EPB_seg_sample_list"
    for n in ("e1", "e2"):
        write(root, f"{n}.json", epb_label(f"{n}.png"))
        write(root, f"{n}.png", png_bytes(32, 32, tag=n))
    write(root, "file_list.txt", "e1.png\ne1.json\ne9.png\n")          # e9 缺失；e2.png 未列入
    write(root, "dataset_summary.json", {"total": 2, "classes": ["EPB"]})   # 不得被当作样本标签
    bad = epb_label("e3.png")
    bad["science_annotation"]["mask"]["imagePath"] = "other.png"
    write(root, "e3.json", bad)
    write(root, "e3.png", png_bytes(32, 32, tag="e3"))
    o = tmp_path / "o"
    _run(root, o)
    r = json.loads((o / "details" / "EPB_seg_sample_list" / "report.json").read_text(encoding="utf-8"))
    assert r["records"] == 3                                                # dataset_summary.json 不计入标签
    p04 = _check(o, "EPB_seg_sample_list", "P04")
    assert ("file_list.txt", "第 3 行") in _failed(p04)
    assert any(f["location"]["file"] == "e2.png" and f["defect_code"] == "E-REF" for f in p04["findings"])
    assert ("e3.json", "science_annotation.mask.imagePath") in _failed(_check(o, "EPB_seg_sample_list", "P02"))
