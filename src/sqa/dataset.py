"""数据集发现、标签与数据文件配对、共享判定状态。

公开数据的两种布局：
    分目录：<数据集>/label/<数据文件名>.json  + <数据集>/data/<数据文件名>
    平铺：  <数据集>/<id>.json + <数据集>/<id>.png   或   <数据集>/<数据文件名>.json
输入目录可以是单个数据集，也可以是包含多个数据集的目录（如 publish/）。
"""
from __future__ import annotations

import fnmatch
import os
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any

from .model import Outcome, Status
from .spec import Spec

_MISSING = object()


def get_path(doc: Any, path: str) -> Any:
    cur = doc
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def is_missing(v: Any) -> bool:
    return v is _MISSING


@dataclass
class Record:
    key: str           # 标签相对路径，唯一标识
    label_path: Path
    expected_data: str  # 由标签文件名推得的数据文件名（可能不含扩展名，见 stem_only）
    stem_only: bool


@dataclass
class DatasetContext:
    dataset_id: str
    root: Path
    spec: Spec
    purpose: str
    records: list[Record]
    data_files: list[str]  # 相对路径，已排序
    parsed: dict[str, Any] = field(default_factory=dict)
    profile_of: dict[str, str | None] = field(default_factory=dict)
    # 同一数据集内各检查共享的判定结果：check_id -> unit -> Outcome
    outcomes: dict[str, dict[str, Outcome]] = field(default_factory=dict)
    cache: dict[str, Any] = field(default_factory=dict)

    def label(self, rec: Record) -> Any:
        return self.parsed.get(rec.key)

    def profile(self, rec: Record) -> str | None:
        return self.profile_of.get(rec.key)

    def profile_spec(self, rec: Record) -> dict[str, Any]:
        return self.spec.profiles.get(self.profile(rec) or "", {})

    @cached_property
    def dominant_profile(self) -> str | None:
        c = Counter(p for p in self.profile_of.values() if p)
        return c.most_common(1)[0][0] if c else None

    @cached_property
    def data_by_name(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for rel in self.data_files:
            index.setdefault(PurePosixPath(rel).name, []).append(rel)
        return index

    @cached_property
    def data_by_stem(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for rel in self.data_files:
            index.setdefault(PurePosixPath(rel).stem, []).append(rel)
        return index

    def data_file_of(self, rec: Record) -> str | None:
        """标签对应的数据文件（相对路径）；找不到返回 None。"""
        hits = (self.data_by_stem if rec.stem_only else self.data_by_name).get(rec.expected_data, [])
        return hits[0] if hits else None

    def dep_status(self, check_id: str, unit: str) -> Status | None:
        o = self.outcomes.get(check_id, {}).get(unit)
        return o.status if o else None


def _ignored(rel: PurePosixPath, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(part, g) for part in rel.parts for g in globs)


def _expected_data(label_name: str, exts: set[str]) -> tuple[str, bool]:
    base = label_name[:-5]  # 去掉 .json
    ext = PurePosixPath(base).suffix.lstrip(".").lower()
    if ext in exts:
        return base, False
    return base, True


def load_dataset(root: Path, spec: Spec, purpose: str) -> DatasetContext:
    layout = spec.raw["layout"]
    exts = {e.lower() for e in layout["data_extensions"]}
    ignore = layout["ignore_globs"]

    records: list[Record] = []
    data_files: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not any(fnmatch.fnmatch(d, g) for g in ignore))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            rel = PurePosixPath(p.relative_to(root).as_posix())
            if _ignored(rel, ignore):
                continue
            if name.lower().endswith(".json"):
                expected, stem_only = _expected_data(name, exts)
                records.append(Record(rel.as_posix(), p, expected, stem_only))
            elif rel.suffix.lstrip(".").lower() in exts:
                data_files.append(rel.as_posix())
    records.sort(key=lambda r: r.key)
    data_files.sort()
    return DatasetContext(root.name, root, spec, purpose, records, data_files)


def _is_dataset(d: Path, spec: Spec) -> bool:
    layout = spec.raw["layout"]
    for ld in layout["label_dirs"]:
        if (d / ld).is_dir() and any(f.suffix == ".json" for f in (d / ld).iterdir()):
            return True
    try:
        names = [f.name for f in d.iterdir() if f.is_file()]
    except OSError:
        return False
    exts = tuple("." + e for e in layout["data_extensions"])
    return any(n.endswith(".json") for n in names) and any(n.lower().endswith(exts) for n in names)


def discover(input_dir: Path, spec: Spec, max_depth: int = 3) -> list[Path]:
    """返回所有数据集根目录：自身是数据集就不再向下找。"""
    if _is_dataset(input_dir, spec):
        return [input_dir]
    if max_depth == 0:
        return []
    found: list[Path] = []
    for d in sorted(input_dir.iterdir()):
        if d.is_dir() and not d.name.startswith((".", "__")):
            found += discover(d, spec, max_depth - 1)
    return found
