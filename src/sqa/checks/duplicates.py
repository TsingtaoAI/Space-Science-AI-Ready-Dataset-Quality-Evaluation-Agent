"""数据划分重复与泄漏：数据文件内容重复。

不比较标签内容：公开数据中没有标注的样本，其标签除文件名外天然相同，按内容查重只会产生误报。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict

from ..model import Location, defect, ok
from ..registry import check

_CHUNK = 1 << 20


def _sha256(path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


@check("R01", scope="dataset")
def duplicates(ctx):
    outcomes = []
    # 数据文件：只对大小相同的文件算哈希
    by_size = defaultdict(list)
    for rel in ctx.data_files:
        by_size[(ctx.root / rel).stat().st_size].append(rel)
    by_hash = defaultdict(list)
    for group in by_size.values():
        if len(group) > 1:
            for rel in group:
                by_hash[_sha256(ctx.root / rel)].append(rel)
    dup_of = {}
    for digest, group in by_hash.items():
        for rel in group[1:]:
            dup_of[rel] = (digest, group[0])
    for rel in ctx.data_files:
        if rel in dup_of:
            digest, first = dup_of[rel]
            outcomes.append(defect(rel, Location(rel), "数据文件内容与另一文件完全相同（重复样本）",
                                   duplicate_of=first, sha256=digest))
        else:
            outcomes.append(ok(rel))
    return outcomes
