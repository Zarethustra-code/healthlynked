"""
export_review.py
----------------
بيصدّر التغييرات المستنية مراجعة (pending_review) لملف JSON
عشان شاشة المراجعة (Review dashboard.html) تقراه وتعرضه.

شغّله بعد compare.py / run_pipeline.py.
"""

import sqlite3
import json
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"
OUT_PATH = BASE / "review_data.json"


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    rows = cur.execute(
        "SELECT id, npi, field, old_value, new_value, source, confidence, reason "
        "FROM proposed_changes WHERE status = 'pending_review' ORDER BY confidence DESC"
    ).fetchall()

    # نجيب اسم الطبيب من providers عشان العرض يبقى أوضح
    data = []
    for r in rows:
        name_row = cur.execute(
            "SELECT name FROM providers WHERE npi = ?", (r["npi"],)
        ).fetchone()
        data.append({
            "id": r["id"],
            "npi": r["npi"],
            "name": name_row["name"] if name_row else "(غير معروف)",
            "field": r["field"],
            "old_value": r["old_value"],
            "new_value": r["new_value"],
            "source": r["source"],
            "confidence": r["confidence"],
            "reason": r["reason"],
        })

    conn.close()

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ اتصدّر {len(data)} تغيير مستني مراجعة")
    print(f"📄 الملف: {OUT_PATH}")
    print("👉 افتح \"Review dashboard.html\" في المتصفّح عشان تراجعهم")


if __name__ == "__main__":
    main()