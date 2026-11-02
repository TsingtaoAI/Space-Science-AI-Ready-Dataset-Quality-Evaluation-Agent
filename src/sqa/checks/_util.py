from __future__ import annotations

import math
import re
import struct
from datetime import datetime, timezone
from typing import Any

from ..dataset import DatasetContext, Record, get_path, is_missing

TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v),
    "boolean": lambda v: isinstance(v, bool),
}

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def parse_time(v: Any) -> datetime | None:
    """解析 ISO 8601 及 'YYYY-MM-DD HH:MM:SS'；无时区按 UTC（公开数据中两种写法混用）。"""
    if not isinstance(v, str) or not v.strip():
        return None
    s = v.strip().replace("Z", "+00:00")
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def as_number(v: Any) -> float | None:
    """数值或数值字符串 → float；其余（含 bool、空串）→ None。"""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def events(ctx: DatasetContext, rec: Record) -> list[dict]:
    cfg = ctx.profile_spec(rec).get("events")
    if not cfg:
        return []
    lst = get_path(ctx.label(rec), f"{cfg['block']}.{cfg['list']}")
    return [e for e in lst if isinstance(e, dict)] if isinstance(lst, list) else []


def events_ptr(ctx: DatasetContext, rec: Record, i: int | None = None, key: str | None = None) -> str:
    cfg = ctx.profile_spec(rec)["events"]
    p = f"{cfg['block']}.{cfg['list']}"
    if i is not None:
        p += f"[{i}]"
    if key:
        p += f".{key}"
    return p


def region_of(points) -> tuple | None:
    """多边形或矩形角点的外接框 (x1, y1, x2, y2)；坐标缺失或非数值时返回 None（证据位置只到标注记录）。"""
    try:
        xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
    except (TypeError, ValueError, IndexError):
        return None
    if not xs:
        return None
    return (round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2))


def shapes(ctx: DatasetContext, rec: Record) -> tuple[str | None, list]:
    """返回 (形状列表路径, 形状列表)；HEK 事件多边形也作为形状返回。"""
    prof = ctx.profile_spec(rec)
    if "shapes" in prof:
        path = prof["shapes"]["path"]
        lst = get_path(ctx.label(rec), path)
        return path, (lst if isinstance(lst, list) else [])
    ev = prof.get("events", {})
    key = ev.get("polygon_key")
    if key:
        out = []
        for e in events(ctx, rec):
            pts = e.get(key)
            # HEK 源数据中部分事件本就没有多边形（null 或空数组），视为无几何，不算缺陷
            if pts in (None, []):
                out.append(None)
            else:
                out.append({"points": pts, "shape_type": "polygon", "label": e.get(ev.get("type_key"))})
        return f"{ev['block']}.{ev['list']}", out
    return None, []


def png_info(ctx: DatasetContext, rel: str) -> dict | None:
    """读 PNG 文件头（只读前 33 字节与末尾 12 字节），结果缓存。"""
    cache = ctx.cache.setdefault("png", {})
    if rel in cache:
        return cache[rel]
    path = ctx.root / rel
    info = None
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(33)
            f.seek(max(0, size - 12))
            tail = f.read(12)
        ok_sig = head[:8] == _PNG_SIG and head[12:16] == b"IHDR"
        w, h = struct.unpack(">II", head[16:24]) if ok_sig else (None, None)
        info = {"valid_header": ok_sig, "width": w, "height": h,
                "has_iend": tail[4:8] == b"IEND", "bytes": size}
    except OSError as e:
        info = {"error": str(e)}
    cache[rel] = info
    return info


_SIZE_TEXT = re.compile(r"(\d+)\s*[x×*]\s*(\d+)")


def declared_size(ctx: DatasetContext, rec: Record) -> tuple[int, int, str] | None:
    """标签声明的图像宽高及其来源字段。"""
    cfg = ctx.profile_spec(rec).get("image_size") or {}
    doc = ctx.label(rec)
    if "width" in cfg:
        w, h = get_path(doc, cfg["width"]), get_path(doc, cfg["height"])
        if not is_missing(w) and not is_missing(h) and as_number(w) and as_number(h):
            return int(as_number(w)), int(as_number(h)), f"{cfg['width']}/{cfg['height']}"
    if "text" in cfg:
        t = get_path(doc, cfg["text"])
        m = _SIZE_TEXT.search(t) if isinstance(t, str) else None
        if m:
            return int(m.group(1)), int(m.group(2)), cfg["text"]
    return None


def declared_sizes(ctx: DatasetContext, rec: Record) -> list[tuple[int, int, str]]:
    """标签中全部的图像尺寸声明（宽高字段、尺寸文本各算一处）。"""
    cfg = ctx.profile_spec(rec).get("image_size") or {}
    doc = ctx.label(rec)
    out = []
    if "width" in cfg:
        w, h = get_path(doc, cfg["width"]), get_path(doc, cfg["height"])
        if not is_missing(w) and not is_missing(h) and as_number(w) and as_number(h):
            out.append((int(as_number(w)), int(as_number(h)), f"{cfg['width']}/{cfg['height']}"))
    if "text" in cfg:
        t = get_path(doc, cfg["text"])
        m = _SIZE_TEXT.search(t) if isinstance(t, str) else None
        if m:
            out.append((int(m.group(1)), int(m.group(2)), cfg["text"]))
    return out


def image_size(ctx: DatasetContext, rec: Record) -> tuple[int, int, str] | None:
    """优先用 PNG 文件头的实际尺寸，其次用声明值。"""
    rel = ctx.data_file_of(rec)
    if rel and rel.lower().endswith(".png"):
        info = png_info(ctx, rel)
        if info and info.get("valid_header"):
            return info["width"], info["height"], f"PNG 文件头 {rel}"
    return declared_size(ctx, rec)


def csv_scan(ctx: DatasetContext, rel: str) -> dict:
    """一次读入时序 CSV 并按列扫描，结果缓存，供 N02 与 D01 共用。

    按列整体转换（map(float) 走 C 实现），只有出错的列才逐个定位行号。
    """
    import collections
    import csv
    import itertools

    cache = ctx.cache.setdefault("csv", {})
    if rel in cache:
        return cache[rel]
    out: dict = {"error": None, "header": [], "rows": 0, "bad": {}, "max_run": {}, "run_threshold": 0}
    try:
        text = (ctx.root / rel).read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as e:
        out["error"] = str(e)
        cache[rel] = out
        return out
    lines = text.splitlines()
    while lines and not lines[-1]:
        lines.pop()
    if not lines or not lines[0].strip():
        out["error"] = "CSV 为空或缺表头"
        cache[rel] = out
        return out
    header = [c.strip() for c in lines[0].split(",")]
    width = len(header)
    n = len(lines) - 1
    bad: dict[str, list[int]] = {}
    flat = ",".join(lines[1:]).split(",") if n else []
    if '"' not in text and len(flat) == n * width and all(line.count(",") == width - 1 for line in lines[1:]):
        # 快速通道：纯数值、无引号、行宽一致，按列步长取值
        rownos = list(range(2, n + 2))
        cols = [flat[j::width] for j in range(width)] if n else [[] for _ in range(width)]
    else:
        try:
            body = list(csv.reader(lines[1:]))
        except csv.Error as e:
            out["error"] = str(e)
            cache[rel] = out
            return out
        good = []
        for i, row in enumerate(body, start=2):
            if len(row) != width:
                bad.setdefault("列数不符", []).append(i)
            else:
                good.append((i, row))
        rownos = [i for i, _ in good]
        cols = list(zip(*[r for _, r in good])) if good else [()] * width
    out["header"], out["rows"] = header, n
    ranges = ctx.spec.raw["physical_ranges"]
    ts_cfg = ctx.spec.raw.get("timeseries", {})
    fill_values = set(ts_cfg.get("fill_values", []))
    run_threshold = max(ts_cfg.get("min_fill_run", 60), n // 10)
    for c, col in zip(header, cols):
        try:
            xs = list(map(float, col))
            finite = math.isfinite(sum(xs))   # NaN/Inf 会传染到和；极少数溢出时退回逐个检查
        except (ValueError, OverflowError):
            xs, finite = None, False
        if xs is None or not finite:
            xs2 = []
            for i, v in zip(rownos, col):
                try:
                    x = float(v)
                except ValueError:
                    x = math.nan
                if not math.isfinite(x):
                    bad.setdefault(f"{c} 非数值", []).append(i)
                xs2.append(x)
            xs = xs2
            vals = [x for x in xs if math.isfinite(x)]
        else:
            vals = xs
        r = ranges.get(f"csv.{c}")
        if r and vals:
            lo, hi = r.get("min", -math.inf), r.get("max", math.inf)
            if min(vals) < lo or max(vals) > hi:
                bad[f"{c} 超出量程"] = [i for i, x in zip(rownos, xs) if math.isfinite(x) and (x < lo or x > hi)]
        if c != "t" and xs and fill_values:
            # 只看填充值的连续段；某填充值出现总次数不到阈值时不可能有达标的连续段
            counts = collections.Counter(x for x in xs if x in fill_values)
            for v, total in counts.items():
                if total >= run_threshold:
                    run = max(sum(1 for _ in g) for k, g in itertools.groupby(xs) if k == v)
                    if run >= run_threshold and run > out["max_run"].get(c, (None, 0))[1]:
                        out["max_run"][c] = (v, run)
    out["bad"] = bad
    out["run_threshold"] = run_threshold
    cache[rel] = out
    return out


def polygon_area(points: list) -> float:
    n = len(points)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = points[i][0], points[i][1]
        x2, y2 = points[(i + 1) % n][0], points[(i + 1) % n][1]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


__all__ = ["TYPE_CHECKS", "parse_time", "as_number", "events", "events_ptr", "shapes", "png_info",
           "declared_size", "image_size", "polygon_area", "get_path", "is_missing", "csv_scan"]
