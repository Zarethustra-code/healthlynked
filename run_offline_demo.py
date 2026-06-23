"""
run_offline_demo.py
-------------------
End-to-end demo that runs the WHOLE reconciliation pipeline with NO internet,
NO API keys, and NO `pip install` — so a reviewer can prove the system's
behavior even when the live NPPES/CMS fetch returns 0 records.

It runs the same stages as run_pipeline.py, but swaps the network fetch for a
deterministic offline sample (seed_sample_data.py):

  1. seed        → reset the DB + load a realistic sample (scenarios + dirty intake)
  2. compare     → detect changes, score confidence, decide auto vs review
  3. apply       → apply auto-updates, queue reviews, write the audit log
  4. export      → write the human-review queue to review_data.json
  5. detect      → directory-health report (duplicates / stale / etc.)
  6. summary     → a clear, scannable rundown of every decision

Reviewer command:
    python3 run_offline_demo.py
"""

import sqlite3
import time
from pathlib import Path

import seed_sample_data
import compare
import apply_changes
import export_review
import detect
from database import DB_PATH
from export_review import OUT_PATH


def banner(step, title):
    print("\n" + "█" * 60)
    print(f"  Stage {step}: {title}")
    print("█" * 60)


def _summary(db_path=DB_PATH):
    """Print a clear, scannable summary of every decision the pipeline made."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    n_prov = cur.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    n_quar = cur.execute("SELECT COUNT(*) FROM providers_quarantine").fetchone()[0]

    auto = cur.execute(
        "SELECT COUNT(*) FROM proposed_changes WHERE decision = 'AUTO_UPDATE'").fetchone()[0]
    review = cur.execute(
        "SELECT COUNT(*) FROM proposed_changes WHERE decision = 'NEEDS_REVIEW'").fetchone()[0]
    conflicts = cur.execute(
        "SELECT COUNT(*) FROM proposed_changes WHERE lower(reason) LIKE '%conflict%'").fetchone()[0]
    applied = cur.execute(
        "SELECT COUNT(*) FROM proposed_changes WHERE status = 'applied'").fetchone()[0]

    # "no change" = providers that had a second source but produced no proposed change.
    with_external = {r[0] for r in cur.execute("SELECT DISTINCT npi FROM external_data")}
    with_change = {r[0] for r in cur.execute("SELECT DISTINCT npi FROM proposed_changes")}
    no_change = len(with_external - with_change)

    # Directory-health detectors (no network / no LLM).
    dupes = detect.find_duplicate_providers(conn)
    stale = detect.find_stale_records(conn)
    conn.close()

    print("\n" + "═" * 60)
    print("  DECISION SUMMARY (offline demo)")
    print("═" * 60)
    print(f"  providers in directory      : {n_prov}")
    print(f"  quarantined (invalid data)  : {n_quar}")
    print("  " + "-" * 56)
    print(f"  🟢 auto-update  (applied)    : {auto}  (written to providers: {applied})")
    print(f"  🟠 needs review (queued)     : {review}")
    print(f"       └─ source conflicts     : {conflicts}")
    print(f"  ⚪ no-change   (sources agree): {no_change}")
    print("  " + "-" * 56)
    print(f"  👯 duplicate clusters        : {len(dupes)}")
    print(f"  🕒 stale (>180d, re-verify)  : {len(stale)}")
    print("═" * 60)

    print("\nWhat this offline demo proves (no internet, no API keys):")
    checks = [
        ("no_change",            no_change >= 1,  f"{no_change} provider(s) where every source agrees"),
        ("auto_update",          auto >= 1,       f"{auto} high-confidence change(s) applied automatically"),
        ("human_review",         review >= 1,     f"{review} change(s) routed to the review queue"),
        ("conflicting sources",  conflicts >= 1,  f"{conflicts} field(s) where sources disagree -> review"),
        ("invalid/dirty values", n_quar >= 1,     f"{n_quar} bad record(s) quarantined; messy-but-valid cleaned"),
        ("duplicate detection",  len(dupes) >= 1, f"{len(dupes)} cluster(s) of same provider under 2 NPIs"),
        ("stale records",        len(stale) >= 1, f"{len(stale)} record(s) flagged for re-verification"),
    ]
    for name, ok, detail in checks:
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name:<20} — {detail}")
    print(f"\n📄 Review queue exported to: {OUT_PATH}")
    print('👉 Open "Review dashboard.html" to review the queued changes.')


def main():
    start = time.time()
    print("🚀 HealthLynked — OFFLINE demo (no internet required)")
    print("=" * 60)

    banner(1, "Seeding the offline sample (replaces the NPPES fetch)")
    seed_sample_data.seed()

    banner(2, "Comparison and change detection")
    compare.main()

    banner(3, "Applying changes")
    apply_changes.main()

    banner(4, "Exporting the human-review queue")
    export_review.main()

    banner(5, "Directory-health report (duplicates / stale / movement)")
    detect.report()

    banner(6, "Decision summary")
    _summary()

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"✅ Offline demo finished successfully in {elapsed:.1f} seconds")
    print("=" * 60)


if __name__ == "__main__":
    main()
