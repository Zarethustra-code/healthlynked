"""
compare.py
----------
Batch comparison / change-detection engine (steps 6-8 of the pipeline).

  Step 6 (match):        compare each column between HealthLynked and every
                         external source, using the shared normalizer.
  Step 7 (independence): corroboration only counts independent sources.
  Step 8 (decide):       score confidence + choose AUTO_UPDATE / NEEDS_REVIEW.

All of the scoring, source-reliability, authority, independence, sensitivity
and safety logic now lives in ONE place -- confidence.py -- which is also used
by live_verify.py. This module just feeds it data from the database and writes
the results to the proposed_changes table.

Output rows: (npi, field, old_value, new_value, source, confidence, decision,
reason) where decision is 'AUTO_UPDATE' or 'NEEDS_REVIEW' (the tokens
apply_changes.py and the DB CHECK constraint expect).
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

import confidence
from normalize import field_compare_form, field_display_form

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"

# Columns we compare between sources.
COMPARE_FIELDS = ["phone", "street", "city", "state", "zip", "specialty", "is_active"]

# Map confidence.py's verdicts onto the legacy DB tokens.
_DECISION = {"auto_update": "AUTO_UPDATE", "human_review": "NEEDS_REVIEW",
             "no_change": "NEEDS_REVIEW"}


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Start clean.
    cur.execute("DELETE FROM proposed_changes")

    # Load HealthLynked records (the source of truth) keyed by NPI.
    providers = {}
    for row in cur.execute(
        "SELECT npi, phone, street, city, state, zip, specialty, is_active FROM providers"
    ):
        providers[row[0]] = dict(zip(
            ["npi", "phone", "street", "city", "state", "zip", "specialty", "is_active"], row))

    # Load every external record, grouped by NPI (supports many sources per provider).
    ext_by_npi = defaultdict(list)
    for row in cur.execute(
        "SELECT npi, source_name, phone, street, city, state, zip, specialty, is_active "
        "FROM external_data"
    ):
        npi = row[0]
        ext_by_npi[npi].append(dict(zip(
            ["npi", "source_name", "phone", "street", "city", "state", "zip",
             "specialty", "is_active"], row)))

    changes = auto = review = 0

    for npi, old in providers.items():
        sources = ext_by_npi.get(npi)
        if not sources:
            continue   # no external data for this provider

        for field in COMPARE_FIELDS:
            old_val = old[field]

            # Build the candidate list across all sources that carry this field.
            candidates = []
            for src in sources:
                raw = src[field]
                if raw is None or str(raw).strip() == "":
                    continue
                candidates.append({
                    "source": src["source_name"],
                    "value": field_compare_form(field, raw),
                    "display": field_display_form(field, raw),
                })

            result = confidence.score_field(
                field, old_val, candidates,
                old_compare=field_compare_form(field, old_val))
            if not result:
                continue   # no real, safe change to propose

            decision = _DECISION[result["decision"]]
            cur.execute(
                "INSERT INTO proposed_changes "
                "(npi, field, old_value, new_value, source, confidence, decision, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (npi, field, str(old_val), str(result["new_value"]),
                 ", ".join(result["supporting_sources"]),
                 result["confidence"], decision, result["reason"]),
            )
            changes += 1
            if decision == "AUTO_UPDATE":
                auto += 1
            else:
                review += 1

    conn.commit()
    conn.close()

    print("=" * 55)
    print("  Change-detection engine (confidence.py)")
    print("=" * 55)
    print(f"🔍 changes detected   : {changes}")
    print(f"🟢 auto-update        : {auto}")
    print(f"🟠 needs human review : {review}")
    print("=" * 55)


if __name__ == "__main__":
    main()
