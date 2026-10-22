#!/usr/bin/env python3
"""Box plots of the warm-up/cool-down experiment (energy and time), shared by the whole team.

Everything is generated from run_table.csv with one command, so all figures look the same:

    python plot_boxplots.py                                  # energy, one figure per benchmark (6)
    python plot_boxplots.py --layout both --metric all       # everything (33 figures)

Layouts (--layout)
    benchmark  one figure per benchmark, the 5 durations side by side (compares durations inside a
               benchmark, i.e. the question of RQ0)                                   -> 6 figures
    duration   one figure per duration, all 6 benchmarks side by side                 -> 5 figures
    both       both of the above
    combined   ONE figure with a panel per benchmark (3 x 2), each with its own y axis, log axis for
               energy by default; made for a full-width `figure*` in the paper   -> 1 figure per metric
    summary    energy only: median energy relative to the 30 s baseline (%), one thin line per
               benchmark plus the average over benchmarks with a 95 % bootstrap CI. Descriptive
               only (the tests use Joules).                                      -> 1 figure
    runorder   energy (or other metric) against the order in which the runs were executed, with a
               rolling median per benchmark. If the orange line moves up and down in the same way
               in every panel, something outside the experiment (background load, power state,
               temperature) changed over time and adds noise to all benchmarks.  -> 1 figure
    Several layouts can be given at once, e.g.  --layout combined summary runorder

Options for the paper
    --compact  small figures (one ACM column, 7 pt text); `combined` becomes 7 in wide
    --notch    notched boxes: if the notches of two boxes do not overlap, the medians differ
               (approximately 95 % confidence). Matplotlib draws the notches on the linear data, so
               with a log axis they can look odd for very skewed data; check the figure.

Metrics (--metric)
    energy     total energy of the benchmark execution in Joules (`energy_j`)
    time       execution time of a run as defined in the report: warm-up + benchmark + cool-down
               (`total_time_s`). By design this is about 2 x duration + benchmark runtime.
    runtime    runtime of the benchmark execution only (`benchmark_time_s`); the kind of "time"
               used in the thesis scripts
    all        energy, time and runtime

Selection of benchmarks: all six by default, or --part 1|2|3 (two benchmarks each) or
--benchmarks NAME [NAME ...]. In the `duration` layout only the selected benchmarks get a box.

Energy source (--source)
    table (default)  column `energy_j` of run_table.csv (EnergiBridge summary)
    raw              recomputed from each run's energibridge.csv as in the thesis scripts: last minus
                     first `PACKAGE_ENERGY (J)`, negative values (counter rollover) are dropped.
                     Our CSVs only have `SYSTEM_POWER (Watts)`, so there the power samples are
                     integrated over time. Both sources agree to <1 %.

Y axis: linear by default for the `benchmark` layout. The `duration` layout puts benchmarks with very
different energy next to each other, so it uses a log axis for energy and runtime. `combined` and
`runorder` use a log axis for energy (a few very high runs would otherwise squash the boxes).
Override with --log / --no-log.

Requires: pandas, numpy, matplotlib.
"""
import argparse
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # no display needed
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
import numpy as np
import pandas as pd

# ---- Fixed experiment design (keep identical for everyone) -------------------------------------
DURATIONS = [5, 30, 60, 120, 210]          # warm-up/cool-down duration levels in seconds
BASELINE = 30                              # reference duration for RQ0
# Order = thermal categories of Table 2: high-compute, moderate, neutral.
BENCHMARKS = ["n-body", "spectral-norm", "scimark-lu", "scimark-sor", "scimark-sparse", "binary-trees"]
LABELS = {
    "n-body": "N-Body", "spectral-norm": "Spectral Norm", "scimark-lu": "LU (Scimark)",
    "scimark-sor": "SOR (Scimark)", "scimark-sparse": "Sparse-MatMult (Scimark)",
    "binary-trees": "Binary Trees",
}
SHORT = {
    "n-body": "N-Body", "spectral-norm": "Spectral\nNorm", "scimark-lu": "LU", "scimark-sor": "SOR",
    "scimark-sparse": "Sparse\nMatMult", "binary-trees": "Binary\nTrees",
}
# Split of the six benchmarks over three people (two each, every benchmark exactly once).
PARTS = {
    1: ["n-body", "spectral-norm"],
    2: ["scimark-lu", "scimark-sor"],
    3: ["scimark-sparse", "binary-trees"],
}
METRICS = {   # name -> column in run_table.csv, axis label, whether a linear axis starts at 0
    "energy":  {"col": "energy_j",         "label": "Energy consumption (J)",   "zero": True},
    "time":    {"col": "total_time_s",     "label": "Execution time per run (s)", "zero": False},
    "runtime": {"col": "benchmark_time_s", "label": "Benchmark runtime (s)",    "zero": True},
}

# ---- Fixed look, so that all figures in the report match ---------------------------------------
COLOR_BOX = "#9ecae1"
COLOR_BASELINE = "#fdae6b"   # the 30 s baseline is highlighted
COLOR_POINTS = "#08306b"
DEFAULT_CSV = (Path(__file__).resolve().parent.parent
               / "experiments" / "warmup_cooldown" / "run_table.csv")


def energy_from_run_csv(path: Path) -> float:
    """Total energy (J) of one run, computed from its raw EnergiBridge CSV (NaN if unusable)."""
    try:
        df = pd.read_csv(path, usecols=lambda c: c in {"Time", "PACKAGE_ENERGY (J)",
                                                       "SYSTEM_POWER (Watts)"})
        if "PACKAGE_ENERGY (J)" in df.columns:            # cumulative counter, as in the thesis
            energy = df["PACKAGE_ENERGY (J)"].iloc[-1] - df["PACKAGE_ENERGY (J)"].iloc[0]
            return float(energy) if energy >= 0 else float("nan")   # negative = counter rollover
        power = pd.to_numeric(df["SYSTEM_POWER (Watts)"], errors="coerce").to_numpy()
        seconds = df["Time"].to_numpy() / 1000.0                    # Time is in milliseconds
        return float(np.nansum(power[:-1] * np.diff(seconds)))      # power integrated over time
    except (OSError, KeyError, IndexError, ValueError, pd.errors.EmptyDataError):
        return float("nan")


def load_runs(csv_path: Path, source: str) -> pd.DataFrame:
    """Read run_table.csv and keep the runs that finished (__done == DONE)."""
    df = pd.read_csv(csv_path)
    df["order"] = np.arange(len(df))     # rows of run_table.csv are in execution order
    required = {"duration", "benchmark", "__done", "__run_id"} | {m["col"] for m in METRICS.values()}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"{csv_path} is missing columns: {sorted(missing)}")

    if source == "raw":   # recompute the energy of every finished run from its own CSV
        run_dir = csv_path.parent
        df["energy_j"] = [energy_from_run_csv(run_dir / rid / "energibridge.csv")
                          if done == "DONE" else float("nan")
                          for rid, done in zip(df["__run_id"], df["__done"])]
    for m in METRICS.values():
        df[m["col"]] = pd.to_numeric(df[m["col"]], errors="coerce")

    unfinished = df[df["__done"] != "DONE"]
    print(f"Loaded {len(df)} runs from {csv_path} (energy source: {source})")
    print(f"Not finished, left out: {len(unfinished)} run(s)"
          + (": " + ", ".join(unfinished["__run_id"].astype(str)) if len(unfinished) else ""))
    return df[df["__done"] == "DONE"].copy()


def select(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Finished runs that have a usable (present, positive) value for this metric."""
    col = METRICS[metric]["col"]
    valid = df[col].notna() & (df[col] > 0)
    if (~valid).any():
        print(f"[{metric}] dropped {int((~valid).sum())} finished run(s) without a usable value: "
              + ", ".join(df.loc[~valid, "__run_id"].astype(str)))
    return df[valid]


# Figure geometry. "normal" is for looking at the figures; "compact" is the real print size of
# one ACM column (about 3.3 in), with 7 pt text, so it stays readable when put into the paper
# at \columnwidth without any scaling.
STYLE = {"size": {"benchmark": (6.0, 4.2), "duration": (7.5, 4.4), "grid": (10.0, 3.3)},
         "dot": 9, "legend": 8, "notch": False}


def use_compact_style() -> None:
    STYLE["size"] = {"benchmark": (3.3, 2.45), "duration": (3.3, 2.45), "grid": (7.0, 2.15)}
    STYLE["dot"], STYLE["legend"] = 4, 6
    plt.rcParams.update({"font.size": 7, "axes.titlesize": 8, "axes.labelsize": 7,
                         "xtick.labelsize": 6.5, "ytick.labelsize": 6.5,
                         "axes.linewidth": 0.6, "xtick.major.width": 0.6,
                         "ytick.major.width": 0.6, "xtick.major.size": 2.5,
                         "ytick.major.size": 2.5})


def draw_boxes(ax, data, colors, show_points):
    positions = np.arange(1, len(data) + 1)
    bp = ax.boxplot(data, positions=positions, widths=0.55, patch_artist=True,
                    showfliers=not show_points, notch=STYLE["notch"],           # points are drawn separately if shown
                    medianprops={"color": "black", "linewidth": 1.5},
                    flierprops={"marker": "o", "markersize": 3, "alpha": 0.6})
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_edgecolor("black")
    if show_points:
        rng = np.random.default_rng(0)  # fixed seed: identical jitter on every machine
        for pos, values in zip(positions, data):
            ax.scatter(pos + rng.uniform(-0.15, 0.15, len(values)), values, s=STYLE["dot"],
                       color=COLOR_POINTS, alpha=0.45, zorder=3, linewidths=0)
    return positions


def finish_axes(ax, metric, log_scale):
    spec = METRICS[metric]
    if log_scale:
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1.0, 2.0, 5.0), numticks=20))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())
    elif spec["zero"]:
        ax.set_ylim(bottom=0)
    ax.set_ylabel(spec["label"] + (", log scale" if log_scale else ""))
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)


def save(fig, out_dir: Path, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out_dir / f"{stem}.{ext}")
    plt.close(fig)
    return out_dir / f"{stem}.png"


def plot_by_benchmark(df, metric, benchmark, out_dir, log_scale, show_points):
    col = METRICS[metric]["col"]
    sub = df[df["benchmark"] == benchmark]
    if sub.empty:
        print(f"[skip] {metric}: no valid runs for {benchmark}")
        return
    data = [sub.loc[sub["duration"] == d, col].to_numpy() for d in DURATIONS]

    fig, ax = plt.subplots(figsize=STYLE["size"]["benchmark"], dpi=300 if STYLE["dot"] < 9 else 150)
    # Durations are not equally spaced (log-spaced levels), so they are used as categories.
    positions = draw_boxes(ax, data, [COLOR_BASELINE if d == BASELINE else COLOR_BOX
                                      for d in DURATIONS], show_points)
    ax.set_xticks(positions)
    ax.set_xticklabels([f"{d} s\n(n={len(x)})" for d, x in zip(DURATIONS, data)])
    ax.set_xlabel("Warm-up/cool-down duration")
    ax.set_title(LABELS.get(benchmark, benchmark), loc="left")
    finish_axes(ax, metric, log_scale)
    # Legend outside the axes so it can never hide a data point.
    if STYLE["dot"] >= 9:   # not in --compact: there the title and legend would collide, so the
        # caption has to say "orange = 30 s baseline" (the combined figure keeps its legend)
        ax.legend(handles=[plt.Rectangle((0, 0), 1, 1, fc=COLOR_BASELINE, ec="black"),
                           plt.Rectangle((0, 0), 1, 1, fc=COLOR_BOX, ec="black")],
                  labels=["30 s baseline", "other durations"], loc="lower right",
                  bbox_to_anchor=(1.0, 1.0), ncol=2, frameon=False, fontsize=STYLE["legend"])
    path = save(fig, out_dir, f"boxplot_{metric}_{benchmark}")
    print(f"[ok] {metric:7s} {benchmark}: runs per duration "
          f"{dict(zip(DURATIONS, (len(x) for x in data)))} -> {path}")


def plot_by_duration(df, metric, duration, benchmarks, out_dir, log_scale, show_points):
    col = METRICS[metric]["col"]
    sub = df[df["duration"] == duration]
    data = [sub.loc[sub["benchmark"] == b, col].to_numpy() for b in benchmarks]
    if all(len(x) == 0 for x in data):
        print(f"[skip] {metric}: no valid runs for duration {duration} s")
        return

    fig, ax = plt.subplots(figsize=STYLE["size"]["duration"], dpi=300 if STYLE["dot"] < 9 else 150)
    color = COLOR_BASELINE if duration == BASELINE else COLOR_BOX
    positions = draw_boxes(ax, data, [color] * len(benchmarks), show_points)
    ax.set_xticks(positions)
    ax.set_xticklabels([f"{SHORT[b]}\n(n={len(x)})" for b, x in zip(benchmarks, data)])
    ax.set_xlabel("Benchmark")
    ax.set_title(f"Warm-up/cool-down {duration} s" + (" (baseline)" if duration == BASELINE else ""),
                 loc="left")
    finish_axes(ax, metric, log_scale)
    path = save(fig, out_dir, f"boxplot_{metric}_duration_{duration}s")
    print(f"[ok] {metric:7s} {duration} s: runs per benchmark "
          f"{dict(zip(benchmarks, (len(x) for x in data)))} -> {path}")


def dpi():
    return 300 if STYLE["dot"] < 9 else 150


def make_grid(n):
    """3 columns, as many rows as needed; returns figure and a flat list of axes."""
    ncols = min(3, n)
    nrows = -(-n // ncols)
    w, h = STYLE["size"]["grid"]
    fig, axes = plt.subplots(nrows, ncols, figsize=(w, h * nrows), dpi=dpi(), squeeze=False)
    flat = list(axes.ravel())
    for ax in flat[n:]:
        ax.set_visible(False)
    return fig, flat, ncols


def save_grid(fig, out_dir, stem, legend=True):
    handles = [plt.Rectangle((0, 0), 1, 1, fc=COLOR_BASELINE, ec="black"),
               plt.Rectangle((0, 0), 1, 1, fc=COLOR_BOX, ec="black")]
    if legend:
        fig.legend(handles, ["30 s baseline", "other durations"], loc="upper right", ncol=2,
                   frameon=False, fontsize=STYLE["legend"])
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.94 if legend else 1))
    for ext in ("png", "pdf"):
        fig.savefig(out_dir / f"{stem}.{ext}")
    plt.close(fig)
    return out_dir / f"{stem}.png"


def plot_combined(df, metric, benchmarks, out_dir, log_scale, show_points):
    """One figure, one panel per benchmark; the 5 durations are the boxes of each panel."""
    col = METRICS[metric]["col"]
    fig, axes, ncols = make_grid(len(benchmarks))
    counts = {}
    for i, (b, ax) in enumerate(zip(benchmarks, axes)):
        sub = df[df["benchmark"] == b]
        data = [sub.loc[sub["duration"] == d, col].to_numpy() for d in DURATIONS]
        counts[b] = {d: len(x) for d, x in zip(DURATIONS, data)}
        positions = draw_boxes(ax, data, [COLOR_BASELINE if d == BASELINE else COLOR_BOX
                                          for d in DURATIONS], show_points)
        ax.set_xticks(positions)
        ax.set_xticklabels([str(d) for d in DURATIONS])
        ax.set_title(LABELS.get(b, b), loc="left")
        finish_axes(ax, metric, log_scale)
        ax.set_ylabel(METRICS[metric]["label"] if i % ncols == 0 else "")
        last_row = i >= len(benchmarks) - ncols
        ax.set_xlabel("Warm-up/cool-down duration (s)" if last_row else "")
    path = save_grid(fig, out_dir, f"boxplot_{metric}_all")
    print(f"[ok] {metric:7s} combined figure -> {path}")
    for b, c in counts.items():
        if any(v != 25 for v in c.values()):
            print(f"     note: {b} has runs per duration {c}")


def plot_summary(df, benchmarks, out_dir, n_boot=2000):
    """Median energy relative to the 30 s median (%), per benchmark and averaged over benchmarks.

    The pooled line is the mean of the per-benchmark relative medians (every benchmark has the same
    weight); its 95 % band comes from bootstrapping the runs inside every duration-benchmark cell.
    Descriptive only: the hypothesis tests are done on Joules, not on relative change."""
    rng = np.random.default_rng(0)
    col = METRICS["energy"]["col"]
    point, boot = {}, {}
    for b in benchmarks:
        sub = df[df["benchmark"] == b]
        cells = {d: sub.loc[sub["duration"] == d, col].to_numpy() for d in DURATIONS}
        if any(len(v) == 0 for v in cells.values()):
            continue
        meds = {d: np.median(v[rng.integers(0, len(v), (n_boot, len(v)))], axis=1)
                for d, v in cells.items()}
        point[b] = np.array([np.median(cells[d]) / np.median(cells[BASELINE]) - 1 for d in DURATIONS]) * 100
        boot[b] = np.array([meds[d] / meds[BASELINE] - 1 for d in DURATIONS]) * 100   # (5, n_boot)
    if not point:
        print("[skip] summary: no complete benchmarks")
        return
    pooled = np.mean([point[b] for b in point], axis=0)
    pooled_boot = np.mean([boot[b] for b in boot], axis=0)
    lo, hi = np.percentile(pooled_boot, [2.5, 97.5], axis=1)

    w, h = STYLE["size"]["benchmark"]
    fig, ax = plt.subplots(figsize=(w, h), dpi=dpi())
    x = np.arange(1, len(DURATIONS) + 1)
    cmap = plt.get_cmap("tab10")
    for i, b in enumerate(point):
        ax.plot(x, point[b], marker="o", markersize=3, linewidth=0.9, alpha=0.75,
                color=cmap(i), label=SHORT[b].replace("\n", " "))
    ax.axhline(0, color="black", linewidth=0.8)
    ax.errorbar(x, pooled, yerr=[pooled - lo, hi - pooled], color="black", linewidth=2, marker="s",
                markersize=4, capsize=3, elinewidth=1.2, label="Average, 95% CI")
    ax.set_xticks(x)
    ax.set_xticklabels([str(d) for d in DURATIONS])
    ax.set_xlabel("Warm-up/cool-down duration (s)")
    ax.set_ylabel("Median energy vs. 30 s baseline (%)")
    ax.grid(axis="y", alpha=0.3)
    ax.set_axisbelow(True)
    ax.legend(fontsize=STYLE["legend"], ncol=3, frameon=False, loc="lower center",
              bbox_to_anchor=(0.5, 1.0), columnspacing=1.0, handlelength=1.5)
    path = save(fig, out_dir, "summary_energy_relative_to_30s")
    print("[ok] summary: average relative change vs 30 s (%), 95% bootstrap CI -> " + str(path))
    for d, m, a, c in zip(DURATIONS, pooled, lo, hi):
        print(f"     {d:>3d} s: {m:+6.1f}  [{a:+6.1f}, {c:+6.1f}]")


def plot_runorder(df, metric, benchmarks, out_dir, log_scale, window=25):
    """Metric against the execution order of the runs, with a rolling median per benchmark."""
    col = METRICS[metric]["col"]
    fig, axes, ncols = make_grid(len(benchmarks))
    for i, (b, ax) in enumerate(zip(benchmarks, axes)):
        sub = df[df["benchmark"] == b].sort_values("order")
        ax.scatter(sub["order"], sub[col], s=STYLE["dot"], color="#6b7f99", alpha=0.45, linewidths=0)
        ax.plot(sub["order"], sub[col].rolling(window, center=True, min_periods=window // 2).median(),
                color="#d94801", linewidth=1.4)
        ax.set_title(LABELS.get(b, b), loc="left")
        finish_axes(ax, metric, log_scale)
        ax.set_ylabel(METRICS[metric]["label"] if i % ncols == 0 else "")
        last_row = i >= len(benchmarks) - ncols
        ax.set_xlabel("Run number in execution order" if last_row else "")
    path = save_grid(fig, out_dir, f"runorder_{metric}", legend=False)
    print(f"[ok] {metric:7s} run-order figure (orange = rolling median of {window} runs) -> {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sel = ap.add_mutually_exclusive_group()
    sel.add_argument("--part", type=int, choices=sorted(PARTS),
                     help="a share of the benchmarks (1, 2 or 3), two benchmarks each")
    sel.add_argument("--benchmarks", nargs="+", choices=BENCHMARKS, metavar="NAME",
                     help="explicit benchmarks: " + ", ".join(BENCHMARKS))
    sel.add_argument("--all", action="store_true", help="all six benchmarks (the default)")
    ap.add_argument("--metric", choices=[*METRICS, "all"], default="energy",
                    help="energy (default), time, runtime or all")
    ap.add_argument("--layout", nargs="+", default=["benchmark"],
                    choices=["benchmark", "duration", "both", "combined", "summary", "runorder"],
                    help="one or more of: benchmark (default), duration, both, combined, summary, "
                         "runorder (see the top of this file)")
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV, help="path to run_table.csv")
    ap.add_argument("--source", choices=["table", "raw"], default="table",
                    help="energy from run_table.csv (table) or recomputed from each run's "
                         "energibridge.csv (raw), see top of file")
    ap.add_argument("--out", type=Path, default=Path("figures"), help="output directory")
    ap.add_argument("--log", action=argparse.BooleanOptionalAction, default=None,
                    help="force a log or linear y axis (default depends on layout and metric)")
    ap.add_argument("--compact", action="store_true",
                    help="small figures (one ACM column wide, 7 pt text) for direct use in the paper")
    ap.add_argument("--notch", action="store_true", help="notched boxes (median confidence interval)")
    ap.add_argument("--no-points", action="store_true", help="hide the individual runs")
    args = ap.parse_args()

    if args.compact:
        use_compact_style()
    STYLE["notch"] = args.notch
    chosen = PARTS[args.part] if args.part else (args.benchmarks or BENCHMARKS)
    chosen = [b for b in BENCHMARKS if b in chosen]      # always in the fixed order
    metrics = list(METRICS) if args.metric == "all" else [args.metric]
    layouts = []
    for lay in args.layout:
        for l in (["benchmark", "duration"] if lay == "both" else [lay]):
            if l not in layouts:
                layouts.append(l)
    if not args.csv.exists():
        sys.exit(f"Cannot find {args.csv}. Pass the path with --csv.")

    runs = load_runs(args.csv, args.source)
    for metric in metrics:
        df = select(runs, metric)
        for layout in layouts:
            default_log = ((layout == "duration" and metric != "time")
                           or (layout in ("combined", "runorder") and metric == "energy"))
            log_scale = args.log if args.log is not None else default_log
            if layout == "combined":
                plot_combined(df, metric, chosen, args.out, log_scale, not args.no_points)
            elif layout == "runorder":
                plot_runorder(df, metric, chosen, args.out, log_scale)
            elif layout == "summary":
                if metric == "energy":
                    plot_summary(df, chosen, args.out)
                else:
                    print(f"[skip] summary is only made for energy, not {metric}")
            elif layout == "benchmark":
                for b in chosen:
                    plot_by_benchmark(df, metric, b, args.out, log_scale, not args.no_points)
            else:
                for d in DURATIONS:
                    plot_by_duration(df, metric, d, chosen, args.out, log_scale, not args.no_points)


if __name__ == "__main__":
    main()
