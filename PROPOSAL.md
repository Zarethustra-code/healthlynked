# HealthLynked — AI-Powered Provider/Practice Directory Pipeline
### Hybrid submission: a working prototype + a production-scaling plan

> **TL;DR.** Healthcare directory data decays constantly. The expensive way to
> fix it is to LLM-and-human every record. This submission does the opposite:
> **free public data → deterministic matching → a transparent confidence score
> → an LLM only for the hard fraction → a human only for what's left.** The
> result is **~$0.17 per 1,000 records in AI spend**, a directory that
> auto-corrects what it's sure about, and a full audit trail behind every
> change. The pipeline already runs end-to-end on ~1,000 live cardiologist
> records pulled from the federal NPPES registry, with 55 passing tests.

---

## 1. What is already built (the working prototype)

This is a **hybrid (Option C)** submission. The repository is a runnable MVP,
not slideware. Every claim below points at code you can run today — pure Python
standard library, one SQLite file, **zero `pip install`**.

| Capability the brief asks for | Where it lives | Status |
|---|---|---|
| Pull provider data from a trusted source | `fetch_data.py` (NPPES API, ~1,000 cardiologists) | ✅ live |
| **Verify one record against a live source, emit the brief's exact JSON** | `live_verify.py` (live NPPES diff) | ✅ live |
| NPI validation (Luhn check digit) | `validation.py` → `is_valid_npi()` | ✅ + tests |
| Normalize names / addresses / phones / specialties | `normalize.py` (display + compare forms) | ✅ + tests |
| **One documented confidence formula, used everywhere** | `confidence.py` | ✅ + tests |
| Batch change detection + auto/review decision | `compare.py` | ✅ + tests |
| Apply auto-updates, queue reviews, write audit log | `apply_changes.py` | ✅ + tests |
| Duplicate / movement / inactive / practice-location detection | `detect.py` | ✅ |
| Per-1,000-record cost model | `cost_estimate.py` | ✅ |
| Accuracy measurement (precision / recall / F1) | `evaluate.py` + `make_dirty_data.py` | ✅ |
| Human-review dashboard | `Review dashboard.html` + `export_review.py` | ✅ |
| Batch quality gate (score /100) | `pull_quality.py` | ✅ |
| Audit trail of every action | `providers_audit_log` table (`database.py`) | ✅ |

**The centerpiece** — `live_verify.py` answers the brief's "Example Problem"
literally. Given a stored record it queries the **live** NPI Registry, diffs
every field through the shared normalizer, scores each difference, and emits the
brief's exact recommendation schema. Two real runs show the safety logic working:

**(a) Live, real federal data — the safety gate in action.** `python3
live_verify.py 1003040676` finds the registry now lists a different phone than
we hold. Because it comes from a *single* source that is not the field's owner,
the engine refuses to auto-overwrite patient-facing data and routes it to a human:

```json
{
  "provider_id": "1003040676", "npi": "1003040676",
  "change_detected": true,
  "changes": [{
    "field": "phone", "old_value": "(516) 972-1555", "new_value": "(212) 305-2913",
    "confidence_score": 0.85, "supporting_sources": ["NPI Registry"]
  }],
  "overall_confidence": 0.85,
  "recommended_action": "human_review",
  "reason": "At least one change did not clear the auto-update bar. Sent to human review.",
  "sources_consulted": ["nppes"]
}
```

This is the corroboration guarantee enforced on **real live data**: one
unconfirmed source is never enough to silently change a phone number. When the
registry agrees with us, it returns `recommended_action: "no_change"`.

**(b) Multi-source agreement → auto_update** (the brief's example 1).
`python3 live_verify.py --demo` runs the same code path with three corroborating
sources and produces the auto-update the brief illustrates (address + phone,
`overall_confidence` ≈ 0.98), plus a conflict case that drops to `human_review`
at 0.60. Today only the **NPPES adapter is wired to a live system**; CMS, state
boards, and practice sites are designed stubs behind the same `Adapter`
contract (see `live_verify.py`), and standing them up is Month-1 work (§13).

---

## 2. Architecture

```
        HealthLynked Provider / Practice Database  (SQLite: providers)
                              │
            ┌─────────────────┴───────────────────┐
            ▼                                       │
 (1) FIND OUTDATED / RISKY RECORDS                  │
     detect.py + freshness (updated_at)             │  runs continuously
            ▼                                       │  or on a schedule
 (2) SEARCH TRUSTED SOURCES                          │  (cron / queue)
     NPPES · CMS · state boards · practice sites     │
     fetch_data.py / live_verify.py adapters          │
            ▼                                       │
 (3) CLEAN & NORMALIZE                                │
     normalize.py (names, addresses, phones, spec.)   │
            ▼                                       │
 (4) MATCH PROVIDER / PRACTICE RECORDS                │
     field_compare_form() + practice-location keys    │
            ▼                                       │
 (5) SCORE CONFIDENCE  ◄── one formula ──────────────┘
     confidence.py (corroboration · authority · sensitivity)
            ▼
 (6) DECISION
     ┌───────────────┬───────────────────┬────────────────────┐
     │  NO CHANGE     │  AUTO-UPDATE       │  HUMAN REVIEW      │
     │  confirmed     │  high conf + safe  │  low conf / conflict│
     └───────┬───────┴─────────┬─────────┴──────────┬─────────┘
             ▼                 ▼                      ▼
         (audit)        apply_changes.py        Review dashboard.html
             └──────────────► AUDIT LOG ◄─────────────┘
                       providers_audit_log
```

Two entry points share the exact same scoring brain (`confidence.py`):

- **Batch** (`compare.py`) — reconcile the whole directory against a refreshed
  source pull. Used for periodic full sweeps.
- **Single-record** (`live_verify.py`) — verify one provider on demand against
  live sources. Used for "is HL_001 still accurate?" lookups and for the
  highest-risk / most-stale records surfaced by `detect.py`.

---

## 3. Data sources — reliability, independence, legality, cost

The directory is only as trustworthy as its sources. We rank them, track which
ones are *independent* of each other, and never pay for what's free.

| Source | What it's authoritative for | Reliability* | Cost | Status today |
|---|---|---|---|---|
| **NPPES / NPI Registry** | NPI, name, specialty/taxonomy, active status | 0.85 | **Free, keyless** | **✅ wired** (live API) + monthly bulk |
| **CMS** (Care Compare, PECOS-derived) | affiliations, practice linkage | 0.85 | **Free** bulk | adapter stub (Month 1) |
| **State medical boards** | license status, active/retired | 0.90 | Free / low | adapter stub (Month 1) |
| **Practice websites** | phone, address, suite, current roster | 0.75 | Bandwidth only | adapter stub (Month 1) |

*Reliability weights are **informed priors**, not yet empirically calibrated.
They are tuned against ground truth via `evaluate.py` (today: synthetic labels;
Month 1: a labeled HealthLynked sample). Only NPPES is wired to a live system
today; the other three are designed adapters behind the same `Adapter` contract.

Two properties drive correctness, both encoded in `confidence.py → SOURCES`:

- **Authority** — each field has a natural owner. A practice publishes its own
  phone first; NPPES is the registry of record for taxonomy and status. A
  change from the field's owner gets a confidence bonus.
- **Independence** — *CMS re-publishes NPPES*, so the two are **not** independent
  evidence. We group sources by `independence_group` and count only one per
  group when measuring corroboration, so a CMS+NPPES "agreement" can't masquerade
  as two independent confirmations.

**Legality / ToS.** NPPES and CMS are public-domain U.S. government data. State
boards are public records. Practice-website reads are rate-limited, identify
themselves by User-Agent, and respect `robots.txt`; we extract only directory
facts (phone, address, roster) — never bulk-scrape, never resell.

---

## 4. The confidence scoring formula (the math behind every decision)

This is the single, documented formula in `confidence.py`. It separates two
ideas the brief conflates: *how sure are we the new value is right* (confidence)
vs *how sure must we be before acting without a human* (the threshold).

**Step 1 — Collapse non-independent sources.** Keep only the most reliable
source per `independence_group`. (CMS doesn't add to NPPES.)

**Step 2 — Corroboration via noisy-OR** over the surviving independent sources:

```
corroboration = 1 − Π (1 − reliabilityₛ)
```

Two independent 0.85 sources → 0.9775, not 1.70. More agreement → more
confidence, with natural diminishing returns, always in [0, 1].

**Step 3 — Authority bonus.** If the field's owner (by source family) is among
the supporters, close part of the remaining gap to certainty:

```
confidence = corroboration + 0.15 × (1 − corroboration)
```

**Step 4 — Conflict penalty.** If independent sources report *different* new
values, multiply confidence by 0.7 and force review.

**Step 5 — Sensitivity-scaled decision threshold:**

```
required(field) = 0.80 + 0.15 × sensitivity(field)
```

so a low-stakes phone edit (sensitivity 0.2 → bar 0.83) auto-applies more
easily than a high-stakes specialty change (0.7 → bar 0.905).

**Step 6 — Hard safety rules** (override the score regardless of confidence):
provider **deactivation**, **name change**, **overwriting good data with a
blank**, **source conflict** (any two surviving distinct values, even within one
source family), and a **single uncorroborated source that is not the field's
owner** all go to a human. That last rule is the corroboration guarantee: one
unconfirmed third-party source can never silently change patient-facing data.

**Record-level:** `overall_confidence` = mean of field confidences; a record
auto-updates only if **every** field change independently clears its bar.

**This reproduces the brief's recommended_actions** (run `python3 confidence.py`):
multi-source address + phone agreement → `auto_update`; practice-site-vs-NPPES
address disagreement → `human_review` at 0.60. (The per-field confidence numbers
are this engine's own — ~0.97/0.98 — not the brief's illustrative 0.92/0.88.)

---

## 5. Safe auto-update rules

Auto-update is where a bad pipeline does real harm (silently removing a doctor
from search). Defenses, all in code:

1. **Column whitelist** — `apply_changes.py → UPDATABLE`; only contact/specialty/
   status fields can ever be written automatically.
2. **Never auto-deactivate** — `is_active 1→0` always goes to a human. Enforced
   in **two layers**: the scoring layer (`confidence.py`) refuses to emit such an
   auto-update, and the apply layer (`apply_changes.py`) independently blocks it
   even if a bad row reaches it (defense-in-depth).
3. **Never auto-rename** — name changes are identity/merge problems; blocked at
   both the scoring and apply layers.
4. **Corroboration required** — a single source that doesn't *own* a field can't
   auto-update it; it needs a second independent source first.
5. **Never blank good data** — a source that simply lacks a field can't propose
   "change to empty" over a populated value (the empty-source guard; regression-
   tested in `test_pipeline.py`).
6. **Conflicts never auto-apply** — any two disagreeing values force review.
7. **DB-level integrity** — `CHECK` constraints (10-digit NPI, 2-letter state,
   5-digit ZIP, `is_active ∈ {0,1}`) reject malformed writes even if logic slips.

---

## 6. Data quality

**NPI validation** — `validation.py` implements the CMS Luhn check-digit
algorithm exactly (10 digits, leading 1/2, prefix constant 24). Tested against
CMS's published example.

**Normalization** (`normalize.py`) — every field returns a `display` form (pretty,
stored) and a `compare` form (canonical, for matching):
- Names: flips `Last, First`, strips titles (Dr/MD/PhD), preserves `O'Connor`.
- Phones: strips to 10 digits, drops US country code, rejects non-conforming.
- Addresses: expands abbreviations (`St.`→`Street`, `Ave`→`Avenue`, directionals),
  splits suite/unit into its own field, normalizes city/state/ZIP.
- Specialties: canonical taxonomy code + normalized description.

**Address normalization is the matching key** — `field_compare_form("street", …)`
is what lets `"100 Main St"` and `"100 Main Street, Ste 4"` reconcile, and what
powers practice-location clustering.

**Detection** (`detect.py`, no network / no LLM — runs over the DB):
- **Duplicate providers** — same normalized name sharing a phone or address under
  different NPIs (double-enrollment, re-issued NPI, data-entry error).
- **Practice-location matching** — cluster providers by canonical address. On the
  live data this surfaces real practices (e.g. *43 providers @ 622 West 168th St*).
  If a practice moves, everyone at it moved — update once, not 43 times.
- **Provider movement** — street/city/state/ZIP differs from a trusted source =
  relocation (state/ZIP included so a cross-state move to a same-named street is
  caught). *Note:* the demo's 114 candidates come from the simulated second
  source; real relocation counts await the live CMS/state adapters (§13).
- **Inactive / retired** — held inactive, or reported inactive by a source.
- **Stale records** — not re-verified in N days → re-verification queue (cheap
  freshness signal, pure SQL date math).

---

## 7. Human review design — only what truly needs a human

The confidence gate exists to **shrink the review queue to the cases that
genuinely need judgment**: conflicts, low corroboration, and high-stakes changes.
Everything a human sees carries:

- the field, old → new value, and **which sources support it**,
- the **confidence score and the bar it was measured against**,
- a **plain-English `reason`** (e.g. *"reported by Practice Website | authoritative
  source for 'city' | confidence 83% vs bar 86% → human review"*).

`export_review.py` writes the queue to JSON; `Review dashboard.html` is a
zero-dependency browser UI a reviewer opens to approve/reject. Reviews are
ordered by confidence so the most-likely-correct items clear fastest.

---

## 8. Audit trail — every change is traceable

`providers_audit_log` records every `ACCEPTED / QUARANTINED / UPDATED /
DEACTIVATED / AUTO_UPDATED / FLAGGED_REVIEW` action with the NPI, a
human-readable detail (`field: 'old' → 'new' (from source)`), and a timestamp.
`proposed_changes` retains the confidence, decision, supporting sources, and
reason for **every** proposal — applied or not. Answering *"why did this change,
and what backed it?"* is a single query.

---

## 9. Cost efficiency (with a per-1,000-records estimate)

`cost_estimate.py` is a transparent, tunable model. Default run:

```
Data acquisition (NPPES/CMS, free)                       $   0.00
LLM resolution (haiku, batched, 15% of records)          $   0.17
Human review (10% of records, 4 min each)                $ 166.67
Compute / bandwidth                                      $   0.05
------------------------------------------------------------------
TOTAL per 1,000 records                                  $ 166.89   ($0.167/record)
Naive 'everything' baseline (Opus/record, no batch, review 100%): $1,683.42
```

**Read this honestly.** The ~10× gap vs the naive baseline is **almost entirely
the human-review lever** (reviewing 10% of records instead of 100%) — *not* the
AI choices. AI spend here is **$0.17, ~0.1% of total**; switching to Haiku +
batch + cache makes the AI line a rounding error, but it was never the cost.
**Human review is 99% of the cost**, so every lever that matters is about
*avoiding the human*:

1. **Free data first** — NPPES/CMS are free and keyless; no paid data APIs.
2. **Deterministic before generative** — validation, normalization, and
   corroboration resolve the majority with arithmetic, not tokens.
3. **Cheapest model, only when needed** — when an LLM *is* required (fuzzy
   match, website extraction), use **Haiku 4.5** (`$1/$5` per 1M) on the
   **Batch API (−50%)** with the shared prompt **cache-warmed (~0.1× input)**.
4. **Confidence gate shrinks review** — raising auto-update precision is the
   single biggest cost lever; at 5% review the per-1,000 cost halves to ~$83.

> The 15% LLM and 10% review rates are **informed targets, not yet measured on
> production data** — calibrating them is a Month-1/2 deliverable (§13). Because
> review is 99% of cost and linear in the review rate, treat these totals as a
> sensitivity model: the *shape* (review dominates, AI is negligible) is robust;
> the exact dollar figure moves with the measured review rate.

---

## 10. Where AI / LLMs fit — and where they deliberately don't

Being "AI-powered" does not mean "LLM-on-every-record." The LLM is a **scalpel
for ambiguity**, applied only after cheap deterministic methods are exhausted:

| Task | Method | Why |
|---|---|---|
| NPI validity, phone/ZIP format | rule / regex | exact, free, instant |
| Name/address canonicalization | `normalize.py` | deterministic, testable |
| "Same value?" comparison | `field_compare_form` | exact after normalization |
| Confidence + decision | `confidence.py` arithmetic | explainable, no tokens |
| **Fuzzy entity match** ("Dr. J. Smith" vs "John Smith MD" at a new address) | **LLM (Haiku, batched)** | judgment beyond string distance |
| **Extract fields from a practice website's free text** | **LLM (Haiku, batched)** | unstructured → structured |
| Summarize a hard review case for the human | LLM (optional) | speeds the reviewer |

This keeps cost low, keeps the core **explainable** (no black box behind a
directory edit), and keeps the LLM where it adds real value.

---

## 11. Scaling to millions of records

The MVP is SQLite + stdlib; the *design* scales without changing its shape:

- **Storage** — swap SQLite for Postgres. Schema, constraints, and indexes
  (already defined in `database.py`) port directly.
- **Source ingestion** — NPPES/CMS publish **monthly bulk files**; load those
  instead of per-record API calls for full sweeps, and reserve the live API for
  on-demand/high-risk checks. Bulk diff of millions of rows is an indexed join,
  not millions of HTTP requests.
- **Work distribution** — `detect.py` prioritizes *which* records to re-verify
  (stale, risky, high-traffic). Re-verification is embarrassingly parallel:
  shard by NPI across workers pulling from a queue.
- **Confidence scoring is O(1) per field** and stateless — it parallelizes
  trivially and never becomes the bottleneck. **Measured:** the scoring engine
  runs **~242,000 field-scores/sec on a single core** (≈ 30,000 records/sec/core
  at ~8 fields each), so 1M records score in well under a minute per core, before
  any parallelism. The bottleneck is never the math — it's source I/O and humans.
- **LLM batching** — accumulate the hard fraction and submit via the Batch API
  (24-hour SLA, −50%); nothing in the critical path waits on an LLM.
- **Cost at 1M scale (be precise):** AI spend is **~$170** per full cycle (the
  $0.17/1k figure × 1,000, on the assumed 15% LLM rate). But the *all-in* cost is
  dominated by review: at the assumed 10% review rate that is **~$167,000** in
  reviewer time per cycle. $170 is the AI line only — the real budget line is
  reviewer time, which the confidence gate exists to minimize.

---

## 12. Accuracy measurement

`evaluate.py` runs the system against `make_dirty_data.py`'s labeled good/bad
records (real errors vs cosmetic noise) and reports a **confusion matrix +
Precision / Recall / Accuracy / F1**, plus a per-error-type breakdown and a
`misclassified.csv` for inspection. This is the mechanism for tuning the
constants in `confidence.py` (thresholds, reliability weights) against ground
truth rather than by feel. **Honest status:** today's labels are *synthetic*
(generated noise), so the current reliability weights are informed priors, not
empirically derived. Calibrating them against a **labeled HealthLynked sample**
is the first concrete deliverable of the engagement (§13, Month 1) — and the
harness is exactly how we'd prove accuracy gains.

The two error types are weighted by harm: a **false auto-update** (bad data goes
live) is worse than a **false review** (a human looks at something fine), which
is exactly why sensitive fields have higher bars and hard-stop rules.

---

## 13. Implementation roadmap (the 3-month consulting plan)

**Month 1 — Productionize the core.**
- Port SQLite → Postgres; load NPPES + CMS monthly bulk files.
- Wire the second and third real adapters (CMS, one pilot state board) behind
  the existing `Adapter` contract in `live_verify.py`.
- Stand up the scheduler (nightly `detect.py` prioritization → re-verify sweep).
- Calibrate `confidence.py` constants against a labeled HealthLynked sample via
  `evaluate.py`; agree on per-field auto-update bars with HL's data team.

**Month 2 — Close the loop & add the LLM scalpel.**
- Harden the review dashboard (auth, queues, audit-on-approve) and feed reviewer
  decisions back as labels to re-tune thresholds.
- Add the Haiku batch worker for fuzzy match + website extraction, with caching;
  instrument real `pct_llm` / `pct_review` to replace the cost-model assumptions.
- Practice-entity model: cluster providers into practices (`detect.py`) and add
  practice-level moves (update a location once for all its providers).

**Month 3 — Scale, monitor, hand off.**
- Shard re-verification across workers; load-test to millions.
- Dashboards for freshness %, auto vs review rate, cost/1,000, accuracy drift.
- Runbooks + on-call alerts (source-format drift, review-queue backlog,
  refresh-failed). Knowledge transfer to HL engineering.

---

## 14. Evaluation-criteria coverage

| Criterion | How this submission addresses it |
|---|---|
| **Accuracy** | Deterministic match + corroboration + `evaluate.py` P/R/F1 harness; harm-weighted thresholds |
| **Scalability** | Bulk-file ingestion, stateless O(1) scoring, sharded re-verification, Postgres path |
| **Cost efficiency** | Free data, deterministic-first, Haiku+batch+cache, gate-minimized review; **~$0.17 AI / 1,000** |
| **Practicality** | Pure stdlib, one DB file, runs today; lean team can operate it; clear `Adapter` extension point |
| **Explainability** | Plain-English `reason` on every decision; documented formula; full `proposed_changes` retention |
| **Data Quality** | `normalize.py` (names/addr/phone/specialty) + NPI Luhn + dedup + practice clustering |
| **Source Reliability** | Reliability weights, authority-by-field, independence grouping, conflict handling |
| **Human Review Design** | Confidence gate routes only conflicts/low-confidence/high-stakes; dashboard ordered by confidence |
| **Audit Trail** | `providers_audit_log` + `proposed_changes` capture what/why/which-sources for every action |

### Bonus items
✅ Working prototype · ✅ Agent/pipeline diagram (§2) · ✅ Cost estimate per 1,000 (§9) ·
✅ Confidence formula (§4) · ✅ Review dashboard (`Review dashboard.html`) ·
✅ Duplicate detection · ✅ Address normalization · ✅ NPI validation ·
✅ Practice-location matching · ✅ Provider-movement detection ·
✅ Inactive/retired detection · ✅ Change history & audit log ·
✅ Safe auto-update rules (§5) · ✅ Implementation roadmap (§13)

---

## 15. How to run it

```bash
# Full batch pipeline end-to-end (rebuilds healthlynked.db from live NPPES)
python3 run_pipeline.py

# Verify ONE provider against the LIVE NPI Registry (the brief's example problem)
python3 live_verify.py 1003040676
python3 live_verify.py --demo            # offline: auto-update + conflict cases

# The confidence formula, reproducing the brief's two worked examples
python3 confidence.py

# Directory-health detectors (duplicates, practices, moves, inactive, stale)
python3 detect.py

# Cost model (override the levers)
python3 cost_estimate.py --pct-llm 0.08 --pct-review 0.05

# Accuracy harness (generate labeled data first) + the test suite
python3 make_dirty_data.py && python3 evaluate.py
python3 -m unittest        # 55 tests
```

*No installation. Python 3 standard library only. Internet required for the
live NPPES steps.*
