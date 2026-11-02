"""分析同一族质量变体之间的差异形式（开发期分析工具，不参与评测）。

用法: python3 variant_diff.py <publish 目录> <族前缀，如 DMSP_SSUSI>
"""
import collections
import hashlib
import json
import os
import sys


def load(ds_root):
    labels, data = {}, {}
    for dp, _, fs in os.walk(ds_root):
        for f in fs:
            p = os.path.join(dp, f)
            if f.endswith(".json"):
                key = f[:-5]
                key = os.path.splitext(key)[0] if os.path.splitext(key)[1] else key
                try:
                    labels[key] = json.load(open(p, encoding="utf-8"))
                except Exception as e:
                    labels[key] = f"<parse error {e.__class__.__name__}>"
            else:
                data[os.path.splitext(f)[0]] = p
    return labels, data


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while c := f.read(1 << 20):
            h.update(c)
    return h.hexdigest()


def items(doc):
    """标签中的标注元素列表。"""
    if not isinstance(doc, dict):
        return None
    sc = doc.get("science_annotation") or {}
    if isinstance(sc.get("specialShapes"), list):
        return sc["specialShapes"]
    if isinstance(sc.get("mask"), dict):
        return sc["mask"].get("shapes")
    for blk in ("hek_event_annotation", "ulf_event_annotation"):
        if isinstance(doc.get(blk), dict):
            return doc[blk].get("matched_events")
    return None


def main():
    root, fam = sys.argv[1], sys.argv[2]
    dss = sorted(d for d in os.listdir(root) if d.rsplit("_", 1)[0] == fam)
    print("variants:", dss)
    L, D = {}, {}
    for ds in dss:
        L[ds], D[ds] = load(os.path.join(root, ds))
        print(f"  {ds}: labels={len(L[ds])} data={len(D[ds])}")
    keys = sorted(set().union(*[set(L[d]) | set(D[d]) for d in dss]))
    print("union sample keys:", len(keys))
    presence = collections.Counter()
    for k in keys:
        presence[(sum(k in L[d] for d in dss), sum(k in D[d] for d in dss))] += 1
    print("(#variants with label, #with data) -> count:", dict(sorted(presence.items())))

    # 数据文件内容：同名样本在各变体中是否相同
    sample = keys[: int(sys.argv[3]) if len(sys.argv) > 3 else 300]
    same = collections.Counter()
    for k in sample:
        hs = [md5(D[d][k]) for d in dss if k in D[d]]
        same[len(set(hs))] += 1
    print("data md5 distinct values per sample (first N):", dict(same))

    # 标签差异：逐变体与多数版本比较
    diff_kinds = collections.Counter()
    shown = collections.Counter()
    for k in keys:
        docs = {d: L[d][k] for d in dss if k in L[d]}
        if len(docs) < 3:
            continue
        canon = collections.Counter(json.dumps(v, sort_keys=True, ensure_ascii=False) for v in docs.values())
        major, n = canon.most_common(1)[0]
        if n < 3:
            diff_kinds["no_majority"] += 1
            continue
        ref = json.loads(major)
        for d, doc in docs.items():
            if json.dumps(doc, sort_keys=True, ensure_ascii=False) == major:
                continue
            if not isinstance(doc, dict):
                kind = "not_object_or_parse_error"
            else:
                changed = []
                for blk in sorted(set(ref) | set(doc)):
                    a, b = ref.get(blk), doc.get(blk)
                    if a != b:
                        if isinstance(a, dict) and isinstance(b, dict):
                            changed += [f"{blk}.{kk}" for kk in sorted(set(a) | set(b)) if a.get(kk) != b.get(kk)]
                        else:
                            changed.append(blk)
                ia, ib = items(ref), items(doc)
                extra = ""
                if isinstance(ia, list) and isinstance(ib, list):
                    extra = f" items {len(ia)}->{len(ib)}"
                kind = ",".join(changed[:6]) + extra
            diff_kinds[(d, kind)] += 1
            if shown[kind] < 2:
                shown[kind] += 1
                ia, ib = items(ref), items(doc) if isinstance(doc, dict) else None
                print(f"\n  [{d}] {k}: {kind}")
                if isinstance(ia, list) and isinstance(ib, list):
                    for x, y in list(zip(ia, ib))[:3]:
                        if x != y:
                            dk = [kk for kk in sorted(set(x) | set(y)) if x.get(kk) != y.get(kk)]
                            print("     ref:", {kk: str(x.get(kk))[:90] for kk in dk})
                            print("     var:", {kk: str(y.get(kk))[:90] for kk in dk})
    print("\nper-variant label diff kinds:")
    for (d, kind), n in sorted(diff_kinds.items(), key=lambda x: (str(x[0]),)):
        print(f"  {d:28s} {n:5d}  {kind}")


if __name__ == "__main__":
    main()
