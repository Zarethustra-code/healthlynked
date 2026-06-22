"""
make_second_source.py
---------------------
Creates a simulated "second source" in the external_data table.

The idea:
  Takes the real providers data and builds a second source (clinic_site) where:
    - Most values match the original (both sources agree -> no change)
    - A portion of them are intentionally different (phone/address/status changed)
  so that the comparison engine has something to detect and decide on.

Each change is applied randomly at a fixed rate so we can measure it later.
"""

import sqlite3
import random
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"

SOURCE_NAME = "clinic_site"   # name of the second source (the clinic website)
random.seed(7)                # reproducible result


def fake_phone():
    """Generates a fresh-looking US phone number."""
    area = random.choice(["212", "646", "718", "917", "516"])
    return f"({area}) {random.randint(200,999)}-{random.randint(1000,9999)}"


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Clear the old second source (so we start clean each time)
    cur.execute("DELETE FROM external_data WHERE source_name = ?", (SOURCE_NAME,))

    rows = cur.execute(
        "SELECT npi, phone, street, unit, city, state, zip, specialty, is_active "
        "FROM providers"
    ).fetchall()

    if not rows:
        print("❌ No data in providers. Run run_pipeline.py first.")
        conn.close()
        return

    changed_phone = 0
    changed_addr = 0
    changed_active = 0
    same = 0

    for (npi, phone, street, unit, city, state, zip_, specialty, is_active) in rows:
        roll = random.random()

        # 15% change the phone
        if roll < 0.15:
            phone = fake_phone()
            changed_phone += 1
        # 10% change the address (the city)
        elif roll < 0.25:
            city = random.choice(["Brooklyn", "Queens", "Bronx", "Albany"])
            changed_addr += 1
        # 3% change the status (inactive)
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
    print(f"  Simulated second source: {SOURCE_NAME}")
    print("=" * 55)
    print(f"📞 Phone changed   : {changed_phone}")
    print(f"📍 Address changed : {changed_addr}")
    print(f"🚫 Status changed  : {changed_active}")
    print(f"✅ Same as original: {same}")
    print("-" * 55)
    print(f"📊 Total second source: {total}")
    print("=" * 55)


if __name__ == "__main__":
    main()
