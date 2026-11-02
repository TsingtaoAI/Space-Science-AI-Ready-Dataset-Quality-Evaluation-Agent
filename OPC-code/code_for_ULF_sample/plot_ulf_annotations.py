#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ULF (ultra-low-frequency wave) annotation visualizer
=======================================================

Overlays the ground-based geomagnetic 1 Hz four-component time series with the
annotated ULF event intervals / masks from the OPC ULF_sample dataset, producing
example figures that show how the annotation (intervals / mask) aligns with data.

This package ships a small bundled example in `sample_data/` (a few positive and
negative samples). Run with no arguments to visualize it; see README.md.

Data format (four-section annotation following the OPC schema):
  - <ST>_ULF_<YYYYmmdd>_<HHMMSS>.csv   time series, header `t,H,D,Z,T` (3600 points @1 Hz)
  - <same name>.csv.json               four-section annotation; event intervals are in
        ulf_event_annotation.matched_events[]
        each item = {start_dt, end_dt, kind, source}
    positive sample: has_events=true, matched_events non-empty
    negative sample: has_events=false, matched_events empty

Plot style: black academic style -- multi-component shared-x subplots + event
intervals overlaid with axvspan color bands + 1%~99% percentile y-limits + high dpi.

Usage
-----
  # batch: pick n samples (roughly half positive / half negative), output PNGs
  # (defaults: --sample-dir sample_data, --out-dir figs, --n 8)
  python plot_ulf_annotations.py

  # explicit, with options
  python plot_ulf_annotations.py --sample-dir sample_data --out-dir figs --n 8 --seed 7

  # single sample
  python plot_ulf_annotations.py \
      --csv sample_data/HZT_ULF_20121208_200000.csv \
      --out figs/HZT_ULF_20121208_200000.png

  # positive-only / negative-only
  python plot_ulf_annotations.py --sample-dir sample_data --n 6 --only positive

Dependencies: numpy, matplotlib (Agg backend, no GUI required).
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from datetime import datetime

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------------------------------------------------------------- style constants
# black academic style: near-black / dark-blue curves; event intervals use the
# Okabe-Ito palette (colorblind-safe, gray-scale distinguishable)
CURVE_COLOR = "#1f3b73"
EVENT_PALETTE = {
    "Pi2": "#E69F00",   # orange
    "Pi1": "#D55E00",   # vermilion
    "Pc2": "#0072B2",   # blue
    "Pc3": "#56B4E9",   # sky blue
    "Pc4": "#009E73",   # green
    "Pc5": "#CC79A7",   # reddish purple
    "other": "#999999",
}
PLATE_DPI = 150
PLATE_DPI_HR = 300

VARS = ["H", "D", "Z", "T"]
VAR_UNIT = {"H": "nT", "D": "deg", "Z": "nT", "T": "nT"}


# ---------------------------------------------------------------- IO helpers
def read_csv(path: str):
    """Read a ULF CSV: returns (t_seconds, {var: np.array}). NULL / non-numeric -> NaN."""
    t = []
    cols = {v: [] for v in VARS}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                tv = float(row["t"])
            except (TypeError, ValueError):
                continue
            t.append(tv)
            for v in VARS:
                try:
                    cols[v].append(float(row[v]))
                except (TypeError, ValueError):
                    cols[v].append(np.nan)
    t = np.asarray(t, dtype=np.float64)
    data = {v: np.asarray(cols[v], dtype=np.float64) for v in VARS}
    return t, data


def read_anno(json_path: str):
    with open(json_path, encoding="utf-8") as f:
        d = json.load(f)
    lh = d.get("label_header", {}) or {}
    ba = d.get("basic_annotation", {}) or {}
    uea = d.get("ulf_event_annotation", {}) or {}
    return lh, ba, uea


def parse_iso(s):
    if not s:
        return None
    s = s.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def event_minutes(uea, start_dt):
    """Convert event absolute times to minutes relative to the window start."""
    out = []
    if start_dt is None:
        return out
    for ev in uea.get("matched_events", []) or []:
        s = parse_iso(ev.get("start_dt"))
        e = parse_iso(ev.get("end_dt"))
        if s is None or e is None:
            continue
        sm = (s - start_dt).total_seconds() / 60.0
        em = (e - start_dt).total_seconds() / 60.0
        if em < 0 or sm > 60:
            continue
        out.append((max(sm, 0.0), min(em, 60.0), ev.get("kind", "other")))
    return out


# ---------------------------------------------------------------- plotting
def plot_one(csv_path: str, out_png: str, dpi: int = PLATE_DPI):
    json_path = csv_path + ".json"
    t, data = read_csv(csv_path)

    lh, ba, uea = ({}, {}, {}) if not os.path.exists(json_path) else read_anno(json_path)
    start_dt = parse_iso(ba.get("startTime")) if ba.get("startTime") else None
    has_events = bool(uea.get("has_events", False))
    events = event_minutes(uea, start_dt)
    label = "positive" if has_events else "negative"
    identifier = lh.get("identifier", os.path.basename(csv_path).replace(".csv", ""))
    station_code = os.path.basename(csv_path).split("_")[0]

    # event mask (per sample point)
    mask = np.zeros(t.shape[0], dtype=int)
    for sm, em, _k in events:
        mask |= ((t / 60.0 >= sm) & (t / 60.0 <= em)).astype(int)

    tmin = t / 60.0  # 0-60 minutes
    n_vars = len(VARS)
    fig, axes = plt.subplots(n_vars + 1, 1, figsize=(8.2, 2.0 * (n_vars + 1)),
                             sharex=True)

    for i, v in enumerate(VARS):
        ax = axes[i]
        col = data[v]
        bad = ~np.isfinite(col)
        ax.plot(tmin[~bad], col[~bad], lw=0.4, color=CURVE_COLOR)
        for sm, em, kind in events:
            c = EVENT_PALETTE.get(kind, EVENT_PALETTE["other"])
            ax.axvspan(sm, em, facecolor=c, alpha=0.28, lw=0, edgecolor="none")
        vv = col[~bad]
        if vv.size > 10:
            lo, hi = np.percentile(vv, [0.5, 99.5])
            pad = 0.15 * (hi - lo)
            if pad <= 0:
                pad = 1.0
            ax.set_ylim(lo - pad, hi + pad)
        ax.set_ylabel(f"{v} ({VAR_UNIT[v]})", fontsize=9)
        ax.tick_params(labelsize=8)
        ax.grid(alpha=0.25, lw=0.4)

    # bottom: event mask strip (illustrates the "mask" form of annotation)
    axm = axes[-1]
    axm.fill_between(tmin, mask, step="mid", color=CURVE_COLOR, alpha=0.65,
                     linewidth=0)
    axm.set_ylim(-0.15, 1.3)
    axm.set_yticks([0, 1])
    axm.set_ylabel("event mask", fontsize=9)
    axm.set_xlabel("minutes from window start (60-min @ 1 Hz)", fontsize=9)
    for sm, em, kind in events:
        c = EVENT_PALETTE.get(kind, EVENT_PALETTE["other"])
        axm.axvspan(sm, em, facecolor=c, alpha=0.35, lw=0, edgecolor="none")

    kinds = sorted({k for _s, _e, k in events})
    title = f"{identifier}  |  {station_code} MERIDIAN/FGM  |  label={label}"
    if kinds:
        title += "  |  events: " + ",".join(kinds)
    fig.suptitle(title, fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    fig.savefig(out_png, dpi=dpi)
    plt.close(fig)
    return out_png, label, len(events), kinds


def collect_samples(sample_dir: str):
    """Return {csv_path: label}."""
    out = {}
    for csv_path in sorted(glob.glob(os.path.join(sample_dir, "*.csv"))):
        jp = csv_path + ".json"
        if not os.path.exists(jp):
            continue
        try:
            _lh, _ba, uea = read_anno(jp)
        except Exception:
            continue
        out[csv_path] = "positive" if uea.get("has_events") else "negative"
    return out


def pick_samples(samples, n, seed, only=None):
    rng = np.random.default_rng(seed)
    pos = [p for p, l in samples.items() if l == "positive"]
    neg = [p for p, l in samples.items() if l == "negative"]
    if only == "positive":
        pos, neg = pos, []
    elif only == "negative":
        pos, neg = [], neg
    half = max(1, n // 2)
    pick_p = list(rng.choice(pos, size=min(half, len(pos)), replace=False)) if pos else []
    pick_n = list(rng.choice(neg, size=min(n - len(pick_p), len(neg)), replace=False)) if neg else []
    # top up from the other side if one side is short
    if len(pick_p) + len(pick_n) < n:
        remain = [p for p in (pos + neg) if p not in set(pick_p) | set(pick_n)]
        extra = list(rng.choice(remain, size=min(n - len(pick_p) - len(pick_n), len(remain)),
                                replace=False)) if remain else []
        if only != "negative":
            pick_p += [x for x in extra if x in pos]
        if only != "positive":
            pick_n += [x for x in extra if x in neg]
    return pick_p + pick_n


def main(argv=None):
    ap = argparse.ArgumentParser(description="OPC ULF annotation visualizer (intervals/mask over data)")
    ap.add_argument("--csv", help="single-sample csv path (same-name .json in the same dir)")
    ap.add_argument("--sample-dir", default="sample_data", help="directory with csv+json samples")
    ap.add_argument("--out-dir", default="figs", help="output directory")
    ap.add_argument("--n", type=int, default=8, help="number of samples to pick in batch mode")
    ap.add_argument("--seed", type=int, default=7, help="random seed")
    ap.add_argument("--only", choices=["positive", "negative"], default=None,
                    help="plot only positive / negative samples")
    ap.add_argument("--dpi", type=int, default=PLATE_DPI, help="output dpi")
    a = ap.parse_args(argv)

    records = []
    if a.csv:
        out = os.path.join(a.out_dir, os.path.basename(os.path.splitext(a.csv)[0]) + ".png")
        p, label, nev, kinds = plot_one(a.csv, out, dpi=a.dpi)
        records.append((os.path.relpath(p, a.out_dir), label, nev, kinds))
        print(f"[ok] {label} n_ev={nev} -> {p}")
    else:
        samples = collect_samples(a.sample_dir)
        if not samples:
            print(f"[err] no csv+json paired samples found in {a.sample_dir}", file=sys.stderr)
            return 2
        picks = pick_samples(samples, a.n, a.seed, a.only)
        for csv_path in picks:
            out = os.path.join(a.out_dir, os.path.basename(os.path.splitext(csv_path)[0]) + ".png")
            p, label, nev, kinds = plot_one(csv_path, out, dpi=a.dpi)
            records.append((os.path.relpath(p, a.out_dir), label, nev, kinds))
            print(f"[ok] {label} n_ev={nev} -> {p}")
    print(f"[done] {len(records)} figure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
