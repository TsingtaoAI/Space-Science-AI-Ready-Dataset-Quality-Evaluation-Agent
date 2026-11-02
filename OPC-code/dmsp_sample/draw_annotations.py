# -*- coding: utf-8 -*-
"""
DMSP SSUSI 标注可视化程序（Linux 服务器版，相对路径）
====================================================
读取 SpaceAI-autoPCA 生成的 JSON 标注文件（science_annotation.specialShapes），
把矩形标注框（PCA / TPA 等）绘制到对应的 PNG 影像上，输出带框的可视化图。

说明：
  - 所有路径均可使用相对路径（相对当前工作目录 CWD），也支持绝对路径；
  - 默认输出目录为 ./annotated/，可通过 --outdir 修改；
  - 适配 Linux（字体优先 /usr/share/fonts 等系统路径），Windows 字体仅作兜底。

用法：
    单文件：
        python draw_annotations.py <标注.json> [影像.png] [-o 输出.png]
        # 不提供影像.png 时，自动在同目录（或 CWD）查找同名 .png
        # 不提供 -o 时，输出到 <--outdir>/<影像名>_annotated.png

    批量（整个目录）：
        python draw_annotations.py <目录> [--outdir 输出目录]
        # 处理目录下所有 *.json，默认输出到 ./annotated/

依赖：Pillow（pip install Pillow）
"""

import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 各类别标注框的颜色 (R,G,B)
LABEL_COLORS = {
    "PCA": (255, 60, 60),      # 亮红
    "TPA": (60, 220, 255),     # 亮青
    "PCA_T": (255, 180, 0),    # 橙
    "TPA_T": (120, 255, 120),  # 亮绿
    "default": (255, 255, 0),  # 默认黄
}

BOX_LINE_WIDTH = 2
LABEL_FONT_SIZE = 13

# 字体候选：优先 Linux 系统字体，其次 Windows（兜底，Linux 上不存在也无妨）
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/simhei.ttf",
]


def load_font(size):
    """加载一个可用的 TTF 字体，失败则用 PIL 默认字体。"""
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def parse_annotation(json_path):
    """读取 JSON，返回 (basic, shapes)。shapes 为矩形框 dict 列表。"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    shapes = []
    science = data.get("science_annotation", {}) or {}
    for s in science.get("specialShapes", []) or []:
        points = s.get("points", []) or []
        if len(points) < 2:
            continue
        shapes.append({
            "label": s.get("label", "unknown"),
            "shape_type": s.get("shape_type", "rectangle"),
            # 矩形用两个对角点表示，归一化为左上/右下
            "x1": min(points[0][0], points[1][0]),
            "y1": min(points[0][1], points[1][1]),
            "x2": max(points[0][0], points[1][0]),
            "y2": max(points[0][1], points[1][1]),
        })
    return science, shapes


def resolve_png(json_path):
    """根据 JSON 路径推断同名 PNG：先查 JSON 同目录，再查 CWD。"""
    base = Path(json_path).with_suffix(".png")
    if base.exists():
        return str(base)
    cwd_candidate = Path(os.getcwd()) / base.name
    if cwd_candidate.exists():
        return str(cwd_candidate)
    return str(base)  # 返回同目录路径，交由调用方报错


def draw_annotations(img_path, json_path, out_path):
    """在影像上绘制标注框并保存。"""
    basic, shapes = parse_annotation(json_path)

    img = Image.open(img_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    font = load_font(LABEL_FONT_SIZE)

    w, h = img.size
    iw = int(basic.get("imageWidth", 0) or 0)
    ih = int(basic.get("imageHeight", 0) or 0)
    if iw > 0 and ih > 0 and (iw, ih) != (w, h):
        print(f"[警告] JSON 尺寸 ({iw}x{ih}) 与实际影像尺寸 ({w}x{h}) 不一致")

    print(f"  影像: {Path(img_path).name} ({w}x{h}), 标注框 {len(shapes)} 个")
    for idx, s in enumerate(shapes, 1):
        color = LABEL_COLORS.get(s.get("label"), LABEL_COLORS["default"])
        x1, y1 = max(0, int(round(s["x1"]))), max(0, int(round(s["y1"])))
        x2, y2 = min(w - 1, int(round(s["x2"]))), min(h - 1, int(round(s["y2"])))
        label = s.get("label", "unknown")

        draw.rectangle([x1, y1, x2, y2], outline=color, width=BOX_LINE_WIDTH)

        # 标签文字放在框上方，溢出画面上边界时移到框内
        text = label
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx, ty = x1, y1 - th - 3
        if ty < 0:
            ty = y1 + 3
        draw.rectangle([tx, ty, tx + tw + 4, ty + th + 4], fill=color)
        draw.text((tx + 2, ty + 1), text, fill=(0, 0, 0), font=font)

        print(f"    [{idx}] {label:<6s} ({x1},{y1}) -> ({x2},{y2})  尺寸 {x2-x1+1}x{y2-y1+1}")

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"  已保存: {out_path}")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="DMSP SSUSI 标注框可视化（Linux/相对路径）")
    parser.add_argument("input", help="JSON 标注文件路径，或包含多个 JSON 的目录路径")
    parser.add_argument("png", nargs="?", help="影像 PNG 路径（可选，默认自动查找同名 PNG）")
    parser.add_argument("-o", "--output", help="输出 PNG 路径（仅单文件模式有效）")
    parser.add_argument("--outdir", default="annotated",
                        help="批量/默认输出目录（相对 CWD，默认 ./annotated）")
    args = parser.parse_args()

    inp = args.input

    # 批量模式
    if os.path.isdir(inp):
        jsons = sorted(
            f for f in os.listdir(inp)
            if f.lower().endswith(".json")
        )
        if not jsons:
            print("目录中没有 .json 文件")
            return 1
        print(f"批量处理 {len(jsons)} 个标注文件，输出目录: {args.outdir}/ ...")
        ok = 0
        for jf in jsons:
            jpath = str(Path(inp) / jf)
            png = resolve_png(jpath)
            if not os.path.exists(png):
                print(f"[跳过] 找不到影像: {png}")
                continue
            out = str(Path(args.outdir) / (Path(jf).stem + "_annotated.png"))
            try:
                draw_annotations(png, jpath, out)
                ok += 1
            except Exception as e:
                print(f"[失败] {jpath}: {e}")
        print(f"完成 {ok}/{len(jsons)} 个文件")
        return 0

    # 单文件模式
    jpath = inp
    if not os.path.exists(jpath):
        print(f"文件不存在: {jpath}")
        return 1
    png = args.png or resolve_png(jpath)
    if not os.path.exists(png):
        print(f"找不到对应影像: {png}")
        return 1
    out = args.output or str(Path(args.outdir) / (Path(jpath).stem + "_annotated.png"))
    draw_annotations(png, jpath, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
