#!/usr/bin/env python3
"""RCT rule statistics (stdlib only).

Usage: analyze.py scores.jsonl > stats.json

Per prompt: Fisher exact test for binary endpoints, permutation test for continuous ones.
Pooled: mean of per-prompt deltas (intervention - control), with arm labels permuted within prompts.
"""
import json
import math
import random
import sys
from collections import defaultdict
from statistics import mean, median

BINARY = ["compliance", "discontinued", "overreach", "applies"]
CONTINUOUS = ["task", "lines", "files"]


def value(row, metric):
    if metric == "discontinued":
        return 1 if row.get("status") in ("discontinued", "budget") else 0
    v = row.get(metric)
    if v is None:
        return None
    return int(v) if isinstance(v, bool) else v


def fisher(a, b, c, d):
    """Two-sided Fisher exact test for the table [[a, b], [c, d]]."""
    r1, r2, c1 = a + b, c + d, a + c
    n = r1 + r2
    if n == 0:
        return 1.0

    def pmf(x):
        return math.comb(r1, x) * math.comb(r2, c1 - x) / math.comb(n, c1)

    p_obs = pmf(a)
    xs = range(max(0, c1 - r2), min(r1, c1) + 1)
    return min(1.0, sum(pmf(x) for x in xs if pmf(x) <= p_obs * (1 + 1e-9)))


def perm_test(strata, iters=10000, seed=0):
    """Statistic: mean over strata of (mean_I - mean_C). Labels shuffled within each stratum."""
    strata = [(c, i) for c, i in strata if c and i]
    if not strata:
        return None, None

    def stat(ss):
        return mean(mean(i) - mean(c) for c, i in ss)

    obs = stat(strata)
    rng = random.Random(seed)
    hits = 0
    for _ in range(iters):
        perm = []
        for c, i in strata:
            pool = list(c) + list(i)
            rng.shuffle(pool)
            perm.append((pool[:len(c)], pool[len(c):]))
        if abs(stat(perm)) >= abs(obs) - 1e-12:
            hits += 1
    return obs, (hits + 1) / (iters + 1)


def main():
    rows = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
    groups = defaultdict(lambda: {"control": [], "intervention": []})
    excluded = defaultdict(int)
    for r in rows:
        if r.get("status") == "error":
            excluded[r["prompt_id"]] += 1
            continue
        groups[r["prompt_id"]][r["arm"]].append(r)

    out = {"per_prompt": {}, "pooled": {}}
    strata = defaultdict(list)

    for pid, g in sorted(groups.items()):
        res = {"n": {arm: len(g[arm]) for arm in g}, "excluded": excluded[pid]}
        for m in BINARY + CONTINUOUS:
            c = [v for v in (value(r, m) for r in g["control"]) if v is not None]
            i = [v for v in (value(r, m) for r in g["intervention"]) if v is not None]
            strata[m].append((c, i))
            entry = {
                "control": mean(c) if c else None,
                "intervention": mean(i) if i else None,
                "n_c": len(c),
                "n_i": len(i),
            }
            if c and i:
                entry["delta"] = entry["intervention"] - entry["control"]
                if m in BINARY:
                    entry["p"] = fisher(sum(i), len(i) - sum(i), sum(c), len(c) - sum(c))
                else:
                    _, entry["p"] = perm_test([(c, i)])
                    entry["median_control"] = median(c)
                    entry["median_intervention"] = median(i)
            res[m] = entry
        out["per_prompt"][pid] = res

    for m, ss in strata.items():
        delta, p = perm_test(ss)
        deltas = [mean(i) - mean(c) for c, i in ss if c and i]
        out["pooled"][m] = {
            "delta": delta,
            "p": p,
            "prompts": len(deltas),
            "positive": sum(d > 0 for d in deltas),
            "negative": sum(d < 0 for d in deltas),
        }

    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    # TODO this entire test is bs, there is not enough trustworthy data to perform any of this.
    main()
