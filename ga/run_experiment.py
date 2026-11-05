"""Driver.

  python run_experiment.py setup                      # build images + network
  python run_experiment.py calibrate                  # fixes arrival rate per (lang, endpoint)
  python run_experiment.py baseline --seeds 5         # default + manual configs, N repeats
  python run_experiment.py ga|random --seeds 0 1      # 160 evals each (pop 16 x 10 gens)
Use --langs / --endpoints to restrict; --pop / --gens to change the budget.
Each (method, lang, endpoint, seed) writes results/<...>.csv and is skipped if complete.
"""
import argparse
import csv
import json
import os
import random
import statistics

import runner
from config import ENDPOINTS, LANGS, key, manual_genome, random_genome, values
from nsga2 import run_nsga2

RES = os.path.join(runner.ROOT, "results")
RATES = os.path.join(RES, "rates.json")
FIELDS = ["method", "lang", "endpoint", "seed", "gen", "genes", "ok", "p95", "p99", "thr"]


def load_rates():
    with open(RATES) as f:
        return json.load(f)


def rows_done(path):
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        return max(sum(1 for _ in f) - 1, 0)


def objectives(res):
    return (res["p95"], -res["thr"])  # minimise p95, maximise throughput


def calibrate(args):
    rates = load_rates() if os.path.exists(RATES) else {}  # merge: allow per-language recalibration
    for lang in args.langs:
        for ep in args.endpoints:
            name = runner.start(lang, None)
            runner.k6(name, "rate", ep, 10, runner.WARMUP)
            rps = statistics.median(runner.k6(name, "sat", ep, 0, 15)["rps"] for _ in range(3))
            runner.teardown(name)
            rates[f"{lang}/{ep}"] = max(1, round(0.3 * rps))
            print(lang, ep, "sat rps", round(rps, 1), "-> rate", rates[f"{lang}/{ep}"])
    os.makedirs(RES, exist_ok=True)
    with open(RATES, "w") as f:
        json.dump(rates, f, indent=2)


def write_logger(path):
    new = not os.path.exists(path)
    f = open(path, "a", newline="")
    w = csv.DictWriter(f, FIELDS)
    if new:
        w.writeheader()

    def log(**row):
        w.writerow(row)
        f.flush()

    return log, f


def run_search(method, lang, ep, seed, args, rate):
    path = os.path.join(RES, f"{method}_{lang}_{ep}_s{seed}.csv")
    budget = args.pop * args.gens
    if rows_done(path) >= budget:
        print("skip", path)
        return
    if os.path.exists(path):
        os.remove(path)  # restart incomplete run from scratch
    log, f = write_logger(path)
    cache = {}

    def evaluate(genome, gen):
        k = key(genome)
        if k not in cache:  # identical genome: reuse measurement, still counts toward budget
            cache[k] = runner.evaluate(lang, ep, genome, rate)
        res = cache[k]
        log(method=method, lang=lang, endpoint=ep, seed=seed, gen=gen, genes=json.dumps(values(genome)),
            ok=res["ok"], p95=res["p95"], p99=res["p99"], thr=res["thr"])
        print(method, lang, ep, f"s{seed} g{gen}", values(genome), f"p95={res['p95']:.1f} thr={res['thr']:.0f}")
        return objectives(res)

    if method == "ga":
        run_nsga2(lang, evaluate, args.pop, args.gens, seed)
    else:
        rng = random.Random(10_000 + seed)
        for i in range(budget):
            evaluate(random_genome(rng, lang), i // args.pop)
    f.close()


def run_baseline(lang, ep, rate, repeats):
    path = os.path.join(RES, f"baseline_{lang}_{ep}.csv")
    if rows_done(path) >= 2 * repeats:
        print("skip", path)
        return
    if os.path.exists(path):
        os.remove(path)
    log, f = write_logger(path)
    for name, genome in [("default", None), ("manual", manual_genome(lang))]:
        for r in range(repeats):
            res = runner.evaluate(lang, ep, genome, rate)
            log(method=name, lang=lang, endpoint=ep, seed=0, gen=r,
                genes=json.dumps(values(genome) if genome else {}),
                ok=res["ok"], p95=res["p95"], p99=res["p99"], thr=res["thr"])
            print(name, lang, ep, r, f"p95={res['p95']:.1f} thr={res['thr']:.0f}")
    f.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["setup", "calibrate", "baseline", "ga", "random"])
    ap.add_argument("--langs", nargs="+", default=LANGS)
    ap.add_argument("--endpoints", nargs="+", default=ENDPOINTS)
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1])
    ap.add_argument("--pop", type=int, default=16)
    ap.add_argument("--gens", type=int, default=10)
    args = ap.parse_args()
    os.makedirs(RES, exist_ok=True)

    if args.cmd == "setup":
        runner.ensure_network()
        runner.build_images()
        return
    if args.cmd == "calibrate":
        return calibrate(args)

    rates = load_rates()
    for lang in args.langs:
        for ep in args.endpoints:
            rate = rates[f"{lang}/{ep}"]
            if args.cmd == "baseline":
                run_baseline(lang, ep, rate, repeats=5)
            else:
                for seed in args.seeds:
                    run_search(args.cmd, lang, ep, seed, args, rate)


if __name__ == "__main__":
    main()
