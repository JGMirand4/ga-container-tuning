"""H1/H2/H3 statistics + convergence plot from results/*.csv.  python analyze.py"""
import glob
import itertools
import os

import numpy as np
import pandas as pd
from scipy import stats

RES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "results")
df = pd.concat([pd.read_csv(p) for p in glob.glob(f"{RES}/*.csv")], ignore_index=True)
df = df[df.ok.astype(str) == "True"]  # penalised/invalid evaluations excluded from stats


def cliffs_delta(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return (sum(x > y for x in a for y in b) - sum(x < y for x in a for y in b)) / (len(a) * len(b))


def hypervolume(points, ref):
    """2-D hypervolume; points=(p95, thr) with p95 minimised, thr maximised; ref=(worst p95, 0)."""
    pts = sorted({(p, t) for p, t in points if p < ref[0] and t > ref[1]})
    hv, best_t = 0.0, ref[1]
    for p, t in pts:
        if t > best_t:
            hv += (ref[0] - p) * (t - best_t)
            best_t = t
    return hv


print("== H1: default config, p95 across languages (Kruskal-Wallis, per endpoint)")
for ep, g in df[df.method == "default"].groupby("endpoint"):
    groups = [x.p95.values for _, x in g.groupby("lang")]
    if len(groups) > 1:
        h, p = stats.kruskal(*groups)
        print(f"  {ep}: H={h:.2f} p={p:.4g}")
    print(g.groupby("lang").p95.agg(["median", "std"]).round(2).to_string())

print("\n== H2/H3: GA vs random vs baselines, per (lang, endpoint)")
for (lang, ep), g in df.groupby(["lang", "endpoint"]):
    ref = (g.p95.max() * 1.1, 0.0)
    hv = {}
    for m in ["ga", "random"]:
        hv[m] = [hypervolume(zip(s.p95, s.thr), ref) for _, s in g[g.method == m].groupby("seed")]
    base = g[g.method == "default"]
    best = {m: g[g.method == m].groupby("seed").p95.min() for m in ["ga", "random"]}
    line = f"  {lang}/{ep}: HV ga={np.mean(hv['ga']):.3g} rnd={np.mean(hv['random']):.3g}" if hv["ga"] and hv["random"] else f"  {lang}/{ep}:"
    if len(hv["ga"]) >= 3 and len(hv["random"]) >= 3:
        u, p = stats.mannwhitneyu(hv["ga"], hv["random"], alternative="greater")
        line += f" MWU p={p:.3g} cliffs={cliffs_delta(hv['ga'], hv['random']):.2f}"
    if len(base) and len(best["ga"]):
        line += f" | p95 gain GA vs default = {100 * (1 - best['ga'].mean() / base.p95.median()):.1f}%"  # H3 metric
    print(line)

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    groups = list(df[df.method.isin(["ga", "random"])].groupby(["lang", "endpoint"]))
    fig, axes = plt.subplots(2, 4, figsize=(16, 7), sharex=True)
    for ax, ((lang, ep), g) in zip(axes.T.flatten(), groups):
        for m, c in [("ga", "tab:blue"), ("random", "tab:gray")]:
            for _, s in g[g.method == m].groupby("seed"):
                ax.plot(np.arange(len(s)), s.p95.cummin().values, color=c, alpha=0.6)
        ax.set_title(f"{lang}/{ep}")
        ax.set_yscale("log")
    fig.supxlabel("evaluation #")
    fig.supylabel("best p95 so far (ms)  blue=GA gray=random")
    fig.savefig(f"{RES}/convergence.png", dpi=120, bbox_inches="tight")
    print("\nsaved results/convergence.png")
except ImportError:
    pass
