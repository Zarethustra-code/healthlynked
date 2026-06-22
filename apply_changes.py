"""
apply_changes.py
----------------
بيطبّق التغييرات المقترحة من محرّك المقارنة.

  AUTO_UPDATE   →  يحدّث جدول providers فعلياً + status = 'applied'
  NEEDS_REVIEW  →  يسيبه لقائمة المراجعة + status = 'pending_review'

كل فعل بيتسجّل في providers_audit_log (تتبّع كامل).
"""

import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"

# الأعمدة المسموح تحديثها في providers (أمان: مايتحدّثش غير دول)
UPDATABLE = {"phone", "street", "unit", "city", "state", "zip", "specialty", "is_active"}


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # نجيب التغييرات الجديدة بس (اللي لسه ماتطبّقتش)
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
                    (npi, f"{field}: '{old_val}' → '{new_val}' (من {source}) [blocked auto: high-stakes]"))
                pending += 1
                continue

            # أمان: نتأكد العمود مسموح تحديثه
            if field not in UPDATABLE:
                skipped += 1
                continue

            # نحدّث جدول providers فعلياً
            cur.execute(
                f"UPDATE providers SET {field} = ? WHERE npi = ?",
                (new_val, npi),
            )
            # نعلّم التغيير إنه اتطبّق
            cur.execute(
                "UPDATE proposed_changes SET status = 'applied' WHERE id = ?",
                (cid,),
            )
            # تتبّع في الـ audit
            cur.execute(
                "INSERT INTO providers_audit_log (npi, action, detail) VALUES (?, 'AUTO_UPDATED', ?)",
                (npi, f"{field}: '{old_val}' → '{new_val}' (من {source})"),
            )
            applied += 1

        else:  # NEEDS_REVIEW
            cur.execute(
                "UPDATE proposed_changes SET status = 'pending_review' WHERE id = ?",
                (cid,),
            )
            cur.execute(
                "INSERT INTO providers_audit_log (npi, action, detail) VALUES (?, 'FLAGGED_REVIEW', ?)",
                (npi, f"{field}: '{old_val}' → '{new_val}' (من {source})"),
            )
            pending += 1

    conn.commit()

    # قائمة المراجعة الحالية
    review_count = cur.execute(
        "SELECT COUNT(*) FROM proposed_changes WHERE status = 'pending_review'"
    ).fetchone()[0]
    conn.close()

    print("=" * 55)
    print("  تطبيق التغييرات")
    print("=" * 55)
    print(f"🟢 اتطبّقوا تلقائي (providers)  : {applied}")
    print(f"🟠 راحوا للمراجعة البشرية      : {pending}")
    if skipped:
        print(f"⏭️  اتجاهلوا (عمود غير مسموح)   : {skipped}")
    print("-" * 55)
    print(f"📋 قائمة المراجعة الحالية      : {review_count}")
    print("=" * 55)


if __name__ == "__main__":
    main()