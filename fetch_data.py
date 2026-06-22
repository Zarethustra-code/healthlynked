"""
fetch_data.py
-------------
Fetches 1000 cardiologists (NPI-1 individuals) from the official NPPES API,
cleans the name and validates the NPI, then stores them in:
   1. healthlynked.db  (the database)
   2. providers.csv     (a CSV file for review)

Note: NPPES returns at most 1200 results for any search, and it requires a
clear search condition (not the state alone). For that reason we aggregate
across several cities.

Uses only Python's built-in libraries (nothing needs to be installed).
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

TARGET = 1000        # the count we want
PAGE_SIZE = 200      # maximum count in a single request
TAXONOMY = "Cardiovascular Disease" # the specialty (cardiologist classification in NPPES)
STATE = "NY"

# Large cities in New York — we aggregate from them in order until we reach 1000
CITIES = ["New York", "Brooklyn", "Bronx", "Buffalo", "Rochester",
          "Albany", "Syracuse", "Yonkers", "Queens", "Staten Island"]


def fetch_page(city, skip):
    """Fetches a page (200 providers) from a given city starting at position skip."""
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
    """Extracts the full name (first + last) from an individual record."""
    basic = record.get("basic", {})
    first = basic.get("first_name", "")
    last = basic.get("last_name", "")
    return f"{first} {last}".strip()


def extract_taxonomy(record):
    """
    Extracts the primary specialty from the record.
    If no primary is flagged, takes the first specialty in the list.
    Returns (code, desc).
    """
    taxonomies = record.get("taxonomies", [])
    if not taxonomies:
        return "", ""

    # Look for the one where primary = True
    for t in taxonomies:
        if t.get("primary"):
            return t.get("code", ""), t.get("desc", "")

    # If there is no primary, take the first one
    first = taxonomies[0]
    return first.get("code", ""), first.get("desc", "")


def extract_status(record):
    """
    Translates the record status into a number:
        "A" (Active)      → 1
        "D" (Deactivated) → 0
        anything else      → 1 (default is active)
    """
    status = record.get("basic", {}).get("status", "A")
    return 0 if str(status).upper() == "D" else 1


def extract_phone(record):
    """
    Extracts the clinic address phone (LOCATION).
    If there is no LOCATION, takes the first address that has a phone.
    """
    addresses = record.get("addresses", [])

    # Preference goes to the LOCATION address (the clinic location)
    for addr in addresses:
        if addr.get("address_purpose") == "LOCATION":
            phone = addr.get("telephone_number", "")
            if phone:
                return phone

    # If there is no LOCATION, take the first available phone
    for addr in addresses:
        phone = addr.get("telephone_number", "")
        if phone:
            return phone

    return ""


def extract_address(record):
    """
    Extracts the clinic address parts (LOCATION).
    Returns (street, city, state, postal_code).
    """
    addresses = record.get("addresses", [])

    # Preference goes to the LOCATION address
    chosen = None
    for addr in addresses:
        if addr.get("address_purpose") == "LOCATION":
            chosen = addr
            break
    if chosen is None and addresses:
        chosen = addresses[0]   # if there is no LOCATION, take the first one
    if chosen is None:
        return "", "", "", ""

    # address_1 + address_2 (if present) in the street
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

    # Open the CSV file and write the header row
    csv_file = open(CSV_PATH, "w", newline="", encoding="utf-8")
    writer = csv.writer(csv_file)
    writer.writerow(["npi", "name", "taxonomy_code", "specialty", "is_active",
                     "phone", "street", "unit", "city", "state", "zip"])

    inserted = 0
    skipped = 0
    collected = 0

    print("=" * 55)
    print("  Starting collection from NPPES API (cardiologists - NY)")
    print("=" * 55)

    for city in CITIES:
        if inserted >= TARGET:
            break

        for skip in range(0, 1200, PAGE_SIZE):
            if inserted >= TARGET:
                break

            print(f"⏳ {city:<14} | skip={skip} | stored so far: {inserted}")
            try:
                results = fetch_page(city, skip)
            except Exception as e:
                print(f"❌ Error in request ({city}, skip={skip}): {e}")
                break

            if not results:
                break  # ran out of results for this city, move on to the next

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

                # The primary specialty
                tax_code, tax_desc = extract_taxonomy(record)
                spec = normalize_specialty(tax_code, tax_desc)

                # The status (active/deactivated)
                active = extract_status(record)

                # The phone (from the clinic address)
                phone = normalize_phone(extract_phone(record))

                # The address (from the clinic address)
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
                if cursor.rowcount > 0:        # actually inserted (not a duplicate)
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
    print(f"📥 Received from the API : {collected}")
    print(f"✅ Stored                : {inserted}")
    print(f"⏭️  Rejected/duplicate     : {skipped}")
    print(f"📁 Database              : {DB_PATH}")
    print(f"📄 CSV file              : {CSV_PATH}")
    print("=" * 55)


if __name__ == "__main__":
    main()
