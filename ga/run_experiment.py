"""Driver.

  python run_experiment.py setup              # build images + network
  python run_experiment.py calibrate          # arrival rate per (lang, endpoint) + common rate per endpoint
  python run_experiment.py baseline           # default, manual and default@common-rate, --repeats N each
  python run_experiment.py ga|random          # NSGA-II / random search, --seeds, --pop x --gens evaluations
  python run_experiment.py reeval             # re-measure the configs picked from each finished search
  python run_experiment.py plan               # estimated wall-clock time of the selected experiment
Use --langs / --endpoints to restrict, --warmup/--duration/--reps/--sat to change per-evaluation timing
and --results to write somewhere else (e.g. smoke tests).
Each output CSV is skipped if complete; an incomplete one is restarted from scratch.
"""
import argparse
import csv
import json
import os
import random
import statistics

import runner
from config import (DEFAULT_COST, ENDPOINTS, LANGS, RATE_FRAC, RATE_WARN, SLO_FACTOR, cost, genome_from_values,
                    key, manual_genome, random_genome, values)
from nsga2 import run_nsga2

FIELDS = ["method", "lang", "endpoint", "seed", "gen", "pick", "genes", "rate",
          "ok", "p95", "p99", "drop", "fail", "p95_fit", "thr", "cost", "k6_cpu"]
PICKS = ["best_p95", "min_cost_slo"]


def rates_path(args):
    return os.path.join(args.results, "rates.json")


def load_rates(args):
    with open(rates_path(args)) as f:
        return json.load(f)


def count_rows(path):
    with open(path) as f:
        return max(sum(1 for _ in f) - 1, 0)


def open_csv(path, needed):
    """Returns (log, file) to write `path` from scratch, or None if it is already complete.
    Files in an older column format are kept as <path>.legacy instead of being deleted."""
    if os.path.exists(path):
        with open(path) as f:
            header = f.readline().strip().split(",")
        if header != FIELDS:
            os.replace(path, path + ".legacy")
            print("old format, moved to", path + ".legacy")
        elif count_rows(path) >= needed:
            print("skip", path)
            return None
        else:
            os.remove(path)  # restart incomplete run from scratch
    f = open(path, "w", newline="")
    w = csv.DictWriter(f, FIELDS)
    w.writeheader()

    def log(**row):
        w.writerow(row)
        f.flush()

    return log, f


def row(method, lang, ep, seed, gen, genome, rate, res, pick=""):
    v = values(genome) if genome else {}
    return dict(method=method, lang=lang, endpoint=ep, seed=seed, gen=gen, pick=pick, genes=json.dumps(v), rate=rate,
                ok=res["ok"], p95=res["p95"], p99=res["p99"], drop=res["drop"], fail=res["fail"],
                p95_fit=res["p95_fit"], thr=res["thr"], cost=cost(v) if v else DEFAULT_COST, k6_cpu=res["k6_cpu"])


def show(label, res):
    print(label, f"ok={res['ok']} p95={res['p95']:.2f} p95_fit={res['p95_fit']:.2f} thr={res['thr']:.0f} "
                 f"drop={res['drop']:.3f} k6cpu={res['k6_cpu']}")


def calibrate(args):
    rates = load_rates(args) if os.path.exists(rates_path(args)) else {}  # merge: allow partial recalibration
    for lang in args.langs:
        for ep in args.endpoints:
            name = runner.start(lang, None)
            if name is None:
                runner.teardown(f"svc-{lang}")
                print("could not start", lang)
                continue
            runner.k6(name, "sat", ep, 0, runner.T["warmup"])  # warm up under full load (JIT, caches, pools)
            sats = [r["rps_ok"] for r in (runner.k6(name, "sat", ep, 0, 15) for _ in range(3)) if r]
            runner.teardown(name)
            if not sats:
                print("k6 failed for", lang, ep)
                continue
            sat = statistics.median(sats)
            rates[f"{lang}/{ep}"] = max(1, round(args.rate_frac * sat))
            rates[f"sat/{lang}/{ep}"] = round(sat, 1)
            print(lang, ep, "sat rps", [round(s) for s in sats], "-> rate", rates[f"{lang}/{ep}"])
    # Common rate per endpoint (same absolute load for every language, for H1): fraction of the slowest one
    for ep in ENDPOINTS:
        sats = [rates.get(f"sat/{lang}/{ep}") for lang in LANGS]
        if all(sats):
            rates[f"common/{ep}"] = max(1, round(args.rate_frac * min(sats)))
            print("common rate", ep, rates[f"common/{ep}"])
    with open(rates_path(args), "w") as f:
        json.dump(rates, f, indent=2)


def run_baseline(lang, ep, rates, args):
    path = os.path.join(args.results, f"baseline_{lang}_{ep}.csv")
    rate = rates[f"{lang}/{ep}"]
    plan = [("default", None, rate), ("manual", manual_genome(lang), rate)]
    if f"common/{ep}" in rates:
        plan.append(("default_common", None, rates[f"common/{ep}"]))
    opened = open_csv(path, len(plan) * args.repeats)
    if not opened:
        return
    log, f = opened
    thr = []
    for method, genome, r in plan:
        for i in range(args.repeats):
            res = runner.evaluate(lang, ep, genome, r)
            log(**row(method, lang, ep, 0, i, genome, r, res))
            show(f"{method} {lang} {ep} #{i}", res)
            if method == "default" and res["ok"]:
                thr.append(res["thr"])
    f.close()
    if thr and rate > RATE_WARN * statistics.median(thr):
        print(f"WARNING {lang}/{ep}: rate {rate} > {RATE_WARN:.0%} of measured default throughput "
              f"{statistics.median(thr):.0f}; the service is near/over saturation. Recalibrate.")


def objectives(res, genome):
    return (res["p95_fit"], -res["thr"], cost(values(genome)))  # all minimised


def run_search(method, lang, ep, seed, args, rate):
    path = os.path.join(args.results, f"{method}_{lang}_{ep}_s{seed}.csv")
    budget = args.pop * args.gens
    opened = open_csv(path, budget)
    if not opened:
        return
    log, f = opened
    cache = {}

    def evaluate(genome, gen):
        k = key(genome)
        if k not in cache:  # identical genome: reuse measurement, still counts toward budget
            cache[k] = runner.evaluate(lang, ep, genome, rate)
        res = cache[k]
        log(**row(method, lang, ep, seed, gen, genome, rate, res))
        show(f"{method} {lang} {ep} s{seed} g{gen} {values(genome)}", res)
        return objectives(res, genome)

    if method == "ga":
        run_nsga2(lang, evaluate, args.pop, args.gens, seed)
    else:
        rng = random.Random(10_000 + seed)
        for i in range(budget):
            evaluate(random_genome(rng, lang), i // args.pop)
    f.close()


def slo_for(lang, ep, args):
    """SLO = SLO_FACTOR x median raw p95 of the default config (from the baseline CSV)."""
    path = os.path.join(args.results, f"baseline_{lang}_{ep}.csv")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        p = [float(r["p95"]) for r in csv.DictReader(f) if r["method"] == "default" and r["ok"] == "True"]
    return args.slo_factor * statistics.median(p) if p else None


def picks_of(path, slo):
    """Configs to re-measure from one search log: lowest p95, and cheapest one meeting the SLO."""
    with open(path) as f:
        ok = [r for r in csv.DictReader(f) if r["ok"] == "True"]
    out = {}
    if ok:
        out["best_p95"] = min(ok, key=lambda r: float(r["p95_fit"]))
        feas = [r for r in ok if float(r["p95_fit"]) <= slo and float(r["drop"]) == 0]
        if feas:
            out["min_cost_slo"] = min(feas, key=lambda r: (float(r["cost"]), float(r["p95_fit"])))
    return out


def run_reeval(lang, ep, rates, args):
    slo = slo_for(lang, ep, args)
    if slo is None:
        print("no baseline for", lang, ep, "- run baseline first")
        return
    for method in ["ga", "random"]:
        for seed in args.seeds:
            src = os.path.join(args.results, f"{method}_{lang}_{ep}_s{seed}.csv")
            if not os.path.exists(src) or count_rows(src) < args.pop * args.gens:
                print("search not finished:", src)
                continue
            picks = picks_of(src, slo)
            opened = open_csv(os.path.join(args.results, f"reeval_{method}_{lang}_{ep}_s{seed}.csv"),
                              len(picks) * args.repeats)
            if not opened:
                continue
            log, f = opened
            for pick, r in picks.items():
                genome = genome_from_values(json.loads(r["genes"]))
                for i in range(args.repeats):  # fresh container each time: independent measurements
                    res = runner.evaluate(lang, ep, genome, rates[f"{lang}/{ep}"])
                    log(**row(method, lang, ep, seed, i, genome, rates[f"{lang}/{ep}"], res, pick=pick))
                    show(f"reeval {method} {lang} {ep} s{seed} {pick} #{i}", res)
            f.close()


def plan(args):
    e = runner.eval_seconds()
    n = len(args.langs) * len(args.endpoints)
    searches = n * 2 * len(args.seeds)
    parts = {
        "calibrate": n * (8 + runner.T["warmup"] + 3 * 15 + 12),
        "baseline": n * 3 * args.repeats * e,
        "ga + random (upper bound; the cache saves repeated genomes)": searches * args.pop * args.gens * e,
        "reeval": searches * len(PICKS) * args.repeats * e,
    }
    print(f"~{e:.0f} s per evaluation, {n} (lang, endpoint) pairs, {searches} searches of {args.pop * args.gens} evals")
    for k, s in parts.items():
        print(f"  {k:60s} {s / 3600:7.1f} h")
    total = sum(parts.values())
    print(f"  {'total':60s} {total / 3600:7.1f} h  (~{total / 86400:.1f} days)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "calibrate", "baseline", "ga", "random", "reeval", "plan"])
    ap.add_argument("--langs", nargs="+", default=LANGS)
    ap.add_argument("--endpoints", nargs="+", default=ENDPOINTS)
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
    ap.add_argument("--pop", type=int, default=12)
    ap.add_argument("--gens", type=int, default=8)
    ap.add_argument("--repeats", type=int, default=5, help="baseline / reeval repetitions")
    ap.add_argument("--rate-frac", type=float, default=RATE_FRAC)
    ap.add_argument("--slo-factor", type=float, default=SLO_FACTOR)
    ap.add_argument("--warmup", type=int)
    ap.add_argument("--duration", type=int)
    ap.add_argument("--reps", type=int)
    ap.add_argument("--sat", type=int)
    ap.add_argument("--results", default=os.path.join(runner.ROOT, "results"))
    args = ap.parse_args()
    runner.configure(warmup=args.warmup, duration=args.duration, reps=args.reps, sat=args.sat)
    os.makedirs(args.results, exist_ok=True)

    if args.cmd == "setup":
        runner.ensure_network()
        runner.build_images()
        return
    if args.cmd == "plan":
        return plan(args)
    if args.cmd == "calibrate":
        return calibrate(args)

    rates = load_rates(args)
    for lang in args.langs:
        for ep in args.endpoints:
            if args.cmd == "baseline":
                run_baseline(lang, ep, rates, args)
            elif args.cmd == "reeval":
                run_reeval(lang, ep, rates, args)
            else:
                for seed in args.seeds:
                    run_search(args.cmd, lang, ep, seed, args, rates[f"{lang}/{ep}"])


if __name__ == "__main__":
    main()
