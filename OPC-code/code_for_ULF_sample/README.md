# ULF annotation visualization

Overlays the annotated ULF event intervals / masks with the ground-based
geomagnetic 1 Hz four-component time series, producing example figures that show
how annotations correspond to the data.

This package is self-contained: a few example samples (positive and negative)
are bundled in `sample_data/`, so you can run the visualizer immediately without
fetching the full dataset.

## Data format

Each sample = one data file + one same-name annotation file:

```
<ST>_ULF_<YYYYmmdd>_<HHMMSS>.csv         time series, header t,H,D,Z,T (3600 points @1 Hz)
<same name>.csv.json                     four-section annotation (label_header /
                                         basic_annotation / science_annotation /
                                         ulf_event_annotation)
```

Key fields used for visualization:

```
basic_annotation.startTime                window start (UTC)
ulf_event_annotation.has_events          true  = positive (contains a ULF event)
                                         false = negative (quiet window)
ulf_event_annotation.matched_events[]    event interval list, each item:
    { "start_dt": "2010-04-03T13:20:00",
      "end_dt":   "2010-04-03T13:40:00",
      "kind":     "Pi2",
      "source":   "F006/_entries_p2pi2.jsonl" }
```

## Usage

Dependencies: `python3 + numpy + matplotlib` (Agg backend, no GUI required).

```bash
# run out of the box on the bundled sample (defaults: --sample-dir sample_data,
# --out-dir figs, --n 8). Execute from this directory.
python3 plot_ulf_annotations.py

# explicit options
python3 plot_ulf_annotations.py \
    --sample-dir sample_data \
    --out-dir figs --n 8 --seed 7

# single sample
python3 plot_ulf_annotations.py \
    --csv sample_data/HZT_ULF_20121208_200000.csv \
    --out figs/HZT_ULF_20121208_200000.png

# positive-only / negative-only; higher dpi
python3 plot_ulf_annotations.py --sample-dir sample_data --n 6 --only positive --dpi 300
```

To visualize your own data, point `--sample-dir` at any directory that follows
the format above (csv + same-name `.csv.json`).

## Output

Each PNG (top to bottom):

1. **H / D / Z / T four-component subplots** (shared x-axis, units nT / deg):
   dark-blue near-black curves (black academic style); **colored shaded bands =
   annotated ULF event intervals** (Okabe-Ito palette, one color per wave type,
   Pi2 = orange).
2. **event mask strip**: a 0/1 mask at the bottom, 1 = that instant falls inside
   an annotated event interval -- i.e. the "mask" form of the annotation.
3. Title: `identifier | station MERIDIAN/FGM | label=positive/negative | event types`.

Positive-sample figures show shaded bands + highlighted mask; negative-sample
figures have no shading and an all-zero mask, forming a direct contrast.

## Contents

| Path | Description |
|---|---|
| `plot_ulf_annotations.py` | visualization script (described above) |
| `README.md` | this file |
| `requirement.txt` | Python dependencies |
| `sample_data/` | bundled example samples (csv + same-name json) |
| `figs/` | example output generated from `sample_data/` (can be deleted and regenerated) |
