"""Search space, host layout, experiment constants and per-language translation of genes."""
import random

LANGS = ["go", "java", "python", "node"]
ENDPOINTS = ["cpu", "io"]

# Host layout (8 cores assumed): service pinned to 0..3, load generator to 4..7
SERVICE_CORES = 4
LOAD_CPUSET = "4-7"

# Load model
RATE_FRAC = 0.5  # fixed arrival rate = RATE_FRAC x saturation throughput of the default config
# Closed-model VUs for the saturation test. /io sleeps 20 ms per request, so 64 VUs would cap the
# measurable throughput at ~64/0.02 = 3200 req/s (the load generator, not the service, would be the limit).
SAT_VUS = {"cpu": 64, "io": 1024}
MAX_VUS = 1000  # open model: max concurrent VUs before k6 starts dropping arrivals

# SLO used for the "minimum cost that still meets the SLO" analysis and re-evaluation picks:
# p95 <= SLO_FACTOR x median p95 of the default config (per language/endpoint), with no dropped arrivals.
SLO_FACTOR = 2.0

# Quality-check thresholds (reported by analysis/analyze.py)
CV_WARN = 0.10  # coefficient of variation of repeated baseline measurements
RATE_WARN = 0.8  # rate / measured default throughput above this = measuring an overloaded service
K6_CPU_WARN = 300.0  # docker stats CPU% of k6 (4 cores = 400%)

CHOICES = {
    "cpus": [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0],
    "mem_mb": [128, 192, 256, 384, 512, 768, 1024, 1536, 2048],  # ~log scale
    "cpuset": [1, 2, 3, 4],
    "workers": [1, 2, 3, 4, 6, 8, 12, 16],
    "heap_pct": [25, 40, 50, 60, 70, 80],  # Java only
}
MIN_MEM = {"java": 256}

# "Reasonable hand-made" baseline
MANUAL = {"cpus": 4.0, "mem_mb": 1024, "cpuset": 4, "workers": 4, "heap_pct": 70}


def cost(v):
    """Resource cost in cloud-like units: vCPUs + GB of memory. v = gene values (None = default config)."""
    if not v:
        return DEFAULT_COST
    return v["cpus"] + v["mem_mb"] / 1024


# The default config has no limits; it is charged as the whole service slice:
# all SERVICE_CORES cores + the largest memory in the search space.
DEFAULT_COST = SERVICE_CORES + max(CHOICES["mem_mb"]) / 1024


def gene_names(lang):
    names = ["cpus", "mem_mb", "cpuset", "workers"]
    if lang == "java":
        names.append("heap_pct")
    return names


def random_genome(rng: random.Random, lang):
    """Genome = dict gene -> index into CHOICES[gene]."""
    return repair(lang, {g: rng.randrange(len(CHOICES[g])) for g in gene_names(lang)})


def values(genome):
    return {g: CHOICES[g][i] for g, i in genome.items()}


def genome_from_values(v):
    return {g: CHOICES[g].index(x) for g, x in v.items()}


def manual_genome(lang):
    return {g: CHOICES[g].index(MANUAL[g]) for g in gene_names(lang)}


def repair(lang, genome):
    g = dict(genome)
    v = values(g)
    # cpus cannot exceed the number of pinned cores
    while v["cpus"] > v["cpuset"]:
        g["cpus"] -= 1
        v = values(g)
    # memory floor per language
    while v["mem_mb"] < MIN_MEM.get(lang, 0):
        g["mem_mb"] += 1
        v = values(g)
    return g


def key(genome):
    return tuple(sorted(genome.items()))


def cpuset_str(n):
    return f"0-{n - 1}"


def env_for(lang, v):
    """Translate gene values to container environment variables."""
    w = str(v["workers"])
    if lang == "go":
        return {"GOMAXPROCS": w}
    if lang == "java":
        return {"WORKERS": w, "JAVA_OPTS": f"-XX:MaxRAMPercentage={v['heap_pct']}"}
    return {"WORKERS": w}  # python (gunicorn workers), node (cluster workers)
