#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
epb_mask_vis.py
================
读取 EPB（Equatorial Plasma Bubble，赤道等离子体泡）标注 JSON 文件，
解析 ``science_annotation.mask`` 中的多边形 ``shapes``，生成目标"掩膜"（mask），
并将掩膜与原图像叠加，输出可视化结果。

标注文件为 LabelMe 风格结构::

    {
      "label_header":       {...},
      "basic_annotation":   {"fileName": "xxx.png", ...},
      "science_annotation": {
          "evenType": "Equatorial Plasma Bubble",
          "mask": {
              "version": "5.1.1",
              "flags": {},
              "shapes": [ {"label":"EPB", "points":[[x,y],...],
                           "shape_type":"polygon", "group_id":null, "flags":{}} ],
              "imagePath":   "xxx.png",
              "imageData":   null,
              "imageHeight": 1024,
              "imageWidth":  1024
          },
          "varList": {...}
      },
      "extension_annotation": {}
    }

用法
----
1) 处理脚本同目录下的默认样例::

       python epb_mask_vis.py

2) 指定单个 JSON / 图像::

       python epb_mask_vis.py --json xxx.json --image xxx.png --outdir ./out

3) 批量处理某个目录下所有 JSON::

       python epb_mask_vis.py --batch /path/to/dir --outdir /path/to/out

输出（写入 ``--outdir``，默认为 JSON 所在目录）
---------------------------------------------
* ``<name>_mask.png``      : 二值掩膜（白=目标区，黑=背景）
* ``<name>_overlay.png``   : 原图 + 彩色半透明掩膜 + 轮廓
* ``<name>_cutout.png``    : 仅保留掩膜区域的原图（背景置黑）
* ``<name>_vis.png``       : 四联图（原图 | 掩膜 | 彩色叠加 | 轮廓叠加）
* ``<name>_zoom.png``      : 掩膜区域局部放大（裁剪原图 | 裁剪叠加），便于小目标核对

依赖: numpy, opencv-python
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_JSON = os.path.join(
    HERE, "adjust20221114224215_QuJ408_3_6300_180000_B1_G3__flipv.json")

# 各标签对应颜色 (BGR)；未列出的标签自动分配
LABEL_COLORS = {
    "EPB": (0, 0, 255),          # 红色
    "bubble": (0, 0, 255),
}
_PALETTE = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255),
            (255, 0, 255), (255, 255, 0), (0, 128, 255), (128, 0, 255)]


# ---------------------------------------------------------------------------
# 标注解析
# ---------------------------------------------------------------------------
def load_annotation(json_path):
    """读取标注 JSON，返回关键字段。"""
    with open(json_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    basic = data.get("basic_annotation", {}) or {}
    sci = data.get("science_annotation", {}) or {}
    mask_obj = sci.get("mask", {}) or {}

    # 图像名优先级: mask.imagePath > basic_annotation.fileName
    img_name = mask_obj.get("imagePath") or basic.get("fileName")
    shapes = mask_obj.get("shapes", []) or []
    width = mask_obj.get("imageWidth")
    height = mask_obj.get("imageHeight")

    return {
        "data": data,
        "basic": basic,
        "science": sci,
        "mask_obj": mask_obj,
        "image_name": img_name,
        "shapes": shapes,
        "width": width,
        "height": height,
    }


def resolve_image_path(json_path, info, image_arg=None):
    """定位与 JSON 对应的原图。"""
    if image_arg:
        return image_arg
    name = info["image_name"]
    if not name:
        return None
    cand = os.path.join(os.path.dirname(os.path.abspath(json_path)), name)
    if os.path.exists(cand):
        return cand
    # 常见退回: 同级 data/ 子目录
    cand2 = os.path.join(os.path.dirname(os.path.abspath(json_path)), "data", name)
    if os.path.exists(cand2):
        return cand2
    return cand


# ---------------------------------------------------------------------------
# 掩膜生成
# ---------------------------------------------------------------------------
def _draw_one_shape(layer, shape):
    """把单个 shape 画进 layer (uint8 单通道)。"""
    stype = (shape.get("shape_type") or "polygon").lower()
    pts = shape.get("points") or []
    if len(pts) < 1:
        return

    arr = np.asarray(pts, dtype=np.float64)

    if stype == "polygon":
        if len(pts) >= 3:
            poly = np.round(arr).astype(np.int32).reshape(-1, 1, 2)
            cv2.fillPoly(layer, [poly], 255)

    elif stype == "rectangle":
        if len(pts) >= 2:
            x1, y1 = arr[0]
            x2, y2 = arr[1]
            cv2.rectangle(layer,
                          (int(round(x1)), int(round(y1))),
                          (int(round(x2)), int(round(y2))), 255, -1)

    elif stype == "circle":
        if len(pts) >= 2:
            cx, cy = arr[0]
            ex, ey = arr[1]
            r = int(round(np.hypot(ex - cx, ey - cy)))
            cv2.circle(layer, (int(round(cx)), int(round(cy))), r, 255, -1)

    elif stype == "line":
        # 线条不作为区域掩膜，仅记录
        pass

    else:  # 未知类型，按多边形尽力处理
        if len(pts) >= 3:
            poly = np.round(arr).astype(np.int32).reshape(-1, 1, 2)
            cv2.fillPoly(layer, [poly], 255)


def build_mask(shapes, height, width):
    """由 shapes 生成二值掩膜及按标签分层的掩膜字典。"""
    mask = np.zeros((height, width), dtype=np.uint8)
    per_label = {}
    for shape in shapes:
        label = shape.get("label") or "object"
        layer = per_label.get(label)
        if layer is None:
            layer = np.zeros((height, width), dtype=np.uint8)
            per_label[label] = layer
        _draw_one_shape(layer, shape)
        mask = np.maximum(mask, layer)
    return mask, per_label


# ---------------------------------------------------------------------------
# 可视化
# ---------------------------------------------------------------------------
def _color_for(label, idx):
    if label in LABEL_COLORS:
        return LABEL_COLORS[label]
    return _PALETTE[idx % len(_PALETTE)]


def make_colored_mask(per_label, height, width):
    """生成彩色掩膜底图 (H,W,3)。"""
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    for idx, (label, layer) in enumerate(per_label.items()):
        color = _color_for(label, idx)
        sel = layer > 0
        canvas[sel] = color
    return canvas


def make_overlay(image, per_label, alpha=0.5, draw_contour=True, thickness=2):
    """原图 + 彩色半透明掩膜 + 轮廓。"""
    overlay = image.copy().astype(np.float32)
    for idx, (label, layer) in enumerate(per_label.items()):
        color = np.array(_color_for(label, idx), dtype=np.float32)
        sel = layer > 0
        overlay[sel] = (1.0 - alpha) * overlay[sel] + alpha * color
    overlay = np.clip(overlay, 0, 255).astype(np.uint8)

    if draw_contour:
        for idx, (label, layer) in enumerate(per_label.items()):
            color = _color_for(label, idx)
            cnts, _ = cv2.findContours(layer, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(overlay, cnts, -1, color, thickness)
    return overlay


def make_cutout(image, mask):
    """仅保留掩膜区域的原图，其余置黑。"""
    out = np.zeros_like(image)
    out[mask > 0] = image[mask > 0]
    return out


def _panel(image, title):
    """给图像加标题栏（顶部黑条 + 白字）。"""
    img = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    bar = np.zeros((34, img.shape[1], 3), dtype=np.uint8)
    cv2.putText(bar, title, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, img])


def make_quad(original, mask, overlay, contour_only):
    """四联图: 原图 | 掩膜 | 彩色叠加 | 轮廓叠加。"""
    mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    panels = [
        _panel(original, "1. Original image"),
        _panel(mask_bgr, "2. Binary mask"),
        _panel(overlay, "3. Mask overlay"),
        _panel(contour_only, "4. Contour only"),
    ]
    top = np.hstack(panels[0:2])
    bot = np.hstack(panels[2:4])
    sep = np.zeros((4, top.shape[1], 3), dtype=np.uint8)
    return np.vstack([top, sep, bot])


def make_zoom(original, overlay, mask, pad=64, min_side=360, max_scale=8.0):
    """围绕掩膜 bbox 裁剪并放大，输出 (原图裁剪 | 叠加裁剪) 并排图。

    EPB 等目标在全天空图像中往往很小（<1% 像素），整图叠加难以看清，
    因此额外给出局部放大视图。
    """
    ys, xs = np.where(mask > 0)
    if ys.size == 0:
        return None
    h, w = original.shape[:2]
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(w, int(xs.max()) + pad + 1)
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(h, int(ys.max()) + pad + 1)
    crop_o = original[y0:y1, x0:x1]
    crop_v = overlay[y0:y1, x0:x1]
    ch, cw = crop_o.shape[:2]
    scale = min(max_scale, max(1.0, float(min_side) / max(1, min(ch, cw))))
    interp = cv2.INTER_NEAREST if scale > 2 else cv2.INTER_LINEAR
    new_size = (int(round(cw * scale)), int(round(ch * scale)))
    crop_o = cv2.resize(crop_o, new_size, interpolation=interp)
    crop_v = cv2.resize(crop_v, new_size, interpolation=interp)
    sep = np.zeros((crop_o.shape[0], 6, 3), dtype=np.uint8)
    both = np.hstack([crop_o, sep, crop_v])
    title = "Zoom x%.1f  (bbox x:%d-%d y:%d-%d, pad=%d)" % (
        scale, int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max()), pad)
    bar = np.zeros((34, both.shape[1], 3), dtype=np.uint8)
    cv2.putText(bar, title, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bar, both])


# ---------------------------------------------------------------------------
# 单文件处理
# ---------------------------------------------------------------------------
def process_one(json_path, outdir=None, image_arg=None, alpha=0.5,
                color_override=None, quiet=False, zoom_pad=64):
    info = load_annotation(json_path)
    img_path = resolve_image_path(json_path, info, image_arg)

    base = os.path.splitext(os.path.basename(json_path))[0]
    outdir = outdir or os.path.dirname(os.path.abspath(json_path))
    os.makedirs(outdir, exist_ok=True)

    image = cv2.imread(img_path, cv2.IMREAD_COLOR) if img_path else None
    height = info["height"]
    width = info["width"]

    if image is None:
        # 无原图时仅按标注尺寸生成掩膜
        if not height or not width:
            raise FileNotFoundError("找不到原图且标注中无 imageWidth/Height: %s" % json_path)
        image = np.zeros((height, width, 3), dtype=np.uint8)
        if not quiet:
            print("  [!] 未找到原图，使用 %dx%d 全黑背景" % (width, height))
    else:
        height, width = image.shape[:2]

    if color_override:
        for k in list(LABEL_COLORS.keys()):
            LABEL_COLORS[k] = color_override

    mask, per_label = build_mask(info["shapes"], height, width)
    colored = make_colored_mask(per_label, height, width)
    overlay = make_overlay(image, per_label, alpha=alpha)
    cutout = make_cutout(image, mask)
    # 轮廓叠加（白底黑轮廓更适合看形状，这里用纯黑底）
    contour_only = np.zeros_like(image)
    for idx, (label, layer) in enumerate(per_label.items()):
        cnts, _ = cv2.findContours(layer, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(contour_only, cnts, -1, _color_for(label, idx), 2)
    quad = make_quad(image, mask, overlay, contour_only)
    zoom = make_zoom(image, overlay, mask, pad=zoom_pad) \
        if zoom_pad is not None else None

    paths = {
        "mask": os.path.join(outdir, base + "_mask.png"),
        "overlay": os.path.join(outdir, base + "_overlay.png"),
        "cutout": os.path.join(outdir, base + "_cutout.png"),
        "vis": os.path.join(outdir, base + "_vis.png"),
    }
    if zoom is not None:
        paths["zoom"] = os.path.join(outdir, base + "_zoom.png")
    cv2.imwrite(paths["mask"], mask)
    cv2.imwrite(paths["overlay"], overlay)
    cv2.imwrite(paths["cutout"], cutout)
    cv2.imwrite(paths["vis"], quad)
    if zoom is not None:
        cv2.imwrite(paths["zoom"], zoom)

    if not quiet:
        n_obj = int((mask > 0).sum())
        ratio = 100.0 * n_obj / float(height * width)
        ys, xs = np.where(mask > 0)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) \
            if n_obj else None
        labels = ", ".join("%s x%d" % (l, len(info["shapes"]))
                           for l in per_label) or "(none)"
        print("=" * 68)
        print("JSON      : %s" % json_path)
        print("Image     : %s  (%dx%d)" % (img_path, width, height))
        print("evenType  : %s" % info["science"].get("evenType"))
        print("Shapes    : %d  [%s]" % (len(info["shapes"]), labels))
        print("Mask px   : %d  (%.3f%%)" % (n_obj, ratio))
        print("Mask bbox : %s" % (bbox,))
        print("Outputs   :")
        for k, v in paths.items():
            print("   - %s" % v)

    return paths


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="读取 EPB 标注 JSON，生成掩膜并与原图叠加可视化。")
    ap.add_argument("--json", default=None,
                    help="标注 JSON 路径（默认使用脚本同目录的样例）")
    ap.add_argument("--image", default=None, help="原图路径（默认按 JSON 自动定位）")
    ap.add_argument("--batch", default=None,
                    help="批量模式：处理该目录下所有 *.json")
    ap.add_argument("--recursive", action="store_true", help="批量模式递归子目录")
    ap.add_argument("--outdir", default=None,
                    help="输出目录（默认与 JSON 同目录）")
    ap.add_argument("--alpha", type=float, default=0.5,
                    help="掩膜叠加透明度 0~1（默认 0.5）")
    ap.add_argument("--color", default=None,
                    help="RGBA/BGR 颜色覆盖，如 '0,0,255'（默认按标签配色）")
    ap.add_argument("--zoom-pad", type=int, default=64,
                    help="局部放大图的裁剪留白像素（默认 64）")
    ap.add_argument("--no-zoom", action="store_true", help="不生成局部放大图")
    args = ap.parse_args(argv)

    color_override = None
    if args.color:
        color_override = tuple(int(c) for c in args.color.split(","))
    zoom_pad = None if args.no_zoom else args.zoom_pad

    if args.batch:
        pattern = "**/*.json" if args.recursive else "*.json"
        files = sorted(glob.glob(os.path.join(args.batch, pattern),
                                 recursive=args.recursive))
        files = [f for f in files if os.sep + "data" + os.sep not in f]
        if not files:
            print("[!] 目录下未找到 JSON: %s" % args.batch)
            return 1
        print("批量处理 %d 个标注文件 ..." % len(files))
        ok = 0
        for f in files:
            try:
                process_one(f, outdir=args.outdir, alpha=args.alpha,
                            color_override=color_override, quiet=True,
                            zoom_pad=zoom_pad)
                ok += 1
                print("  [OK] %s" % os.path.basename(f))
            except Exception as exc:            # noqa: BLE001
                print("  [FAIL] %s -> %s" % (os.path.basename(f), exc))
        print("-" * 68)
        print("完成: %d/%d" % (ok, len(files)))
        return 0 if ok else 1

    json_path = args.json or DEFAULT_JSON
    if not os.path.exists(json_path):
        print("[!] 找不到标注文件: %s" % json_path)
        return 1
    process_one(json_path, outdir=args.outdir, image_arg=args.image,
                alpha=args.alpha, color_override=color_override,
                zoom_pad=zoom_pad)
    return 0


if __name__ == "__main__":
    sys.exit(main())
