"""
apply_changes.py
----------------
Applies the changes proposed by the comparison engine.

  AUTO_UPDATE   →  actually updates the providers table + status = 'applied'
  NEEDS_REVIEW  →  leaves it for the review queue + status = 'pending_review'

Every action is recorded in providers_audit_log (full audit trail).
"""

import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"

# Columns allowed to be updated in providers (safety: only these get updated)
UPDATABLE = {"phone", "street", "unit", "city", "state", "zip", "specialty", "is_active"}


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Fetch only the new changes (the ones not applied yet)
    changes = cur.execute(
        "SELECT id, npi, field, old_value, new_value, source, decision "
        "FROM proposed_changes WHERE status = 'new'"
    ).fetchall()

    applied = 0
    pending = 0
    skipped = 0

    for (cid, npi, field, old_val, new_val, source, decision) in changes:

        if decision == "AUTO_UPDATE":
            # Defense-in-depth backstop (mirrors confidence.py's hard rules):
            # never auto-deactivate a provider or auto-rename, even if a bad
            # AUTO_UPDATE row somehow reaches this layer. Route it to review.
            if field == "name" or (field == "is_active"
                                   and str(old_val) == "1" and str(new_val) == "0"):
                cur.execute(
                    "UPDATE proposed_changes SET status = 'pending_review' WHERE id = ?",
                    (cid,))
                cur.execute(
                    "INSERT INTO providers_audit_log (npi, action, detail) VALUES (?, 'FLAGGED_REVIEW', ?)",
                    (npi, f"{field}: '{old_val}' → '{new_val}' (from {source}) [blocked auto: high-stakes]"))
                pending += 1
                continue

            # Safety: make sure the column is allowed to be updated
            if field not in UPDATABLE:
                skipped += 1
                continue

            # Actually update the providers table
            cur.execute(
                f"UPDATE providers SET {field} = ? WHERE npi = ?",
                (new_val, npi),
            )
            # Mark the change as applied
            cur.execute(
                "UPDATE proposed_changes SET status = 'applied' WHERE id = ?",
                (cid,),
            )
            # Record in the audit trail
            cur.execute(
                "INSERT INTO providers_audit_log (npi, action, detail) VALUES (?, 'AUTO_UPDATED', ?)",
                (npi, f"{field}: '{old_val}' → '{new_val}' (from {source})"),
            )
            applied += 1

        else:  # NEEDS_REVIEW
            cur.execute(
                "UPDATE proposed_changes SET status = 'pending_review' WHERE id = ?",
                (cid,),
            )
            cur.execute(
                "INSERT INTO providers_audit_log (npi, action, detail) VALUES (?, 'FLAGGED_REVIEW', ?)",
                (npi, f"{field}: '{old_val}' → '{new_val}' (from {source})"),
            )
            pending += 1

    conn.commit()

    # Current review queue
    review_count = cur.execute(
        "SELECT COUNT(*) FROM proposed_changes WHERE status = 'pending_review'"
    ).fetchone()[0]
    conn.close()

    print("=" * 55)
    print("  Applying changes")
    print("=" * 55)
    print(f"🟢 Auto-applied (providers)     : {applied}")
    print(f"🟠 Sent to human review         : {pending}")
    if skipped:
        print(f"⏭️  Skipped (column not allowed) : {skipped}")
    print("-" * 55)
    print(f"📋 Current review queue         : {review_count}")
    print("=" * 55)


if __name__ == "__main__":
    main()