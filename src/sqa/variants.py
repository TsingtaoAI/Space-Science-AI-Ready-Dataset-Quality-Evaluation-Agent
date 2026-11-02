"""质量变体对照引擎。

组委会说明：各数据集附有「由同一基础数据构造的不同质量版本」。公开数据中，同一组变体内
同名样本一一对应（文件名一致），缺陷注入发生在个别版本的个别样本上。因此对每个样本取
各版本的多数一致结果作为参考，偏离参考的版本即存在缺陷；元素级逐一匹配后可区分漏标、
虚标、错标、位置偏移，并给出偏移量等程度计量。

确认规则：同一样本至少 3 个版本完全一致、且超过参与版本数的一半，才作为参考；
否则相关差异只落「待判定」，不判缺陷。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .codes import field_code
from .dataset import DatasetContext, Record, get_path, is_missing

PARTIAL_HASH_LIMIT = 8 << 20   # 小于该大小的数据文件全量哈希，更大的取 大小 + 首尾各 1 MB
_EDGE = 1 << 20


def sample_key(name: str) -> str:
    p = PurePosixPath(name)
    return p.stem if p.suffix else p.name


def fingerprint(path, cache: dict | None = None) -> str:
    """数据文件指纹：<= 8MB 全量 SHA-256，否则 大小 + 首尾各 1MB 的 SHA-256。可传入缓存避免重复读取。"""
    key = str(path)
    if cache is not None and key in cache:
        return cache[key]
    fp = _fingerprint(path)
    if cache is not None:
        cache[key] = fp
    return fp


def _fingerprint(path) -> str:
    size = path.stat().st_size
    h = hashlib.sha256(str(size).encode())
    with path.open("rb") as f:
        if size <= PARTIAL_HASH_LIMIT:
            while chunk := f.read(_EDGE):
                h.update(chunk)
        else:
            h.update(f.read(_EDGE))
            f.seek(size - _EDGE)
            h.update(f.read(_EDGE))
    return h.hexdigest()


def canon(x: Any) -> str:
    return json.dumps(x, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 变体分组
# ---------------------------------------------------------------------------

def sample_keys(ctx: DatasetContext) -> set[str]:
    return {sample_key(r.expected_data) for r in ctx.records} | {sample_key(PurePosixPath(d).name) for d in ctx.data_files}


def group_variants(ctxs: list[DatasetContext], min_jaccard: float) -> list[list[DatasetContext]]:
    """同一标注族、样本名集合 Jaccard 相似度达到阈值的数据集归为一组（并查集）。"""
    keys = {c.dataset_id: sample_keys(c) for c in ctxs}
    parent = {c.dataset_id: c.dataset_id for c in ctxs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, a in enumerate(ctxs):
        for b in ctxs[i + 1:]:
            if a.dominant_profile != b.dominant_profile or not a.dominant_profile:
                continue
            ka, kb = keys[a.dataset_id], keys[b.dataset_id]
            if ka and kb and len(ka & kb) / len(ka | kb) >= min_jaccard:
                parent[find(a.dataset_id)] = find(b.dataset_id)
    groups = defaultdict(list)
    for c in ctxs:
        groups[find(c.dataset_id)].append(c)
    return [sorted(g, key=lambda c: c.dataset_id) for g in sorted(groups.values(), key=lambda g: min(c.dataset_id for c in g))]


# ---------------------------------------------------------------------------
# 标注元素
# ---------------------------------------------------------------------------

@dataclass
class Element:
    index: int
    raw: dict
    label: Any
    points: list | None
    start: float | None
    end: float | None


def _ts(v) -> float | None:
    from .checks._util import parse_time
    t = parse_time(v)
    return t.timestamp() if t else None


def element_spec(profile: dict) -> tuple:
    """(元素列表路径, 类别键, 几何键, 起始键, 结束键, 时刻键, 身份键列表)。"""
    if "shapes" in profile:
        return profile["shapes"]["path"], "label", "points", None, None, None, ()
    ev = profile.get("events")
    if ev:
        return (f"{ev['block']}.{ev['list']}", ev.get("type_key"), ev.get("polygon_key"),
                ev.get("start_key"), ev.get("end_key"), ev.get("point_time_key"), tuple(ev.get("identity_keys", ())))
    return None, None, None, None, None, None, ()


def elements(doc: dict, profile: dict) -> tuple[str | None, list[Element] | None]:
    path, lk, gk, sk, ek, pk, _ = element_spec(profile)
    if path is None:
        return None, None
    lst = get_path(doc, path)
    if not isinstance(lst, list):
        return path, None
    out = []
    for i, e in enumerate(lst):
        if not isinstance(e, dict):
            continue
        pts = e.get(gk) if gk else None
        start = _ts(e.get(sk)) if sk else None
        end = _ts(e.get(ek)) if ek else None
        if start is None and end is None and pk:
            start = end = _ts(e.get(pk))   # 只有单一时刻的事件
        out.append(Element(i, e, e.get(lk) if lk else None, pts if isinstance(pts, list) and pts else None, start, end))
    return path, out


def _bbox(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _area(points) -> float:
    if len(points) == 2:
        x1, y1, x2, y2 = _bbox(points)
        return (x2 - x1) * (y2 - y1)
    s = 0.0
    for i in range(len(points)):
        x1, y1 = points[i][:2]
        x2, y2 = points[(i + 1) % len(points)][:2]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2


def _center(points):
    x1, y1, x2, y2 = _bbox(points)
    return (x1 + x2) / 2, (y1 + y2) / 2


def _iou(a, b) -> float:
    ax1, ay1, ax2, ay2 = _bbox(a)
    bx1, by1, bx2, by2 = _bbox(b)
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _numeric_points(points) -> bool:
    try:
        return all(len(p) >= 2 and all(isinstance(c, (int, float)) and math.isfinite(c) for c in p[:2])
                   for p in points)
    except TypeError:
        return False


def geometry_measure(ref_pts, var_pts) -> dict | None:
    if not (ref_pts and var_pts and _numeric_points(ref_pts) and _numeric_points(var_pts)):
        return None
    (rx, ry), (vx, vy) = _center(ref_pts), _center(var_pts)
    x1, y1, x2, y2 = _bbox(ref_pts)
    scale = max(1.0, math.sqrt(max(0.0, (x2 - x1) * (y2 - y1))))
    ra, va = _area(ref_pts), _area(var_pts)
    offset = math.hypot(vx - rx, vy - ry)
    return {"offset_px": round(offset, 3), "offset_norm": round(offset / scale, 4),
            "area_ratio": round(va / ra, 4) if ra > 0 else None, "iou": round(_iou(ref_pts, var_pts), 4)}


def time_measure(ref: Element, var: Element) -> dict | None:
    if None in (ref.start, ref.end, var.start, var.end):
        return None
    dur = max(3600.0 if ref.end == ref.start else 1.0, ref.end - ref.start)  # 单一时刻的事件按 1 小时归一
    off = max(abs(var.start - ref.start), abs(var.end - ref.end))
    return {"time_offset_s": round(off, 3), "start_offset_s": round(var.start - ref.start, 3),
            "end_offset_s": round(var.end - ref.end, 3), "relative_deviation": round(off / dur, 4)}


_IGNORED_ATTRS = {"index"}


def _attr_mismatch(a: dict, b: dict, skip: set[str]) -> float:
    keys = (set(a) | set(b)) - skip
    if not keys:
        return 0.0
    return sum(a.get(k) != b.get(k) for k in keys) / len(keys)


def match_elements(ref: list[Element], var: list[Element], keys: tuple) -> tuple[list, list, list]:
    """返回 (配对列表, 参考中未匹配=漏标, 变体中未匹配=虚标)。

    先配完全相同的元素，再按 几何中心距离/目标尺度 + 时间偏移/时长 + 其余属性差异比例 + 类别差异
    的代价贪心配对；代价超过 2.0 视为不是同一目标。
    """
    _, lk, gk, sk, ek, pk, idk = keys
    skip = {k for k in (lk, gk, sk, ek, pk) if k}
    pairs = []
    rem_r, rem_v = list(ref), list(var)
    # ① 身份字段配对：两边都唯一时才采用
    if idk:
        def ident(e):
            vals = tuple(e.raw.get(k) for k in idk)
            return vals if any(v not in (None, "") for v in vals) else None
        rc = Counter(ident(e) for e in rem_r)
        vc = Counter(ident(e) for e in rem_v)
        vmap = {ident(e): e for e in rem_v if ident(e) is not None and vc[ident(e)] == 1}
        for r in list(rem_r):
            k = ident(r)
            if k is not None and rc[k] == 1 and k in vmap:
                v = vmap.pop(k)
                pairs.append((r, v))
                rem_r.remove(r)
                rem_v.remove(v)
    by_canon = defaultdict(list)
    for v in rem_v:
        by_canon[canon(v.raw)].append(v)
    for r in list(rem_r):
        bucket = by_canon.get(canon(r.raw))
        if bucket:
            v = bucket.pop(0)
            pairs.append((r, v))
            rem_r.remove(r)
            rem_v.remove(v)
    cand = []
    for r in rem_r:
        for v in rem_v:
            cost = _attr_mismatch(r.raw, v.raw, skip)
            if r.label != v.label:
                cost += 0.3
            g = geometry_measure(r.points, v.points) if (r.points or v.points) else None
            if r.points or v.points:
                cost += g["offset_norm"] if g else 1.5
            t = time_measure(r, v)
            if t:
                cost += min(t["relative_deviation"], 3.0)
            cand.append((round(cost, 6), r.index, v.index, r, v))
    cand.sort(key=lambda x: x[:3])
    used_r, used_v = set(), set()
    for cost, ri, vi, r, v in cand:
        if cost > 2.0 or ri in used_r or vi in used_v:
            continue
        used_r.add(ri)
        used_v.add(vi)
        pairs.append((r, v))
    missing = [r for r in rem_r if r.index not in used_r]
    spurious = [v for v in rem_v if v.index not in used_v]
    return pairs, missing, spurious


def flatten(doc: dict, skip_path: str | None) -> dict[str, Any]:
    """标签展开为 路径 → 值（两层），跳过元素列表本身。"""
    out = {}
    for blk, v in doc.items():
        if isinstance(v, dict):
            for k, x in v.items():
                p = f"{blk}.{k}"
                if skip_path and (p == skip_path or skip_path.startswith(p + ".")):
                    if isinstance(x, dict):
                        for kk, y in x.items():
                            if f"{p}.{kk}" != skip_path:
                                out[f"{p}.{kk}"] = y
                    continue
                out[p] = x
        else:
            out[blk] = v
    return out


def field_measure(ref_v, var_v) -> dict:
    from .checks._util import as_number, parse_time
    a, b = as_number(ref_v), as_number(var_v)
    if a is not None and b is not None:
        return {"reference": ref_v, "actual": var_v,
                "relative_deviation": round(abs(b - a) / abs(a), 6) if a else None}
    ta, tb = parse_time(ref_v), parse_time(var_v)
    if ta and tb:
        return {"reference": ref_v, "actual": var_v, "time_offset_s": (tb - ta).total_seconds()}
    return {"reference": ref_v, "actual": var_v}


def csv_measure(ref_path, var_path) -> dict:
    """时序数据与参考版本比较：采样点增删、数值改动。"""
    def rows(p):
        with p.open(newline="", encoding="utf-8", errors="replace") as f:
            return list(csv.reader(f))
    a, b = rows(ref_path), rows(var_path)
    n_ref, n_var = max(0, len(a) - 1), max(0, len(b) - 1)
    changed = sum(1 for x, y in zip(a[1:], b[1:]) if x != y) if n_ref == n_var else None
    return {"points_reference": n_ref, "points_actual": n_var, "points_delta": n_var - n_ref,
            "rows_changed": changed,
            "points_change_ratio": round(abs(n_var - n_ref) / n_ref, 6) if n_ref and n_var != n_ref
            else (round(changed / n_ref, 6) if n_ref and changed else 0.0)}


# ---------------------------------------------------------------------------
# 组内比较
# ---------------------------------------------------------------------------

@dataclass
class Diff:
    check_id: str
    dataset_id: str
    unit: str
    status: str          # "fail" | "pending"
    location: dict
    message: str
    evidence: dict = field(default_factory=dict)
    missing: str | None = None


@dataclass
class GroupResult:
    members: list[str]
    samples: int
    confirmed_samples: int
    diffs: list[Diff]
    # dataset_id -> check_id -> 被比较过的单元（用于分母）
    compared: dict[str, dict[str, set]] = field(default_factory=dict)
    totals: dict[str, dict[str, int]] = field(default_factory=dict)
    # dataset_id -> {"reference": Counter, "actual": Counter}：已确认样本上的类别直方图
    hist: dict[str, dict[str, Counter]] = field(default_factory=dict)


def unit_sample(unit: str) -> str:
    """把标签单元、数据文件单元、样本单元统一到样本名。"""
    name = PurePosixPath(unit.split(":", 1)[-1]).name
    return sample_key(name[:-5] if name.endswith(".json") else name)


def select_reference(values: dict[str, str], rank: dict[str, int], invalid: set[str], min_agree: int):
    """返回 (参考内容 | None, 支持该内容的版本数, 参考版本 | None, 规则说明)。规则见规格 variants。"""
    if len(values) < min_agree:
        return None, 0, None, f"参与比较的版本少于 {min_agree} 个"
    cnt = Counter(values.values())
    order = sorted(values, key=lambda d: (rank.get(d, 0), d))
    best = next((d for d in order if d not in invalid), None)
    if best is None:
        top, k = cnt.most_common(1)[0]
        ties = [v for v, m in cnt.items() if m == k]
        if len(ties) == 1 and k >= min_agree and k * 2 > len(values):
            return top, k, None, "各版本记录均未通过独立检查，取多数一致者"
        return None, k, None, "各版本记录均未通过独立检查且无多数版本"
    bv = values[best]
    valid_vals = {v for d, v in values.items() if d not in invalid}
    if cnt[bv] == 1 and any(cnt[v] >= min_agree for v in valid_vals if v != bv):
        return None, 1, best, "质量序最靠前的版本孤立，且另有内容获多个版本支持"
    return bv, cnt[bv], best, "质量序最靠前且内容有效的版本"


def compare_group(group: list[DatasetContext], min_agree: int, rank: dict[str, int] | None = None,
                  invalid_label: dict[str, set[str]] | None = None,
                  invalid_data: dict[str, set[str]] | None = None) -> GroupResult:
    n = len(group)
    rank = rank or {}
    diffs: list[Diff] = []
    compared = {c.dataset_id: defaultdict(set) for c in group}
    totals = {c.dataset_id: Counter() for c in group}
    hist = {c.dataset_id: {"reference": Counter(), "actual": Counter()} for c in group}
    rec_by = {c.dataset_id: {sample_key(r.expected_data): r for r in c.records} for c in group}
    data_by = {c.dataset_id: {sample_key(PurePosixPath(d).name): d for d in c.data_files} for c in group}
    keys = sorted(set().union(*[set(rec_by[c.dataset_id]) | set(data_by[c.dataset_id]) for c in group]))
    confirmed = 0

    for key in keys:
        # ---- 样本是否存在（标签 + 数据）----
        has = {c.dataset_id: (key in rec_by[c.dataset_id]) or (key in data_by[c.dataset_id]) for c in group}
        present = sum(has.values())
        for c in group:
            if present * 2 > n and present >= min_agree:
                totals[c.dataset_id]["samples_reference"] += 1
                compared[c.dataset_id]["X01"].add(f"sample:{key}")
            if not has[c.dataset_id] and present * 2 > n and present >= min_agree:
                diffs.append(Diff("X01", c.dataset_id, f"sample:{key}", "fail",
                                  {"file": key, "pointer": "样本"}, "样本在本版本中整体缺失（多数版本中存在）",
                                  {"present_in_versions": present, "versions": n}))

        # ---- 数据文件内容 ----
        fps = {}
        for c in group:
            rel = data_by[c.dataset_id].get(key)
            if rel:
                fps[c.dataset_id] = fingerprint(c.root / rel, c.cache.setdefault("fingerprints", {}))
        ref_fp, k, ref_ds, why = select_reference(fps, rank, (invalid_data or {}).get(key, set()), min_agree)
        for c in group:
            if c.dataset_id not in fps:
                continue
            rel = data_by[c.dataset_id][key]
            compared[c.dataset_id]["X02"].add(rel)
            if fps[c.dataset_id] == ref_fp:
                continue
            if ref_fp is None:
                if len(set(fps.values())) > 1:
                    diffs.append(Diff("X02", c.dataset_id, rel, "pending", {"file": rel},
                                      f"各版本数据内容不一致，无法确定参考版本（{why}）", {"versions_compared": len(fps)},
                                      missing="原始数据校验和，或更多内容一致的同源版本"))
                continue
            ref_ds = ref_ds or next(d for d, f in fps.items() if f == ref_fp)
            ref_ctx = next(g for g in group if g.dataset_id == ref_ds)
            ev = {"agreeing_versions": k, "versions": len(fps), "reference_version": ref_ds, "reference_rule": why}
            if rel.lower().endswith(".csv"):
                ev.update(csv_measure(ref_ctx.root / data_by[ref_ds][key], c.root / rel))
                diffs.append(Diff("X02", c.dataset_id, rel, "fail", {"file": rel},
                                  "数据内容与参考版本不一致（数值或采样点被改动）", ev))
            else:
                # 图像像素被改动：发榜方污染项目中没有对应类型，只列为待复核事项，不判缺陷
                ev.update({"bytes_reference": (ref_ctx.root / data_by[ref_ds][key]).stat().st_size,
                           "bytes_actual": (c.root / rel).stat().st_size})
                diffs.append(Diff("X02", c.dataset_id, rel, "pending", {"file": rel},
                                  "数据内容与参考版本不一致（图像被改动），不在已知污染类型内，待人工复核", ev,
                                  missing="原始图像或处理记录，确认改动是否属于缺陷"))

        # ---- 标签 ----
        docs = {}
        for c in group:
            rec = rec_by[c.dataset_id].get(key)
            if rec is not None and isinstance(c.label(rec), dict) and c.profile(rec):
                docs[c.dataset_id] = (c, rec, c.label(rec))
        ref_canon, k, ref_ds, why = select_reference({d: canon(v[2]) for d, v in docs.items()}, rank,
                                                     (invalid_label or {}).get(key, set()), min_agree)
        if ref_canon is not None:
            confirmed += 1
        ref_doc = json.loads(ref_canon) if ref_canon else None
        if ref_doc is not None:
            any_c, any_rec, _ = next(iter(docs.values()))
            _, ref_els = elements(ref_doc, any_c.profile_spec(any_rec))
            ref_labels = Counter(str(e.label) for e in (ref_els or []) if e.label is not None)
            for c in group:
                hist[c.dataset_id]["reference"].update(ref_labels)
                if c.dataset_id in docs:
                    _, els = elements(docs[c.dataset_id][2], c.profile_spec(docs[c.dataset_id][1]))
                    hist[c.dataset_id]["actual"].update(str(e.label) for e in (els or []) if e.label is not None)
        for d, (c, rec, doc) in docs.items():
            for cid in ("X03", "X04", "X05", "X06", "X07", "X08"):
                compared[d][cid].add(rec.key)
            if ref_canon is not None and canon(doc) == ref_canon:
                _count_elements(totals[d], doc, c, rec)
                continue
            if ref_doc is None:
                if len({canon(v[2]) for v in docs.values()}) > 1:
                    diffs.append(Diff("X07", d, rec.key, "pending", {"file": rec.key},
                                      f"各版本标签不一致，无法确定参考版本（{why}）", {"versions_compared": len(docs)},
                                      missing="标注定义或专家复核，确定哪一版正确"))
                _count_elements(totals[d], doc, c, rec)
                continue
            diffs += _diff_label(c, rec, doc, ref_doc, k, len(docs), totals[d], ref_ds, why)
    return GroupResult([c.dataset_id for c in group], len(keys), confirmed, diffs,
                       {d: dict(v) for d, v in compared.items()}, {d: dict(v) for d, v in totals.items()}, hist)


def _count_elements(t: Counter, doc, c, rec):
    _, els = elements(doc, c.profile_spec(rec))
    if els is not None:
        t["elements_reference"] += len(els)
        t["elements_actual"] += len(els)
        t["geometries"] += sum(1 for e in els if e.points or e.start is not None)


def _diff_label(c: DatasetContext, rec: Record, doc: dict, ref: dict, agree: int, versions: int,
                t: Counter, ref_ds: str | None = None, why: str | None = None) -> list[Diff]:
    prof = c.profile_spec(rec)
    spec = element_spec(prof)
    path, ref_els = elements(ref, prof)
    _, var_els = elements(doc, prof)
    base_ev = {"agreeing_versions": agree, "versions": versions, "reference_version": ref_ds, "reference_rule": why}
    out: list[Diff] = []
    if ref_els is not None and var_els is not None:
        t["elements_reference"] += len(ref_els)
        t["elements_actual"] += len(var_els)
        t["geometries"] += sum(1 for e in ref_els if e.points or e.start is not None)
        pairs, missing, spurious = match_elements(ref_els, var_els, spec)
        lk = spec[1]
        leftover = pair_leftovers(missing, spurious, spec) if c.spec.raw["variants"].get("pair_leftovers") else []
        if leftover:
            used_r, used_v = {id(r) for r, _ in leftover}, {id(v) for _, v in leftover}
            missing = [r for r in missing if id(r) not in used_r]
            spurious = [v for v in spurious if id(v) not in used_v]
        for r, v in leftover:
            ptr = f"{path}[{v.index}]"
            ev = {**base_ev, "pairing": "同一样本内未匹配元素按身份字段或数量一一对应", "reference_element": _brief(r.raw, spec)}
            if lk and r.label != v.label:
                out.append(Diff("X03", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": f"{ptr}.{lk}", **_region(v, spec)},
                                "类别错标：与参考版本的类别不同", {**ev, "reference": r.label, "actual": v.label}))
            g = geometry_measure(r.points, v.points) if (r.points and v.points and r.points != v.points) else None
            tm = time_measure(r, v) if (r.start, r.end) != (v.start, v.end) else None
            if g or tm:
                out.append(Diff("X06", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": ptr, **_region(v, spec)},
                                "位置/边界偏移：偏移幅度超出同一目标的匹配阈值",
                                {**ev, **(g or {}), **(tm or {}), "severity": severity(g, tm),
                                 "defect_code": shift_code(c, rec, g, tm)}))
            elif not (lk and r.label != v.label):
                out.append(Diff("X07", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": ptr},
                                "标注元素内容与参考版本不一致", {**ev, "defect_code": "E-DOC"}))
        for r in missing:
            out.append(Diff("X04", c.dataset_id, rec.key, "fail",
                            {"file": rec.key, "pointer": f"{path}（参考第 {r.index} 项）",
                             **_region(r, spec)}, "漏标：参考版本中存在的标注在本版本缺失",
                            {**base_ev, "reference_element": _brief(r.raw, spec)}))
        for v in spurious:
            out.append(Diff("X05", c.dataset_id, rec.key, "fail",
                            {"file": rec.key, "pointer": f"{path}[{v.index}]", **_region(v, spec)},
                            "虚标：本版本多出参考版本中没有的标注", {**base_ev, "element": _brief(v.raw, spec)}))
        for r, v in pairs:
            if r.raw == v.raw:
                continue
            ptr = f"{path}[{v.index}]"
            if lk and r.label != v.label:
                out.append(Diff("X03", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": f"{ptr}.{lk}", **_region(v, spec)},
                                "类别错标：与参考版本的类别不同", {**base_ev, "reference": r.label, "actual": v.label}))
            g = geometry_measure(r.points, v.points) if (r.points and v.points and r.points != v.points) else None
            tm = time_measure(r, v) if (r.start, r.end) != (v.start, v.end) else None
            if g or tm:
                ev = {**base_ev, **(g or {}), **(tm or {})}
                ev["severity"] = severity(g, tm)
                ev["defect_code"] = shift_code(c, rec, g, tm)
                out.append(Diff("X06", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": ptr, **_region(v, spec)},
                                "位置/边界偏移：与参考版本相比几何或时间发生偏移", ev))
            skip = {k for k in spec[1:6] if k} | set(spec[6])
            others = sorted(k for k in set(r.raw) | set(v.raw) if r.raw.get(k) != v.raw.get(k) and k not in skip)
            numeric, textual = {}, {}
            for k in others:
                fm = field_measure(r.raw.get(k), v.raw.get(k))
                (numeric if fm.get("relative_deviation") is not None or "time_offset_s" in fm else textual)[k] = fm
            if numeric:
                devs = [m["relative_deviation"] for m in numeric.values() if m.get("relative_deviation") is not None]
                out.append(Diff("X08", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": ptr, **_region(v, spec)},
                                f"标注数值被扰动：{len(numeric)} 个数值字段与参考版本不同",
                                {**base_ev, "fields": numeric, "max_relative_deviation": max(devs) if devs else None}))
            if textual:
                out.append(Diff("X07", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": ptr},
                                f"标注属性与参考版本不一致：{', '.join(textual)}",
                                {**base_ev, "fields": textual, "defect_code": "L2-A"}))
    fr, fv = flatten(ref, path), flatten(doc, path)
    for k in sorted(set(fr) | set(fv)):
        if fr.get(k, "<缺失>") != fv.get(k, "<缺失>"):
            out.append(Diff("X07", c.dataset_id, rec.key, "fail", {"file": rec.key, "pointer": k},
                            "声明字段与参考版本不一致", {**base_ev, **field_measure(fr.get(k, "<缺失>"), fv.get(k, "<缺失>")),
                                                          "defect_code": field_code(c.spec, k, "E-DOC")}))
    return out


def pair_leftovers(missing: list[Element], spurious: list[Element], keys: tuple) -> list[tuple[Element, Element]]:
    """同一样本内参考与本版本都剩有未匹配元素时一一配对（视为同一目标发生了大幅偏移）。

    配对条件：两侧身份字段（identity_keys）都有且相同；或两侧都没有身份字段且未匹配数量相等。
    身份不同的（如虚构事件没有正式身份标识）不配对，仍分别判为漏标与虚标。按代价（不设上限）贪心配对。
    """
    if not missing or not spurious:
        return []
    _, lk, gk, sk, ek, pk, idk = keys

    def ident(e):
        if not idk:
            return None
        vals = tuple(e.raw.get(k) for k in idk)
        return vals if any(v not in (None, "", "None") for v in vals) else None

    equal_count = len(missing) == len(spurious)
    cand = []
    for r in missing:
        for v in spurious:
            ir, iv = ident(r), ident(v)
            if not ((ir is not None and ir == iv) or (ir is None and iv is None and equal_count)):
                continue
            cost = 0.3 if r.label != v.label else 0.0
            g = geometry_measure(r.points, v.points) if (r.points and v.points) else None
            if g:
                cost += g["offset_norm"]
            t = time_measure(r, v)
            if t:
                cost += t["relative_deviation"]
            cand.append((round(cost, 6), r.index, v.index, r, v))
    cand.sort(key=lambda x: x[:3])
    used_r, used_v, out = set(), set(), []
    for _, ri, vi, r, v in cand:
        if ri in used_r or vi in used_v:
            continue
        used_r.add(ri)
        used_v.add(vi)
        out.append((r, v))
    return out


def shift_code(c: DatasetContext, rec: Record, g: dict | None, t: dict | None) -> str:
    """偏移结论的缺陷代码：时间偏移 L2-T；外接框中心几乎不动而面积明显变化 L3-MIS；其余几何偏移 L2-G。"""
    if not g:
        return "L2-T" if t else "L2-G"
    cfg = c.spec.raw["variants"].get("coverage_change") or {}
    ar = g.get("area_ratio")
    if (c.profile(rec) in cfg.get("profiles", ()) and ar and ar > 0 and g["offset_norm"] < cfg["max_offset_norm"]
            and abs(math.log(ar)) >= cfg["min_abs_log_area"]):
        return "L3-MIS"
    return "L2-G"


def _region(e: Element, spec) -> dict:
    """证据位置中的图像区域（外接框）或时间区间。"""
    if e.points and _numeric_points(e.points):
        return {"region": [round(x, 2) for x in _bbox(e.points)]}
    sk, ek, pk = spec[3], spec[4], spec[5]
    if sk and ek and (e.raw.get(sk) or e.raw.get(ek)):
        return {"interval": [e.raw.get(sk), e.raw.get(ek)]}
    if pk and e.raw.get(pk):
        return {"interval": [e.raw.get(pk), e.raw.get(pk)]}
    return {}


def _brief(raw: dict, spec) -> dict:
    keep = {k for k in spec[1:6] if k} | set(spec[6])
    return {k: raw[k] for k in raw if k in keep}


def severity(g: dict | None, t: dict | None) -> dict:
    """同一形态内的严重度：两轴各归一到 [0,1] 后 1:1 加权（评分准则 §2.4）。"""
    if g:
        a = min(1.0, g["offset_norm"])
        ar = g.get("area_ratio")
        b = min(1.0, abs(math.log(ar))) if ar and ar > 0 else 1.0
        return {"form": "image", "axes": {"position_offset": a, "area_ratio": b}, "score": round((a + b) / 2, 4)}
    a = min(1.0, t["time_offset_s"] / 3600)
    b = min(1.0, t["relative_deviation"])
    return {"form": "time", "axes": {"time_offset": a, "relative_deviation": b}, "score": round((a + b) / 2, 4)}
