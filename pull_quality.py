"""
pull_quality.py
---------------
Pull Quality Score evaluation — step 4 of the system map.

It inspects "the whole batch" (not a single record) and gives a score out of 100.
General-purpose function: takes the name of any table and inspects it.

The weights (exactly as in the system map):
  Pull with no errors          20
  Row count is reasonable      15
  Key columns are present      15
  Data is not heavily missing  15
  No strange duplicates        15
  Values in the right columns  10
  Pull date is clear           10
  ─────────────────────────  ────
  Total                       100

Golden rule: every point lost has a clear cause and an action.
"""

import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"

# The key columns expected in any provider pull
KEY_COLUMNS = ["npi", "phone", "city", "state"]

# The approximate expected count (if it drops a lot = danger)
# Note: TARGET in fetch_data = 1000, and the second source is one row per doctor,
# so the maximum possible count ≈ 1000. EXPECTED_MIN must stay below that, otherwise the check always fails.
EXPECTED_MIN = 800    # below this is considered an incomplete pull


def check_pull_quality(table, expected_min=EXPECTED_MIN):
    """
    Inspects the quality of a pull (table) and returns (score, report).
    report = a list of every check: (name, points earned, full points, reason).
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Get the names of the columns that actually exist
    existing_cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]

    # Get all the rows
    rows = cur.execute(f"SELECT * FROM {table}").fetchall()
    total = len(rows)
    col_index = {name: i for i, name in enumerate(existing_cols)}

    report = []

    # --- 1) Pull with no errors (20): is there any data at all? ---
    if total > 0:
        report.append(("Pull with no errors", 20, 20, "The pull contains data"))
    else:
        report.append(("Pull with no errors", 0, 20, "❌ The pull is completely empty"))

    # --- 2) Row count is reasonable (15) ---
    if total >= expected_min:
        report.append(("Row count is reasonable", 15, 15, f"{total} rows (expected ≥ {expected_min})"))
    else:
        pts = round(15 * total / expected_min) if expected_min else 0
        report.append(("Row count is reasonable", pts, 15,
                        f"⚠️ {total} only (expected ≥ {expected_min}) — sudden drop"))

    # --- 3) Key columns are present (15) ---
    missing_cols = [c for c in KEY_COLUMNS if c not in existing_cols]
    if not missing_cols:
        report.append(("Key columns are present", 15, 15, "All important columns are present"))
    else:
        pts = round(15 * (len(KEY_COLUMNS) - len(missing_cols)) / len(KEY_COLUMNS))
        report.append(("Key columns are present", pts, 15,
                        f"❌ missing: {', '.join(missing_cols)}"))

    # --- 4) Data is not heavily missing (15) ---
    # Compute the empty ratio in the important columns that are present
    present_keys = [c for c in KEY_COLUMNS if c in col_index]
    empty_count = 0
    cells = 0
    for row in rows:
        for c in present_keys:
            cells += 1
            val = row[col_index[c]]
            if val is None or str(val).strip() == "":
                empty_count += 1
    empty_ratio = (empty_count / cells) if cells else 0
    if empty_ratio <= 0.05:
        report.append(("Data is not missing", 15, 15,
                        f"empty ratio {empty_ratio:.1%} (acceptable)"))
    else:
        pts = max(0, round(15 * (1 - empty_ratio)))
        report.append(("Data is not missing", pts, 15,
                        f"⚠️ empty ratio {empty_ratio:.1%} (high)"))

    # --- 5) No strange duplicates (15) ---
    if "npi" in col_index:
        npis = [row[col_index["npi"]] for row in rows]
        unique = len(set(npis))
        dup_ratio = 1 - (unique / total) if total else 0
        if dup_ratio <= 0.02:
            report.append(("No strange duplicates", 15, 15,
                            f"{unique} unique out of {total}"))
        else:
            pts = max(0, round(15 * (1 - dup_ratio)))
            report.append(("No strange duplicates", pts, 15,
                            f"⚠️ duplication {dup_ratio:.1%}"))
    else:
        report.append(("No strange duplicates", 0, 15, "❌ no npi column to check"))

    # --- 6) Values in the right columns (10): a simple check — the npi is 10 digits ---
    if "npi" in col_index:
        bad = sum(1 for row in rows
                  if not str(row[col_index["npi"]] or "").isdigit()
                  or len(str(row[col_index["npi"]] or "")) != 10)
        bad_ratio = bad / total if total else 0
        if bad_ratio <= 0.02:
            report.append(("Values in the right columns", 10, 10, "The NPI is in the right form"))
        else:
            pts = max(0, round(10 * (1 - bad_ratio)))
            report.append(("Values in the right columns", pts, 10,
                            f"⚠️ {bad_ratio:.1%} NPIs have the wrong form"))
    else:
        report.append(("Values in the right columns", 5, 10, "no npi to check"))

    # --- 7) Pull date is clear (10) ---
    if "fetched_at" in existing_cols:
        report.append(("Pull date is clear", 10, 10, "fetched_at is present"))
    else:
        report.append(("Pull date is clear", 0, 10, "⚠️ no date column"))

    conn.close()

    score = sum(p for _, p, _, _ in report)
    return score, report


def print_report(table):
    score, report = check_pull_quality(table)

    print("=" * 60)
    print(f"  Pull Quality Report: {table}")
    print("=" * 60)
    for name, pts, full, reason in report:
        mark = "✅" if pts == full else "⚠️ "
        print(f"  {mark} {name:<24} {pts:>2}/{full:<2}  | {reason}")
    print("-" * 60)
    print(f"  📊 Pull Quality Score: {score}/100")

    # The verdict + action (like the diagram examples)
    if score >= 85:
        print("  ✅ The pull is healthy — proceed to comparison")
    elif score >= 60:
        print("  ⚠️  The pull has notes — review the causes before comparing")
    else:
        print("  ❌ The pull is broken — stop the comparison and investigate the cause")
    print("=" * 60)


if __name__ == "__main__":
    print_report("external_data")
