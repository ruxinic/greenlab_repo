from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy import stats
from statsmodels.stats.multitest import multipletests

HERE = Path(__file__).resolve().parent
CSV = (HERE.parent / "warmup_cooldown" / "experiments"
       / "warmup_cooldown" / "run_table.csv")
OUT = HERE

DURS = [5, 30, 60, 120, 210]
BASE, REF = 30, 210
ORDER = ["n-body", "spectral-norm", "scimark-lu", "scimark-sor",
         "scimark-sparse", "binary-trees"]
TEST = "mwu"     # "mwu" (Mann-Whitney) or "t" (Welch t-test)
YLIM = None      # e.g. (0, 3) to clip Figure A
MIN_N = 3        # min runs per cell to run a test
SHOW_CI = True   # error bars in Figure B
THRESH = 1.10    # RQ1 rule: mean A at most 10% above 210s
RNG = np.random.default_rng(0)

# ---------- load and clean
raw = pd.read_csv(CSV)
df = raw[raw["__done"] == "DONE"].rename(columns={
    "duration": "dur", "benchmark": "bench",
    "energy_j": "energy", "benchmark_time_s": "time",
    "total_time_s": "total"})
df["ts"] = pd.to_datetime(df["timestamp"])
df = df[["bench", "dur", "energy", "time", "total", "ts"]]

n_done = len(df)
for col in ["dur", "energy", "time", "total"]:
    num = pd.to_numeric(df[col], errors="coerce")
    bad = df[num.isna()]
    if len(bad):
        print(f"non-numeric values in '{col}':")
        print(bad[[col]].drop_duplicates().to_string())
    df[col] = num
df = df.dropna()
print(f"excluded (not DONE): {len(raw) - n_done} of {len(raw)}")
print(f"dropped (non-numeric): {n_done - len(df)}")
print(df.groupby(["bench", "dur"]).size().unstack())

BENCH = [b for b in ORDER if b in set(df.bench)]
BENCH += sorted(set(df.bench) - set(BENCH))

# absolute deviation from the combination median (A_ijk)
med = df.groupby(["bench", "dur"]).energy.transform("median")
df["A"] = (df.energy - med).abs()


def bh(pvals):
    if not pvals:
        return {}
    keys = list(pvals)
    adj = multipletests([pvals[k] for k in keys],
                        method="fdr_bh")[1]
    return dict(zip(keys, adj))


def stars(p):
    return "***" if p < .001 else "**" if p < .01 else \
           "*" if p < .05 else ""


def boot_ci(x, n=2000):
    x = np.asarray(x)
    m = RNG.choice(x, (n, len(x))).mean(axis=1)
    return np.percentile(m, [2.5, 97.5])


# ---------- RQ0: each duration vs 30s baseline, BH over all
p0 = {}
for b in BENCH:
    d = df[df.bench == b]
    ref = d[d.dur == BASE].energy
    for x in DURS:
        if x == BASE:
            continue
        cur = d[d.dur == x].energy
        if len(cur) < MIN_N or len(ref) < MIN_N:
            continue
        if TEST == "mwu":
            p = stats.mannwhitneyu(cur, ref).pvalue
        else:
            p = stats.ttest_ind(cur, ref, equal_var=False).pvalue
        p0[(b, x)] = p
p0 = bh(p0)
print("\nRQ0 adjusted p-values vs 30s:")
for k, v in p0.items():
    print(f"  {k[0]:15s} {k[1]:>3d}s  p={v:.4f} {stars(v)}")

# ---------- Figure A: relative energy vs 30s baseline
fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.6),
                         sharey=True, sharex=True)
for ax, b in zip(axes.flat, BENCH):
    d = df[df.bench == b]
    base_runs = d[d.dur == BASE].energy
    ax.set_title(b, fontsize=9)
    if base_runs.empty:
        continue
    base = base_runs.median()
    groups = [d[d.dur == x].energy / base for x in DURS]
    pos = [i for i, g in enumerate(groups) if len(g)]
    ax.boxplot([groups[i] for i in pos], positions=pos,
               widths=.6, showfliers=False)
    # set ticks AFTER boxplot, which overwrites them
    ax.set_xticks(range(len(DURS)))
    ax.set_xticklabels(DURS)
    for i in pos:  # all runs drawn, outliers included
        g = groups[i]
        ax.scatter(i + RNG.uniform(-.18, .18, len(g)), g,
                   s=5, alpha=.5, color="k", zorder=3)
        key = (b, DURS[i])
        if key in p0 and stars(p0[key]):
            top = YLIM[1] * .93 if YLIM else g.max() * 1.01
            ax.text(i, top, stars(p0[key]),
                    ha="center", va="bottom", fontsize=9)
    ax.axhline(1, ls="--", lw=.8, color="r")
    if YLIM:
        ax.set_ylim(*YLIM)
for ax in axes[:, 0]:
    ax.set_ylabel("Energy / 30s median")
fig.supxlabel("Warm-up/cool-down duration (s)", fontsize=10)
fig.tight_layout()
fig.savefig(OUT / "fig_rq0_relative_energy.pdf")

# ---------- RQ1: A at each duration vs A at 210s (one-sided)
p1 = {}
for b in BENCH:
    d = df[df.bench == b]
    ref = d[d.dur == REF].A
    for x in DURS:
        if x == REF:
            continue
        cur = d[d.dur == x].A
        if len(cur) < MIN_N or len(ref) < MIN_N:
            continue
        p1[(b, x)] = stats.mannwhitneyu(
            cur, ref, alternative="greater").pvalue
p1 = bh(p1)

# ---------- Figure B: variability vs total time
fig, ax = plt.subplots(figsize=(3.6, 3.2))
colors = plt.cm.tab10.colors
rows, recs = [], {}
for c, b in zip(colors, BENCH):
    d = df[df.bench == b]
    ref = d[d.dur == REF].A.mean()
    if np.isnan(ref) or ref == 0:
        print(f"{b}: no usable {REF}s data, skipped in Fig. B")
        continue
    xs, ys, lo, hi, ok, ds = [], [], [], [], [], []
    for x in DURS:
        s = d[d.dur == x]
        if s.empty:
            continue
        ci = boot_ci(s.A) / ref
        ds.append(x)
        xs.append(s.total.mean())
        ys.append(s.A.mean() / ref)
        lo.append(ci[0])
        hi.append(ci[1])
        ok.append(x == REF or p1.get((b, x), 1.0) >= .05)
        rows.append(dict(
            bench=b, dur=x,
            mean_A=s.A.mean(), sd=s.energy.std(),
            ratio_vs_210=s.A.mean() / ref,
            p_adj_larger_than_210=p1.get((b, x), np.nan),
            total_time_s=s.total.mean()))
    ys, lo, hi = map(np.array, (ys, lo, hi))
    ax.plot(xs, ys, "-", color=c, lw=1, label=b)
    if SHOW_CI:
        ax.errorbar(xs, ys, yerr=[ys - lo, hi - ys], fmt="none",
                    ecolor=c, lw=.6, alpha=.5)
    for xv, yv, f in zip(xs, ys, ok):
        ax.plot(xv, yv, "o", ms=4, color=c,
                mfc=c if f else "white")
    # rule: shortest d not sig. larger than 210s and <= THRESH
    rec = [x for x, y, f in zip(ds, ys, ok) if f and y <= THRESH]
    recs[b] = rec[0] if rec else REF
ax.axhline(THRESH, ls="--", lw=.8, color="r")
ax.axhline(1.0, ls=":", lw=.6, color="gray")
ax.set_xscale("log")
ax.set_xlabel("Total time per run (s)")
ax.set_ylabel("Mean |deviation| / value at 210 s")
ax.legend(fontsize=6, frameon=False)
fig.tight_layout()
fig.savefig(OUT / "fig_rq1_tradeoff.pdf")

# ---------- RQ1 table and recommendation
summ = pd.DataFrame(rows)
summ["not_sig_larger"] = (
    summ.p_adj_larger_than_210.isna()
    | (summ.p_adj_larger_than_210 >= .05))
summ["within_10pct"] = summ.ratio_vs_210 <= THRESH
summ["suitable"] = summ.not_sig_larger & summ.within_10pct
summ.to_csv(OUT / "rq1_summary.csv", index=False)
print("\nRQ1 summary (p_adj: one-sided, A larger than at 210s):")
print(summ.round(3).to_string(index=False))
print("\nRQ1 recommended duration per benchmark:")
for b, r in recs.items():
    print(f"  {b:15s} {r}s")
if recs:
    print(f"Overall recommendation (longest): "
          f"{max(recs.values())}s")

# ---------- Figure C (diagnostic): drift over execution order
fig, axes = plt.subplots(2, 3, figsize=(7.2, 4.6), sharex=True)
sc = None
for ax, b in zip(axes.flat, BENCH):
    d = df[df.bench == b].sort_values("ts")
    ax.set_title(b, fontsize=9)
    base_runs = d[d.dur == BASE].energy
    if base_runs.empty:
        continue
    base = base_runs.median()
    t = (d.ts - df.ts.min()).dt.total_seconds() / 3600
    sc = ax.scatter(t, d.energy / base, s=6,
                    c=d.dur.map(DURS.index), cmap=plt.cm.viridis,
                    vmin=0, vmax=len(DURS) - 1)
    ax.axhline(1, ls="--", lw=.8, color="r")
for ax in axes[:, 0]:
    ax.set_ylabel("Energy / 30s median")
fig.supxlabel("Hours since first run", fontsize=10)
if sc is not None:
    cb = fig.colorbar(sc, ax=axes, ticks=range(len(DURS)))
    cb.ax.set_yticklabels(DURS)
    cb.set_label("Duration (s)")
fig.savefig(OUT / "fig_drift_check.pdf")

print(f"\nfigures saved in {OUT}")