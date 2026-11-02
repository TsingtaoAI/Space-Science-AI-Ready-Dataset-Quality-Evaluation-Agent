#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OPC 数据可视化 —— 独立简洁版。
用法::

    # 第 10 个含事件样本 -> 单张 PNG
    python viz_aia_ar.py --index 10 --out out/aia_10.png
    # 指定文件名
    python viz_aia_ar.py --file aia_20100605_032600_0094A.png
    # 列出含事件样本
    python viz_aia_ar.py --list --limit 20
    # 只打印标注文本摘要，不出图
    python viz_aia_ar.py --file xxx.png --no-plot
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Polygon as MplPolygon
from matplotlib.lines import Line2D
from PIL import Image

# 中文字体：优先本机/源端可用字体，缺失时回退（英文标签仍可读）
for _cand in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei",
              "Source Han Sans SC", "DejaVu Sans"):
    try:
        font_manager.findfont(_cand, fallback_to_default=False)
        # 文本面板显式指定 family="monospace"，故 monospace 家族也必须挂上中文字体
        plt.rcParams["font.sans-serif"] = [_cand] + list(plt.rcParams["font.sans-serif"])
        plt.rcParams["font.monospace"] = [_cand] + list(plt.rcParams["font.monospace"])
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

# ---------------------------------------------------------------- 默认配置
DEFAULT_ROOT = ""
DATASET_NAME = ""
IMG_PER_PANEL = 1024          # 4096px 原图下采样到该边长再显示（bbox 坐标按比例缩放）
DEFAULT_ZOOM = 4# 每个事件最多放大显示几个
ZOOM_PAD_PX = 90# 放大裁剪框外扩像素（原图坐标）
FALLBACK_DISK_R = 1620.0      # 4096px 全画幅下 AIA 日面半径的经验值（自动定标失败时兜底）
ZOOM_BOX = 420# 放大子图的目标边长（原图像素）


# ================================================================ 基础工具
def load_label(root: str, name: str) -> Dict[str, Any]:
    """读取 label/<name>.png.json。"""
    path = os.path.join(root, "label", name + ".png.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def list_labels(root: str) -> List[str]:
    lb = os.path.join(root, "label")
    if not os.path.isdir(lb):
        raise FileNotFoundError(lb)
    return sorted(f[:-len(".png.json")] for f in os.listdir(lb) if f.endswith(".png.json"))


def load_gray(root: str, name: str) -> Tuple[np.ndarray, str]:
    """读取 PNG 并转 float64 灰度（原图 4096x4096）。"""
    path = os.path.join(root, "data", name if name.endswith(".png") else name + ".png")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with Image.open(path) as im:
        arr = np.asarray(im.convert("L"), dtype=np.float64)
    return arr, path


def stretch(img: np.ndarray, mask: Optional[np.ndarray] = None,
            p_low: float = 0.5, p_high: float = 99.9,
            asinh_gain: float = 400.0) -> np.ndarray:
    """百分位裁剪 + asinh 拉伸，返回 0~1 归一化数组（显示用）。

    AIA 94A 动态范围极大（活动区极亮 + 临边增亮 + 日面外近零），
    纯线性拉伸会把日面之外压成全黑、日面内部又过曝，故用 asinh
    压缩高动态范围（等价于 sqrt/log 型压缩，无参数魔数依赖）。
    """
    ref = img[mask] if mask is not None else img
    ref = ref[np.isfinite(ref)]
    lo, hi = np.percentile(ref, [p_low, p_high])
    if hi <= lo:
        lo, hi = float(np.nanmin(ref)), float(np.nanmax(ref))
    if hi <= lo:
        return np.zeros_like(img)
    x = np.clip(img, lo, hi)
    span = max(hi - lo, 1e-12)
    x = (x - lo) / span
    g = max(asinh_gain, 1e-6)
    return np.arcsinh(x * g) / np.arcsinh(g)


def detect_disk(img: np.ndarray) -> Tuple[float, float, float]:
    """自动定标日面圆盘（中心 x, 中心 y, 半径），纯像素方法，不用任何观测信息。

    做法：以画幅中心为初始猜测，统计径向平均亮度曲线，在
    [0.30, 0.56] * 半边长 区间内寻找**最外侧**的强下降沿（日面边缘的亮度骤降）。
    """
    h, w = img.shape
    cx = cy = (w - 1) / 2.0
    yy, xx = np.mgrid[:h, :w]
    r = np.hypot(xx - cx, yy - cy)

    nb = 256
    edges = np.linspace(0, 0.72 * w, nb + 1)
    idx = np.digitize(r.ravel(), edges) - 1
    prof = np.bincount(idx, weights=img.ravel(), minlength=nb) / \
        np.maximum(np.bincount(idx, minlength=nb), 1)
    centers = 0.5 * (edges[:-1] + edges[1:])

    lo_i = np.searchsorted(centers, 0.30 * w)
    hi_i = np.searchsorted(centers, 0.56 * w)
    seg = prof[lo_i:hi_i]
    if seg.size < 5:
        return cx, cy, FALLBACK_DISK_R

    d = np.diff(prof)
    lo_i2 = max(lo_i, 1)
    cand = [(d[i], centers[i + 1]) for i in range(lo_i2, hi_i - 1)]
    # 阈值：最大降幅的 35%，取满足阈值的最外侧候选
    thr = max(d[lo_i2:hi_i - 1].max() * 0.35, 1e-6)
    outer = [rr for dd, rr in cand if dd <= -thr]
    radius = outer[-1] if outer else FALLBACK_DISK_R
    return cx, cy, float(radius)


def disk_mask(shape: Tuple[int, int], cx: float, cy: float,
              r: float) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[:h, :w]
    return np.hypot(xx - cx, yy - cy) <= r


# ================================================================ 标签解析
def events_of(label: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(label.get("hek_event_annotation", {}).get("matched_events", []) or [])


def polygons_of(ev: Dict[str, Any]) -> Optional[np.ndarray]:
    """从事件标注取bbox_polygon_pixel -> (N,2) 数组；无/非法返回 None。"""
    poly = ev.get("bbox_polygon_pixel")
    if not poly:
        return None
    a = np.asarray(poly, dtype=float)
    if a.ndim != 2 or a.shape[0] < 3 or a.shape[1] != 2:
        return None
    if not np.isfinite(a).all():
        return None
    return a


def event_tag(ev: Dict[str, Any], i: int) -> str:
    """事件短标签：优先用 SOL_standard 尾段（HelioLocator LxxxCxxx）。"""
    sol = ev.get("SOL_standard") or ""
    if "L" in sol:
        tail = sol.split("L", 1)[1]
        return "AR#%d %s" % (i + 1, tail)
    kb = ev.get("kb_archivid") or ""
    if kb:
        return "AR#%d %s" % (i + 1, kb.rsplit("_", 1)[-1])
    return "AR#%d" % (i + 1)


def fmt(val: Any, digits: int = 3, unit: str = "") -> str:
    if val is None or val == "":
        return "-"
    if isinstance(val, (int, float)):
        s = ("%%.%dg" % digits) % val
        return s + unit
    return "%s%s" % (val, unit)


def sci_of(label: Dict[str, Any]) -> Dict[str, Any]:
    return label.get("science_annotation", {}) or {}


def basic_of(label: Dict[str, Any]) -> Dict[str, Any]:
    return label.get("basic_annotation", {}) or {}


# ================================================================ 绘图
COLOR_CYCLE = ["#ff5c5c", "#ffd23f", "#4ade80", "#38bdf8", "#c084fc",
                "#fb923c", "#f472b6", "#a3e635"]


def overlay_events(ax, img_disp: np.ndarray, events: Sequence[Dict[str, Any]],
                   scale: float, title: str) -> int:
    """在降采样图上叠加所有 bbox 多边形，返回有效标注数。"""
    n_ok = 0
    ax.imshow(img_disp, cmap="gray", vmin=0, vmax=1, interpolation="nearest",
              zorder=1)
    for i, ev in enumerate(events):
        poly = polygons_of(ev)
        if poly is None:
            continue
        n_ok += 1
        col = COLOR_CYCLE[i % len(COLOR_CYCLE)]
        p = poly * scale
        ax.add_patch(MplPolygon(p, closed=True, fill=False, lw=1.8,
                                edgecolor=col, alpha=0.95, zorder=5))
        ax.annotate(event_tag(ev, i), xy=(p[:, 0].mean(), p[:, 1].min()),
                    xytext=(0, 4), textcoords="offset points",
                    color=col, fontsize=7.5, weight="bold", zorder=6,
                    bbox=dict(boxstyle="round,pad=0.15", fc="black",
                              ec=col, alpha=0.55, lw=0.6))
    ax.set_xlim(0, img_disp.shape[1])
    ax.set_ylim(img_disp.shape[0], 0)          # 像素坐标：y 向下
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("%s   [%s bbox / 共 %d 事件]"
                 % (title, n_ok, len(events)),
                 fontsize=10, color="#e6e6e6", pad=6)
    for sp in ax.spines.values():
        sp.set_color("#555555")
    return n_ok


def info_panel(ax, label: Dict[str, Any], events: Sequence[Dict[str, Any]],
               root: str, name: str, dy: float = 0.0235) -> None:
    """右侧文本面板：文件名/观测时间（仅显示）+ 事件元数据表。

    `dy` 为行距（轴分数）。轴是物理英寸高的，图变矮时固定分数会让行挤在一起，
    故由 render 按 figsize 传入，保证行距恒为 ~0.225 英寸。
    """
    ax.axis("off")
    b = basic_of(label)
    s = sci_of(label)
    hek = label.get("hek_event_annotation", {}) or {}

    lines = [
        ("样本", name),
        ("观测时间", "%s → %s" % (b.get("startTime"), b.get("endTime"))),
        ("通道(wavelnth)", "%s / instrume=%s" % (s.get("wavelnth"), s.get("instrume"))),
        ("事件类型", ",".join(hek.get("event_types", []) or []) or "-"),
        ("has_events", str(hek.get("has_events"))),
        ("event_count", str(hek.get("event_count"))),
        ("有效多边形", "%d / %d" % (sum(polygons_of(e) is not None for e in events),
                                   len(events))),
        ("数据根", root if len(root) <= 48 else "…" + root[-47:]),
    ]
    y = 0.988
    for k, v in lines:
        ax.text(0.0, y, k, fontsize=8, color="#7dd3fc", va="top",
                transform=ax.transAxes, family="monospace")
        val = str(v)
        if len(val) > 46:
            val = val[:23] + "…" + val[-22:]
        ax.text(0.36, y, val, fontsize=8, color="#e5e7eb", va="top",
                transform=ax.transAxes, family="monospace")
        y -= dy

    # 事件属性表（AR 的共性字段）
    rows = []
    for i, ev in enumerate(events):
        rows.append((
            event_tag(ev, i),
            fmt(ev.get("ar_polarity"), 4),
            fmt(ev.get("area_atdiskcenter"), 4),
            fmt(ev.get("unsignedflux"), 4),
            fmt(ev.get("maxmagfieldstrength"), 4),
            str(ev.get("obs_channelid") or "-"),
        ))
    if rows:
        y -= dy * 0.42
        ax.text(0.0, y, "AR 属性（来自 HEK/SMART）", fontsize=8.5,
                color="#fbbf24", va="top", transform=ax.transAxes,
                family="monospace", weight="bold")
        y -= dy * 1.10
        hdr = ("标签", "极性", "面积极km2", "无符号磁通", "最大场强", "通道")
        colw = (0.22, 0.09, 0.16, 0.16, 0.14, 0.13)
        x = 0.0
        for wdt, htxt in zip(colw, hdr):
            ax.text(x, y, htxt, fontsize=7.0, color="#9ca3af", va="top",
                    transform=ax.transAxes, family="monospace")
            x += wdt
        y -= dy * 0.90
        for r_i, row in enumerate(rows):
            x = 0.0
            for j, (wdt, cell) in enumerate(zip(colw, row)):
                ax.text(x, y, cell, fontsize=6.6,
                        color=COLOR_CYCLE[r_i % len(COLOR_CYCLE)] if j == 0 else "#d1d5db",
                        va="top", transform=ax.transAxes, family="monospace")
                x += wdt
            y -= dy * 0.80
            if y < dy * 0.6:
                break


def zoom_panel(ax, img: np.ndarray, poly: np.ndarray, tag: str,
               color: str, target: int = ZOOM_BOX) -> Tuple[float, float]:
    """事件区域放大图（原始像素级裁剪，不下采样——保证细节）。"""
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    pad = ZOOM_PAD_PX
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = max((x1 - x0) / 2.0 + pad, (y1 - y0) / 2.0 + pad, target / 2.0)
    h, w = img.shape
    ix0 = int(np.clip(cx - half, 0, w - 1)); ix1 = int(np.clip(cx + half, ix0 + 8, w))
    iy0 = int(np.clip(cy - half, 0, h - 1)); iy1 = int(np.clip(cy + half, iy0 + 8, h))
    crop = img[iy0:iy1, ix0:ix1]
    lo, hi = np.percentile(crop, [0.5, 99.9])
    norm = np.clip((crop - lo) / max(hi - lo, 1e-12), 0, 1)
    ax.imshow(np.arcsinh(norm * 60.0) / np.arcsinh(60.0), cmap="gray",
              vmin=0, vmax=1, interpolation="nearest")
    sc = 1.0
    ax.add_patch(MplPolygon((poly - [ix0, iy0]) * sc, closed=True, fill=False,
                            lw=1.6, edgecolor=color))
    ax.set_xlim(0, ix1 - ix0); ax.set_ylim(iy1 - iy0, 0)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(tag, fontsize=8, color=color, pad=3)
    for sp in ax.spines.values():
        sp.set_color(color); sp.set_alpha(0.6)
    return float(ix0), float(iy0)


# ================================================================ 主流程
def pick_sample(root: str, index: int, has_event_only: bool,
                name: Optional[str]) -> str:
    names = list_labels(root)
    if name:
        return name
    if has_event_only:
        cand = []
        for nm in names:
            lb = load_label(root, nm)
            if lb.get("hek_event_annotation", {}).get("has_events"):
                cand.append(nm)
        names = cand or names
    if not names:
        raise SystemExit("no label found under %s" % os.path.join(root, "label"))
    return names[index % len(names)]


def summarize(label: Dict[str, Any], events: Sequence[Dict[str, Any]]) -> str:
    b = basic_of(label)
    hek = label.get("hek_event_annotation", {}) or {}
    out = [
        "=" * 78,
        "文件      : %s" % b.get("fileName"),
        "观测时间  : %s" % b.get("startTime"),
        "science   : instrume=%s wavelnth=%s" % (
            sci_of(label).get("instrume"), sci_of(label).get("wavelnth")),
        "事件      : has_events=%s event_count=%s types=%s" % (
            hek.get("has_events"), hek.get("event_count"),
            ",".join(hek.get("event_types", []) or [])),
        "-" * 78,
    ]
    for i, ev in enumerate(events):
        poly = polygons_of(ev)
        geo = "bbox 缺失/非法"
        if poly is not None:
            geo = "bbox=(%d 点) x[%.0f,%.0f] y[%.0f,%.0f]" % (
                poly.shape[0], poly[:, 0].min(), poly[:, 0].max(),
                poly[:, 1].min(), poly[:, 1].max())
        out.append("[%d] %s" % (i + 1, event_tag(ev, i)))
        out.append("     %s" % geo)
        for k in ("SOL_standard", "event_starttime", "event_endtime", "ar_polarity",
                  "area_atdiskcenter", "unsignedflux", "maxmagfieldstrength",
                  "obs_observatory", "obs_instrument", "obs_channelid"):
            if k in ev:
                out.append("     %-22s %s" % (k, fmt(ev.get(k))))
    out.append("=" * 78)
    return "\n".join(out)


def render(root: str, name: str, out_png: str, zoom: int = DEFAULT_ZOOM) -> None:
    label = load_label(root, name)
    events = events_of(label)
    img, data_path = load_gray(root, name)
    cx, cy, r_disk = detect_disk(img)

    disp = np.asarray(Image.fromarray((stretch(img) * 255).astype(np.uint8))
                      .resize((IMG_PER_PANEL, IMG_PER_PANEL), Image.LANCZOS),
                      dtype=float) / 255.0
    scale = disp.shape[1] / img.shape[1]

    print(summarize(label, events))
    print("图像      : %s  shape=%s" % (data_path, img.shape))
    print("日面定标  : center=(%.1f, %.1f)  R=%.1f px  (R/W=%.4f, 自动像素定标)"
          % (cx, cy, r_disk, r_disk / img.shape[1]))

    with plt.style.context("dark_background"):
        n_zoom = max(0, min(zoom, len(events)))
        # 无事件样本没有放大子图，压掉下排避免大片空白
        has_row2 = n_zoom > 0
        fig = plt.figure(figsize=(16, 9.6 if has_row2 else 5.6), dpi=110,
                         facecolor="#0b0f14")
        gs = fig.add_gridspec(2, 4, height_ratios=[1.3, 1.0 if has_row2 else 0.02],
                              width_ratios=[1.0, 1.0, 1.0, 1.55],
                              hspace=0.24, wspace=0.10,
                              left=0.015, right=0.988,
                              top=0.92 if has_row2 else 0.84, bottom=0.07)

        ax_full = fig.add_subplot(gs[0, 0:2])
        overlay_events(ax_full, disp, events, scale,
                       "原图 + AR bbox_polygon_pixel 叠加   [%s]" % name)

        # 图例
        handles = [Line2D([0], [0], color=COLOR_CYCLE[i % len(COLOR_CYCLE)], lw=2,
                          label=event_tag(ev, i))
                   for i, ev in enumerate(events)]
        if handles:
            ax_full.legend(handles=handles, loc="lower left", fontsize=7.5,
                           facecolor="#111827", edgecolor="#374151",
                           labelcolor="#e5e7eb", framealpha=0.85)

        # 下排：事件区域放大（最多 4 个）
        for k, col in enumerate([0, 1, 2]):
            axz = fig.add_subplot(gs[1, col])
            if k >= n_zoom:
                axz.axis("off")
                continue
            poly = polygons_of(events[k])
            if poly is None:
                axz.axis("off")
                axz.text(0.5, 0.5, "事件 %d 无 bbox\n（标注缺多边形）" % (k + 1),
                         color="#9ca3af", ha="center", va="center",
                         fontsize=9, transform=axz.transAxes)
                continue
            zoom_panel(axz, img, poly, event_tag(events[k], k),
                       COLOR_CYCLE[k % len(COLOR_CYCLE)])

        # 上排中：亮度剖面 + 日面定标示意
        axp = fig.add_subplot(gs[0, 2])
        prof = []
        h, w = img.shape
        yy, xx = np.mgrid[0:h:4, 0:w:4]
        rr = np.hypot(xx - cx, yy - cy)
        ii = np.clip((rr / 4).astype(int), 0, 1023)
        g4 = img[::4, ::4]
        cnt = np.bincount(ii.ravel(), minlength=1024)
        ssum = np.bincount(ii.ravel(), weights=g4.ravel(), minlength=1024)
        prof = np.divide(ssum, np.maximum(cnt, 1))
        pr = np.arange(1024) * 4.0
        axp.plot(pr, prof, lw=1.0, color="#38bdf8")
        axp.axvline(r_disk, color="#f87171", ls="--", lw=1.2,
                    label="R_disk=%.0f px" % r_disk)
        axp.set_xlim(0, 0.72 * w)
        axp.set_xlabel("半径 (px, 图像中心起)", fontsize=7.5)
        axp.set_ylabel("径向平均亮度", fontsize=7.5)
        axp.tick_params(labelsize=7)
        axp.legend(fontsize=7, facecolor="#111827", edgecolor="#374151",
                   labelcolor="#e5e7eb")
        axp.set_title("径向亮度剖面（自动定标依据）", fontsize=9, pad=5)
        axp.grid(alpha=0.2, lw=0.5)

        axax = fig.add_subplot(gs[0:2, 3])
        info_panel(axax, label, events, root, name,
                   dy=0.225 / (fig.get_figheight() * (0.92 if has_row2 else 0.84)))

        fig.suptitle("OPC · %s 可视化  —  纯像素几何（无 sunpy / 无观测时间依赖）"
                     % DATASET_NAME, fontsize=13, color="#f9fafb",
                     y=0.965 if has_row2 else 0.955)
        os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
        fig.savefig(out_png, facecolor=fig.get_facecolor())
        print("输出      : %s" % out_png)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="OPC AIA 活动区样例数据可视化")
    ap.add_argument("--root", default=DEFAULT_ROOT, help="数据集根目录（含 data/ 与 label/）")
    ap.add_argument("--file", help="指定数据文件名（不含扩展名亦可）")
    ap.add_argument("--index", type=int, default=0, help="按排序取第 N 个样本")
    ap.add_argument("--any", action="store_true", help="允许选到无事件样本（默认优先取含事件样本）")
    ap.add_argument("--zoom", type=int, default=DEFAULT_ZOOM, help="放大子图个数上限")
    ap.add_argument("--out", default="viz_aia.png", help="输出 PNG 路径")
    ap.add_argument("--list", action="store_true", help="列出样本名")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--no-plot", action="store_true", help="只打印标注摘要")
    args = ap.parse_args(argv)

    if args.list:
        for i, nm in enumerate(list_labels(args.root)[: args.limit]):
            print("%6d  %s" % (i, nm))
        return 0

    fn = args.file
    if fn and fn.endswith(".png"):
        fn = fn[:-4]
    name = pick_sample(args.root, args.index, not args.any, fn)

    if args.no_plot:
        label = load_label(args.root, name)
        print(summarize(label, events_of(label)))
        return 0

    render(args.root, name, args.out, zoom=max(0, args.zoom))
    return 0


if __name__ == "__main__":
    sys.exit(main())