"""Minimal NSGA-II over index-encoded genomes. Objectives are all minimised."""
import random

from config import CHOICES, gene_names, random_genome, repair


def dominates(a, b):
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def fast_nds(objs):
    n = len(objs)
    S = [[] for _ in range(n)]
    cnt = [0] * n
    fronts = [[]]
    for p in range(n):
        for q in range(n):
            if dominates(objs[p], objs[q]):
                S[p].append(q)
            elif dominates(objs[q], objs[p]):
                cnt[p] += 1
        if cnt[p] == 0:
            fronts[0].append(p)
    i = 0
    while fronts[i]:
        nxt = []
        for p in fronts[i]:
            for q in S[p]:
                cnt[q] -= 1
                if cnt[q] == 0:
                    nxt.append(q)
        i += 1
        fronts.append(nxt)
    return fronts[:-1]


def crowding(objs, front):
    d = {i: 0.0 for i in front}
    for m in range(len(objs[0])):
        srt = sorted(front, key=lambda i: objs[i][m])
        d[srt[0]] = d[srt[-1]] = float("inf")
        span = objs[srt[-1]][m] - objs[srt[0]][m] or 1.0
        for a, b, c in zip(srt, srt[1:], srt[2:]):
            d[b] += (objs[c][m] - objs[a][m]) / span
    return d


def _rank_crowd(objs):
    rank, crowd = {}, {}
    for r, f in enumerate(fast_nds(objs)):
        crowd.update(crowding(objs, f))
        for i in f:
            rank[i] = r
    return rank, crowd


def _tournament(rng, rank, crowd, n):
    a, b = rng.randrange(n), rng.randrange(n)
    return a if (rank[a], -crowd[a]) < (rank[b], -crowd[b]) else b


def _offspring(rng, lang, pop, rank, crowd, pc=0.9):
    names = gene_names(lang)
    p1 = pop[_tournament(rng, rank, crowd, len(pop))]
    p2 = pop[_tournament(rng, rank, crowd, len(pop))]
    child = {g: (p2[g] if rng.random() < 0.5 and rng.random() < pc else p1[g]) for g in names}
    for g in names:
        if rng.random() < 1 / len(names):
            if rng.random() < 0.5:
                child[g] = min(max(child[g] + rng.choice([-1, 1]), 0), len(CHOICES[g]) - 1)
            else:
                child[g] = rng.randrange(len(CHOICES[g]))
    return repair(lang, child)


def run_nsga2(lang, evaluate, pop_size, gens, seed):
    """evaluate(genome, gen) -> objective tuple. Total evaluations = pop_size * gens."""
    rng = random.Random(seed)
    pop = [random_genome(rng, lang) for _ in range(pop_size)]
    objs = [evaluate(g, 0) for g in pop]
    for gen in range(1, gens):
        rank, crowd = _rank_crowd(objs)
        kids = [_offspring(rng, lang, pop, rank, crowd) for _ in range(pop_size)]
        kobjs = [evaluate(k, gen) for k in kids]
        allp, allo = pop + kids, objs + kobjs
        new_idx = []
        for f in fast_nds(allo):
            if len(new_idx) + len(f) <= pop_size:
                new_idx += f
            else:
                cd = crowding(allo, f)
                new_idx += sorted(f, key=lambda i: -cd[i])[: pop_size - len(new_idx)]
                break
        pop = [allp[i] for i in new_idx]
        objs = [allo[i] for i in new_idx]
    return pop, objs
