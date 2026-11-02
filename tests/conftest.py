"""合成测试数据：目录布局与字段结构仿照组委会公开数据（OPC-V34），数值为合成值。"""
from __future__ import annotations

import copy
import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def png_bytes(w: int, h: int, truncated: bool = False, tag: str = "") -> bytes:
    """最小合法 PNG；tag 写进 tEXt 块，使不同文件内容不同。"""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00" * (w * 3) for _ in range(h))
    data = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"tag\x00" + tag.encode()) + chunk(b"IDAT", zlib.compress(raw)))
    return data if truncated else data + chunk(b"IEND", b"")


def write(root: Path, rel: str, content) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, (dict, list)):
        p.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    elif isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")


def hek_label(name, polygon=((10, 10), (60, 10), (60, 50), (10, 50), (10, 10)), **over):
    d = {
        "label_header": {"fileName": name, "labelCreateTime": "2026-09-28T18:16:58", "annotator": "anonymous",
                         "labelVersion": "V1.0.0.0", "annotationMethod": "auto"},
        "basic_annotation": {"fileName": name, "relativePath": "data/", "modality": "image", "fileFormat": "png",
                             "startTime": "2012-04-29T21:00:00", "endTime": "2012-04-29T21:00:00",
                             "timestamp": "2026-09-28T18:16:58"},
        "science_annotation": {"dateObs": "2012-04-29T21:00:00", "rsunObs": 952.8, "dsunObs": 1.506e11},
        "hek_event_annotation": {
            "has_events": True, "event_count": 1, "image_time": "2012-04-29T21:00:00", "event_types": ["AR"],
            "matched_events": [{"event_type": "AR", "concept": "Active Region",
                                "event_starttime": "2012-04-29 20:00:00", "event_endtime": "2012-04-30 00:00:00",
                                "area_atdiskcenter": str(264274064 + zlib.crc32(name.encode()) % 1000),
                                "bbox_polygon_pixel": [list(p) for p in polygon]}]},
    }
    for path, value in over.items():
        blk, key = path.split("__")
        if value is _DELETE:
            d[blk].pop(key, None)
        else:
            d[blk][key] = value
    return d


_DELETE = object()


def ulf_label(name, start="2010-11-22T13:00:00Z", end="2010-11-22T13:01:39Z", n_points=100,
              events=(("2010-11-22T13:00:10", "2010-11-22T13:00:40", "Pi2"),)):
    return {
        "label_header": {"identifier": "id-1", "fileName": name, "note": name, "labelVersion": "V1.0.0.0",
                         "annotationMethod": "auto"},
        "basic_annotation": {"fileName": name, "relativePath": ".", "modality": "timeseries", "fileFormat": "csv",
                             "startTime": start, "endTime": end},
        "science_annotation": {"components": ["H", "D", "Z", "T"], "sampling_rate_hz": 1, "duration_s": 100,
                               "n_points": n_points, "units": "nT (H/Z/T), deg (D)", "dateObs": start},
        "ulf_event_annotation": {"has_events": bool(events), "event_count": len(events),
                                 "event_types": sorted({k for _, _, k in events}),
                                 "matched_events": [{"start_dt": a, "end_dt": b, "kind": k, "source": "F006/_entries_p2pi2.jsonl"}
                                                    for a, b, k in events]},
    }


def ulf_csv(n=100, nan_row=None):
    rows = ["t,H,D,Z,T"]
    for i in range(n):
        h = "nan" if i == nan_row else f"{37.5 + (i % 7) * 0.01:.2f}"
        rows.append(f"{i},{h},{-12.6 + (i % 5) * 0.01:.2f},{92.4 + (i % 3) * 0.01:.2f},{3.42 + (i % 2) * 0.01:.2f}")
    return "\n".join(rows) + "\n"


def dmsp_label(name, shapes_=None, w=39, h=35):
    return {
        "label_header": {"fileName": name, "labelVersion": "1.0.0.0", "annotationMethod": ["semi-auto"]},
        "basic_annotation": {"fileName": name, "relativePath": ".", "modality": ["image"], "fileFormat": "PNG",
                             "startTime": "2014-02-15T00:00:00Z", "endTime": "2014-02-15T23:59:59Z",
                             "imageHeight": h, "imageWidth": w},
        "extension_annotation": {"note": name},
        "science_annotation": {"specialShapes": shapes_ if shapes_ is not None else [
            {"label": "PCA", "points": [[5.5, 5.5], [20.0, 20.0]], "shape_type": "rectangle", "flags": {}}],
            "qualityLevel": "GOOD"},
    }


def epb_label(name, label="EPB"):
    return {
        "label_header": {"fileName": name, "labelCreatTime": "2026-03-16 12:03:30", "labelVersion": "V0.0.0.1",
                         "annotationMethod": "manual"},
        "basic_annotation": {"fileName": name, "relativePath": "dataset_root/data/", "modality": "image",
                             "fileFormat": "PNG", "timestamp": "2013-09-29 22:01:15", "imgSize": "32x32 pixels"},
        "science_annotation": {"evenType": "Equatorial Plasma Bubble", "mask": {
            "shapes": [{"label": label, "points": [[3, 3], [20, 4], [12, 18]], "shape_type": "polygon"}],
            "imagePath": name, "imageHeight": 32, "imageWidth": 32}},
        "extension_annotation": {"note": name},
    }


@pytest.fixture
def publish(tmp_path) -> Path:
    root = tmp_path / "publish"

    # --- HEK 图像：分目录布局，100x80 图像 ---
    hek = root / "HMI_AR_sample_aaaa"
    clean_names = [f"clean{i}.png" for i in range(3)]
    for n in clean_names:
        write(hek, f"label/{n}.json", hek_label(n))
        write(hek, f"data/{n}", png_bytes(100, 80, tag=f"data/{n}"))
    bad = {
        "nodata.png": hek_label("nodata.png"),                                            # P01
        "misref.png": hek_label("misref.png", basic_annotation__fileName="other.png",
                                label_header__fileName="other.png"),                      # P02
        "fields.png": hek_label("fields.png", label_header__labelVersion=_DELETE,
                                basic_annotation__modality=_DELETE),                      # S03
        "outside.png": hek_label("outside.png", polygon=((90, 70), (130, 70), (130, 90), (90, 90), (90, 70))),  # B01
        "rsun.png": hek_label("rsun.png"),                                                # V03
        "count.png": hek_label("count.png"),                                              # S05
        "types.png": hek_label("types.png"),                                              # S06 + C02
        "trunc.png": hek_label("trunc.png"),                                              # N03
    }
    bad["rsun.png"]["science_annotation"]["rsunObs"] = 9528.0
    bad["count.png"]["hek_event_annotation"]["event_count"] = 3
    ev = copy.deepcopy(bad["types.png"]["hek_event_annotation"]["matched_events"][0])
    ev.update(event_type="CH", concept="Active Region")
    bad["types.png"]["hek_event_annotation"]["matched_events"].append(ev)
    bad["types.png"]["hek_event_annotation"]["event_count"] = 2
    for n, d in bad.items():
        write(hek, f"label/{n}.json", d)
        if n != "nodata.png":
            write(hek, f"data/{n}", png_bytes(100, 80, truncated=(n == "trunc.png"), tag=f"data/{n}"))
    write(hek, "data/orphan.png", png_bytes(100, 80, tag="data/orphan.png"))                                     # P03
    write(hek, "label/broken.png.json", '{"label_header": {,}}')                         # S01
    write(hek, "data/broken.png", png_bytes(100, 80, tag="data/broken.png"))
    write(hek, "label/array.png.json", [1, 2, 3])                                         # S02
    write(hek, "data/array.png", png_bytes(100, 80, tag="data/array.png"))

    # 同族第二个子集：完全干净，用于族内排序
    hek2 = root / "HMI_AR_sample_bbbb"
    for n in clean_names:
        write(hek2, f"label/{n}.json", hek_label(n))
        write(hek2, f"data/{n}", png_bytes(100, 80, tag=f"data/{n}"))

    # --- ULF 时序：平铺，<数据文件名>.json ---
    ulf = root / "ULF_sample_cccc"
    write(ulf, "ok.csv.json", ulf_label("ok.csv"))
    write(ulf, "ok.csv", ulf_csv())
    write(ulf, "late.csv.json", ulf_label("late.csv", events=(("2010-11-22T13:03:20", "2010-11-22T13:05:00", "Pi2"),)))  # B02：与时间窗完全不相交
    write(ulf, "cross.csv.json", ulf_label("cross.csv", events=(("2010-11-22T13:01:20", "2010-11-22T13:05:00", "Pi2"),)))  # 跨文件边界，不判
    write(ulf, "cross.csv", ulf_csv())
    write(ulf, "late.csv", ulf_csv())
    write(ulf, "short.csv.json", ulf_label("short.csv"))                                   # D01
    write(ulf, "short.csv", ulf_csv(n=90))
    write(ulf, "nan.csv.json", ulf_label("nan.csv"))                                       # N02
    write(ulf, "nan.csv", ulf_csv(nan_row=5))
    write(ulf, "dupe.csv.json", ulf_label("dupe.csv", events=(("2010-11-22T13:00:10", "2010-11-22T13:00:40", "Pi2"),) * 2))  # M01
    write(ulf, "dupe.csv", ulf_csv())
    write(ulf, "oov.csv.json", ulf_label("oov.csv", events=(("2010-11-22T13:00:10", "2010-11-22T13:00:40", "ULF-X9"),)))  # C01
    write(ulf, "oov.csv", ulf_csv())

    # --- DMSP 极光：平铺，<id>.json + <id>.png ---
    dm = root / "DMSP_SSUSI_dddd"
    write(dm, "a1.json", dmsp_label("a1.png"))
    write(dm, "a1.png", png_bytes(39, 35, tag="a1.png"))
    write(dm, "a2.json", dmsp_label("a2.png", shapes_=[
        {"label": "__OOV__", "points": [[5, 5], [20, 20]], "shape_type": "rectangle"}]))    # C01
    write(dm, "a2.png", png_bytes(39, 35, tag="a2.png"))
    write(dm, "a3.json", dmsp_label("a3.png", w=390, h=350))                               # D02
    write(dm, "a3.png", png_bytes(39, 35, tag="a3.png"))
    write(dm, "a4.json", dmsp_label("a4.png"))                                             # R01（与 a1 数据相同）
    write(dm, "a4.png", png_bytes(39, 35, tag="a1.png"))

    # --- EPB 分割：平铺 ---
    epb = root / "EPB_seg_sample_eeee"
    write(epb, "e1.json", epb_label("e1.png"))
    write(epb, "e1.png", png_bytes(32, 32, tag="e1.png"))
    write(epb, "e2.json", epb_label("e2.png", label="__OOV__"))                            # C01
    write(epb, "e2.png", png_bytes(32, 33, tag="e2.png"))                                                # D02
    e3 = epb_label("e3.png")                                                                              # M04：平移缩放的复制多边形
    poly = [[2, 2], [10, 3], [14, 8], [9, 12], [4, 11], [1, 6]]
    e3["science_annotation"]["mask"]["shapes"] = [
        {"label": "EPB", "points": poly, "shape_type": "polygon"},
        {"label": "EPB", "points": [[x * 1.5 + 12, y * 1.5 + 10] for x, y in poly], "shape_type": "polygon"},
        {"label": "EPB", "points": [[2, 20], [9, 21], [12, 26], [8, 30], [3, 29], [1, 24]], "shape_type": "polygon"}]
    write(epb, "e3.json", e3)
    write(epb, "e3.png", png_bytes(32, 32, tag="e3.png"))
    return root


@pytest.fixture
def variants(tmp_path) -> Path:
    """同一基础数据的 4 个质量版本：v1~v3 干净，v4 注入各类缺陷。"""
    root = tmp_path / "vpublish"
    base = {f"s{i}": [{"label": "PCA", "points": [[5.0 + i, 5.0], [20.0 + i, 20.0]], "shape_type": "rectangle", "flags": {}}]
            for i in range(7)}
    for v in ("v1", "v2", "v3", "v4"):
        ds = root / f"DMSP_SSUSI_{v}"
        for sid, shapes_ in base.items():
            shapes_ = copy.deepcopy(shapes_)
            w = 39
            png = png_bytes(39, 35, tag=sid)
            if v == "v4":
                if sid == "s0":
                    shapes_[0]["points"] = [[p[0] + 6, p[1] + 8] for p in shapes_[0]["points"]]   # 偏移 10px
                if sid == "s1":
                    png = png_bytes(39, 35, tag="changed")                                            # 数据被改
                if sid == "s2":
                    shapes_[0]["label"] = "TPA"                                                       # 错标
                if sid == "s3":
                    shapes_ = []                                                                      # 漏标
                if sid == "s4":
                    shapes_.append({"label": "PCA", "points": [[30.0, 25.0], [36.0, 32.0]], "shape_type": "rectangle", "flags": {}})
                if sid == "s5":
                    continue                                                                          # 样本缺失
                if sid == "s6":
                    w = 390                                                                           # 声明宽度被改
            write(ds, f"{sid}.json", dmsp_label(f"{sid}.png", shapes_=shapes_, w=w))
            write(ds, f"{sid}.png", png)
    return root


@pytest.fixture
def hek_variants(tmp_path) -> Path:
    """HEK 事件的 4 个质量版本；v4 打乱事件顺序并水平镜像第 0 个事件，另把第 1 个事件的面积扰动。"""
    root = tmp_path / "hpublish"
    def doc(name, mirror=False, shuffle=False, perturb=False):
        d = hek_label(name)
        evs = []
        for i in range(3):
            poly = [[100 + 50 * i, 10], [130 + 50 * i, 10], [130 + 50 * i, 40], [100 + 50 * i, 40], [100 + 50 * i, 10]]
            if mirror and i == 0:
                poly = [[400 - x, y] for x, y in poly]
            evs.append({"kb_archivid": f"id-{name}-{i}", "event_type": "AR", "concept": "Active Region",
                        "event_starttime": "2012-04-29 20:00:00", "event_endtime": "2012-04-30 00:00:00",
                        "area_atdiskcenter": "1000" if not (perturb and i == 1) else "1234.5",
                        "bbox_polygon_pixel": poly})
        if shuffle:
            evs = evs[::-1]
        d["hek_event_annotation"].update(event_count=3, matched_events=evs)
        return d
    for v in ("v1", "v2", "v3", "v4"):
        ds = root / f"AIA_AR_sample_{v}"
        for k in range(3):
            n = f"img{k}.png"
            bad = v == "v4" and k == 0
            write(ds, f"label/{n}.json", doc(n, mirror=bad, shuffle=bad, perturb=bad))
            write(ds, f"data/{n}", png_bytes(400, 80, tag=n))
    return root


@pytest.fixture
def nested_variants(tmp_path) -> Path:
    """5 个质量版本，缺陷嵌套：v3~v5 共有同一处错标（3 票多数），v4、v5 另缺数据文件（使其在质量序上靠后）；
    样本 s9 在所有版本中都缺数据（基础数据问题）。"""
    root = tmp_path / "npublish"
    for v in ("v1", "v2", "v3", "v4", "v5"):
        ds = root / f"DMSP_SSUSI_{v}"
        for i in range(10):
            sid = f"s{i}"
            shapes_ = [{"label": "PCA", "points": [[5.0 + i, 5.0], [20.0 + i, 20.0]], "shape_type": "rectangle", "flags": {}}]
            if sid == "s0" and v in ("v3", "v4", "v5"):
                shapes_[0]["label"] = "TPA"                       # 嵌套的错标
            write(ds, f"{sid}.json", dmsp_label(f"{sid}.png", shapes_=shapes_))
            missing = sid == "s9" or (v in ("v4", "v5") and sid in ("s7", "s8")) or (v == "v3" and sid == "s8")
            if not missing:
                write(ds, f"{sid}.png", png_bytes(39, 35, tag=sid))
    return root


@pytest.fixture
def physics_ds(tmp_path) -> Path:
    """单数据集即可判定的物理一致性缺陷。图像 400×400（相当于 4096 降采样约 10 倍）。"""
    root = tmp_path / "ppublish"
    def cme(name, ke):
        d = hek_label(name, polygon=())
        d["hek_event_annotation"].update(event_types=["CME"], event_count=1, matched_events=[
            {"cme_time": "2012-04-29T21:00:00", "linear_speed": "619", "mass": "1e15", "kinetic_energy": ke, "width": "56"}])
        return d
    ds = root / "CME_C2_sample_pppp"
    write(ds, "label/ok.png.json", cme("ok.png", "1.9e30"))          # 0.5·1e15·(6.19e7)² ≈ 1.916e30
    write(ds, "label/bad.png.json", cme("bad.png", "4.0e30"))        # 偏离 2 倍
    for n in ("ok.png", "bad.png"):
        write(ds, f"data/{n}", png_bytes(400, 400, tag=n))
    def ar(name, poly, npx):
        d = hek_label(name, polygon=poly)
        d["hek_event_annotation"]["matched_events"][0]["event_npixels"] = npx
        return d
    hmi = root / "HMI_AR_sample_qqqq"
    sq = lambda x, y, s: ((x, y), (x + s, y), (x + s, y + s), (x, y + s), (x, y))
    write(hmi, "label/ok.png.json", ar("ok.png", sq(190, 190, 20), 150))       # 中心 (200,200)，面积比 400/150
    write(hmi, "label/off.png.json", ar("off.png", sq(370, 370, 20), 150))     # 中心离日心 ≈ 1.5 R
    write(hmi, "label/scaled.png.json", ar("scaled.png", sq(150, 150, 100), 150))  # 面积比 10000/150
    for n in ("ok.png", "off.png", "scaled.png"):
        write(hmi, f"data/{n}", png_bytes(400, 400, tag=n))
    return root


# --- 模拟的 OpenAI 兼容模型服务（测试模型子代理与开发工具用）---
import json as _json  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, HTTPServer  # noqa: E402


class _Mock:
    def __init__(self, replies):
        self.replies = replies          # 文档抽取依次返回的 JSON；用完后重复最后一个
        self.calls = 0

    def handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                system = body["messages"][0]["content"]
                if "数据审核助手" in system:
                    content = _json.dumps(mock.replies[min(mock.calls, len(mock.replies) - 1)], ensure_ascii=False)
                    mock.calls += 1
                else:
                    content = "由标注负责人按缺陷清单逐条修正，修正后重跑本检查确认不通过数为 0。"
                out = _json.dumps({"choices": [{"message": {"content": content}}],
                                  "usage": {"prompt_tokens": 100, "completion_tokens": 20}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass
        return H


@pytest.fixture
def llm_server(monkeypatch):
    servers = []

    def start(replies):
        mock = _Mock(replies)
        srv = HTTPServer(("127.0.0.1", 0), mock.handler())
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{srv.server_port}/v1")
        monkeypatch.setenv("LLM_API_KEY", "test-key")
        monkeypatch.setenv("LLM_MODEL", "mock-model")
        return mock
    yield start
    for s in servers:
        s.shutdown()
