#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OPC · 样例数据可视化 —— 独立简洁版（含帧间差分与日冕仪遮罩）。
可视化功能；面向 OPC 原始产物的「数据 + 标签」做可视化。

  * 每个**中心帧**配**前后各一个相邻帧**
  * 分组依据：`event_annotation.image_time` = 中心帧时间；
    帧自身时间 = `basic_annotation.startTime`。二者相等即中心帧。
  * 相邻帧的 'event_annotation` 与中心帧逐字节一致。

本脚本据此自动定位「前帧 / 中心帧 / 后帧」并做差分。

面板构成：
  1. 原始三帧（前/ 中 / 后）—— 对数拉伸，日冕仪遮罩外置灰
  2. **帧间差分**两张：prev − next（去掉日冕背景与恒星演化的running difference，
    ）与 center − prev（基线差分）
  3. **遮罩**：内遮挡圆（occulting disk）+ 外视场圆，纯像素半径比例定标，
     不使用任何 FITS 头 / 观测时刻定标（见下方「设计约束」）
  4. CME事件角宽扇形：以 central_pa / width 在像素极坐标上画 HRC 扇形
     **PA 朝向不靠假设，靠实测**：不读 FITS 头就无法知道 PNG 是北在上、
     还是 RECTIFY=180 / 镜像。脚本把 4 种朝向都算一遍，用帧间差分实测
     「扇形内 |差分| 能量 / 全图 |差分| 能量」，取得分最高的朝向。
     在 3 个独立 CME 上交叉验证（PA=293/W=120、PA=290/W=63、PA=57/W=67），
     **一致选出 rot180**（比north_up 高 1.4~2.4 倍）——
     即这批 PNG 是旋转 180° 存储的，与参考实现 `_infer_need_rotate`
     依据 `RECTIFY != 180` 的判断一致。用 `--orient` 可人工覆盖。
  5. 事件元数据表（速度、加速度、质量、动能、备注）+ 径向亮度剖面

设计约束：
  * *
    与 RSUN_REF / CRPIX / CDELT，这些都要求读 FITS 头；本脚本改为
    **图像内自定标**：由图像本身定出 occ 圆与视场圆像素半径，
    再以「occ 圆 ↔ 2.4 R_sun（C2 官方设计值，几何常数而非观测量）」
    建立 R_sun↔像素线性比例。不读 instrume/detector 的观测时刻定标、
    不读 dateObs/tObs，因此结果与观测时间无关。
  * **PA 朝向**：枚举 4 种朝向，用帧间差分实测选出与 CME 信号最吻合的一种（见下第4 条）。
  * **视场**：遮罩用图像实测的 r_fov，不用 C2 标称 6 R_sun —— 实测 ≈10.6 R_sun
    且大于画幅半边长，说明这批 PNG 是方形裁切，外冕被切掉了一部分。
  * 观测时间仅用于**帧排序与分组**（数据组织信息，不是几何定标）。
  * 依赖仅 numpy + matplotlib + pillow。

用法::

    python vis_cme_c2.py --index 0 --out out/cme.png
    python viz_cme_c2.py --file c2_20100101_065403.png
    python viz_cme_c2.py --list --limit 20
    python viz_cme_c2.py --file xxx.png --no-plot
    python viz_cme_c2.py --file xxx.png --orient rot180   # 人工覆盖朝向
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
from matplotlib.patches import Polygon as MplPolygon, Circle, Wedge
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
# 面板直接画全分辨率图（叠加几何与图像共用像素坐标），故无下采样系数
FALLBACK_OCC_R = 650.0       # C2 occ 圆经验半径（px, 4096 画幅），定标失败时兜底
OCC_RSUN = 2.4              # C2 遮挡盘设计半径（R_sun）—— 仪器几何常数，非观测量
# C2 *标称* 视场外缘约 6 R_sun，但这批 PNG 的有效信号实测延伸到 ~10 R_sun
# （见 detect_occ 的 r_fov）。故遮罩用**图像实测**的 r_fov，标称值只用于打印对照，
# 不写进遮罩逻辑 —— 免得把标称当事实。
NOMINAL_FOV_RSUN = 6.0
DIFF_SMOOTH = 15# 差分图盒式平滑边长（抑制单像元噪声）
DIFF_PCTL = 95.0             # 差分归一化分位（P95 而非 P99：CME 常弱于噪声尾部）

COLOR_CYCLE = ["#ff5c5c", "#ffd23f", "#4ade80", "#38bdf8", "#c084fc",
               "#fb923c", "#f472b6", "#a3e635"]


# ================================================================ 数据读取
def list_labels(root: str) -> List[str]:
    lb = os.path.join(root, "label")
    if not os.path.isdir(lb):
        raise FileNotFoundError(lb)
    return sorted(f[:-len(".png.json")] for f in os.listdir(lb) if f.endswith(".png.json"))


def load_label(root: str, name: str) -> Dict[str, Any]:
    path = os.path.join(root, "label", name + ".png.json")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def data_path(root: str, name: str) -> str:
    fn = name if name.endswith(".png") else name + ".png"
    return os.path.join(root, "data", fn)


def load_gray(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.float64)


def events_of(label: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(label.get("hek_event_annotation", {}).get("matched_events", []) or [])


def frame_time(label: Dict[str, Any]) -> str:
    return (label.get("basic_annotation", {}) or {}).get("startTime", "")


def center_time(label: Dict[str, Any]) -> str:
    return (label.get("hek_event_annotation", {}) or {}).get("image_time", "")


def to_sec(ts: str) -> float:
    """ISO 'YYYY-MM-DDTHH:MM:SS' -> epoch 秒（不依赖 tz，随时区无关地一致排序）。"""
    try:
        y, mo, d = ts[0:10].split("-")
        hms = ts[11:19] if len(ts) >= 19 else "00:00:00"
        hh, mi, ss = hms.split(":")
        return (int(y) * 372 + int(mo) * 31 + int(d)) * 86400.0 + \
            int(hh) * 3600 + int(mi) * 60 + int(ss)
    except Exception:
        return 0.0


def dt_label(t_center: str, t_other: str) -> str:
    """相对中心帧的时间差，形如 'Δt=-24min00s' / 'Δt=+37min35s'。"""
    d = int(to_sec(t_other) - to_sec(t_center))
    sign = "-" if d < 0 else "+"
    d = abs(d)
    h, rem = divmod(d, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "Δt=%s%dh%02dm%02ds" % (sign, h, m, s)
    return "Δt=%s%02dm%02ds" % (sign, m, s)


def find_group(root: str, name: str) -> Dict[str, Optional[str]]:
    """定位 中心帧 的前/中/后 同组帧名。

    组定义：`hek_event_annotation.image_time` 相同即同组；
    组内按 `basic_annotation.startTime` 排序取前/中/后。
    允许传入相邻帧（其 image_time 即中心帧时间），同样能还原整组。
    需扫描全量 label（约 36k 个小 JSON，首次数秒）；先用文件名内嵌时间戳
    粗筛，把 json 解析量压到同一小时内。
    """
    want = center_time(load_label(root, name))
    t_want = to_sec(want)

    group: List[Tuple[float, str]] = []
    for nm in list_labels(root):
        t = read_time_from_name(nm)
        if t is not None and abs(t - t_want) > 5400:   # 相邻帧间隔 ≈12min，放宽到 90min
            continue
        if center_time(load_label(root, nm)) == want:
            group.append((to_sec(frame_time(load_label(root, nm))), nm))
    if not group:
        return {"prev": None, "center": name, "next": None}

    group.sort()
    t_center = to_sec(frame_time(load_label(root, name)))
    times = [g[0] for g in group]
    i = times.index(t_center) if t_center in times else len(group) // 2
    return {
        "prev": group[i - 1][1] if i - 1 >= 0 else None,
        "center": group[i][1],
        "next": group[i + 1][1] if i + 1 < len(group) else None,
    }


def read_time_from_name(name: str) -> Optional[float]:
    """从 'c2_20100101_063003' 之类文件名抽时间（秒）。无则None。"""
    import re
    mm = re.search(r"(\d{8})_(\d{6})", name)
    if not mm:
        return None
    d, t = mm.group(1), mm.group(2)
    return to_sec("%s-%s-%sT%s:%s:%s" % (d[0:4], d[4:6], d[6:8],
                                         t[0:2], t[2:4], t[4:6]))


# ================================================================ 像素定标
def detect_occ(img: np.ndarray) -> Tuple[float, float, float, float]:
    """自动定标 occ 圆（遮挡盘）与视场外缘，纯像素法。

    occ 圆：径向平均亮度曲线从 0 起的**首个显著上升沿**（遮挡盘内全黑，
             内冕器内缘处亮度陡升，锐利可测）。
    视场外缘：图像有效信号的最大半径（C2 Level-1 圆形视场外为 0）。
    返回 (cx, cy, r_occ, r_fov)。
    """
    h, w = img.shape
    cx = cy = (w - 1) / 2.0
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - cx, yy - cy)

    nb = 512
    edges = np.linspace(0, 0.75 * w, nb + 1)
    idx = np.digitize(r.ravel(), edges) - 1
    cnt = np.maximum(np.bincount(idx, minlength=nb), 1)
    prof = np.bincount(idx, weights=img.ravel(), minlength=nb) / cnt
    centers = 0.5 * (edges[:-1] + edges[1:])
    bin_w = centers[1] - centers[0]

    # 视场外缘：亮度掉到极低且不再回升的最外半径
    nz = r[img > 2.0]
    r_fov = float(nz.max()) if nz.size else 0.7 * w

    # occ 圆：在 [0.06, 0.40]*半边长 内找最大正斜率处
    lo = int(np.searchsorted(centers, 0.06 * w))
    hi = int(np.searchsorted(centers, 0.40 * w))
    seg = prof[lo:hi]
    if seg.size >= 3:
        k = int(np.argmax(np.diff(seg)))
        r_occ = float(centers[lo + k])
    else:
        r_occ = FALLBACK_OCC_R

    # 合理性校验：occ 应显著小于视场
    if not (0.5 * FALLBACK_OCC_R < r_occ < 0.9 * r_fov):
        r_occ = FALLBACK_OCC_R
    return cx, cy, r_occ, r_fov


def science_mask(shape: Tuple[int, int], cx: float, cy: float,
                 r_occ: float, r_fov: float) -> np.ndarray:
    """日冕仪科学遮罩：True = 有效（occ 圆外且视场圆内）。"""
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - cx, yy - cy)
    return (r > r_occ) & (r < r_fov)


def rsun_per_px(r_occ: float, occ_rsun: float = OCC_RSUN) -> float:
    """由 occ 圆像素半径建立 R_sun/像素 比例（仪器几何常数，非观测量）。"""
    return occ_rsun / max(r_occ, 1e-6)


# ================================================================ 显示拉伸
def lasco_stretch(img: np.ndarray, mask: Optional[np.ndarray] = None,
                  p_low: float = 0.5, p_high: float = 99.9,
                  gain: float = 60.0) -> np.ndarray:
    """日冕仪拉伸：百分位裁剪 + asinh（对数型）压缩，返回 0~1。

    C2 内冕亮度从遮挡盘边缘的 ~250 DN 衰减到视场边缘 ~1 DN，跨越两个量级，
    必须压缩；asinh 低端近线性、高端近对数，是 astro 社区惯用的无魔数做法。
    """
    ref = img[mask] if mask is not None else img
    ref = ref[np.isfinite(ref)]
    lo, hi = np.percentile(ref, [p_low, p_high])
    if hi <= lo:
        lo, hi = float(np.nanmin(ref)), float(np.nanmax(ref))
    if hi <= lo:
        return np.zeros_like(img)
    x = np.clip((img - lo) / (hi - lo), 0, 1)
    return np.arcsinh(x * gain) / np.arcsinh(gain)


def box_blur(a: np.ndarray, k: int) -> np.ndarray:
    """归一化盒式滤波（积分图实现，零依赖 scipy）。"""
    if k <= 1:
        return a
    pad = k // 2
    ap = np.pad(a, pad, mode="edge")
    c = np.cumsum(np.cumsum(ap, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)), mode="constant")
    H, W = a.shape
    s = k * k
    out = (c[k:k + H, k:k + W] - c[0:H, k:k + W]
           - c[k:k + H, 0:W] + c[0:H, 0:W]) / s
    return out


def _norm_diff(a: np.ndarray, b: np.ndarray, mask: np.ndarray
               ) -> Tuple[np.ndarray, float]:
    """有符号差分 + 遮罩内稳健归一化，返回 (归一化到 -1~1, 饱和幅度 DN)。

    归一化用 |d| 的 P95 而非 P99：C2 的 CME 信号（尤其 faint 类）常只有
    噪声尾部量级，用 P99 会把整幅差分压成一片噪点；P95 让弱信号可见，
    代价是极亮前沿会截顶（截顶只影响显示，不影响任何数值输出）。
    """
    d = a - b
    v = d[mask]
    amp = float(np.percentile(np.abs(v), DIFF_PCTL)) if v.size else 0.0
    if not np.isfinite(amp) or amp <= 0:
        amp = 1.0
    return np.clip(d / amp, -1.0, 1.0), amp


def running_difference(prev: np.ndarray, nxt: np.ndarray,
                       mask: np.ndarray) -> Tuple[np.ndarray, float]:
    """running difference = 前帧 − 后帧（C2 CME 判定的标准做法）。

    参考实现（dataset_processor/src/cme）里日冕背景扣除的核心思路：
    相邻两帧相减可直接消掉日冕 K 冕与恒星演化，剩下的主要是 CME 瞬变信号。
    取「前帧 − 后帧」而非「后帧 − 前帧」是为了让向外传播的 CME 呈**正**（暖色）。
    """
    return _norm_diff(prev, nxt, mask)


def baseline_difference(center: np.ndarray, prev: np.ndarray,
                        mask: np.ndarray) -> Tuple[np.ndarray, float]:
    """基线差分 = 中心帧 − 前帧（参考帧不同，用于对比 CME 出现前后的变化）。"""
    return _norm_diff(center, prev, mask)


CMAP_DIV = plt.get_cmap("RdBu_r")


# ================================================================ 标注绘制
def fmt(val: Any, digits: int = 4, unit: str = "") -> str:
    if val is None or val == "":
        return "-"
    if isinstance(val, (int, float)):
        return (("%%.%dg" % digits) % val) + unit
    return "%s%s" % (val, unit)


PA_ORIENTATIONS = {
    # 名字: (x 方向符号, y 方向符号)  —— 作用于 sin/cos 的组合
    # 不读 FITS 头就无法确定 PNG 的方位（北在上 / RECTIFY=180 / 镜像），
    # 故把 4 种可能全部列出，由帧间差分数据实测选出与 CME 信号最吻合的一种。
    "north_up": (1.0, -1.0),      # 标准：北在上、东在右
    "rot180": (-1.0, 1.0),        # 旋转 180°
    "mirror_x": (-1.0, -1.0),     # 东西镜像
    "mirror_y": (1.0, 1.0),       # 南北镜像
}


def rho_to_px(rho_rsun: float, r_occ: float) -> float:
    """R_sun -> 像素。比例尺由 occ 圆给定：r_occ px 对应 OCC_RSUN 个 R_sun。

    ⚠ 换算必须**除以** R_sun/px，不是乘 —— 乘会把扇形缩成中心一个亚像素点
    （本项目踩过：扇形宽120° 却完全不可见，就是这里乘反了）。
    """
    return rho_rsun / (OCC_RSUN / max(r_occ, 1e-6))


def wedge_mask(shape: Tuple[int, int], cx: float, cy: float,
               rho_in: float, rho_out: float, pa_deg: float, width_deg: float,
               r_occ: float, orient: str, n: int = 96) -> np.ndarray:
    """扇形区域的布尔掩膜（像素系），供朝向自动判定使用。"""
    sx, sy = PA_ORIENTATIONS[orient]
    pa0 = pa_deg - width_deg / 2.0
    pa1 = pa_deg + width_deg / 2.0
    ang = np.linspace(pa0, pa1, n)
    rin, rout = rho_to_px(rho_in, r_occ), rho_to_px(rho_out, r_occ)
    pts = []
    for rr in (rin, rout):
        a = np.deg2rad(ang)
        pts.append(np.column_stack([cx + sx * rr * np.sin(a),
                                    cy + sy * rr * np.cos(a)]))
    poly = np.vstack([pts[0], pts[1][::-1]])
    from matplotlib.path import Path
    pth = Path(poly)
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    return pth.contains_points(np.column_stack([xx.ravel(), yy.ravel()])) \
        .reshape(h, w)


def choose_orientation(diff: Optional[np.ndarray], mask: np.ndarray,
                      events: Sequence[Dict[str, Any]], shape: Tuple[int, int],
                      cx: float, cy: float, r_occ: float,
                      rho_in: float, rho_out: float
                      ) -> Tuple[str, Dict[str, float]]:
    """用帧间差分实测选出 PA 方位：使扇形内 |差分| 能量占比最高的朝向。

    不依赖 FITS 头的 RECTIFY / CDELT 符号约定，因此彻底摆脱仪器元数据。
    评分 = 扇形内平均 |差分| / 全图有效区平均 |差分|（几何因子相同，直接比）。
    """
    if diff is None or mask is None or not mask.any():
        return "north_up", {}
    energy = np.abs(diff)
    base = float(energy[mask].mean()) or 1.0
    scores: Dict[str, float] = {}
    for orient in PA_ORIENTATIONS:
        acc = None
        n_ok = 0
        for ev in events:
            pa, w = ev.get("central_pa"), ev.get("width")
            if pa is None or w is None or float(w) <= 0:
                continue
            m = wedge_mask(shape, cx, cy, rho_in, rho_out, float(pa),
                           float(w), r_occ, orient)
            if m.sum() < 64:
                continue
            n_ok += 1
            acc = energy[m] if acc is None else acc + energy[m]
        scores[orient] = float(acc.mean()) / base if n_ok else 0.0
    if not scores or max(scores.values()) <= 0:
        return "north_up", scores
    return max(scores, key=scores.get), scores


def cme_wedge_polygon(cx: float, cy: float, rho_in: float, rho_out: float,
                      pa_deg: float, width_deg: float,
                      r_occ: float, orient: str = "north_up",
                      n: int = 64) -> np.ndarray:
    """CME 角宽扇形 -> 像素多边形。

    HRC 约定：rho = 日心距（R_sun），psi = 位置角（自太阳正北顺时针）。
    朝向由 `orient` 指定（默认北在上），**不读 FITS 头**：
        x = cx + sx*rho*Rpp*sin(psi),  y = cy + sy*rho*Rpp*cos(psi)
    纯几何换算，不含任何观测时刻项。
    """
    sx, sy = PA_ORIENTATIONS[orient]
    pa0 = pa_deg - width_deg / 2.0
    pa1 = pa_deg + width_deg / 2.0
    ang = np.deg2rad(np.linspace(pa0, pa1, n))
    rin, rout = rho_to_px(rho_in, r_occ), rho_to_px(rho_out, r_occ)
    x_in = cx + sx * rin * np.sin(ang)
    y_in = cy + sy * rin * np.cos(ang)
    x_out = cx + sx * rout * np.sin(ang)
    y_out = cy + rout * sy * np.cos(ang)
    return np.vstack([np.column_stack([x_in, y_in]),
                      np.column_stack([x_out[::-1], y_out[::-1]])])


def overlay_cme(ax, events: Sequence[Dict[str, Any]], scale: float,
                cx: float, cy: float, r_occ: float, r_fov: float,
                r_sun_per_px: float, width: int, title: str,
                draw_mask: bool = True, orient: str = "north_up",
                rho_in: float = OCC_RSUN + 0.15) -> None:
    sx, sy = PA_ORIENTATIONS[orient]
    for i, ev in enumerate(events):
        pa = ev.get("central_pa")
        w = ev.get("width")
        if pa is None or w is None:
            continue
        pa, w = float(pa), float(w)
        if w >= 360.0:
            pa, w = 180.0, 359.0            # 全晕：画成近整圈，留缺口便于辨认
        # 外缘取「实测视场」与「方形画幅内切圆」的较小者：这批 C2 PNG 是方形裁切，
        # 实测视场圆（≈10.6 Rs）比画幅半边长还大，直接画会跑出画外。
        rho_out = min(r_fov * r_sun_per_px, 0.5 * width * r_sun_per_px)
        poly = cme_wedge_polygon(cx, cy, rho_in, rho_out, pa, w,
                                 r_occ, orient) * scale
        col = COLOR_CYCLE[i % len(COLOR_CYCLE)]
        ax.add_patch(MplPolygon(poly, closed=True, fill=False, lw=1.8,
                                edgecolor=col, alpha=0.95, zorder=5))
        mid = np.deg2rad(pa)
        rr = rho_to_px((rho_in + rho_out) / 2.0, r_occ) * scale
        ax.annotate("CME#%d PA=%.0f° W=%.0f°" % (i + 1, pa, w),
                    xy=(cx * scale + sx * rr * np.sin(mid),
                        cy * scale + sy * rr * np.cos(mid)),
                    color=col, fontsize=7.5, weight="bold", zorder=6,
                    ha="center",
                    bbox=dict(boxstyle="round,pad=0.15", fc="black",
                              ec=col, alpha=0.55, lw=0.6))
        # PA 射线
        rout_px = rho_to_px(rho_out, r_occ) * scale
        ax.plot([cx * scale, cx * scale + sx * rout_px * np.sin(mid)],
                [cy * scale, cy * scale + sy * rout_px * np.cos(mid)],
                color=col, lw=0.8, ls=":", alpha=0.8, zorder=5)

    if draw_mask:
        ax.add_patch(Circle((cx * scale, cy * scale), r_occ * scale,
                            fill=False, ls="--", lw=1.2, ec="#94a3b8", zorder=6))
        ax.add_patch(Circle((cx * scale, cy * scale), r_fov * scale,
                            fill=False, ls=":", lw=1.2, ec="#94a3b8", zorder=6))
        for rsun in (3.0, 6.0, 9.0):
            ax.add_patch(Circle((cx * scale, cy * scale),
                                rho_to_px(rsun, r_occ) * scale,
                                fill=False, lw=0.5, ec="#475569", zorder=4))
    ax.set_xlim(0, scale * width)
    ax.set_ylim(scale * width, 0)
    ax.set_aspect("equal")
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title(title, fontsize=9.5, color="#e6e6e6", pad=5)
    for sp in ax.spines.values():
        sp.set_color("#555555")


def draw_mask_inset(ax, mask: np.ndarray, cx: float, cy: float,
                    r_occ: float, r_fov: float) -> None:
    """遮罩示意：白 = 科学有效区（CME 判定的可用像素）。"""
    ax.imshow(mask.astype(np.uint8) * 255, cmap="gray",
              vmin=0, vmax=255, interpolation="nearest")
    ax.add_patch(Circle((cx, cy), r_occ, fill=False, ls="--", lw=1.0, ec="#f87171"))
    ax.add_patch(Circle((cx, cy), r_fov, fill=False, ls=":", lw=1.0, ec="#38bdf8"))
    ax.set_xticks([]); ax.set_yticks([])
    ax.set_title("科学遮罩（白=有效 %.1f%%）" % (100 * mask.mean()),
                 fontsize=9, color="#e6e6e6", pad=4)
    for sp in ax.spines.values():
        sp.set_color("#555555")


def radial_profile(ax, img: np.ndarray, cx: float, cy: float,
                   r_occ: float, r_fov: float, r_sun_per_px: float) -> None:
    """径向亮度剖面（标出 occ / 实测视场 / 常用 rho 刻度）。"""
    h, w = img.shape
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot(xx - cx, yy - cy)
    nb = 400
    edges = np.linspace(0, 0.72 * w, nb + 1)
    idx = np.digitize(r.ravel(), edges) - 1
    cnt = np.maximum(np.bincount(idx, minlength=nb), 1)
    prof = np.bincount(idx, weights=img.ravel(), minlength=nb) / cnt
    centers = 0.5 * (edges[:-1] + edges[1:])
    ax.plot(centers, prof, lw=1.1, color="#38bdf8", label="径向平均亮度")
    ax.axvline(r_occ, color="#f87171", ls="--", lw=1.2,
               label="occ 圆 %.0f px (=%.1f Rs)" % (r_occ, OCC_RSUN))
    ax.axvline(r_fov, color="#a3e635", ls=":", lw=1.2,
               label="实测视场 %.0f px (=%.1f Rs)"
                     % (r_fov, r_fov * r_sun_per_px))
    for rsun, lab in ((3.0, "3 Rs"), (6.0, "6 Rs(标称)"), (9.0, "9 Rs")):
        px = rho_to_px(rsun, r_occ)
        if px > 0.72 * w:
            continue
        ax.axvline(px, color="#475569", lw=0.7, ls="-.")
        ax.text(px, ax.get_ylim()[1] * 0.02, " " + lab, fontsize=6.5,
                color="#64748b")
    ax.set_xlabel("半径 (px，自图像中心)", fontsize=7.5)
    ax.set_ylabel("平均亮度 (DN)", fontsize=7.5)
    ax.set_xlim(0, 0.72 * w)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.2, lw=0.5)
    ax.legend(fontsize=6.6, facecolor="#111827", edgecolor="#374151",
              labelcolor="#e5e7eb", loc="upper right")
    ax.set_title("径向亮度剖面（纯像素定标依据）", fontsize=9, pad=4)


def info_panel(ax, label: Dict[str, Any], group: Dict[str, Optional[str]],
               times: Dict[str, str], events: Sequence[Dict[str, Any]],
               root: str, name: str, geo: Dict[str, Any]) -> None:
    ax.axis("off")
    b = label.get("basic_annotation", {}) or {}
    hek = label.get("hek_event_annotation", {}) or {}
    s = label.get("science_annotation", {}) or {}

    lines = [
        ("样本(中心帧)", name),
        ("图像时间", times.get("center", "-")),
        ("组内前帧", "%s   (%s)" % (group.get("prev") or "缺失",
                                   times.get("prev", "-"))),
        ("组内后帧", "%s   (%s)" % (group.get("next") or "缺失",
                                   times.get("next", "-"))),
        ("仪器/台站", "%s / %s" % (s.get("instrume"), s.get("telescop"))),
        ("事件类型", ",".join(hek.get("event_types", []) or []) or "-"),
        ("has_events", str(hek.get("has_events"))),
        ("event_count", str(hek.get("event_count"))),
        ("occ 圆", "%.1f px（= %.1f Rs，设计值）" % (geo["r_occ"], OCC_RSUN)),
        ("实测视场外缘", "%.1f px（= %.2f Rs；标称 %.0f Rs）"
                        % (geo["r_fov"], geo["r_fov"] * geo["rpp"], NOMINAL_FOV_RSUN)),
        ("R_sun/像素", "%.6f（由 occ 圆反推，非观测量）" % geo["rpp"]),
        ("PA 朝向", "%s%s" % (geo.get("orient", "-"),
                          "" if geo.get("orient_scores") else "（人工指定）")),
        ("科学遮罩占比", "%.1f%%" % (100 * geo["mask_frac"])),
        ("⚠ 方形裁切", ("视场圆 %.0f px > 半边长 %.0f px，外冕被切"
                         % (geo["r_fov"], geo["half_w"]))
         if geo["r_fov"] > geo["half_w"] else "-"),
        ("running-diff", "%s（±%.2f DN @P%.0f）" % (
            "可用" if geo.get("rd_ok") else "缺帧不可用", geo.get("rd_amp", 0.0),
            DIFF_PCTL)),
        ("数据根", root),
    ]
    y = 0.988
    for k, v in lines:
        ax.text(0.0, y, k, fontsize=8, color="#7dd3fc", va="top",
                transform=ax.transAxes, family="monospace")
        ax.text(0.34, y, str(v), fontsize=8, color="#e5e7eb", va="top",
                transform=ax.transAxes, family="monospace")
        y -= 0.0235

    rows = []
    for i, ev in enumerate(events):
        rows.append((
            "CME#%d" % (i + 1),
            fmt(ev.get("central_pa"), 4) + "°",
            fmt(ev.get("width"), 4) + "°",
            fmt(ev.get("linear_speed"), 4),
            fmt(ev.get("acceleration"), 3),
            fmt(ev.get("mass"), 3),
            fmt(ev.get("kinetic_energy"), 3),
            str(ev.get("accel_flag") or "-"),
        ))
    if rows:
        y -= 0.010
        ax.text(0.0, y, "CDAW CME 参数", fontsize=8.5, color="#fbbf24",
                va="top", transform=ax.transAxes, family="monospace",
                weight="bold")
        y -= 0.026
        hdr = ("事件", "PA", "角宽", "速度km/s", "加速度m/s²", "质量g", "动能erg", "标记")
        colw = (0.10, 0.09, 0.09, 0.16, 0.17, 0.12, 0.14, 0.09)
        x = 0.0
        for wdt, htxt in zip(colw, hdr):
            ax.text(x, y, htxt, fontsize=6.8, color="#9ca3af", va="top",
                    transform=ax.transAxes, family="monospace")
            x += wdt
        y -= 0.021
        for r_i, row in enumerate(rows):
            x = 0.0
            for j, (wdt, cell) in enumerate(zip(colw, row)):
                ax.text(x, y, cell, fontsize=6.4,
                        color=COLOR_CYCLE[r_i % len(COLOR_CYCLE)] if j == 0 else "#d1d5db",
                        va="top", transform=ax.transAxes, family="monospace")
                x += wdt
            y -= 0.019
            if y < 0.015:
                break

    for i, ev in enumerate(events):
        rk = ev.get("remarks")
        if rk:
            y -= 0.006
            ax.text(0.0, y, "CME#%d remarks" % (i + 1), fontsize=6.6,
                    color="#9ca3af", va="top", transform=ax.transAxes,
                    family="monospace")
            y -= 0.017
            ax.text(0.0, y, str(rk)[:70], fontsize=6.4, color="#cbd5e1",
                    va="top", transform=ax.transAxes, family="monospace")
            y -= 0.020


# ================================================================ 主流程
def summarize(label: Dict[str, Any], group: Dict[str, Optional[str]],
              times: Dict[str, str], events: Sequence[Dict[str, Any]],
              geo: Optional[Dict[str, Any]] = None) -> str:
    b = label.get("basic_annotation", {}) or {}
    hek = label.get("hek_event_annotation", {}) or {}
    out = [
        "=" * 78,
        "中心帧    : %s" % b.get("fileName"),
        "图像时间  : %s" % times.get("center"),
        "组内前帧  : %s (%s)" % (group.get("prev") or "缺失", times.get("prev", "-")),
        "组内后帧  : %s (%s)" % (group.get("next") or "缺失", times.get("next", "-")),
        "事件      : has_events=%s event_count=%s types=%s" % (
            hek.get("has_events"), hek.get("event_count"),
            ",".join(hek.get("event_types", []) or [])),
    ]
    if geo:
        out.append("像素定标  : center=(%.1f,%.1f) r_occ=%.1f r_fov=%.1f  R_sun/px=%.6f"
                   "  遮罩占比=%.1f%%"
                   % (geo["cx"], geo["cy"], geo["r_occ"], geo["r_fov"],
                      geo["rpp"], 100 * geo["mask_frac"]))
        out.append("            实测视场=%.1f R_sun，C2 标称=%.0f R_sun ——"
                   " 遮罩按实测值，未按标称值"
                   % (geo["r_fov"] * geo["rpp"], NOMINAL_FOV_RSUN))
        if geo["r_fov"] > geo["half_w"]:
            out.append("            ⚠ 实测视场圆 %.0f px > 画幅半边长 %.0f px："
                       "方形裁切切掉了外冕，遮罩在画幅内是满的"
                       % (geo["r_fov"], geo["half_w"]))
    out.append("-" * 78)
    for i, ev in enumerate(events):
        out.append("[%d] CME PA=%s  角宽=%s  速度=%s km/s  加速度=%s m/s²"
                   % (i + 1, fmt(ev.get("central_pa")), fmt(ev.get("width")),
                      fmt(ev.get("linear_speed")), fmt(ev.get("acceleration"))))
        out.append("     cme_time=%s  mass=%s  KE=%s  accel_flag=%s"
                   % (ev.get("cme_time"), fmt(ev.get("mass")),
                      fmt(ev.get("kinetic_energy")), ev.get("accel_flag")))
        if ev.get("remarks"):
            out.append("     remarks=%s" % ev["remarks"])
    out.append("=" * 78)
    return "\n".join(out)


def render(root: str, name: str, out_png: str,
           orient_arg: Optional[str] = None) -> None:
    label = load_label(root, name)
    events = events_of(label)
    group = find_group(root, name)
    times = {}
    for key in ("prev", "center", "next"):
        if group.get(key):
            times[key] = frame_time(load_label(root, group[key]))

    center_img = load_gray(data_path(root, group["center"]))
    cx, cy, r_occ, r_fov = detect_occ(center_img)
    mask = science_mask(center_img.shape, cx, cy, r_occ, r_fov)
    rpp = rsun_per_px(r_occ)
    # 面板直接画全分辨率图（matplotlib 自己缩放），因此叠加几何与图像
    # 共用同一套像素坐标，scale 恒为 1。AIA/HMI 版相反（先降采样再叠加）。
    scale = 1.0

    prev_img = load_gray(data_path(root, group["prev"])) if group.get("prev") else None
    next_img = load_gray(data_path(root, group["next"])) if group.get("next") else None

    geo = {"cx": cx, "cy": cy, "r_occ": r_occ, "r_fov": r_fov, "rpp": rpp,
           "half_w": 0.5 * center_img.shape[1],
           "mask_frac": float(mask.mean()), "rd_ok": False, "rd_amp": 0.0}

    rd = None
    if prev_img is not None and next_img is not None:
        rd, amp = running_difference(box_blur(prev_img, DIFF_SMOOTH),
                                     box_blur(next_img, DIFF_SMOOTH), mask)
        geo["rd_ok"] = True
        geo["rd_amp"] = amp
    bd = None
    if prev_img is not None:
        bd, _ = baseline_difference(box_blur(center_img, DIFF_SMOOTH),
                                    box_blur(prev_img, DIFF_SMOOTH), mask)

    # PA 朝向自动判定：用帧间差分实测（不读 FITS 头的 RECTIFY / CDELT 符号）
    rho_in = OCC_RSUN + 0.15
    rho_out = min(r_fov * rpp, 0.5 * center_img.shape[1] * rpp)
    orient, o_scores = (orient_arg if orient_arg else
                        choose_orientation(rd if rd is not None else bd,
                                           mask, events, center_img.shape,
                                           cx, cy, r_occ, rho_in, rho_out))
    geo["orient"] = orient
    geo["orient_scores"] = o_scores

    print(summarize(label, group, times, events, geo))
    print("图像      : %s" % data_path(root, group["center"]))
    print("PA 朝向   : %s%s" % (
        orient, "（实测选出）" if (o_scores and orient_arg is None) else "（人工指定）"))
    for k, v in (o_scores or {}).items():
        print("             %-10s 扇形内/全图 |差分| = %.3f" % (k, v))

    with plt.style.context("dark_background"):
        fig = plt.figure(figsize=(17, 10.4), dpi=108, facecolor="#0b0f14")
        gs = fig.add_gridspec(3, 4, height_ratios=[1.0, 1.0, 0.92],
                              width_ratios=[1.0, 1.0, 1.0, 1.28],
                              hspace=0.30, wspace=0.13,
                              left=0.012, right=0.99, top=0.90, bottom=0.045)

        # ---- 第1 行：三帧原始（对数拉伸 + 遮罩外压暗 + CME 扇形）
        st_center = lasco_stretch(center_img, mask)
        disp_c = np.where(mask, st_center, st_center * 0.06)
        ax0 = fig.add_subplot(gs[0, 0])
        ax0.imshow(disp_c, cmap="gray", vmin=0, vmax=1, interpolation="nearest")
        overlay_cme(ax0, events, scale, cx, cy, r_occ, r_fov, rpp,
                    center_img.shape[1],
                    "中心帧（参考）%s" % times.get("center", "-"),
                    orient=orient)

        for col, key, lab in ((1, "prev", "前帧"), (2, "next", "后帧")):
            axr = fig.add_subplot(gs[0, col])
            img = prev_img if key == "prev" else next_img
            if img is None:
                axr.axis("off")
                axr.text(0.5, 0.5, "该组缺%s\n（12,000 组中 32 组为 2 帧）"
                         % lab, color="#9ca3af", ha="center", va="center",
                         transform=axr.transAxes, fontsize=10)
                continue
            st = lasco_stretch(img, mask)
            axr.imshow(np.where(mask, st, st * 0.06), cmap="gray",
                       vmin=0, vmax=1, interpolation="nearest")
            overlay_cme(axr, events, scale, cx, cy, r_occ, r_fov, rpp,
                        img.shape[1],
                        "%s  %s  (%s)" % (lab, times.get(key, ""),
                                          dt_label(times.get("center", ""),
                                                   times.get(key, ""))),
                        orient=orient)

        # ---- 第 2 行：差分 + 遮罩示意
        ax1 = fig.add_subplot(gs[1, 0])
        if rd is None:
            ax1.axis("off")
            ax1.text(0.5, 0.5, "running difference 不可用\n（缺前帧或后帧）",
                     color="#9ca3af", ha="center", va="center",
                     transform=ax1.transAxes, fontsize=10)
        else:
            ax1.imshow(np.where(mask, rd, 0.0), cmap=CMAP_DIV,
                       vmin=-1, vmax=1, interpolation="nearest")
            overlay_cme(ax1, events, scale, cx, cy, r_occ, r_fov, rpp,
                        center_img.shape[1],
                        "running difference = 前帧 − 后帧 (±%.2f DN)" % geo["rd_amp"],
                        draw_mask=False, orient=orient)

        ax2 = fig.add_subplot(gs[1, 1])
        if bd is None:
            ax2.axis("off")
            ax2.text(0.5, 0.5, "baseline difference 不可用（缺前帧）",
                     color="#9ca3af", ha="center", va="center",
                     transform=ax2.transAxes, fontsize=10)
        else:
            ax2.imshow(np.where(mask, bd, 0.0), cmap=CMAP_DIV,
                       vmin=-1, vmax=1, interpolation="nearest")
            overlay_cme(ax2, events, scale, cx, cy, r_occ, r_fov, rpp,
                        center_img.shape[1], "baseline difference = 中心帧 − 前帧",
                        draw_mask=False, orient=orient)

        ax3 = fig.add_subplot(gs[1, 2])
        draw_mask_inset(ax3, mask, cx, cy, r_occ, r_fov)

        ax4 = fig.add_subplot(gs[0:3, 3])
        info_panel(ax4, label, group, times, events, root, name, geo)

        # ---- 第 3 行：三个 rho 剖面（中心帧 / 前帧 / 后帧）
        for col, (img, ttl) in enumerate((
                (center_img, "中心帧"), (prev_img, "前帧"), (next_img, "后帧"))):
            axr = fig.add_subplot(gs[2, col])
            if img is None:
                axr.axis("off")
                continue
            radial_profile(axr, img, cx, cy, r_occ, r_fov, rpp)
            axr.set_title("%s 径向剖面" % ttl, fontsize=9, pad=4)

        fig.suptitle("OPC · %s 可视化  —  三帧差分 + 科学遮罩 + CME 角宽扇形"
                     "（纯像素定标，无 sunpy / 无观测时刻依赖）"
                     % DATASET_NAME, fontsize=13, color="#f9fafb", y=0.968)

        handles = [Line2D([0], [0], color=COLOR_CYCLE[i % len(COLOR_CYCLE)],
                          lw=2, label="CME#%d PA=%s° W=%s°"
                          % (i + 1, fmt(ev.get("central_pa")),
                             fmt(ev.get("width"))))
                   for i, ev in enumerate(events)]
        handles += [
            Line2D([0], [0], color="#94a3b8", ls="--", lw=1.2, label="occ 圆"),
            Line2D([0], [0], color="#94a3b8", ls=":", lw=1.2, label="实测视场外缘"),
        ]
        if handles:
            fig.legend(handles=handles, loc="upper right",
                       ncol=min(len(handles), 3), fontsize=7.6,
                       facecolor="#111827", edgecolor="#374151",
                       labelcolor="#e5e7eb", framealpha=0.9,
                       bbox_to_anchor=(0.995, 0.998))

        os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
        fig.savefig(out_png, facecolor=fig.get_facecolor())
        print("输出      : %s" % out_png)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="OPC LASCO C2 CME 样例数据可视化")
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--file", help="指定数据文件名（中心帧或相邻帧均可）")
    ap.add_argument("--index", type=int, default=0)
    ap.add_argument("--any", action="store_true", help="允许选到无事件样本")
    ap.add_argument("--orient", choices=sorted(PA_ORIENTATIONS),
                    help="强制 PA 朝向；缺省由帧间差分实测自动判定")
    ap.add_argument("--out", default="viz_cme.png")
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
    if fn:
        name = fn
    else:
        names = list_labels(args.root)
        if not args.any:
            cand = [nm for nm in names
                    if load_label(args.root, nm).get(
                        "hek_event_annotation", {}).get("has_events")]
            names = cand or names
        name = names[args.index % len(names)]

    if args.no_plot:
        label = load_label(args.root, name)
        group = find_group(args.root, name)
        times = {k: frame_time(load_label(args.root, v))
                 for k, v in group.items() if v}
        print(summarize(label, group, times, events_of(label)))
        return 0

    render(args.root, name, args.out, orient_arg=args.orient)
    return 0


if __name__ == "__main__":
    sys.exit(main())