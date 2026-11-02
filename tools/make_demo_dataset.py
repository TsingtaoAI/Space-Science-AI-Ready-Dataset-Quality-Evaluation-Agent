"""生成录屏用的合成演示数据集（不含任何比赛数据，可以发给外部模型服务）。

仿照极光矩形标注的目录与字段结构，12 个样本、全部为合成值；附一份说明文档，其中故意写错两处声明：
样本数写成 20（实际 12）、声称包含 TPA 类（实际没有）、声称覆盖到 3 月 15 日（实际只有 2 月 15 日），
另在数据中注入一处类别错标与一处尺寸声明错误，供演示常规检查。

另有 --cme：生成 LASCO C2 风格的合成数据集（4 组 × 3 帧，同组帧共享 image_time 与事件标注），注入一处时刻抖动、
一处类别改写，并附 file_list.txt（漏列一个文件、多列一个不存在的文件）与 dataset_summary.json，用于演示借鉴发榜方
参考代码后加入的跨样本一致检查（C05 / B05）、文件清单核对（P04）与抽查工具。

用法：python3 tools/make_demo_dataset.py <输出目录> [--cme]
"""
from __future__ import annotations

import json
import struct
import sys
import zlib
from pathlib import Path

README = """# DMSP 极光演示数据集（合成）

本数据集共 20 个样本，包含 PCA 与 TPA 两类极光。
观测时间覆盖 2014-02-15 至 2014-03-15。
图像为 39×35 像素的 PNG 文件，每个样本附一份 JSON 标签。
"""


def png(w: int, h: int, tag: str) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00" * (w * 3) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"tag\x00" + tag.encode()) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def label(name: str, cls: str = "PCA", w: int = 39, h: int = 35) -> dict:
    return {
        "label_header": {"fileName": name, "labelVersion": "1.0.0.0", "annotationMethod": ["semi-auto"]},
        "basic_annotation": {"fileName": name, "relativePath": ".", "modality": ["image"], "fileFormat": "PNG",
                             "startTime": "2014-02-15T00:00:00Z", "endTime": "2014-02-15T23:59:59Z",
                             "imageHeight": h, "imageWidth": w},
        "science_annotation": {"specialShapes": [{"label": cls, "points": [[5.5, 5.5], [20.0, 20.0]],
                                                  "shape_type": "rectangle", "flags": {}}]},
    }


def cme_label(name: str, frame_time: str, center: str, cme_time: str, types=("CME",)) -> dict:
    m, v = 3.0e15, 520.0
    return {
        "label_header": {"fileName": name, "labelVersion": "V1.0.0.0", "annotationMethod": "auto"},
        "basic_annotation": {"fileName": name, "relativePath": "data/", "modality": "image", "fileFormat": "png",
                             "startTime": frame_time, "endTime": frame_time},
        "science_annotation": {"dateObs": frame_time},
        "hek_event_annotation": {"has_events": True, "event_count": 1 if "CME" in types else 0, "image_time": center,
                                 "event_types": list(types),
                                 "matched_events": [{"cme_time": cme_time, "central_pa": 270, "width": 60, "mpa": 265,
                                                     "linear_speed": v, "mass": m, "kinetic_energy": 0.5 * m * (v * 1e5) ** 2}]},
    }


def make_cme(out: Path) -> Path:
    ds = out / "CME_C2_demo"
    names = []
    for g in range(4):
        center = f"2012-05-0{g + 1}T12:00:00"
        cme_time = f"2012-05-0{g + 1}T11:24:00"
        for k, frame in enumerate(("11:48:00", "12:00:00", "12:12:00")):
            ft = f"2012-05-0{g + 1}T{frame}"
            name = f"c2_2012050{g + 1}_{frame.replace(':', '')}.png"
            ct = "2012-05-02T10:24:00" if (g, k) == (1, 2) else cme_time       # 时刻抖动 1 小时（B05）
            types = ("FL",) if (g, k) == (2, 0) else ("CME",)                   # 类别被改写（C05）
            lab = ds / "label" / f"{name}.json"
            lab.parent.mkdir(parents=True, exist_ok=True)
            lab.write_text(json.dumps(cme_label(name, ft, center, ct, types), ensure_ascii=False), encoding="utf-8")
            (ds / "data").mkdir(parents=True, exist_ok=True)
            (ds / "data" / name).write_bytes(png(64, 64, name))
            names.append(name)
    listed = [n for n in names if n != "c2_20120504_120000.png"] + ["c2_20120509_120000.png"]   # P04：漏列一个、多列一个
    (ds / "file_list.txt").write_text("\n".join(listed) + "\n", encoding="utf-8")
    (ds / "dataset_summary.json").write_text(json.dumps({"frames": 12, "groups": 4, "note": "合成演示数据"},
                                                        ensure_ascii=False), encoding="utf-8")
    return ds


def main(out: Path) -> int:
    ds = out / "DMSP_SSUSI_demo"
    ds.mkdir(parents=True, exist_ok=True)
    for i in range(1, 13):
        name = f"demo{i:02d}.png"
        cls = "__OOV__" if i == 3 else "PCA"                 # 类别错标（C01）
        w, h = (390, 350) if i == 5 else (39, 35)             # 尺寸声明错误（D02）
        (ds / f"demo{i:02d}.json").write_text(json.dumps(label(name, cls, w, h), ensure_ascii=False), encoding="utf-8")
        (ds / name).write_bytes(png(39, 35, name))
    (ds / "README.md").write_text(README, encoding="utf-8")
    print(f"演示数据集已生成：{ds}（12 个样本 + README.md）")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = Path(args[0] if args else "demo_data")
    if "--cme" in sys.argv:
        print(f"CME 演示数据集已生成：{make_cme(target)}（4 组 × 3 帧 + file_list.txt + dataset_summary.json）")
        sys.exit(0)
    sys.exit(main(target))
