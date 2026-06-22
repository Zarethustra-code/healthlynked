# HealthLynked — AI-Powered Provider/Practice Directory Pipeline
### Hybrid submission: a working prototype + a production-scaling plan

> **TL;DR.** Healthcare directory data decays constantly. The expensive way to
> fix it is to LLM-and-human every record. This submission does the opposite:
> **free public data → deterministic matching → a transparent confidence score
> → an LLM only for the hard fraction → a human only for what's left.** The
> result is **~$0.17 per 1,000 records in AI spend**, a directory that
> auto-corrects what it's sure about, and a full audit trail behind every
> change. The pipeline already runs end-to-end on ~1,000 live cardiologist
> records pulled from the federal NPPES registry, with 65 passing tests.

---

## 1. What is already built (the working prototype)

This is a **hybrid (Option C)** submission. The repository is a runnable MVP,
not slideware. Every claim below points at code you can run today — pure Python
standard library, one SQLite file, **zero `pip install`**.

| Capability the brief asks for | Where it lives | Status |
|---|---|---|
| Pull provider data from a trusted source | `fetch_data.py` (NPPES API, ~1,000 cardiologists) | ✅ live |
| **Verify one record against TWO live sources, emit the brief's exact JSON** | `live_verify.py` + `cms_source.py` (live NPPES + CMS) | ✅ live |
| NPI validation (Luhn check digit) | `validation.py` → `is_valid_npi()` | ✅ + tests |
| Normalize names / addresses / phones / specialties | `normalize.py` (display + compare forms) | ✅ + tests |
| **One documented confidence formula, used everywhere** | `confidence.py` | ✅ + tests |
| Batch change detection + auto/review decision | `compare.py` | ✅ + tests |
| Apply auto-updates, queue reviews, write audit log | `apply_changes.py` | ✅ + tests |
| Duplicate / movement / inactive / practice-location detection | `detect.py` | ✅ |
| **LLM extraction of fields from a practice page's free text** | `llm_extract.py` (live Messages API + offline demo) | ✅ + tests |
| Per-1,000-record cost model | `cost_estimate.py` | ✅ |
| Accuracy measurement (precision / recall / F1) | `evaluate.py` + `make_dirty_data.py` | ✅ |
| Human-review dashboard | `Review dashboard.html` + `export_review.py` | ✅ |
| Batch quality gate (score /100) | `pull_quality.py` | ✅ |
| Audit trail of every action | `providers_audit_log` table (`database.py`) | ✅ |

**The centerpiece** — `live_verify.py` answers the brief's "Example Problem"
literally. Given a stored record it queries **two genuinely-independent live
federal sources — NPPES and the CMS National Downloadable File** — diffs every
field through the shared normalizer, scores each difference, and emits the
brief's exact recommendation schema. Real runs (no fixtures):

**(a) Two live sources disagree → human_review.** `python3 live_verify.py
1003040676` consults both registries. NPPES reports a New York phone; the CMS
NDF reports a Boston practice address entirely. The engine sees the cross-source
disagreement and refuses to act (verbatim from a live run — live data may shift):

```json
{
  "provider_id": "1003040676", "npi": "1003040676",
  "change_detected": true,
  "changes": [
    {"field": "phone",  "old_value": "(516) 972-1555",      "new_value": "(212) 305-2913", "confidence_score": 0.595, "supporting_sources": ["NPI Registry"]},
    {"field": "street", "old_value": "622 West 168th Street","new_value": "800 WASHIGTON ST","confidence_score": 0.85,  "supporting_sources": ["CMS"]},
    {"field": "city",   "old_value": "New York",            "new_value": "BOSTON",          "confidence_score": 0.85,  "supporting_sources": ["CMS"]},
    {"field": "state",  "old_value": "NY",                  "new_value": "MA",              "confidence_score": 0.85,  "supporting_sources": ["CMS"]},
    {"field": "zip",    "old_value": "10032",               "new_value": "021111552",       "confidence_score": 0.85,  "supporting_sources": ["CMS"]}
  ],
  "overall_confidence": 0.799,
  "recommended_action": "human_review",
  "reason": "Sources disagree on at least one field. Manual verification recommended.",
  "sources_consulted": ["cms", "nppes"]
}
```

The phone scores **0.595** because NPPES and CMS report *different* numbers — a
true cross-source conflict (the ×0.7 penalty); the address fields are CMS-only
(0.85). The record's mean is 0.799, and because at least one field conflicts the
whole record is routed to a human. No field is silently overwritten.

**(b) Two live sources confirm → no_change.** For `1003082850`, in a live run
NPPES *and* CMS both agreed with what we hold — the record was confirmed accurate
by two independent federal feeds (`"recommended_action": "no_change"`,
`"sources_consulted": ["cms", "nppes"]`).

**(c) Corroborated agreement → auto_update** (the brief's example 1).
`python3 live_verify.py --demo` shows the case where independent sources agree on
a *new* value: address + phone confirmed by multiple sources → auto-update at
`overall_confidence` ≈ 0.98, plus a conflict case that drops to `human_review`.

Two of the four named sources (NPPES, CMS) are **wired to live systems today**;
state boards and practice sites are designed stubs behind the same `Adapter`
contract (see `live_verify.py` / `cms_source.py`), and the LLM practice-site
extractor (§10) is also live.

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
| **NPPES / NPI Registry** (self-reported) | NPI, name, specialty/taxonomy, active status | 0.85 | **Free, keyless** | **✅ wired** (live API) + bulk |
| **CMS NDF** (Doctors & Clinicians, PECOS-derived) | practice phone/address, affiliations | 0.85 | **Free, keyless** | **✅ wired** (live API) |
| **State medical boards** | license status, active/retired | 0.90 | Free / low | adapter stub (Month 1) |
| **Practice websites** | phone, address, suite, current roster | 0.75 | Bandwidth only | **✅ via LLM** (`llm_extract.py`) |

*Reliability weights are **informed priors**, not yet empirically calibrated.
They are tuned against ground truth via `evaluate.py` (today: synthetic labels;
Month 1: a labeled HealthLynked sample). **Two of the four sources are wired to
live systems today** (NPPES + CMS NDF); state boards remain a designed adapter
behind the same `Adapter` contract.

Two properties drive correctness, both encoded in `confidence.py → SOURCES`:

- **Authority** — each field has a natural owner. A practice publishes its own
  phone first; NPPES is the registry of record for taxonomy and status. A
  change from the field's owner gets a confidence bonus.
- **Independence** — corroboration only counts *independent* sources. NPPES
  (self-reported registry) and the CMS NDF (Medicare-enrollment / PECOS-derived)
  are **different collection processes**, so they are independent and genuinely
  disagree in the wild (we observed one provider listed in NY by NPPES and in
  Boston by CMS) — when they *do* agree, that is real confirmation. By contrast,
  the NPPES API and its monthly **bulk file** are the *same data* via two
  channels, so they share an `independence_group` and never count as two
  confirmations. We keep only the most reliable source per group before scoring.
  *Caveat:* full independence is a modeling **approximation** — NPPES and the CMS
  NDF share some federal-enrollment lineage and are partially correlated, so
  two-source agreement is slightly optimistic. Calibration (§13) replaces the
  binary grouping with measured agreement rates; until then the conservative
  per-field thresholds and the human-review gate bound any over-corroboration.

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
source per `independence_group` (e.g. the NPPES API and its bulk file are the
same data — they don't corroborate each other; NPPES and CMS NDF do).

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
powers practice-location clustering. (Matching is intentionally at **street
granularity**: a suite/unit-only change is treated as the same location and is
*not* currently surfaced as a change — a known limitation slated for the
practice-entity work in §13, Month 2.)

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
| **Extract fields from a practice website's free text** | **LLM (Haiku, batched)** — *implemented* | unstructured → structured |
| Summarize a hard review case for the human | LLM (optional) | speeds the reviewer |

This keeps cost low, keeps the core **explainable** (no black box behind a
directory edit), and keeps the LLM where it adds real value.

**Wired and demonstrated, not just designed.** `llm_extract.py` implements the
website-extraction case against the **live Anthropic Messages API** (Haiku 4.5,
**structured outputs** for guaranteed-valid JSON, **raw HTTPS via `urllib` — no
SDK**, so the zero-dependency property holds). Crucially, the LLM is wired as a
*source adapter*: its output is not trusted directly but flows into the same
`confidence.py` engine as every other source. `python3 llm_extract.py --pipeline`
shows it end to end — the LLM reads a messy "Contact Us" blurb, extracts the
fields, and because **NPPES independently corroborates the new phone** it
auto-updates (0.97), while the single-source address fields are held for human
review. The LLM proposes; the deterministic engine decides. Set
`ANTHROPIC_API_KEY` for a live call; the bundled demo runs offline. (At scale
these calls go through the Batch API, −50%, per §9.)

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
  trivially and never becomes the bottleneck. **Measured** (run `python3
  benchmark.py`): the scoring engine does **~200,000+ field-scores/sec on a
  single core** (≈ 25,000–30,000 records/sec/core at ~8 fields each), so 1M
  records score in well under a minute per core, before any parallelism. The
  bottleneck is never the math — it's source I/O and humans.
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
- Port SQLite → Postgres; load NPPES + CMS monthly bulk files (NPPES + CMS NDF
  live adapters already exist — `live_verify.py` / `cms_source.py`).
- Wire one pilot **state-board** adapter behind the existing `Adapter` contract
  (the third real source).
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

# Verify ONE provider against TWO live sources — NPPES + CMS (the example problem)
python3 live_verify.py 1003040676        # real cross-source disagreement -> review
python3 live_verify.py 1003082850        # both sources confirm -> no_change
python3 live_verify.py --demo            # offline: corroborated auto-update + conflict
python3 cms_source.py 1003076902         # look up one NPI in the live CMS NDF

# The confidence formula, reproducing the brief's two worked examples
python3 confidence.py

# Directory-health detectors (duplicates, practices, moves, inactive, stale)
python3 detect.py

# LLM field extraction from a practice page (offline demo; set ANTHROPIC_API_KEY for live)
python3 llm_extract.py             # extract structured fields from free text
python3 llm_extract.py --pipeline  # LLM extraction -> corroborate (NPPES) -> decision

# Cost model (override the levers)
python3 cost_estimate.py --pct-llm 0.08 --pct-review 0.05

# Accuracy harness (generate labeled data first) + the test suite
python3 make_dirty_data.py && python3 evaluate.py
python3 -m unittest        # 65 tests
```

*No installation. Python 3 standard library only. Internet required for the
live NPPES steps.*
