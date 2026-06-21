"""
fetch_data.py
-------------
بيجيب 1000 طبيب قلب (أفراد NPI-1) من NPPES API الرسمي،
بينظّف الاسم ويتأكد من الـ NPI، وبيخزّنهم في:
   1. healthlynked.db  (قاعدة البيانات)
   2. providers.csv     (ملف CSV للمراجعة)

ملاحظة: NPPES بيرجّع أقصى 1200 نتيجة لأي بحث، وبيطلب شرط بحث
واضح (مش الولاية لوحدها). عشان كده بنجمّع من كذا مدينة.

بيستخدم بس المكتبات الجاهزة في بايثون (مفيش حاجة تتثبّت).
"""

import sqlite3
import csv
import json
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlencode

from validation import is_valid_npi
from normalize import normalize_name, normalize_specialty, normalize_phone, normalize_address

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"
CSV_PATH = BASE / "providers.csv"
API_URL = "https://npiregistry.cms.hhs.gov/api/"

TARGET = 1000        # العدد اللي عايزينه
PAGE_SIZE = 200      # أقصى عدد في الطلب الواحد
TAXONOMY = "Cardiovascular Disease" # التخصص (تصنيف أطباء القلب في NPPES)
STATE = "NY"

# مدن كبيرة في نيويورك — بنجمّع منها بالترتيب لحد ما نكمّل 1000
CITIES = ["New York", "Brooklyn", "Bronx", "Buffalo", "Rochester",
          "Albany", "Syracuse", "Yonkers", "Queens", "Staten Island"]


def fetch_page(city, skip):
    """بيجيب صفحة (200 طبيب) من مدينة معيّنة بادئة من موضع skip."""
    params = {
        "version": "2.1",
        "enumeration_type": "NPI-1",
        "taxonomy_description": TAXONOMY,
        "city": city,
        "state": STATE,
        "limit": PAGE_SIZE,
        "skip": skip,
    }
    url = API_URL + "?" + urlencode(params)
    with urlopen(url, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data.get("results", [])


def extract_name(record):
    """بيطلّع الاسم الكامل (first + last) من سجل الفرد."""
    basic = record.get("basic", {})
    first = basic.get("first_name", "")
    last = basic.get("last_name", "")
    return f"{first} {last}".strip()


def extract_taxonomy(record):
    """
    بيطلّع التخصص الأساسي (primary) من السجل.
    لو مفيش primary مُعلَّم، بياخد أول تخصص في القائمة.
    بيرجّع (code, desc).
    """
    taxonomies = record.get("taxonomies", [])
    if not taxonomies:
        return "", ""

    # ندوّر على اللي primary = True
    for t in taxonomies:
        if t.get("primary"):
            return t.get("code", ""), t.get("desc", "")

    # لو مفيش primary، ناخد الأول
    first = taxonomies[0]
    return first.get("code", ""), first.get("desc", "")


def extract_status(record):
    """
    بيترجم حالة السجل لرقم:
        "A" (Active)      → 1
        "D" (Deactivated) → 0
        أي حاجة تانية      → 1 (الافتراضي نشط)
    """
    status = record.get("basic", {}).get("status", "A")
    return 0 if str(status).upper() == "D" else 1


def extract_phone(record):
    """
    بيطلّع تليفون عنوان العيادة (LOCATION).
    لو مفيش LOCATION، بياخد أول عنوان فيه تليفون.
    """
    addresses = record.get("addresses", [])

    # الأفضلية لعنوان LOCATION (مكان العيادة)
    for addr in addresses:
        if addr.get("address_purpose") == "LOCATION":
            phone = addr.get("telephone_number", "")
            if phone:
                return phone

    # لو مفيش LOCATION، ناخد أول تليفون موجود
    for addr in addresses:
        phone = addr.get("telephone_number", "")
        if phone:
            return phone

    return ""


def extract_address(record):
    """
    بيطلّع أجزاء عنوان العيادة (LOCATION).
    بيرجّع (street, city, state, postal_code).
    """
    addresses = record.get("addresses", [])

    # الأفضلية لعنوان LOCATION
    chosen = None
    for addr in addresses:
        if addr.get("address_purpose") == "LOCATION":
            chosen = addr
            break
    if chosen is None and addresses:
        chosen = addresses[0]   # لو مفيش LOCATION، ناخد الأول
    if chosen is None:
        return "", "", "", ""

    # address_1 + address_2 (لو موجود) في الشارع
    street = chosen.get("address_1", "")
    if chosen.get("address_2"):
        street = f"{street} {chosen['address_2']}"

    return (
        street,
        chosen.get("city", ""),
        chosen.get("state", ""),
        chosen.get("postal_code", ""),
    )


def main():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # نفتح ملف الـ CSV ونكتب صف العناوين
    csv_file = open(CSV_PATH, "w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    writer.writerow(["npi", "name", "taxonomy_code", "specialty", "is_active",
                     "phone", "street", "unit", "city", "state", "zip"])

    inserted = 0
    skipped = 0
    collected = 0

    print("=" * 55)
    print("  بدء التجميع من NPPES API (أطباء قلب - NY)")
    print("=" * 55)

    for city in CITIES:
        if inserted >= TARGET:
            break

        for skip in range(0, 1200, PAGE_SIZE):
            if inserted >= TARGET:
                break

            print(f"⏳ {city:<14} | skip={skip} | المخزّن حتى الآن: {inserted}")
            try:
                results = fetch_page(city, skip)
            except Exception as e:
                print(f"❌ خطأ في الطلب ({city}, skip={skip}): {e}")
                break

            if not results:
                break  # خلصت نتائج المدينة دي، ننتقل للي بعدها

            for record in results:
                collected += 1
                npi = str(record.get("number", "")).strip()
                raw_name = extract_name(record)

                if not is_valid_npi(npi):
                    skipped += 1
                    continue

                clean = normalize_name(raw_name)
                if not clean["display"]:
                    skipped += 1
                    continue

                # التخصص الأساسي
                tax_code, tax_desc = extract_taxonomy(record)
                spec = normalize_specialty(tax_code, tax_desc)

                # الحالة (نشط/متوقف)
                active = extract_status(record)

                # التليفون (من عنوان العيادة)
                phone = normalize_phone(extract_phone(record))

                # العنوان (من عنوان العيادة)
                st, ci, sta, zp = extract_address(record)
                addr = normalize_address(st, ci, sta, zp)

                cursor.execute(
                    "INSERT OR IGNORE INTO providers "
                    "(npi, name, taxonomy_code, specialty, is_active, phone, "
                    "street, unit, city, state, zip) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (npi, clean["display"], spec["code"], spec["display"],
                     active, phone["display"],
                     addr["street"], addr["unit"], addr["city"], addr["state"], addr["zip"]),
                )
                if cursor.rowcount > 0:        # دخل فعلاً (مش متكرر)
                    inserted += 1
                    writer.writerow([npi, clean["display"], spec["code"],
                                     spec["display"], active, phone["display"],
                                     addr["street"], addr["unit"], addr["city"],
                                     addr["state"], addr["zip"]])

            conn.commit()
            time.sleep(0.5)

    conn.close()
    csv_file.close()

    print("=" * 55)
    print(f"📥 وصلنا من الـ API : {collected}")
    print(f"✅ اتخزّنوا        : {inserted}")
    print(f"⏭️  اترفضوا/متكرر    : {skipped}")
    print(f"📁 قاعدة البيانات  : {DB_PATH}")
    print(f"📄 ملف الـ CSV     : {CSV_PATH}")
    print("=" * 55)


if __name__ == "__main__":
    main()