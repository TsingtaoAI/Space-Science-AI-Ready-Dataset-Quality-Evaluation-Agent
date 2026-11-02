#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OPC · 活动区样例数据可视化 —— 独立简洁版。

  1. **无符号幅值显示（默认）**：HMI_AR_sample 的 PNG 是 |B_los| 幅值图 ——
     实测像元非负、日面内中位数为 0、约 26% 像元恰为 0（宁静区被压到零）。
     因此默认单极色表 + log1p 压缩，不用发散色表（那会凭空造出「极性」）。
     若换成有符号磁图，加 `--signed` 切为蓝-灰-红发散色表 + 中位数经验零点。
  2. 显式给出**日面遮罩**（自动定标圆盘外置灰）：标注 bbox 只应落在日面内，
     越界框一眼可辨。
  3. 用**磁斑阈值等值线**（日面内分位阈值，无符号数据下零等值线无意义）
     替代 AIA 版没有的「零场线」面板。
  4. 展示 SHARP 独有参数（剪切角、螺度、扭转参数 alpha、垂直电流密度…），
     AIA 版只有 SMART 的面积/磁通/场强。
  5. 额外输出 **bbox 内磁通像元统计**（覆盖度、超阈像元占比、强度分位），
     替代无法从无符号 PNG 得到的极性构成。

设计约束：
  * 几何全部来自 PNG 像素 + `bbox_polygon_pixel`。
  * 观测时间仅用于显示与样本检索。
  * 依赖仅 numpy + matplotlib + pillow。

用法::

    python viz_hmi_ar.py --index 0 --out out/hmi.png
    python viz_hmi_ar.py --file hmi_20100501_090000.png
    python viz_hmi_ar.py --list --limit 20
    python viz_hmi_ar.py --file xxx.png --no-plot
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
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.patches import Polygon as MplPolygon, Circle
from matplotlib.lines import Line2D
from PIL import Image

for _cand in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
              "WenQuanYi Zen Hei", "Source Han Sans SC", "DejaVu Sans"):
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
IMG_PER_PANEL = 1024
DEFAULT_ZOOM = 3
ZOOM_PAD_PX = 110
ZOOM_BOX = 460
FALLBACK_DISK_R = 1888.0     # 4096画幅 HMI 日面半径经验值（自动定标失败时兜底）
FLUX_PCTL = 99.0            # 磁斑阈值等值线：日面内该分位（无量纲，像素灰度）

COLOR_CYCLE = ["#ff5c5c", "#ffd23f", "#4ade80", "#38bdf8", "#c084fc",
               "#fb923c", "#f472b6", "#a3e635"]

# 单极色表（|B| 幅值：金->红->黑，逆 astro 的 asinh 观感），自绘以免版本差异
CMAP_ABS = LinearSegmentedColormap.from_list("hmi_abs", [
    (0.00, "#000000"), (0.15, "#1b0a12"), (0.35, "#5c0f1e"),
    (0.55, "#a81c22"), (0.72, "#e0601f"), (0.86, "#f7c04a"),
    (1.00, "#fdfbe8"),
], N=256)

# 有符号模式下的发散色表
CMAP_DIV = LinearSegmentedColormap.from_list("hmi_div", [
    (0.00, "#0b3a8c"), (0.20, "#2f6fd0"), (0.40, "#9dc4f0"),
    (0.50, "#202020"), (0.60, "#f0b49d"), (0.80, "#d0342f"),
    (1.00, "#7a0d0a"),
], N=256)


# ================================================================ 基础工具
def load_label(root: str, name: str) -> Dict[str, Any]:
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
    fn = name if name.endswith(".png") else name + ".png"
    path = os.path.join(root, "data", fn)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with Image.open(path) as im:
        arr = np.asarray(im.convert("L"), dtype=np.float64)
    return arr, path


def detect_disk(img: np.ndarray) -> Tuple[float, float, float]:
    """自动定标日面圆盘（纯像素法）。

    以画幅中心为初始猜测，统计径向平均亮度，在 [0.30, 0.56]*半边长 区间内
    寻找最外侧的强下降沿。HMI 磁图日面外恒为 0，下降沿极锐利，定标很稳。
    """
    h, w = img.shape
    cx = cy = (w - 1) / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - cx, yy - cy)

    nb = 256
    edges = np.linspace(0, 0.72 * w, nb + 1)
    idx = np.digitize(r.ravel(), edges) - 1
    prof = np.bincount(idx, weights=img.ravel(), minlength=nb) / \
        np.maximum(np.bincount(idx, minlength=nb), 1)
    centers = 0.5 * (edges[:-1] + edges[1:])

    lo_i = max(int(np.searchsorted(centers, 0.30 * w)), 1)
    hi_i = int(np.searchsorted(centers, 0.56 * w))
    if hi_i - lo_i < 5:
        return cx, cy, FALLBACK_DISK_R

    d = np.diff(prof)
    thr = max(-d[lo_i:hi_i - 1].min() * 0.35, 1e-6)
    outer = [centers[i + 1] for i in range(lo_i, hi_i - 1) if d[i] <= -thr]
    radius = outer[-1] if outer else FALLBACK_DISK_R
    return cx, cy, float(radius)


def disk_mask(shape: Tuple[int, int], cx: float, cy: float, r: float) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    return np.hypot(xx - cx, yy - cy) <= r


def normalize_mag(img: np.ndarray, mask: np.ndarray,
                  signed: bool) -> Tuple[np.ndarray, Dict[str, float]]:
    """把磁图归一化到色表显示域，返回 (数组, 定标信息)。

    无符号（默认）：y = log1p(x / p50pos) / log1p(hi / p50pos)
        —— p50pos 为日面内**正值**像元的中位数（宁静区基线），
           log1p 同时把被压到 0 的宁静区和 200+ DN 的磁斑都拉到可分辨区。
    有符号（--signed）：以日面内中位数为零点做双极对称归一化到 [-1, 1]。
    """
    v = img[mask]
    v = v[np.isfinite(v)]
    if v.size == 0:
        return np.zeros_like(img), {"zero": 0.0, "base": 1.0, "hi": 1.0}

    if signed:
        zero = float(np.median(v))
        amp = float(np.percentile(np.abs(v - zero), 99.5)) or 1.0
        out = np.clip((img - zero) / amp, -1.0, 1.0)
        return out, {"zero": zero, "base": 0.0, "hi": amp}

    pos = v[v > 0]
    base = float(np.median(pos)) if pos.size else 1.0
    base = max(base, 1e-6)
    hi = float(np.percentile(v, FLUX_PCTL)) or 1.0
    hi = max(hi, base * 2.0)
    out = np.log1p(np.clip(img, 0, None) / base) / np.log1p(hi / base)
    return np.clip(out, 0.0, 1.0), {"zero": 0.0, "base": base, "hi": hi}


# ================================================================ 标签解析
def events_of(label: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(label.get("hek_event_annotation", {}).get("matched_events", []) or [])


def polygons_of(ev: Dict[str, Any]) -> Optional[np.ndarray]:
    poly = ev.get("bbox_polygon_pixel")
    if not poly:
        return None
    a = np.asarray(poly, dtype=float)
    if a.ndim != 2 or a.shape[0] < 3 or a.shape[1] != 2 or not np.isfinite(a).all():
        return None
    return a


def event_tag(ev: Dict[str, Any], i: int) -> str:
    sol = ev.get("SOL_standard") or ""
    if "L" in sol:
        return "AR#%d %s" % (i + 1, sol.split("L", 1)[1])
    kb = ev.get("kb_archivid") or ""
    if kb:
        return "AR#%d %s" % (i + 1, kb.rsplit("_", 1)[-1])
    return "AR#%d" % (i + 1)


def fmt(val: Any, digits: int = 4, unit: str = "") -> str:
    if val is None or val == "":
        return "-"
    if isinstance(val, (int, float)):
        return (("%%.%dg" % digits) % val) + unit
    return "%s%s" % (val, unit)


def sci_of(label: Dict[str, Any]) -> Dict[str, Any]:
    return label.get("science_annotation", {}) or {}


def basic_of(label: Dict[str, Any]) -> Dict[str, Any]:
    return label.get("basic_annotation", {}) or {}


# ================================================================ 绘图
def show_mag(ax, disp: np.ndarray, mask: Optional[np.ndarray], signed: bool) -> None:
    """显示磁图；日面外压暗为中性灰，避免与物理内容混淆。"""
    out = disp
    if mask is not None and not signed:
        out = np.where(mask, disp, disp * 0.10)
    elif mask is not None and signed:
        out = np.where(mask, disp, np.clip(0.5 + (disp - 0.5) * 0.12, -1, 1))
    ax.imshow(out, cmap=CMAP_DIV if signed else CMAP_ABS,
              vmin=-1 if signed else 0, vmax=1, interpolation="nearest")


def overlay_events(ax, events: Sequence[Dict[str, Any]], scale: float,
                   disk: Tuple[float, float, float], width: int,
                   title: str) -> int:
    n_ok = 0
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
    cx, cy, r = disk
    ax.add_patch(Circle((cx * scale, cy * scale), r * scale, fill=False,
                        ls=":", lw=1.1, ec="#e5e7eb", alpha=0.55, zorder=4))
    ax.set_xlim(0, scale * width)
    ax.set_ylim(scale * width, 0)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("%s   [%d bbox / 共 %d 事件]" % (title, n_ok, len(events)),
                 fontsize=10, color="#e6e6e6", pad=6)
    for sp in ax.spines.values():
        sp.set_color("#555555")


def zoom_panel(ax, img: np.ndarray, norm: np.ndarray, poly: np.ndarray,
               tag: str, color: str, mask: Optional[np.ndarray],
               signed: bool, target: int = ZOOM_BOX) -> None:
    x0, y0 = poly.min(axis=0)
    x1, y1 = poly.max(axis=0)
    pad = ZOOM_PAD_PX
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = max((x1 - x0) / 2.0 + pad, (y1 - y0) / 2.0 + pad, target / 2.0)
    h, w = norm.shape
    ix0 = int(np.clip(cx - half, 0, w - 1)); ix1 = int(np.clip(cx + half, ix0 + 8, w))
    iy0 = int(np.clip(cy - half, 0, h - 1)); iy1 = int(np.clip(cy + half, iy0 + 8, h))
    show_mag(ax, norm[iy0:iy1, ix0:ix1],
             mask[iy0:iy1, ix0:ix1] if mask is not None else None, signed)
    ax.add_patch(MplPolygon(poly - [ix0, iy0], closed=True, fill=False,
                            lw=1.6, edgecolor=color))
    ax.set_xlim(0, ix1 - ix0); ax.set_ylim(iy1 - iy0, 0)
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(tag, fontsize=8, color=color, pad=3)
    for sp in ax.spines.values():
        sp.set_color(color); sp.set_alpha(0.6)


def flux_profile(ax, img: np.ndarray, polys: Sequence[np.ndarray],
                 thr: float) -> None:
    """bbox 内磁通像元统计（无符号数据下最直观的「这个框框住了什么」）。"""
    if not polys:
        ax.axis("off"); return
    names, cover, frac, p95, area = [], [], [], [], []
    for i, poly in enumerate(polys):
        x0, y0 = np.floor(poly.min(axis=0)).astype(int)
        x1, y1 = np.ceil(poly.max(axis=0)).astype(int)
        sub = img[max(y0, 0):y1, max(x0, 0):x1]
        if sub.size == 0:
            continue
        names.append("AR#%d" % (i + 1))
        area.append(sub.size / 1e4)          # 万像素
        cover.append(float((sub > thr).mean()))
        frac.append(float((sub > 0).mean()))
        p95.append(float(np.percentile(sub, 95)))
    x = np.arange(len(names))
    ax.bar(x - 0.19, cover, 0.38, label="超磁斑阈像元占比", color="#e0601f")
    ax.bar(x + 0.19, frac, 0.38, label="非零(有磁通)像元占比", color="#4a7ec0")
    ax2 = ax.twinx()
    ax2.plot(x, p95, "o-", color="#fbbf24", lw=1.5, ms=6, label="bbox 内 P95 灰度")
    ax2.set_ylabel("P95 灰度 (DN)", fontsize=7.5, color="#fbbf24")
    ax2.tick_params(labelsize=7, colors="#fbbf24")
    ax.set_xticks(x); ax.set_xticklabels(names, fontsize=7.5)
    ax.set_ylim(0, 1); ax2.set_ylim(0, max(p95) * 1.35 if p95 else 1)
    ax.tick_params(labelsize=7)
    ax.set_title("bbox 内磁通像元统计（磁斑阈值 = 日面内 P%.0f = %.0f DN）"
                 % (FLUX_PCTL, thr), fontsize=9, pad=5)
    ax.grid(axis="y", alpha=0.2, lw=0.5)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left",
              facecolor="#111827", edgecolor="#374151", labelcolor="#e5e7eb")


def info_panel(ax, label: Dict[str, Any], events: Sequence[Dict[str, Any]],
               root: str, name: str, geo: Dict[str, Any]) -> None:
    ax.axis("off")
    b = basic_of(label)
    s = sci_of(label)
    hek = label.get("hek_event_annotation", {}) or {}

    lines = [
        ("样本", name),
        ("观测时间", "%s → %s" % (b.get("startTime"), b.get("endTime"))),
        ("仪器/通道", "%s / %s / %s" % (s.get("instrume"), s.get("telescop"),
                                       s.get("wavelnth"))),
        ("rsunObs(仅显示)", fmt(s.get("rsunObs"), 6)),
        ("事件类型", ",".join(hek.get("event_types", []) or []) or "-"),
        ("has_events", str(hek.get("has_events"))),
        ("event_count", str(hek.get("event_count"))),
        ("有效多边形", "%d / %d" % (sum(polygons_of(e) is not None for e in events),
                                   len(events))),
        ("日面圆盘", "c=(%.0f,%.0f) R=%.0f px" % (geo["cx"], geo["cy"], geo["r"])),
        ("显示模式", "无符号 |B| · log1p" if not geo["signed"] else "有符号 · 中位数零点"),
        ("宁静区基线", "P50(正值) = %.2f DN" % geo["base"]),
        ("磁斑阈值", "P%.0f(全图) = %.0f DN" % (FLUX_PCTL, geo["thr"])),
        ("零像元占比", "%.1f%%（日面内）" % (100 * geo["zero_frac"])),
        ("bbox 越界日面", str(geo["n_out"])),
        ("数据根", root),
    ]
    y = 0.988
    for k, v in lines:
        ax.text(0.0, y, k, fontsize=8, color="#7dd3fc", va="top",
                transform=ax.transAxes, family="monospace")
        ax.text(0.36, y, str(v), fontsize=8, color="#e5e7eb", va="top",
                transform=ax.transAxes, family="monospace")
        y -= 0.0235

    rows = []
    for i, ev in enumerate(events):
        rows.append((
            event_tag(ev, i),
            fmt(ev.get("area_atdiskcenter"), 4),
            fmt(ev.get("unsignedflux"), 4),
            fmt(ev.get("meanshearangle"), 3),
            fmt(ev.get("absnetcurrenthelicity"), 3),
            fmt(ev.get("meantwistalpha"), 3),
            fmt(ev.get("meanvertcurrentdensity"), 3),
        ))
    if rows:
        y -= 0.010
        ax.text(0.0, y, "SHARP 特征（来自 HEK/HMI）", fontsize=8.5, color="#fbbf24",
                va="top", transform=ax.transAxes, family="monospace",
                weight="bold")
        y -= 0.026
        hdr = ("标签", "面积km2", "磁通", "剪切角deg", "净螺度", "alpha", "Jz")
        colw = (0.21, 0.15, 0.13, 0.16, 0.14, 0.12, 0.13)
        x = 0.0
        for wdt, htxt in zip(colw, hdr):
            ax.text(x, y, htxt, fontsize=7.0, color="#9ca3af", va="top",
                    transform=ax.transAxes, family="monospace")
            x += wdt
        y -= 0.021
        for r_i, row in enumerate(rows):
            x = 0.0
            for j, (wdt, cell) in enumerate(zip(colw, row)):
                ax.text(x, y, cell, fontsize=6.6,
                        color=COLOR_CYCLE[r_i % len(COLOR_CYCLE)] if j == 0 else "#d1d5db",
                        va="top", transform=ax.transAxes, family="monospace")
                x += wdt
            y -= 0.019
            if y < 0.015:
                break


# ================================================================ 主流程
def pick_sample(root: str, index: int, has_event_only: bool,
                name: Optional[str]) -> str:
    names = list_labels(root)
    if name:
        return name
    if has_event_only:
        cand = [nm for nm in names
                if load_label(root, nm).get("hek_event_annotation", {}).get("has_events")]
        names = cand or names
    if not names:
        raise SystemExit("no label found under %s" % os.path.join(root, "label"))
    return names[index % len(names)]


def summarize(label: Dict[str, Any], events: Sequence[Dict[str, Any]],
              geo: Optional[Dict[str, Any]] = None) -> str:
    b = basic_of(label)
    hek = label.get("hek_event_annotation", {}) or {}
    out = [
        "=" * 78,
        "文件      : %s" % b.get("fileName"),
        "观测时间  : %s" % b.get("startTime"),
        "science   : instrume=%s telescop=%s" % (
            sci_of(label).get("instrume"), sci_of(label).get("telescop")),
        "事件      : has_events=%s event_count=%s types=%s" % (
            hek.get("has_events"), hek.get("event_count"),
            ",".join(hek.get("event_types", []) or [])),
    ]
    if geo:
        out.append("像素定标  : center=(%.1f,%.1f) R=%.1f px  base=%.2f  thr=%.1f"
                   "  零像元=%.1f%%  越界框=%d"
                   % (geo["cx"], geo["cy"], geo["r"], geo["base"], geo["thr"],
                      100 * geo["zero_frac"], geo["n_out"]))
    out.append("-" * 78)
    for i, ev in enumerate(events):
        poly = polygons_of(ev)
        g = "bbox 缺失/非法"
        if poly is not None:
            g = "bbox=(%d 点) x[%.0f,%.0f] y[%.0f,%.0f]" % (
                poly.shape[0], poly[:, 0].min(), poly[:, 0].max(),
                poly[:, 1].min(), poly[:, 1].max())
        out.append("[%d] %s" % (i + 1, event_tag(ev, i)))
        out.append("     %s" % g)
        for k in ("SOL_standard", "event_starttime", "event_endtime",
                  "area_atdiskcenter", "unsignedflux", "meanshearangle",
                  "absnetcurrenthelicity", "meantwistalpha",
                  "meanvertcurrentdensity", "meangradienttotal",
                  "obs_observatory", "obs_instrument", "obs_channelid"):
            if k in ev:
                out.append("     %-24s %s" % (k, fmt(ev.get(k))))
    out.append("=" * 78)
    return "\n".join(out)


def render(root: str, name: str, out_png: str, zoom: int = DEFAULT_ZOOM,
           signed: bool = False) -> None:
    label = load_label(root, name)
    events = events_of(label)
    img, data_path = load_gray(root, name)
    cx, cy, r_disk = detect_disk(img)
    mask = disk_mask(img.shape, cx, cy, r_disk)
    norm, scale_info = normalize_mag(img, mask, signed)

    dv = img[mask]
    thr = float(np.percentile(dv, FLUX_PCTL)) if dv.size else 1.0
    zero_frac = float((dv == 0).mean()) if dv.size else 0.0

    n_out = 0
    for ev in events:
        poly = polygons_of(ev)
        if poly is None:
            continue
        x0, y0 = np.floor(poly.min(axis=0)).astype(int)
        x1, y1 = np.ceil(poly.max(axis=0)).astype(int)
        if x0 < 0 or y0 < 0 or x1 > img.shape[1] or y1 > img.shape[0] or \
                not mask[max(y0, 0):y1, max(x0, 0):x1].all():
            n_out += 1

    geo = {"cx": cx, "cy": cy, "r": r_disk, "signed": signed,
           "base": scale_info["base"], "hi": scale_info["hi"], "thr": thr,
           "zero_frac": zero_frac, "n_out": n_out}

    scale = IMG_PER_PANEL / img.shape[1]
    polys = [p for p in (polygons_of(e) for e in events) if p is not None]
    n_zoom = max(0, min(zoom, len(polys)))

    print(summarize(label, events, geo))
    print("图像      : %s  shape=%s" % (data_path, img.shape))

    with plt.style.context("dark_background"):
        fig = plt.figure(figsize=(16.5, 9.8), dpi=110, facecolor="#0b0f14")
        gs = fig.add_gridspec(2, 3, height_ratios=[1.0, 0.95],
                              width_ratios=[1.25, 1.0, 1.5],
                              hspace=0.24, wspace=0.14,
                              left=0.012, right=0.988, top=0.92, bottom=0.04)

        ax_full = fig.add_subplot(gs[0, 0])
        show_mag(ax_full, norm, mask, signed)
        overlay_events(ax_full, events, scale, (cx, cy, r_disk), img.shape[1],
                       "HMI BLOS 幅值图 + bbox_polygon_pixel   [%s]" % name)

        handles = [Line2D([0], [0], color=COLOR_CYCLE[i % len(COLOR_CYCLE)],
                          lw=2, label=event_tag(ev, i))
                   for i, ev in enumerate(events)]
        if handles:
            ax_full.legend(handles=handles, loc="lower left", fontsize=7.2,
                           facecolor="#111827", edgecolor="#374151",
                           labelcolor="#e5e7eb", framealpha=0.85)

        # 磁斑阈值等值线（无符号数据下零等值线无意义）
        ax_z = fig.add_subplot(gs[0, 1])
        step = 2
        sub = norm[::step, ::step]
        zz, xx = np.mgrid[0:norm.shape[0]:step, 0:norm.shape[1]:step]
        level = np.log1p(thr / max(scale_info["base"], 1e-6)) / \
            np.log1p(scale_info["hi"] / max(scale_info["base"], 1e-6))
        try:
            ax_z.contour(xx * step, zz * step, sub,
                         levels=[float(np.clip(level, 0.01, 0.99))],
                         colors="#22d3ee", linewidths=1.2)
            for i, ev in enumerate(events):
                poly = polygons_of(ev)
                if poly is not None:
                    ax_z.add_patch(MplPolygon(poly, closed=True, fill=False, lw=1.4,
                                              ec=COLOR_CYCLE[i % len(COLOR_CYCLE)]))
        except Exception as exc:            # 等值线失败不应中断出图
            ax_z.text(0.5, 0.5, "磁斑等值线提取失败\n(%s)" % exc, color="#f87171",
                      ha="center", va="center", transform=ax_z.transAxes, fontsize=9)
        ax_z.set_xlim(0, norm.shape[1]); ax_z.set_ylim(norm.shape[0], 0)
        ax_z.set_aspect("equal"); ax_z.set_xticks([]); ax_z.set_yticks([])
        ax_z.set_title("磁斑阈值等值线（青, %.0f DN）+ bbox" % thr,
                       fontsize=9.5, pad=5)
        for sp in ax_z.spines.values():
            sp.set_color("#555555")

        ax_info = fig.add_subplot(gs[0:2, 2])
        info_panel(ax_info, label, events, root, name, geo)

        valid = [(i, polygons_of(e)) for i, e in enumerate(events)
                 if polygons_of(e) is not None]
        for k in range(3):
            axz = fig.add_subplot(gs[1, k])
            if k >= n_zoom:
                axz.axis("off")
                continue
            ev_idx, poly = valid[k]
            zoom_panel(axz, img, norm, poly, event_tag(events[ev_idx], ev_idx),
                       COLOR_CYCLE[ev_idx % len(COLOR_CYCLE)], mask, signed)

        mode = "有符号双极" if signed else "无符号 |B| + log1p"
        fig.suptitle("OPC · %s 可视化  —  %s（纯像素几何，无 sunpy / 无观测时间依赖）"
                     % (DATASET_NAME, mode), fontsize=13, color="#f9fafb", y=0.965)

        out2 = os.path.splitext(out_png)[0] + "_flux.png"
        fig2 = plt.figure(figsize=(11, 4.4), dpi=110, facecolor="#0b0f14")
        axp2 = fig2.add_subplot(111)
        axp2.set_facecolor("#0b0f14")
        flux_profile(axp2, img, polys, thr)
        fig2.suptitle("%s  bbox 内磁通像元统计" % name, fontsize=11,
                      color="#f9fafb", y=0.97)
        fig2.tight_layout(rect=[0, 0, 1, 0.93])

        os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
        fig.savefig(out_png, facecolor=fig.get_facecolor())
        fig2.savefig(out2, facecolor=fig2.get_facecolor())
        print("输出      : %s" % out_png)
        print("输出      : %s" % out2)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="OPC HMI 活动区样例数据可视化")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--file", help="指定数据文件名")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--any", action="store_true", help="允许选到无事件样本")
    ap.add_argument("--zoom", type=int, default=DEFAULT_ZOOM)
    ap.add_argument("--signed", action="store_true",
                    help="按有符号磁图处理（蓝-灰-红发散色表）")
    ap.add_argument("--out", default="viz_hmi.png")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--no-plot", action="store_true")
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

    render(args.root, name, args.out, zoom=max(0, args.zoom), signed=args.signed)
    return 0


if __name__ == "__main__":
    sys.exit(main())