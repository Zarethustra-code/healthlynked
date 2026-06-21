"""
process.py
----------
قلب المعالجة: بياخد البيانات، يقرّر لكل سجل، ويوزّعه:

  سليم   → جدول providers
  مرفوض  → جدول providers_quarantine (مع سبب الرفض)
  + كل عملية بتتسجّل في providers_audit_log

بيعتمد على: validation.py + normalize.py + database.py
"""

import csv
import sqlite3
from pathlib import Path

from validation import is_valid_npi
from normalize import normalize_name

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"
IN_PATH = BASE / "dirty_providers.csv"

MIN_NAME_LEN = 2


def check_record(npi, name):
    """
    بيفحص السجل ويرجّع:
        (True,  "", clean_name)            لو سليم
        (False, سبب الرفض, "")             لو مرفوض
    """
    if not is_valid_npi(npi):
        return False, "Invalid NPI", ""

    clean = normalize_name(name)
    if len(clean["compare"].replace(" ", "")) < MIN_NAME_LEN:
        return False, "Invalid name (too short / empty)", ""

    return True, "", clean["display"]


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    with open(IN_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    accepted = 0
    quarantined = 0

    for r in rows:
        npi = r["npi"]
        name = r["name"]

        ok, reason, clean_name = check_record(npi, name)

        if ok:
            # سليم → providers (INSERT OR IGNORE عشان لو متكرر)
            cur.execute(
                "INSERT OR IGNORE INTO providers (npi, name) VALUES (?, ?)",
                (npi, clean_name),
            )
            if cur.rowcount > 0:
                accepted += 1
                cur.execute(
                    "INSERT INTO providers_audit_log (npi, action, detail) "
                    "VALUES (?, 'ACCEPTED', ?)",
                    (npi, "Passed validation"),
                )
        else:
            # مرفوض → quarantine (مع السبب) + status افتراضي Rejected
            cur.execute(
                "INSERT INTO providers_quarantine "
                "(npi, name, rejection_reason, status) VALUES (?, ?, ?, 'Rejected')",
                (npi, name, reason),
            )
            quarantined += 1
            cur.execute(
                "INSERT INTO providers_audit_log (npi, action, detail) "
                "VALUES (?, 'QUARANTINED', ?)",
                (npi, reason),
            )

    conn.commit()

    # نقرأ الأعداد النهائية من الجداول للتأكيد
    n_providers = cur.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    n_quar = cur.execute("SELECT COUNT(*) FROM providers_quarantine").fetchone()[0]
    n_audit = cur.execute("SELECT COUNT(*) FROM providers_audit_log").fetchone()[0]

    conn.close()

    print("=" * 55)
    print("  المعالجة والتوزيع")
    print("=" * 55)
    print(f"✅ اتقبلوا (providers)        : {accepted}")
    print(f"🔒 اتعزلوا (quarantine)       : {quarantined}")
    print("-" * 55)
    print(f"📊 إجمالي providers           : {n_providers}")
    print(f"📊 إجمالي quarantine          : {n_quar}")
    print(f"📊 إجمالي audit_log           : {n_audit}")
    print("=" * 55)


if __name__ == "__main__":
    main()