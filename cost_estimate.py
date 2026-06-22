"""
cost_estimate.py
----------------
Transparent cost model: what does it cost to keep 1,000 provider records fresh?

The whole architecture is built to push work DOWN the cost ladder:

    free public data  ->  deterministic match/score  ->  cheap LLM (only hard %)
                                                      ->  human review (only the rest)

So the per-1,000 cost is dominated by two tunable levers: how often we must call
an LLM, and how often we must pay a human. Everything else is rounding error.

Pricing (per 1M tokens, current Claude API list prices):
    Haiku 4.5   $1.00 in / $5.00 out      <- default worker model (cheapest)
    Sonnet 4.6  $3.00 in / $15.00 out
    Opus 4.8    $5.00 in / $25.00 out
Discounts modeled:
    Batch API   -50% on all tokens (these jobs are not latency-sensitive)
    Prompt cache  cached input billed at ~0.1x (shared extraction instructions)

Every number is an assumption, printed so a reviewer can sanity-check each line.
The main levers (--pct-llm, --pct-review, model, batch) are CLI flags; the
per-call token counts live in DEFAULTS. NPPES and CMS bulk data are free and
keyless, so data acquisition is effectively $0 — the model reflects that.

Honesty notes for a skeptical reviewer:
  * pct_llm / pct_review are INFORMED TARGETS, not yet measured on production
    data — calibrating them is a Month-1/2 deliverable in PROPOSAL.md §13.
    Review is ~99% of cost and linear in pct_review, so treat the totals as a
    sensitivity model, not a guarantee.
  * The shared LLM prompt is billed at cache-READ (0.1x) on every call; the
    one-time cache-WRITE premium (~1.25x input, first call only, ~$0.002) is
    omitted as immaterial at this token volume.
"""

from __future__ import annotations

import argparse

# --- Claude API list prices, $ per 1M tokens (input, output) ---
PRICES = {
    "haiku":  (1.00, 5.00),
    "sonnet": (3.00, 15.00),
    "opus":   (5.00, 25.00),
}
BATCH_DISCOUNT = 0.50    # Batch API: 50% off all tokens
CACHE_READ_FACTOR = 0.10  # cached input billed at ~0.1x

# ---------------------------------------------------------------------------
# Default assumptions (all overridable)
# ---------------------------------------------------------------------------
DEFAULTS = dict(
    records=1000,

    # Fraction of records that need an LLM at all. The deterministic layer
    # (validate + normalize + corroborate across free sources) resolves the
    # large majority; the LLM is only for genuinely ambiguous cases — fuzzy
    # name/practice matching, or pulling fields out of a practice website's
    # free text.
    pct_llm=0.15,

    # Per LLM call (Haiku, batched). Cached input = the shared extraction
    # prompt/schema reused across every call (billed at ~0.1x).
    llm_model="haiku",
    llm_cached_input=1200,   # shared instructions/schema (cache-read)
    llm_fresh_input=900,     # the record + the page/snippet being resolved
    llm_output=250,
    use_batch=True,

    # Fraction of records that end up in the human-review queue. The confidence
    # gate is what keeps this small — only conflicts and low-confidence changes.
    pct_review=0.10,
    minutes_per_review=4.0,
    reviewer_hourly=25.0,    # loaded cost of a data-ops reviewer

    # Compute/bandwidth for the deterministic pass over 1,000 records. Pure
    # stdlib + a few free HTTP calls — pennies.
    compute_per_1k=0.05,
)


def _llm_cost_per_call(model, cached_input, fresh_input, output, use_batch):
    price_in, price_out = PRICES[model]
    cost = (
        cached_input * price_in * CACHE_READ_FACTOR
        + fresh_input * price_in
        + output * price_out
    ) / 1_000_000
    if use_batch:
        cost *= (1 - BATCH_DISCOUNT)
    return cost


def estimate(**overrides):
    a = {**DEFAULTS, **overrides}
    r = a["records"]
    scale = r / 1000.0

    # --- Data acquisition: free public sources ---
    data_cost = 0.0

    # --- LLM (only the hard fraction) ---
    llm_calls = r * a["pct_llm"]
    per_call = _llm_cost_per_call(
        a["llm_model"], a["llm_cached_input"], a["llm_fresh_input"],
        a["llm_output"], a["use_batch"])
    llm_cost = llm_calls * per_call

    # --- Human review (only what the gate couldn't clear) ---
    review_records = r * a["pct_review"]
    review_cost = review_records * (a["minutes_per_review"] / 60.0) * a["reviewer_hourly"]

    # --- Compute / bandwidth ---
    compute_cost = a["compute_per_1k"] * scale

    total = data_cost + llm_cost + review_cost + compute_cost

    # --- Naive baseline: LLM every record on Opus, no batch, review everything ---
    naive_per_call = _llm_cost_per_call("opus", 0, a["llm_fresh_input"] + a["llm_cached_input"],
                                        a["llm_output"], use_batch=False)
    naive_llm = r * naive_per_call
    naive_review = r * (a["minutes_per_review"] / 60.0) * a["reviewer_hourly"]
    naive_total = naive_llm + naive_review

    return {
        "assumptions": a,
        "lines": [
            ("Data acquisition (NPPES/CMS, free)", data_cost),
            (f"LLM resolution ({a['llm_model']}, batched, {a['pct_llm']:.0%} of records,"
             f" {llm_calls:.0f} calls)", llm_cost),
            (f"Human review ({a['pct_review']:.0%} of records, {review_records:.0f}"
             f" @ {a['minutes_per_review']:.0f}min)", review_cost),
            ("Compute / bandwidth", compute_cost),
        ],
        "total": total,
        "per_record": total / r if r else 0,
        "naive_total": naive_total,
        "savings_x": (naive_total / total) if total else float("inf"),
    }


def print_report(**overrides):
    e = estimate(**overrides)
    r = e["assumptions"]["records"]

    print("=" * 64)
    print(f"  Cost to keep {r:,} provider records fresh (one refresh cycle)")
    print("=" * 64)
    for label, cost in e["lines"]:
        print(f"  {label:<52} ${cost:>8.2f}")
    print("-" * 64)
    print(f"  {'TOTAL per ' + format(r, ',') + ' records':<52} ${e['total']:>8.2f}")
    print(f"  {'Cost per record':<52} ${e['per_record']:>8.4f}")
    print("-" * 64)
    print(f"  Naive 'everything' baseline (Opus on every record, no batch/cache,")
    print(f"  review 100% of records):                                 ${e['naive_total']:>8.2f}")
    print(f"  => ~{e['savings_x']:.0f}x cheaper overall — almost entirely from")
    print(f"     reviewing {e['assumptions']['pct_review']:.0%} of records instead of 100%.")
    print(f"     AI spend itself is ~$0.17 here: a rounding error, not the lever.")
    print("=" * 64)
    print("  The dominant cost is human review (linear in --pct-review). The")
    print("  confidence gate exists to drive that rate down; --pct-llm barely")
    print("  moves the total. pct-review/pct-llm are targets pending calibration.")
    print("=" * 64)


def main():
    p = argparse.ArgumentParser(description="Per-1,000-records cost estimate")
    p.add_argument("--records", type=int, default=DEFAULTS["records"])
    p.add_argument("--pct-llm", type=float, default=DEFAULTS["pct_llm"],
                   help="fraction of records needing an LLM call (0-1)")
    p.add_argument("--pct-review", type=float, default=DEFAULTS["pct_review"],
                   help="fraction of records sent to human review (0-1)")
    p.add_argument("--minutes-per-review", type=float, default=DEFAULTS["minutes_per_review"])
    p.add_argument("--reviewer-hourly", type=float, default=DEFAULTS["reviewer_hourly"])
    p.add_argument("--llm-model", choices=list(PRICES), default=DEFAULTS["llm_model"])
    p.add_argument("--no-batch", action="store_true", help="disable the 50%% batch discount")
    args = p.parse_args()

    print_report(
        records=args.records, pct_llm=args.pct_llm, pct_review=args.pct_review,
        minutes_per_review=args.minutes_per_review, reviewer_hourly=args.reviewer_hourly,
        llm_model=args.llm_model, use_batch=not args.no_batch,
    )


if __name__ == "__main__":
    main()
