"""
make_second_source.py
---------------------
بيعمل "مصدر تاني" محاكاة (simulated) في جدول external_data.

الفكرة:
  بياخد بيانات providers الحقيقية، ويعمل منها مصدر تاني (clinic_site) حيث:
    - معظم القيم زي الأصل (المصدرين متفقين → مفيش تغيير)
    - نسبة منها مختلفة عمداً (تليفون/عنوان/حالة اتغيّروا)
  عشان يكون عند محرّك المقارنة حاجة يكتشفها ويقرّر فيها.

كل تغيير بيتعمل عشوائياً بنسبة محدّدة عشان نعرف نقيس بعدين.
"""

import sqlite3
import random
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"

SOURCE_NAME = "clinic_site"   # اسم المصدر التاني (موقع العيادة)
random.seed(7)                # نتيجة قابلة للتكرار


def fake_phone():
    """بيولّد تليفون أمريكي شكله جديد."""
    area = random.choice(["212", "646", "718", "917", "516"])
    return f"({area}) {random.randint(200,999)}-{random.randint(1000,9999)}"


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # نفضّي المصدر التاني القديم (عشان نبدأ نظيف كل مرة)
    cur.execute("DELETE FROM external_data WHERE source_name = ?", (SOURCE_NAME,))

    rows = cur.execute(
        "SELECT npi, phone, street, unit, city, state, zip, specialty, is_active "
        "FROM providers"
    ).fetchall()

    if not rows:
        print("❌ مفيش بيانات في providers. شغّل run_pipeline.py الأول.")
        conn.close()
        return

    changed_phone = 0
    changed_addr = 0
    changed_active = 0
    same = 0

    for (npi, phone, street, unit, city, state, zip_, specialty, is_active) in rows:
        roll = random.random()

        # 15% نغيّر التليفون
        if roll < 0.15:
            phone = fake_phone()
            changed_phone += 1
        # 10% نغيّر العنوان (المدينة)
        elif roll < 0.25:
            city = random.choice(["Brooklyn", "Queens", "Bronx", "Albany"])
            changed_addr += 1
        # 3% نغيّر الحالة (متوقف)
        elif roll < 0.28:
            is_active = 0
            changed_active += 1
        else:
            same += 1

        cur.execute(
            "INSERT INTO external_data "
            "(npi, source_name, phone, street, unit, city, state, zip, specialty, is_active) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (npi, SOURCE_NAME, phone, street, unit, city, state, zip_, specialty, is_active),
        )

    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM external_data").fetchone()[0]
    conn.close()

    print("=" * 55)
    print(f"  المصدر التاني المحاكاة: {SOURCE_NAME}")
    print("=" * 55)
    print(f"📞 تليفون اتغيّر   : {changed_phone}")
    print(f"📍 عنوان اتغيّر    : {changed_addr}")
    print(f"🚫 حالة اتغيّرت    : {changed_active}")
    print(f"✅ زي الأصل        : {same}")
    print("-" * 55)
    print(f"📊 إجمالي المصدر التاني: {total}")
    print("=" * 55)


if __name__ == "__main__":
    main()