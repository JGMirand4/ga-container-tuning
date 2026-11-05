# GA container tuning

How much does a genetic algorithm (NSGA-II) improve one service across languages (Go, Java, Python, Node)
by tuning container resources, vs. default / hand-made / random search with the same evaluation budget?

## Layout
- `services/*`  identical API: `/cpu` (recursive fib(25)), `/io` (20 ms simulated wait), `/health`
- `loadtest/k6.js`  `MODE=rate` (open model, p95/p99) or `MODE=sat` (closed model, max throughput)
- `ga/`  `config.py` (genes, bounds), `runner.py` (docker + k6), `nsga2.py`, `run_experiment.py`
- `analysis/analyze.py`  Kruskal-Wallis (H1), hypervolume + Mann-Whitney + Cliff's delta (H2), GA gain per lang (H3), convergence plot

## Run (Linux host, 8 cores, Docker; load generator pinned to cores 4-7, services to 0-3)
```
cd ga
python run_experiment.py setup
python run_experiment.py calibrate        # arrival rate = 30% of default-config saturation
python run_experiment.py baseline
python run_experiment.py ga     --seeds 0 1
python run_experiment.py random --seeds 0 1
python ../analysis/analyze.py             # needs pandas scipy numpy matplotlib
```
Smoke test: `python run_experiment.py ga --langs go --endpoints cpu --seeds 0 --pop 4 --gens 2`

## Design notes / deviations from the plan
- Genes: `cpus`, `mem_mb`, `cpuset` (size, contiguous from core 0), `workers` (GOMAXPROCS / pool size / gunicorn workers / Node cluster), `heap_pct` (Java only). Repair: `cpus <= cpuset`, Java mem >= 256 MB.
- Fitness (min): `(p95, -throughput)`; p95 = median of 3x30 s at fixed arrival rate after 20 s warm-up; throughput from a 10 s saturation run per evaluation (cheaper than only for Pareto members).
- OOM / crash / >5% errors / >20% dropped iterations -> penalised (`ok=False`, excluded from stats).
- Identical genomes within a run are measured once (cache) but still count toward the 160-evaluation budget.
- "Default" = no cpu/mem limit, no tuning env, but pinned to cores 0-3 so the load generator never competes.
- I/O endpoint uses a simulated 20 ms wait (no database).
- Pin base images by digest and set CPU governor to `performance` before the real run.
- Docker Desktop on macOS runs in a VM: fine for a smoke test, not for the real measurements.
