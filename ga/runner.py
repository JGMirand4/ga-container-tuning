"""Evaluate one container configuration: start, warm up, load test, tear down."""
import json
import os
import statistics
import subprocess
import tempfile
import time
import urllib.request

from config import LOAD_CPUSET, SERVICE_CORES, cpuset_str, env_for, values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NET = "gatune"
HOST_PORT = 18080
K6_IMAGE = "grafana/k6:0.54.0"
PENALTY_P95 = 1e6  # ms

# Test timings (seconds)
WARMUP, DURATION, SAT, REPS = 20, 30, 10, 3


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
    sh(cmd)
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


def k6(name, mode, endpoint, rate, seconds, vus=64):
    out = tempfile.mkdtemp()
    os.chmod(out, 0o777)
    cmd = [
        "docker", "run", "--rm", "--network", NET, "--cpuset-cpus", LOAD_CPUSET,
        "-v", f"{out}:/out", "-v", f"{ROOT}/loadtest:/scripts:ro",
        "-e", f"TARGET=http://{name}:8080", "-e", f"ENDPOINT={endpoint}", "-e", f"MODE={mode}",
        "-e", f"RATE={rate}", "-e", f"DURATION={seconds}s", "-e", f"VUS={vus}",
        K6_IMAGE, "run", "--quiet", "/scripts/k6.js",
    ]
    sh(cmd, check=False)
    try:
        with open(f"{out}/summary.json") as f:
            return json.load(f)
    except Exception:
        return None


def invalid():
    return {"ok": False, "p95": PENALTY_P95, "p99": PENALTY_P95, "thr": 0.0}


def evaluate(lang, endpoint, genome, rate):
    """Returns dict(ok, p95, p99, thr). p95/p99 = median of REPS runs at fixed arrival rate;
    thr = max throughput from a short saturation run."""
    name = start(lang, genome)
    if name is None:
        teardown(f"svc-{lang}")
        return invalid()
    try:
        k6(name, "rate", endpoint, rate, WARMUP)  # discarded
        runs = [k6(name, "rate", endpoint, rate, DURATION) for _ in range(REPS)]
        if any(r is None for r in runs) or crashed(name):
            return invalid()
        if statistics.median(r["fail"] for r in runs) > 0.05:
            return invalid()
        sat = k6(name, "sat", endpoint, rate, SAT)
        if sat is None or crashed(name):
            return invalid()
        # Soft penalty: dropped iterations (arrivals the service could not absorb) inflate p95,
        # so under-provisioned configs get a graded, not flat, fitness.
        drop = statistics.median(r["dropped_frac"] for r in runs)
        return {
            "ok": True,
            "p95": statistics.median(r["p95"] for r in runs) * (1 + 10 * drop),
            "p99": statistics.median(r["p99"] for r in runs) * (1 + 10 * drop),
            "thr": sat["rps"],
        }
    finally:
        teardown(name)
