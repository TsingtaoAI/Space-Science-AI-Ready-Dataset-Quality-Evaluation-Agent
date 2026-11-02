"""抽查一条结论：按回查链把结论、证据索引、原始文件与图像区域 / 时间区间画在一页 HTML 里。

借鉴发榜方参考代码（OPC-code）的可视化思路——标注框叠加在原图上、事件区间叠加在时序曲线上——但只用标准库：
图像以 data URI 嵌入，几何与曲线用 SVG 画，不需要 numpy / matplotlib，可以在答辩现场任意电脑上运行。

用法：python3 tools/show_evidence.py <输出目录> <数据根目录> <结论编号> [--out 抽查.html]
例如：python3 tools/show_evidence.py out /data/publish B03-HMI_AR_sample_ykt33mm8o2-000008
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import struct
import sys
from pathlib import Path


def find_finding(out: Path, fid: str) -> dict:
    with (out / "06_问题清单" / "findings.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if f'"{fid}"' in line:
                r = json.loads(line)
                if r["findingId"] == fid:
                    return r
    raise SystemExit(f"问题清单中没有结论 {fid}")


def find_index(out: Path, eid: str) -> dict:
    with (out / "07_证据索引" / "evidence_index.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["evidenceId"] == eid:
                return row
    raise SystemExit(f"证据索引中没有 {eid}")


def find_payload(out: Path, did: str, eid: str) -> dict:
    p = out / "07_证据索引" / "evidence" / f"{did}.jsonl"
    with p.open(encoding="utf-8") as f:
        for line in f:
            if f'"{eid}"' in line:
                return json.loads(line)
    return {}


def get_path(doc, ptr: str):
    cur = doc
    for part in ptr.replace("]", "").replace("[", ".").split("."):
        if part == "":
            continue
        if isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def png_size(p: Path) -> tuple[int, int] | None:
    with p.open("rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def data_file_for(label: Path) -> Path | None:
    """标签对应的数据文件：同目录或 ../data/ 下的同名文件（去掉 .json；没有扩展名时按同名前缀找）。"""
    base = label.name[:-5]
    for d in (label.parent, label.parent.parent / "data"):
        if (d / base).is_file():
            return d / base
        hits = sorted(d.glob(base + ".*")) if d.is_dir() else []
        hits = [h for h in hits if h.suffix != ".json"]
        if hits:
            return hits[0]
    return None


def image_svg(img: Path, region, label_doc, pointer, ref_doc=None) -> str:
    size = png_size(img)
    if not size:
        return "<p>数据文件不是 PNG，无法叠加显示。</p>"
    w, h = size
    uri = "data:image/png;base64," + base64.b64encode(img.read_bytes()).decode()
    shapes = []
    if region:
        x1, y1, x2, y2 = region
        sw = max(2, w // 400)
        shapes.append(f"<rect x='{x1}' y='{y1}' width='{max(1, x2 - x1)}' height='{max(1, y2 - y1)}' fill='none' "
                      f"stroke='#ff3b30' stroke-width='{sw}'/>")
        # 结论指向的标注元素：路径以 [i] 结尾即元素本身，否则取上一级
        el = None
        if pointer and label_doc is not None:
            el = get_path(label_doc, pointer if pointer.endswith("]") else pointer.rsplit(".", 1)[0])
        pts = (el.get("bbox_polygon_pixel") or el.get("points")) if isinstance(el, dict) else None
        if isinstance(pts, list) and len(pts) >= 2:
            if len(pts) == 2:
                pts = [pts[0], [pts[1][0], pts[0][1]], pts[1], [pts[0][0], pts[1][1]]]
            path = " ".join(f"{p[0]},{p[1]}" for p in pts)
            shapes.append(f"<polygon points='{path}' fill='rgba(255,204,0,0.15)' stroke='#ffcc00' stroke-width='{sw}'/>")
        # 参考版本（同源版本对照的基准）中同一列表的全部标注，青色虚线，便于看出偏移、漏标、虚标
        ref_boxes = []
        if ref_doc is not None and pointer and "[" in pointer:
            ref_list = get_path(ref_doc, pointer[:pointer.rindex("[")])
            for e in ref_list if isinstance(ref_list, list) else []:
                rp = (e.get("bbox_polygon_pixel") or e.get("points")) if isinstance(e, dict) else None
                if isinstance(rp, list) and len(rp) >= 2:
                    if len(rp) == 2:
                        rp = [rp[0], [rp[1][0], rp[0][1]], rp[1], [rp[0][0], rp[1][1]]]
                    xs, ys = [q[0] for q in rp], [q[1] for q in rp]
                    ref_boxes.append((min(xs), min(ys), max(xs), max(ys)))
                    shapes.insert(0, "<polygon points='" + " ".join(f"{q[0]},{q[1]}" for q in rp) +
                                  f"' fill='none' stroke='#00c8ff' stroke-width='{sw}' stroke-dasharray='{3 * sw},{2 * sw}'/>")
    zoom = ""
    if region:
        x1, y1, x2, y2 = region
        if ref_boxes:   # 放大窗口同时框住离结论位置最近的参考标注，偏移一眼可见
            cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
            b = min(ref_boxes, key=lambda r: ((r[0] + r[2]) / 2 - cx) ** 2 + ((r[1] + r[3]) / 2 - cy) ** 2)
            x1, y1, x2, y2 = min(x1, b[0]), min(y1, b[1]), max(x2, b[2]), max(y2, b[3])
        pad = max(20, int(max(x2 - x1, y2 - y1) * 0.6))
        vb = f"{max(0, x1 - pad)} {max(0, y1 - pad)} {min(w, x2 - x1 + 2 * pad)} {min(h, y2 - y1 + 2 * pad)}"
        zoom = (f"<figure><svg viewBox='{vb}' style='width:100%;max-width:520px;background:#000'>"
                f"<use href='#src'/>{''.join(shapes)}</svg><figcaption>证据区域放大（红框为结论位置，黄色为本版本该标注的几何，青色虚线为参考版本的标注）</figcaption></figure>")
    # 图像只嵌入一次，全图与放大图都引用它
    defs = f"<svg width='0' height='0' style='position:absolute'><defs><image id='src' href='{uri}' width='{w}' height='{h}'/></defs></svg>"
    full = (f"<figure><svg viewBox='0 0 {w} {h}' style='width:100%;max-width:520px;background:#000'>"
            f"<use href='#src'/>{''.join(shapes)}</svg><figcaption>全图 {w}×{h}</figcaption></figure>")
    return f"{defs}<div class='figs'>{full}{zoom}</div>"


def series_svg(csv_path: Path, interval) -> str:
    rows = list(csv.reader(csv_path.open(encoding="utf-8", errors="replace")))
    if len(rows) < 3:
        return "<p>时序文件为空。</p>"
    header, body = rows[0], rows[1:]
    n = len(body)
    out = []
    for ci, name in enumerate(header[1:], 1):
        ys = []
        for r in body:
            try:
                ys.append(float(r[ci]))
            except (ValueError, IndexError):
                ys.append(None)
        good = [y for y in ys if y is not None]
        if not good:
            continue
        lo, hi = min(good), max(good)
        span = (hi - lo) or 1.0
        step = max(1, n // 1200)
        pts = " ".join(f"{i / (n - 1) * 1000:.1f},{90 - (y - lo) / span * 80:.1f}" for i, y in list(enumerate(ys))[::step] if y is not None)
        out.append(f"<svg viewBox='0 0 1000 100' style='width:100%;height:90px'><polyline points='{pts}' fill='none' "
                   f"stroke='#14213D' stroke-width='1'/><text x='4' y='14' font-size='12'>{html.escape(name)}</text></svg>")
    note = f"<p>标注区间：{html.escape(str(interval))}（在时序上的位置以文件起止时刻换算）</p>" if interval else ""
    return note + "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("data_root", type=Path)
    ap.add_argument("finding_id")
    ap.add_argument("--out", dest="html_out", type=Path)
    a = ap.parse_args()
    f = find_finding(a.out, a.finding_id)
    idx = find_index(a.out, f["evidenceRef"])
    payload = find_payload(a.out, f["datasetId"], f["evidenceRef"])
    src = a.data_root / f["datasetId"] / idx["file"] if idx["file"] else None
    checks = []
    label_doc, fig = None, "<p>结论定位到数据集整体，没有单个文件可显示。</p>"
    if src and src.is_file():
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        same = digest == idx["contentHash"]
        checks.append(("文件校验值与证据索引一致", "是" if same else f"否（实际 {digest[:16]}…）"))
        if src.suffix == ".json":
            label_doc = json.loads(src.read_text(encoding="utf-8"))
            data = data_file_for(src)
        else:
            data = src
        region = json.loads(idx["region"]) if idx.get("region") else None
        interval = json.loads(idx["interval"]) if idx.get("interval") else None
        ref_doc = None
        ref_ver = (payload.get("evidence") or {}).get("reference_version")
        if ref_ver and (a.data_root / ref_ver / idx["file"]).is_file() and src.suffix == ".json":
            ref_doc = json.loads((a.data_root / ref_ver / idx["file"]).read_text(encoding="utf-8"))
            checks.append(("参考版本", f"{ref_ver}（青色虚线）"))
        if data and data.suffix.lower() == ".png":
            fig = image_svg(data, region, label_doc, idx.get("pointer"), ref_doc)
        elif data and data.suffix.lower() == ".csv":
            fig = series_svg(data, interval)
    else:
        checks.append(("原始文件", f"在数据根目录下找不到 {idx['file']}"))
    pointed = get_path(label_doc, idx["pointer"]) if label_doc is not None and idx.get("pointer") else None
    e = html.escape
    page = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>抽查 {e(a.finding_id)}</title>
<style>body{{font-family:system-ui,'PingFang SC',sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#14213D}}
table{{border-collapse:collapse;width:100%;margin:8px 0 18px}}td{{border:1px solid #ccc;padding:4px 8px;font-size:14px;vertical-align:top}}
td:first-child{{width:180px;background:#f3f0e8}}pre{{background:#f6f4ee;padding:10px;white-space:pre-wrap;font-size:12px}}
.figs{{display:flex;gap:16px;flex-wrap:wrap}}figure{{margin:0;flex:1;min-width:280px}}figcaption{{font-size:12px;color:#555}}</style></head><body>
<h1>抽查 · {e(a.finding_id)}</h1>
<h2>① 结论（06_问题清单/findings.jsonl）</h2><table>
<tr><td>状态 / 缺陷代码</td><td>{e(f['status'])} · {e(f['defectType'])} {e(f.get('defectTypeZh', ''))}</td></tr>
<tr><td>说明</td><td>{e(f['message'])}</td></tr>
<tr><td>检查编号 / 规则版本</td><td>{e(f['checkId'])} / {e(f['ruleVersion'])}</td></tr>
<tr><td>程度</td><td>{e(json.dumps(f.get('severity'), ensure_ascii=False))}</td></tr></table>
<h2>② 证据索引（07_证据索引/evidence_index.csv）</h2><table>
<tr><td>证据编号</td><td>{e(idx['evidenceId'])}</td></tr><tr><td>定位</td><td>{e(idx['locatorType'])}：{e(idx['locator'])}</td></tr>
<tr><td>文件校验值</td><td>{e(idx['contentHash'])}</td></tr><tr><td>运行编号</td><td>{e(idx['capturedBy'])}</td></tr>
{''.join(f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in checks)}</table>
<h2>③ 原始文件</h2>{fig}
<h3>标签中该位置的内容（{e(idx.get('pointer') or '—')}）</h3><pre>{e(json.dumps(pointed, ensure_ascii=False, indent=2)) if pointed is not None else '—'}</pre>
<h3>证据（07_证据索引/evidence/{e(f['datasetId'])}.jsonl）</h3><pre>{e(json.dumps(payload.get('evidence', {}), ensure_ascii=False, indent=2))}</pre>
</body></html>"""
    dst = a.html_out or Path(f"抽查-{a.finding_id}.html")
    dst.write_text(page, encoding="utf-8")
    print(f"已生成 {dst}（{dst.stat().st_size // 1024} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
