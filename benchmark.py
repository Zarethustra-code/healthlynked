"""
benchmark.py
------------
Reproducible throughput for the confidence scoring engine (the claim in
PROPOSAL.md §11). Pure stdlib; single core.

    python3 benchmark.py            # default 200k field-scores
    python3 benchmark.py 500000     # custom count
"""

import sys
import time

import confidence

_CANDS = [
    {"source": "practice_site", "value": "2125559000", "display": "(239) 555-9000"},
    {"source": "nppes", "value": "2125559000", "display": "(239) 555-9000"},
]


def run(n=200_000):
    # warm up (let the interpreter settle) before timing
    for _ in range(2000):
        confidence.score_field("phone", "(212) 555-1111", _CANDS, old_compare="2125551111")

    t0 = time.perf_counter()
    for _ in range(n):
        confidence.score_field("phone", "(212) 555-1111", _CANDS, old_compare="2125551111")
    dt = time.perf_counter() - t0

    per_sec = n / dt
    print(f"{n:,} field-scores in {dt:.2f}s")
    print(f"  -> {per_sec:,.0f} field-scores / sec / core")
    print(f"  -> ~{per_sec / 8:,.0f} records / sec / core  (at ~8 compared fields/record)")
    return per_sec


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    run(n)
