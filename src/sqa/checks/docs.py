"""文档与内容不一致：声明之间、声明与文件之间。"""
from __future__ import annotations

from pathlib import PurePosixPath

from ..model import Location, defect, merge, ok, pending
from ..registry import check
from ._util import as_number, csv_scan, declared_sizes, get_path, is_missing, png_info, parse_time


def _ts_severity(offset_s: float, window_change_s: float, duration_s: float) -> dict:
    """时序形态的严重度（评分准则 §2.4：时间偏移、采样点变化两轴归一后 1:1）。"""
    a = min(1.0, offset_s / 3600)
    b = min(1.0, abs(window_change_s) / duration_s) if duration_s else 0.0
    return {"form": "timeseries", "axes": {"time_offset": round(a, 6), "sample_points": round(b, 6)},
            "score": round((a + b) / 2, 4)}


@check("D01")
def timeseries_declarations(ctx, rec):
    doc = ctx.label(rec)
    sci = doc.get("science_annotation") or {}
    rate, dur, npts = (as_number(sci.get(k)) for k in ("sampling_rate_hz", "duration_s", "n_points"))
    outs = []
    loc = Location(rec.key, pointer="science_annotation")
    if None in (rate, dur, npts):
        return pending(rec.key, loc, "采样率、时长或点数缺失，无法核对",
                       missing="science_annotation.sampling_rate_hz/duration_s/n_points")
    consistent = abs(rate * dur - npts) <= 1
    if not consistent:
        outs.append(defect(rec.key, loc, "采样率 × 时长与声明点数不符", sampling_rate_hz=rate,
                           duration_s=dur, n_points=npts, defect_code="L2-M"))
    s = parse_time(get_path(doc, "basic_annotation.startTime"))
    e = parse_time(get_path(doc, "basic_annotation.endTime"))
    if s and e:
        window = (e - s).total_seconds() + 1 / rate  # 结束时刻为最后一个采样点
        if abs(window - dur) > max(1.0, 1 / rate):
            # 起止时刻与观测时刻 dateObs 也对不上：起止时刻被改动（时段抖动）；否则是时长参数被改动
            obs = parse_time(get_path(doc, "science_annotation.dateObs"))
            shifted = obs is not None and abs((s - obs).total_seconds()) > ctx.spec.raw.get("time_tolerance_s", 1)
            off = abs((s - obs).total_seconds()) if shifted else None
            outs.append(defect(rec.key, Location(rec.key, pointer="basic_annotation.startTime" if shifted else "science_annotation.duration_s"),
                               "声明时长与起止时刻不符", duration_s=dur, window_s=window,
                               defect_code="L2-T" if shifted else "L2-M",
                               **({"time_offset_s": off, "severity": _ts_severity(off, window - dur, dur)} if shifted else {})))
    rel = ctx.data_file_of(rec)
    if rel and rel.lower().endswith(".csv"):
        scan = csv_scan(ctx, rel)
        if scan["error"]:
            return defect(rec.key, Location(rel), "CSV 无法读取", error=scan["error"])
        header, rows = scan["header"], scan["rows"]
        if rows != npts:
            # 标签自身参数自洽而行数不符：数据体被删改（时序断裂 / 重复，不可重推）；否则是声明点数被改动
            outs.append(defect(rec.key, Location(rel) if consistent else Location(rec.key, pointer="science_annotation.n_points"),
                               "CSV 实际行数与声明点数不符", declared=npts, actual=rows,
                               defect_code="L1-TS" if consistent else "L2-M"))
        comps = sci.get("components")
        if isinstance(comps, list) and header[1:] != comps:
            outs.append(defect(rec.key, Location(rec.key, pointer="science_annotation.components"),
                               "CSV 列与声明的分量不符", declared=comps, actual=header, file=rel, defect_code="L2-M"))
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("D02")
def image_size_declared(ctx, rec):
    decls = declared_sizes(ctx, rec)
    rel = ctx.data_file_of(rec)
    if not decls:
        return pending(rec.key, Location(rec.key), "标签未声明图像尺寸", missing="imageWidth/imageHeight 或 imgSize")
    info = png_info(ctx, rel) if rel.lower().endswith(".png") else None
    if not info or not info.get("valid_header"):
        return pending(rec.key, Location(rel), "无法读取图像实际尺寸", missing="可读的 PNG 文件头")
    actual = [info["width"], info["height"]]
    # 每一处尺寸声明（宽高字段、尺寸文本）都须与文件一致
    outs = [defect(rec.key, Location(rec.key, pointer=src), "声明的图像尺寸与文件不一致",
                   declared=[w, h], actual=actual, file=rel,
                   relative_deviation=round(abs(w * h - actual[0] * actual[1]) / (actual[0] * actual[1]), 6))
            for w, h, src in decls if [w, h] != actual]
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("D03")
def format_modality_time(ctx, rec):
    doc = ctx.label(rec)
    outs = []
    fmt = get_path(doc, "basic_annotation.fileFormat")
    name = get_path(doc, "basic_annotation.fileName")
    if isinstance(fmt, str) and isinstance(name, str):
        ext = PurePosixPath(name).suffix.lstrip(".").lower()
        if ext and fmt.strip().lower() != ext:
            outs.append(defect(rec.key, Location(rec.key, pointer="basic_annotation.fileFormat"),
                               "声明的文件格式与扩展名不一致", declared=fmt, extension=ext))
    mod = get_path(doc, "basic_annotation.modality")
    mods = mod if isinstance(mod, list) else [mod]
    expected = ctx.profile_spec(rec).get("modality")
    if expected and not is_missing(mod) and expected not in [str(m).lower() for m in mods]:
        outs.append(defect(rec.key, Location(rec.key, pointer="basic_annotation.modality"),
                           "声明的模态与标注内容不符", declared=mod, expected=expected))
    st, obs = get_path(doc, "basic_annotation.startTime"), get_path(doc, "science_annotation.dateObs")
    ts, to = parse_time(st), parse_time(obs)
    if ts and to and abs((ts - to).total_seconds()) > ctx.spec.raw.get("time_tolerance_s", 1):
        off = abs((ts - to).total_seconds())
        te = parse_time(get_path(doc, "basic_annotation.endTime"))
        dur = as_number(get_path(doc, "science_annotation.duration_s"))
        win = (te - ts).total_seconds() if te else None
        outs.append(defect(rec.key, Location(rec.key, pointer="basic_annotation.startTime", interval=(st, get_path(doc, "basic_annotation.endTime"))),
                           "声明的起止时刻与观测时刻 dateObs 不一致（时段起止被改动）", startTime=st, dateObs=obs,
                           time_offset_s=off, defect_code="L2-T",
                           severity=_ts_severity(off, (win - dur) if (win is not None and dur) else 0.0, dur or 3600.0)))
    return merge(rec.key, outs) if outs else ok(rec.key)
