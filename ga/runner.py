"""Evaluate one container configuration: start, warm up, load test, tear down."""
import json
import os
import shutil
import statistics
import subprocess
import tempfile
import time
import urllib.request

from config import LOAD_CPUSET, MAX_VUS, SAT_VUS, SERVICE_CORES, cpuset_str, env_for, values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = "gatune"
HOST_PORT = 18080
K6_IMAGE = "grafana/k6:0.54.0"
K6_NAME = "gatune-k6"
PENALTY_P95 = 1e6  # ms, fitness of invalid configs
DROP_WEIGHT = 10  # p95_fit = p95 * (1 + DROP_WEIGHT * dropped fraction)
FAIL_MAX = 0.05  # error rate above this = invalid config

# Test timings (seconds / repetitions); change with configure() or the driver's CLI flags
T = {"warmup": 15, "duration": 20, "sat": 10, "reps": 2}


def configure(**kw):
    T.update({k: v for k, v in kw.items() if v is not None})


def eval_seconds():
    """Rough wall-clock time of one evaluation (container start + k6 runs + docker overhead)."""
    k6_runs = T["reps"] + 2
    return 8 + T["warmup"] + T["reps"] * T["duration"] + T["sat"] + 3 * k6_runs


def sh(cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def ensure_network():
    sh(["docker", "network", "create", NET], check=False)


def build_images():
    for lang in ["go", "java", "python", "node"]:
        sh(["docker", "build", "-t", f"gatune/{lang}", f"{ROOT}/services/{lang}"])


def teardown(name):
    sh(["docker", "rm", "-f", name], check=False)


def start(lang, genome):
    """genome=None -> default config: no cpu/mem limit, no tuning env (cores 0..3 still pinned)."""
    name = f"svc-{lang}"
    teardown(name)
    cmd = ["docker", "run", "-d", "--name", name, "--network", NET, "-p", f"127.0.0.1:{HOST_PORT}:8080"]
    if genome is None:
        cmd += ["--cpuset-cpus", cpuset_str(SERVICE_CORES)]
    else:
        v = values(genome)
        mem = f"{v['mem_mb']}m"
        cmd += ["--cpus", str(v["cpus"]), "--cpuset-cpus", cpuset_str(v["cpuset"]), "--memory", mem, "--memory-swap", mem]
        for k, val in env_for(lang, v).items():
            cmd += ["-e", f"{k}={val}"]
    cmd.append(f"gatune/{lang}")
    if sh(cmd, check=False).returncode != 0:
        return None
    for _ in range(60):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{HOST_PORT}/health", timeout=1)
            return name
        except Exception:
            time.sleep(0.5)
    return None


def crashed(name):
    r = sh(["docker", "inspect", "-f", "{{.State.OOMKilled}} {{.State.Running}}", name], check=False)
    oom, running = r.stdout.split()[:2] if r.stdout.split() else ("true", "false")
    return oom == "true" or running != "true"


def k6_cpu():
    """CPU% of the load generator right now (100 = one core). None if unavailable."""
    r = sh(["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}", K6_NAME], check=False)
    try:
        return float(r.stdout.strip().rstrip("%"))
    except ValueError:
        return None


def k6(name, mode, endpoint, rate, seconds, sample_cpu=False):
    """Run one k6 test. Returns the summary dict (plus k6_cpu if sampled) or None on failure."""
    out = tempfile.mkdtemp()
    os.chmod(out, 0o777)
    teardown(K6_NAME)
    cmd = [
        "docker", "run", "--rm", "--name", K6_NAME, "--network", NET, "--cpuset-cpus", LOAD_CPUSET,
        "-v", f"{out}:/out", "-v", f"{ROOT}/loadtest:/scripts:ro",
        "-e", f"TARGET=http://{name}:8080", "-e", f"ENDPOINT={endpoint}", "-e", f"MODE={mode}",
        "-e", f"RATE={rate}", "-e", f"DURATION={seconds}s", "-e", f"VUS={SAT_VUS[endpoint]}",
        "-e", f"MAXVUS={MAX_VUS}",
        K6_IMAGE, "run", "--quiet", "/scripts/k6.js",
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cpu = None
    try:
        if sample_cpu:
            time.sleep(2 + seconds / 2)  # container start + half of the test
            cpu = k6_cpu()
        p.wait(timeout=seconds + 120)
    except subprocess.TimeoutExpired:
        p.kill()
        teardown(K6_NAME)
    try:
        with open(f"{out}/summary.json") as f:
            res = json.load(f)
        res["k6_cpu"] = cpu
        return res
    except Exception:
        return None
    finally:
        shutil.rmtree(out, ignore_errors=True)


def invalid(**measured):
    nan = float("nan")
    res = {"ok": False, "p95": nan, "p99": nan, "drop": nan, "fail": nan, "thr": 0.0, "k6_cpu": nan}
    res.update(measured)
    res["p95_fit"] = PENALTY_P95
    return res


def evaluate(lang, endpoint, genome, rate):
    """Returns dict(ok, p95, p99, drop, fail, p95_fit, thr, k6_cpu).
    p95/p99/drop/fail = raw medians of T['reps'] runs at a fixed arrival rate;
    p95_fit = p95 with the soft penalty for dropped arrivals (used only as fitness);
    thr = successful req/s in a short saturation run."""
    name = start(lang, genome)
    if name is None:
        teardown(f"svc-{lang}")
        return invalid()
    try:
        k6(name, "rate", endpoint, rate, T["warmup"])  # discarded
        runs = [k6(name, "rate", endpoint, rate, T["duration"], sample_cpu=True) for _ in range(T["reps"])]
        if any(r is None for r in runs) or crashed(name):
            return invalid()
        med = {k: statistics.median(r[k] for r in runs) for k in ["p95", "p99", "fail", "dropped_frac"]}
        cpus = [r["k6_cpu"] for r in runs if r["k6_cpu"] is not None]
        measured = {"p95": med["p95"], "p99": med["p99"], "drop": med["dropped_frac"], "fail": med["fail"],
                    "k6_cpu": max(cpus) if cpus else float("nan")}
        if med["fail"] > FAIL_MAX:
            return invalid(**measured)
        sat = k6(name, "sat", endpoint, 0, T["sat"])
        if sat is None or crashed(name):
            return invalid(**measured)
        # Soft penalty: dropped iterations (arrivals the service could not absorb) inflate p95,
        # so under-provisioned configs get a graded, not flat, fitness. Raw p95 is kept separately.
        return {"ok": True, **measured, "p95_fit": med["p95"] * (1 + DROP_WEIGHT * med["dropped_frac"]),
                "thr": sat["rps_ok"]}
    finally:
        teardown(name)
