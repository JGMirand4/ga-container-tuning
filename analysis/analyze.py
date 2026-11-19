"""Quality checks, H1/H2/H3 statistics and convergence plot.

  python analyze.py [results_dir]        # default: ../results   (e.g. ../results/mac for the Mac smoke data)
"""
import glob
import itertools
import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ga"))
from config import CV_WARN, DEFAULT_COST, K6_CPU_WARN, RATE_WARN, SLO_FACTOR, cost  # noqa: E402

RES = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 else os.path.join(ROOT, "results")
RNG = np.random.default_rng(0)


def load():
    frames = []
    for kind in ["baseline", "ga", "random", "reeval"]:
        for p in sorted(glob.glob(f"{RES}/{kind}_*.csv")):
            d = pd.read_csv(p)
            d["i"] = np.arange(len(d))  # evaluation index within the file (before filtering invalid rows)
            d["kind"] = kind
            frames.append(d)
    if not frames:
        sys.exit(f"no result CSVs in {RES}")
    df = pd.concat(frames, ignore_index=True)
    # Older CSVs (before raw/penalised p95 were split) stored the penalised value in p95
    for c, default in [("p95_fit", None), ("drop", 0.0), ("pick", ""), ("k6_cpu", np.nan), ("rate", np.nan)]:
        if c not in df:
            df[c] = df["p95"] if c == "p95_fit" else default
    if "cost" not in df:
        df["cost"] = [cost(json.loads(g)) if json.loads(g) else DEFAULT_COST for g in df.genes]
    df["pick"] = df["pick"].fillna("")
    df["ok"] = df.ok.astype(str) == "True"
    rates_file = os.path.join(RES, "rates.json")
    if df.rate.isna().any() and os.path.exists(rates_file):
        rates = json.load(open(rates_file))
        miss = df.rate.isna()
        df.loc[miss, "rate"] = [rates.get(f"{l}/{e}") for l, e in zip(df.lang[miss], df.endpoint[miss])]
    return df


def cliffs_delta(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return (sum(x > y for x in a for y in b) - sum(x < y for x in a for y in b)) / (len(a) * len(b))


def kruskal(groups):
    """Kruskal-Wallis H, p and effect size epsilon^2 = (H - k + 1) / (n - k)."""
    groups = [np.asarray(g) for g in groups if len(g)]
    if len(groups) < 2:
        return None
    h, p = stats.kruskal(*groups)
    n, k = sum(map(len, groups)), len(groups)
    return h, p, max(0.0, (h - k + 1) / (n - k))


def boot_gain(a, b, n=5000):
    """Relative reduction 1 - median(a)/median(b), with a 95% bootstrap CI."""
    a, b = np.asarray(a), np.asarray(b)
    point = 1 - np.median(a) / np.median(b)
    sa = RNG.choice(a, (n, len(a))).astype(float)
    sb = RNG.choice(b, (n, len(b))).astype(float)
    bs = 1 - np.median(sa, 1) / np.median(sb, 1)
    return point, np.percentile(bs, 2.5), np.percentile(bs, 97.5)


def hv2d(P, ref):
    hv, best_y = 0.0, ref
    for x, y in sorted(map(tuple, P)):
        if y < best_y:
            hv += (ref - x) * (best_y - y)
            best_y = y
    return hv


def hv3d(P, ref=1.1):
    """Hypervolume of minimised, normalised points P (n x 3) w.r.t. (ref, ref, ref), by slicing on axis 2."""
    P = np.asarray(P)
    if not len(P):
        return 0.0
    zs = np.unique(P[:, 2])
    hv = 0.0
    for z, z_next in zip(zs, list(zs[1:]) + [ref]):
        hv += hv2d(P[P[:, 2] <= z][:, :2], ref) * (z_next - z)
    return hv


def normalised_objectives(g):
    """(log p95_fit, -thr, cost) scaled to [0, 1] over the whole group, so methods/seeds share one scale."""
    F = np.column_stack([np.log10(g.p95_fit.values), -g.thr.values, g.cost.values]).astype(float)
    lo, hi = F.min(0), F.max(0)
    return (F - lo) / np.where(hi > lo, hi - lo, 1.0)


def fmt_p(p):
    return f"{p:.3g}"


df = load()
ok = df[df.ok]
base = ok[ok.kind == "baseline"]
default = base[base.method == "default"]
slo = {k: SLO_FACTOR * g.p95.median() for k, g in default.groupby(["lang", "endpoint"])}

print(f"results: {RES}\n")
print("== Quality checks")
print(f"-- coefficient of variation of repeated baseline runs (warn > {CV_WARN:.0%})")
for (lang, ep, m), g in base.groupby(["lang", "endpoint", "method"]):
    cv_p = g.p95.std() / g.p95.mean()
    cv_t = g.thr.std() / g.thr.mean()
    flag = "  <-- noisy" if max(cv_p, cv_t) > CV_WARN else ""
    print(f"  {lang:6s} {ep:3s} {m:14s} CV p95={cv_p:6.1%}  CV thr={cv_t:6.1%}{flag}")
print(f"-- arrival rate vs measured default throughput (warn > {RATE_WARN:.0%})")
for (lang, ep), g in default.groupby(["lang", "endpoint"]):
    ratio = g.rate.iloc[0] / g.thr.median()
    flag = "  <-- overloaded, recalibrate" if ratio > RATE_WARN else ""
    print(f"  {lang:6s} {ep:3s} rate={g.rate.iloc[0]:.0f} thr={g.thr.median():.0f} ratio={ratio:.0%}{flag}")
k6 = df.k6_cpu.dropna()
if len(k6):
    print(f"-- k6 CPU: max {k6.max():.0f}%, {np.mean(k6 > K6_CPU_WARN):.1%} of evaluations above {K6_CPU_WARN:.0f}%"
          + ("  <-- load generator may be the bottleneck" if k6.max() > K6_CPU_WARN else ""))
else:
    print("-- k6 CPU: not recorded")
invalid = df[~df.ok & df.kind.isin(["ga", "random"])]
if len(df[df.kind.isin(["ga", "random"])]):
    print(f"-- invalid search evaluations (OOM/crash/>5% errors): {len(invalid)}")

print("\n== H1: default config across languages (Kruskal-Wallis, effect size eps^2)")
for ep, g in default.groupby("endpoint"):
    for metric, label in [("p95", "p95 @ 50% of own capacity"), ("thr", "saturation throughput")]:
        r = kruskal([x[metric].values for _, x in g.groupby("lang")])
        if r:
            print(f"  {ep} {label}: H={r[0]:.2f} p={fmt_p(r[1])} eps2={r[2]:.2f}")
    common = base[(base.method == "default_common") & (base.endpoint == ep)]
    r = kruskal([x.p95.values for _, x in common.groupby("lang")])
    if r:
        print(f"  {ep} p95 @ common rate {common.rate.iloc[0]:.0f} req/s: H={r[0]:.2f} p={fmt_p(r[1])} eps2={r[2]:.2f}")
    table = g.groupby("lang").agg(p95_median=("p95", "median"), thr_median=("thr", "median"))
    if len(common):
        table["p95_common"] = common.groupby("lang").p95.median()
    print(table.round(2).to_string())
    langs = sorted(g.lang.unique())
    print("  pairwise Cliff's delta of saturation throughput (row vs col):")
    for a, b in itertools.combinations(langs, 2):
        d = cliffs_delta(g[g.lang == a].thr, g[g.lang == b].thr)
        print(f"    {a} vs {b}: {d:+.2f}")

print("\n== H2: GA vs random search, normalised 3-D hypervolume (p95, throughput, cost) per seed")
search = ok[ok.kind.isin(["ga", "random"])]
for (lang, ep), g in search.groupby(["lang", "endpoint"]):
    F = normalised_objectives(g)
    hv = {m: [hv3d(F[(g.method == m).values & (g.seed == s).values]) for s in sorted(g[g.method == m].seed.unique())]
          for m in ["ga", "random"]}
    line = f"  {lang}/{ep}: HV ga={np.mean(hv['ga']) if hv['ga'] else float('nan'):.3f} (n={len(hv['ga'])}) " \
           f"random={np.mean(hv['random']) if hv['random'] else float('nan'):.3f} (n={len(hv['random'])})"
    if len(hv["ga"]) >= 3 and len(hv["random"]) >= 3:
        _, p = stats.mannwhitneyu(hv["ga"], hv["random"], alternative="greater")
        line += f"  MWU(ga>random) p={fmt_p(p)} cliffs={cliffs_delta(hv['ga'], hv['random']):+.2f}"
    else:
        line += "  (need >= 3 seeds per method for the test; 5 recommended)"
    print(line)
    if (lang, ep) in slo:
        feas = g[(g.p95_fit <= slo[(lang, ep)]) & (g["drop"] == 0)]
        mc = {m: feas[feas.method == m].groupby("seed").cost.min() for m in ["ga", "random"]}
        print(f"      cheapest config meeting SLO p95<={slo[(lang, ep)]:.1f}ms during search (median over seeds): "
              f"ga={mc['ga'].median() if len(mc['ga']) else float('nan'):.2f} "
              f"random={mc['random'].median() if len(mc['random']) else float('nan'):.2f} (default={DEFAULT_COST:.2f})")

print("\n== H3: gain over the default config, re-measured independently (reeval_*.csv)")
reeval = ok[ok.kind == "reeval"]
if not len(reeval):
    print("  no re-evaluations yet (python run_experiment.py reeval). Search-log minima are optimistic, not reported.")
per_seed = []
for (lang, ep, m), g in reeval.groupby(["lang", "endpoint", "method"]):
    d = default[(default.lang == lang) & (default.endpoint == ep)]
    if not len(d):
        continue
    best = g[g.pick == "best_p95"]
    if len(best):
        gain, lo, hi = boot_gain(best.p95, d.p95)
        tgain = best.thr.median() / d.thr.median() - 1
        _, p = stats.mannwhitneyu(best.p95, d.p95, alternative="two-sided")
        print(f"  {lang}/{ep} {m:6s} best_p95: p95 -{gain:.1%} [95% CI {lo:.1%}, {hi:.1%}] vs default "
              f"(MWU p={fmt_p(p)}, cliffs={cliffs_delta(best.p95, d.p95):+.2f}), throughput {tgain:+.1%}")
    cheap = g[g.pick == "min_cost_slo"]
    if len(cheap):
        meets = np.mean((cheap.p95_fit <= slo[(lang, ep)]) & (cheap["drop"] == 0))
        saving = 1 - cheap.cost.median() / DEFAULT_COST
        print(f"  {lang}/{ep} {m:6s} min_cost_slo: cost {cheap.cost.median():.2f} ({saving:.0%} below default), "
              f"meets SLO in {meets:.0%} of re-measurements")
    for s, gs in g.groupby("seed"):
        b, c = gs[gs.pick == "best_p95"], gs[gs.pick == "min_cost_slo"]
        per_seed.append(dict(lang=lang, endpoint=ep, method=m, seed=s,
                             p95_gain=1 - b.p95.median() / d.p95.median() if len(b) else np.nan,
                             cost_saving=1 - c.cost.median() / DEFAULT_COST if len(c) else np.nan))
if per_seed:
    ps = pd.DataFrame(per_seed)
    print("  -- does the GA gain depend on the language? (Kruskal-Wallis over per-seed GA gains)")
    for ep, g in ps[ps.method == "ga"].groupby("endpoint"):
        for metric in ["p95_gain", "cost_saving"]:
            r = kruskal([x[metric].dropna().values for _, x in g.groupby("lang")])
            med = g.groupby("lang")[metric].median().map(lambda v: f"{v:.0%}").to_dict()
            print(f"    {ep} {metric}: {med}" + (f"  H={r[0]:.2f} p={fmt_p(r[1])} eps2={r[2]:.2f}" if r else ""))

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    runs = df[df.kind.isin(["ga", "random"])]  # includes invalid rows, so the x-axis is the true evaluation index
    groups = list(runs.groupby(["lang", "endpoint"]))
    if groups:
        cols = max(1, (len(groups) + 1) // 2)
        fig, axes = plt.subplots(2, cols, figsize=(4 * cols, 7), sharex=True, squeeze=False)
        for ax in axes.flatten()[len(groups):]:
            ax.set_visible(False)
        for ax, ((lang, ep), g) in zip(axes.flatten(), groups):
            for m, c in [("ga", "tab:blue"), ("random", "tab:gray")]:
                for _, s in g[g.method == m].groupby("seed"):
                    ax.plot(s.i.values, s.p95_fit.cummin().values, color=c, alpha=0.6)
            ax.set_title(f"{lang}/{ep}")
            ax.set_yscale("log")
        fig.supxlabel("evaluation #")
        fig.supylabel("best p95 (fitness) so far, ms   blue=GA  gray=random")
        fig.savefig(f"{RES}/convergence.png", dpi=120, bbox_inches="tight")
        print(f"\nsaved {RES}/convergence.png")
except ImportError:
    pass
