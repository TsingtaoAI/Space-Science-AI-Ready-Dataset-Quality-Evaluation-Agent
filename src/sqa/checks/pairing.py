"""文件配对与引用：标签文件名是配对依据，标签内 fileName 必须与之相符。"""
from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath

from ..model import Location, defect, merge, not_applicable, ok, pending
from ..registry import check
from ._util import get_path


@check("P01")
def data_exists(ctx, rec):
    if ctx.data_file_of(rec) is None:
        kind = "同名（不含扩展名）" if rec.stem_only else ""
        return defect(rec.key, Location(rec.key), f"标签引用的数据文件不存在：找不到{kind}数据文件",
                      expected_data=rec.expected_data)
    return ok(rec.key)


def _matches(name: str, rec) -> bool:
    p = PurePosixPath(name)
    return (p.stem if rec.stem_only else p.name) == rec.expected_data


@check("P02")
def names_consistent(ctx, rec):
    doc = ctx.label(rec)
    hdr, basic = get_path(doc, "label_header.fileName"), get_path(doc, "basic_annotation.fileName")
    outs = []
    names = {}
    for ptr, v in (("label_header.fileName", hdr), ("basic_annotation.fileName", basic)):
        if isinstance(v, str) and v.strip():
            names[ptr] = v
            if not _matches(v, rec):
                outs.append(defect(rec.key, Location(rec.key, pointer=ptr),
                                   "标签内文件名与标签文件名不一致（配对错位）",
                                   declared=v, expected=rec.expected_data))
    if len(names) == 2 and hdr != basic:
        outs.append(defect(rec.key, Location(rec.key, pointer="label_header.fileName"),
                           "label_header 与 basic_annotation 的 fileName 不一致",
                           label_header=hdr, basic_annotation=basic))
    if not names:
        outs.append(defect(rec.key, Location(rec.key, pointer="basic_annotation.fileName"),
                           "标签内没有可用的 fileName"))
    # 分割标注的掩膜还记录了所属图像（发榜方参考代码 epb_mask_vis.py 优先读取 mask.imagePath）
    ip = get_path(doc, "science_annotation.mask.imagePath")
    if isinstance(ip, str) and ip.strip() and not _matches(PurePosixPath(ip).name, rec):
        outs.append(defect(rec.key, Location(rec.key, pointer="science_annotation.mask.imagePath"),
                           "掩膜记录的图像文件名与标签文件名不一致（配对错位）", declared=ip, expected=rec.expected_data))
    return merge(rec.key, outs) if outs else ok(rec.key)


@check("P03", scope="dataset")
def one_label_per_file(ctx):
    users = defaultdict(list)
    for rec in ctx.records:
        rel = ctx.data_file_of(rec)
        if rel:
            users[rel].append(rec.key)
    outcomes = []
    for rel in ctx.data_files:
        u = users.get(rel, [])
        if not u:
            outcomes.append(defect(rel, Location(rel), "孤儿数据文件：没有任何标签对应"))
        elif len(u) > 1:
            outcomes.append(defect(rel, Location(rel), "多份标签对应同一数据文件", labels=u))
        else:
            outcomes.append(ok(rel))
    return outcomes


@check("P04", scope="dataset")
def file_list(ctx):
    """文件清单（如 file_list.txt）与实际文件一致：清单列出而缺失 → 缺失；存在而未列出 → 引用错误。"""
    import fnmatch
    globs = ctx.spec.raw["layout"].get("file_lists", [])
    lists = sorted(p for p in ctx.root.iterdir() if p.is_file() and any(fnmatch.fnmatch(p.name, g) for g in globs))
    if not lists:
        return [not_applicable(f"dataset:{ctx.dataset_id}", "数据集根目录没有文件清单")]
    present = {PurePosixPath(r).name for r in ctx.data_files} | {PurePosixPath(r.key).name for r in ctx.records}
    listed, outcomes = set(), []
    for lp in lists:
        try:
            lines = lp.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            outcomes.append(pending(lp.name, Location(lp.name), f"文件清单无法读取：{type(e).__name__}", missing="UTF-8 编码的文件清单"))
            continue
        entries = [(i, s.strip()) for i, s in enumerate(lines, 1) if s.strip() and not s.strip().startswith("#")]
        if not entries:
            outcomes.append(pending(lp.name, Location(lp.name), "文件清单为空", missing="非空的文件清单"))
            continue
        for i, s in entries:
            name = PurePosixPath(s.replace("\\", "/")).name
            listed.add(name)
            unit = f"{lp.name}:{i}"
            outcomes.append(ok(unit) if name in present else
                            defect(unit, Location(lp.name, pointer=f"第 {i} 行"), "文件清单列出的文件在数据集中不存在",
                                   listed=s))
    if listed:
        for rel in ctx.data_files:
            if PurePosixPath(rel).name not in listed:
                outcomes.append(defect(rel, Location(rel), "数据文件未列入文件清单", file_list=[p.name for p in lists],
                                       defect_code="E-REF"))
    return outcomes
