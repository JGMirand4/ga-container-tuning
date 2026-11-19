"""Run the whole experiment unattended, writing progress (% done, current step, ETA) to a log.

  python pipeline.py --results ../results/reduzido --langs go python --endpoints cpu \
      --seeds 0 1 2 --pop 6 --gens 4 --repeats 4 --reeval-repeats 3 \
      --warmup 5 --duration 8 --reps 1 --sat 5
  tail -f ../results/reduzido/progresso.log

Steps: build images -> calibrate -> baseline -> ga -> random -> reeval -> analyze.
Progress counts CSV rows written (one per evaluation; cached genomes count too) against the planned total.
"""
import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time

import run_experiment as rx
import runner
from config import ENDPOINTS, LANGS, RATE_FRAC, SLO_FACTOR


class Progress:
    def __init__(self, path, total):
        self.path, self.total, self.done, self.t0, self.step = path, total, 0, time.time(), "início"
        self.resumed = 0  # evaluations already done in an earlier run (excluded from the ETA rate)

    def skip(self, n, what):
        self.done += n
        self.resumed += n
        self.write(f"[{self.done:4d}/{self.total}] {100 * self.done / self.total:5.1f}%  já concluído: {what}")

    def write(self, msg):
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        print(line, flush=True)
        with open(self.path, "a") as f:
            f.write(line + "\n")

    def stage(self, name):
        self.step = name
        self.write(f"== etapa: {name}")

    def tick(self, detail):
        self.done += 1
        pct = 100 * self.done / self.total
        elapsed = time.time() - self.t0
        eta = elapsed / max(self.done - self.resumed, 1) * (self.total - self.done)
        finish = dt.datetime.now() + dt.timedelta(seconds=eta)
        self.write(f"[{self.done:4d}/{self.total}] {pct:5.1f}%  {self.step:9s} {detail}  "
                   f"decorrido {elapsed / 60:5.1f} min, faltam ~{eta / 60:5.1f} min (término ~{finish:%H:%M})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--langs", nargs="+", default=LANGS)
    ap.add_argument("--endpoints", nargs="+", default=ENDPOINTS)
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2, 3, 4])
    ap.add_argument("--pop", type=int, default=12)
    ap.add_argument("--gens", type=int, default=8)
    ap.add_argument("--repeats", type=int, default=10, help="baseline repetitions")
    ap.add_argument("--reeval-repeats", type=int, default=5)
    ap.add_argument("--rate-frac", type=float, default=RATE_FRAC)
    ap.add_argument("--slo-factor", type=float, default=SLO_FACTOR)
    for t in ["warmup", "duration", "reps", "sat"]:
        ap.add_argument(f"--{t}", type=int)
    ap.add_argument("--results", default=os.path.join(runner.ROOT, "results"))
    args = ap.parse_args()
    args.results = os.path.abspath(args.results)
    os.makedirs(args.results, exist_ok=True)
    runner.configure(warmup=args.warmup, duration=args.duration, reps=args.reps, sat=args.sat)

    n = len(args.langs) * len(args.endpoints)
    searches = n * 2 * len(args.seeds)
    total = n * 3 * args.repeats + searches * args.pop * args.gens + searches * (len(rx.PICKS) + 1) * args.reeval_repeats
    prog = Progress(os.path.join(args.results, "progresso.log"), total)
    prog.write(f"início: {n} pares (linguagem, endpoint), {searches} buscas de {args.pop * args.gens} avaliações, "
               f"{total} avaliações previstas, ~{runner.eval_seconds():.0f} s cada "
               f"(estimativa {total * runner.eval_seconds() / 3600:.1f} h, menos o que o cache economizar)")

    # Every CSV row written = one evaluation done
    open_csv = rx.open_csv

    def image_ok(lang):
        return runner.sh(["docker", "image", "inspect", f"gatune/{lang}"], check=False).returncode == 0

    def counted_open_csv(path, needed):
        opened = open_csv(path, needed)
        if not opened:
            prog.skip(needed, os.path.basename(path))
            return None
        log, f = opened

        def log_and_tick(**row):
            if str(row["ok"]) != "True" and not image_ok(row["lang"]):
                # The image vanished (e.g. docker image prune): stop before burning the budget on invalid rows
                f.close()
                os.remove(path)
                prog.write(f"ERRO: a imagem gatune/{row['lang']} não existe mais. {os.path.basename(path)} foi "
                           f"descartado. Rode o pipeline de novo: ele reconstrói a imagem e retoma daqui.")
                sys.exit(2)
            log(**row)
            p95 = row["p95"]
            prog.tick(f"{row['method']} {row['lang']}/{row['endpoint']} s{row['seed']} ok={row['ok']} "
                      f"p95={p95:.1f}ms vazão={row['thr']:.0f}" if p95 == p95 else
                      f"{row['method']} {row['lang']}/{row['endpoint']} s{row['seed']} inválida")

        return log_and_tick, f

    rx.open_csv = counted_open_csv
    # reeval picks may be fewer than 2 (no config met the SLO): count the missing ones as done
    picks_of = rx.picks_of

    def counted_picks(path, slo):
        p = picks_of(path, slo)
        for _ in range(len(rx.PICKS) - len(p)):
            for _ in range(args.reeval_repeats):
                prog.tick("reeval pulada (nenhuma config. atendeu o SLO)")
        return p

    rx.picks_of = counted_picks

    prog.stage("imagens")
    runner.ensure_network()
    for lang in args.langs:
        r = runner.sh(["docker", "build", "-t", f"gatune/{lang}", f"{runner.ROOT}/services/{lang}"], check=False)
        prog.write(f"imagem {lang}: {'ok' if r.returncode == 0 else 'FALHOU'}")
        if r.returncode != 0:
            prog.write(r.stderr[-2000:])
            sys.exit(1)
    runner.sh(["docker", "pull", runner.K6_IMAGE], check=False)

    base = ["--results", args.results, "--langs", *args.langs, "--endpoints", *args.endpoints,
            "--seeds", *map(str, args.seeds), "--pop", str(args.pop), "--gens", str(args.gens),
            "--rate-frac", str(args.rate_frac), "--slo-factor", str(args.slo_factor)]
    for t in ["warmup", "duration", "reps", "sat"]:
        if getattr(args, t) is not None:
            base += [f"--{t}", str(getattr(args, t))]

    def step(cmd, repeats):
        prog.stage(cmd)
        sys.argv = ["run_experiment.py", cmd, *base, "--repeats", str(repeats)]
        rx.main()

    rates_file = os.path.join(args.results, "rates.json")
    have = json.load(open(rates_file)) if os.path.exists(rates_file) else {}
    if all(f"{lang}/{ep}" in have for lang in args.langs for ep in args.endpoints):
        prog.write("calibração já feita (rates.json): mantida, para não mudar a carga no meio do experimento")
    else:
        step("calibrate", args.repeats)
    step("baseline", args.repeats)
    step("ga", args.repeats)
    step("random", args.repeats)
    step("reeval", args.reeval_repeats)

    prog.stage("análise")
    out = subprocess.run([sys.executable, os.path.join(runner.ROOT, "analysis", "analyze.py"), args.results],
                         capture_output=True, text=True)
    with open(os.path.join(args.results, "analise.txt"), "w") as f:
        f.write(out.stdout + out.stderr)
    prog.write(f"análise gravada em {os.path.join(args.results, 'analise.txt')}")
    prog.write(f"FIM: 100% em {(time.time() - prog.t0) / 3600:.2f} h")


if __name__ == "__main__":
    main()
