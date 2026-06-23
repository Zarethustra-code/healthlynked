# HealthLynked — Provider Data Quality Pipeline

A small, dependency-free Python pipeline that collects healthcare provider
(cardiologist) records, **validates** and **normalizes** them, reconciles them
against a second data source, and routes proposed changes to either automatic
update or human review.

Everything runs on the Python standard library (`sqlite3`, `csv`, `json`,
`urllib`) — there is nothing to `pip install`. All data lives in a single
SQLite file, `healthlynked.db`.

> All code, comments, and output are in English.

---

## 📌 Bounty submission — start here

This repo is a **hybrid (Option C)** submission for the HealthLynked
provider-data-quality bounty: a runnable prototype **plus** a production plan.

- **Read the proposal:** [`PROPOSAL.md`](PROPOSAL.md) — architecture, the
  confidence formula, source reliability/independence, safe-update rules, cost
  model, scaling, 3-month roadmap, and a criteria/bonus coverage matrix.
- **See it run in 60 seconds (offline, no setup):**
  ```bash
  python3 confidence.py             # the confidence formula on the brief's examples
  python3 live_verify.py --demo     # corroborated auto-update + a conflict case
  python3 llm_extract.py --pipeline # LLM reads free text -> corroborate -> decide
  ```
- **See it run on LIVE data (needs internet):**
  ```bash
  python3 live_verify.py 1003040676 # NPPES vs CMS disagree -> human_review
  python3 live_verify.py 1003082850 # NPPES + CMS confirm    -> no_change
  ```
- **Run the test suite:** `python3 -m unittest` (65 tests).

**Key new modules:** `confidence.py` (one scoring engine), `live_verify.py`
+ `cms_source.py` (two live sources: NPPES + CMS), `llm_extract.py` (LLM as a
source adapter), `detect.py` (duplicates / movement / inactive / practices),
`cost_estimate.py` (per-1,000-record cost). Still **zero `pip install`**.

---

## What it does

1. **Fetch** ~1,000 cardiologists (NPI-1 individuals) from the official
   [NPPES registry API](https://npiregistry.cms.hhs.gov/api/).
2. **Validate** every record (e.g. NPI checksum via the Luhn algorithm).
3. **Normalize** names, specialties, phone numbers, and addresses into a clean
   display form plus a canonical compare form.
4. **Simulate a second source** (`clinic_site`) that deliberately disagrees with
   some records, so the reconciliation engine has something to detect.
5. **Score the pull quality** of each batch out of 100.
6. **Compare** the two sources field-by-field, compute a confidence score, and
   decide `AUTO_UPDATE` vs `NEEDS_REVIEW` — with a human-readable explanation.
7. **Apply** automatic updates, queue the rest for human review, and log every
   action to an audit trail.
8. **Export** the review queue to JSON for a browser-based review dashboard.

---

## Quick start

```bash
# Run the full pipeline end-to-end (wipes and rebuilds healthlynked.db)
python3 run_pipeline.py

# Export the human-review queue, then open the dashboard
python3 export_review.py
open "Review dashboard.html"
```

`run_pipeline.py` runs these stages in order:

| Stage | Module                  | Purpose                                                   |
|------:|-------------------------|-----------------------------------------------------------|
| 1     | `database.py`           | Create the SQLite tables                                  |
| 2     | `fetch_data.py`         | Pull provider records from NPPES                          |
| 3     | `make_second_source.py` | Build the simulated `clinic_site` source                 |
| 4     | `pull_quality.py`       | Score the second source's pull quality (/100)            |
| 5     | `compare.py`            | Detect changes, score confidence, decide auto vs review  |
| 6     | `apply_changes.py`      | Apply auto-updates, flag reviews, write the audit log    |

> The live fetch (stage 2) needs internet. If NPPES/CMS is unreachable and 0
> records come back, `run_pipeline.py` now **stops with a clear error instead of
> reporting a false success** — and points you at the offline demo below.

---

## Run offline demo (no internet, no API keys)

Reviewers can prove the full pipeline behavior with **zero network access** and
nothing to install:

```bash
python3 run_offline_demo.py
```

Instead of fetching from the live APIs, this seeds the database from a small,
deterministic, hand-built sample (`seed_sample_data.py`) and then runs the real
comparison → scoring → apply → export → directory-health stages, finishing with
a scannable decision summary.

The sample is **23 providers + 3 quarantined records**, deliberately shaped so
the run exercises every decision path:

| Capability            | What the demo shows                                              |
|-----------------------|-----------------------------------------------------------------|
| `no_change`           | Providers whose second source agrees on every field             |
| `auto_update`         | High-confidence change (authoritative / multi-source) applied   |
| `human_review`        | A real change that does not clear the safety bar                |
| conflicting sources   | Two sources disagree on the new value → never auto-applied       |
| invalid/dirty values  | Bad NPI / empty name → quarantined; messy-but-valid → cleaned    |
| duplicate detection   | Two NPIs that look like the same provider (same name + phone)   |
| stale records         | Providers not re-verified in >180 days → re-verify queue        |

Two helper files back this path (still standard-library only):

- **`seed_sample_data.py`** — resets `healthlynked.db` using the existing schema
  and loads the labeled offline sample (the no-internet replacement for
  `fetch_data.py`).
- **`run_offline_demo.py`** — orchestrates seed → compare → apply → export →
  detect and prints the decision summary.

---

## Database schema (`healthlynked.db`)

Created by `database.py`:

| Table                  | Role                                                              |
|------------------------|------------------------------------------------------------------|
| `providers`            | Clean, validated records (the source of truth)                   |
| `providers_quarantine` | Rejected records, kept with a rejection reason                   |
| `providers_audit_log`  | Full audit trail of every accept / quarantine / update action    |
| `external_data`        | The second source used for comparison                            |
| `proposed_changes`     | Output of the comparison engine (with confidence + decision)     |

---

## Modules

### Core pipeline
- **`run_pipeline.py`** — orchestrates the whole flow with one command.
- **`database.py`** — defines and creates the five tables.
- **`fetch_data.py`** — collects providers from NPPES across several NY cities.
  The dataset size is controlled by the `TARGET` constant (currently `1000`).
- **`make_second_source.py`** — generates a reproducible second source that
  changes ~15% of phones, ~10% of cities, and ~3% of statuses.
- **`pull_quality.py`** — batch-level quality score (row count, key columns,
  missing data, duplicates, value formats, freshness), each with a reason.
- **`compare.py`** — the reconciliation engine. Uses field authority, source
  independence, and field sensitivity to compute confidence and choose
  `AUTO_UPDATE` (≥ threshold) or `NEEDS_REVIEW`, with an explanation string.
- **`apply_changes.py`** — applies auto-updates to `providers`, queues reviews,
  and records every action in the audit log.
- **`export_review.py`** — exports `pending_review` changes to `review_data.json`.
- **`Review dashboard.html`** — browser UI that reads `review_data.json`.

### Validation & normalization (shared library)
- **`validation.py`** — "is this valid?" checks. Currently `is_valid_npi()`
  (10 digits, leading 1/2, Luhn check digit).
- **`normalize.py`** — "convert to canonical form." Handles names (incl.
  `Last, First` flips and title stripping), specialties, phones (US format),
  and addresses (abbreviation expansion + unit separation). Each returns a
  `display` form and a `compare` form.

### Evaluation / testing harness
- **`make_dirty_data.py`** — produces `dirty_providers.csv` with known
  good/bad records (real errors vs cosmetic noise) for the **record-validity**
  harness.
- **`process.py`** — runs raw records through validation/normalization and
  splits them into `providers` vs `providers_quarantine`.
- **`evaluate.py`** — **record-validity** harness only: scores the NPI/name
  intake gate against `dirty_providers.csv` and reports a confusion matrix plus
  Precision / Recall / Accuracy / F1. It does **not** measure update-decision or
  real-world accuracy.
- **`evaluate_update_decisions.py`** — **update-decision** harness (MVP): runs
  labeled fixtures (no_change / auto_update / human_review / conflict /
  blocked-unsafe) through the real `confidence.py` engine and reports
  correct/false auto-update, correct/false human-review, and missed-change.
- **`benchmark.py`** — **scalability** harness: throughput (field-scores/sec)
  of the scoring engine; run separately on a large dataset.
- **`count_columns.py`** — small helper for inspecting CSV columns.

---

## Evaluation scope (what each harness does and does not prove)

The project separates three different questions so no number is overclaimed:

| Harness | Question it answers | Status / honesty |
|---|---|---|
| `evaluate.py` | **Record validity** — is a record well-formed enough to admit (vs. quarantine)? | Binary classifier vs. **synthetic** labels. |
| `evaluate_update_decisions.py` | **Update-decision accuracy** — given a proposed change, does the engine pick auto / review / no-change / block correctly? | **Synthetic** labeled fixtures; proves logic + guards regressions. |
| `benchmark.py` | **Scalability** — how many decisions per second per core? | Reproducible throughput; run on a large dataset separately. |

> **None of these prove real-world update accuracy.** The synthetic labels show
> the pipeline *behaves as designed*; they are not evidence of field-level
> accuracy on live providers.

**How to actually validate before relying on the numbers:**
- **Accuracy** must be measured on a **manually reviewed HealthLynked sample**
  (real providers, human-confirmed correct values), then used to recalibrate the
  `confidence.py` weights/thresholds. This is the first deliverable in
  `PROPOSAL.md` (§12–§13, Month 1).
- **Scalability** must be tested **separately** on a **large benchmark dataset**
  (e.g. the full NPPES/CMS bulk files), not inferred from the small demo.

---

## Generated artifacts

| File                 | Produced by                  | Contents                                  |
|----------------------|------------------------------|-------------------------------------------|
| `healthlynked.db`    | the pipeline                 | All tables / data                         |
| `providers.csv`      | `fetch_data.py`              | CSV mirror of fetched providers           |
| `review_data.json`   | `export_review.py`           | Pending changes for the dashboard         |
| `dirty_providers.csv`| `make_dirty_data.py`         | Labeled test data for evaluation          |
| `misclassified.csv`  | `evaluate.py`                | Records the system decided incorrectly    |

---

## Configuration knobs

- **Dataset size** — `TARGET` in `fetch_data.py` (default `1000`).
- **Specialty / location** — `TAXONOMY`, `STATE`, and `CITIES` in `fetch_data.py`.
- **Decision tuning** — `FIELD_AUTHORITY`, `FIELD_SENSITIVITY`, and
  `AUTO_THRESHOLD` in `compare.py`.
- **Pull-quality expectations** — `EXPECTED_MIN` and `KEY_COLUMNS` in
  `pull_quality.py`.

---

## Requirements

- Python 3 (standard library only)
- Internet access for the **live** NPPES fetch step (`run_pipeline.py`)
- **No internet and no API keys** for the offline demo (`run_offline_demo.py`)
