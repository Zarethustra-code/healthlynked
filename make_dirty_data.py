"""
make_dirty_data.py
------------------
Takes the real 1000 providers from healthlynked.db and creates a "dirtied"
copy of them to test the system.

The idea:
  ~30% real errors    → the system should reject them   (expected_valid = False)
  ~70% valid/messy    → the system should accept them    (expected_valid = True)
                         (cosmetic noise gets cleaned and passes, not rejected)

Each row is written with:
  - error_type      : the type of dirtying we applied (for review)
  - expected_valid  : whether the system should accept it (True) or reject it (False)

The result is written to dirty_providers.csv
"""

import sqlite3
import csv
import random
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"
OUT_PATH = BASE / "dirty_providers.csv"

random.seed(42)   # so the result comes out the same every time (reproducible)


# ===========================================================================
#  NPI dirtying functions  (all are real errors → expected_valid = False)
# ===========================================================================

def npi_luhn_fail(npi):
    """Changes the last digit so the check digit becomes wrong."""
    last = int(npi[-1])
    wrong = (last + 1) % 10
    return npi[:-1] + str(wrong)

def npi_too_short(npi):
    """Drops the first digit (like a leading zero lost in Excel)."""
    return npi[1:]

def npi_too_long(npi):
    """Adds an extra digit at the end."""
    return npi + "3"

def npi_symbols(npi):
    """Inserts a dash and parentheses in the middle of the number."""
    return f"{npi[:3]}-{npi[3:6]}({npi[6:9]}){npi[9]}"

def npi_letters(npi):
    """Replaces 3 digits with letters."""
    return npi[:5] + "ABC" + npi[8:]

def npi_bad_prefix(npi):
    """Makes the first digit 3 (providers must start with 1 or 2)."""
    return "3" + npi[1:]

def npi_null(npi):
    """Removes the NPI entirely."""
    return ""


# ===========================================================================
#  Name dirtying functions
# ===========================================================================

# --- real name errors → expected_valid = False ---
def name_empty(name):
    """Completely empty name."""
    return ""

def name_single_letter(name):
    """Single-letter name (not a real provider)."""
    return random.choice(["J.", "Doc J", "X"])

# --- cosmetic noise (valid, gets cleaned) → expected_valid = True ---
def name_titles(name):
    """Surrounds the name with academic titles."""
    return f"Dr. {name}, MD, PhD, FACS"

def name_messy_spaces(name):
    """Double spaces and random dots."""
    parts = name.split()
    return "  ".join(parts) + " ..."

def name_reversed(name):
    """Reverses the name (last name first) with a comma."""
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[-1]}, {parts[0]}"
    return name

def name_muddled_case(name):
    """Mixed upper and lower case letters."""
    return "".join(
        c.upper() if random.random() > 0.5 else c.lower()
        for c in name
    )


# ===========================================================================
#  Categories
# ===========================================================================

# real errors (should be rejected)
REAL_ERRORS = [
    ("npi_luhn_fail",   npi_luhn_fail,   "npi"),
    ("npi_too_short",   npi_too_short,   "npi"),
    ("npi_too_long",    npi_too_long,    "npi"),
    ("npi_symbols",     npi_symbols,     "npi"),
    ("npi_letters",     npi_letters,     "npi"),
    ("npi_bad_prefix",  npi_bad_prefix,  "npi"),
    ("npi_null",        npi_null,        "npi"),
    ("name_empty",      name_empty,      "name"),
    ("name_single",     name_single_letter, "name"),
]

# cosmetic noise (should pass after cleaning)
COSMETIC_NOISE = [
    ("name_titles",       name_titles,       "name"),
    ("name_messy_spaces", name_messy_spaces, "name"),
    ("name_reversed",     name_reversed,     "name"),
    ("name_muddled_case", name_muddled_case, "name"),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT npi, name FROM providers").fetchall()
    conn.close()

    if not rows:
        print("❌ No data in the database. Run fetch_data.py first.")
        return

    out = open(OUT_PATH, "w", newline="", encoding="utf-8")
    writer = csv.writer(out)
    writer.writerow(["npi", "name", "error_type", "expected_valid"])

    count_real = 0
    count_cosmetic = 0
    count_clean = 0

    for npi, name in rows:
        roll = random.random()

        if roll < 0.30:
            # 30% real error
            err_name, err_func, field = random.choice(REAL_ERRORS)
            if field == "npi":
                npi = err_func(npi)
            else:
                name = err_func(name)
            writer.writerow([npi, name, err_name, False])
            count_real += 1

        elif roll < 0.55:
            # 25% cosmetic noise (valid)
            err_name, err_func, field = random.choice(COSMETIC_NOISE)
            name = err_func(name)
            writer.writerow([npi, name, err_name, True])
            count_cosmetic += 1

        else:
            # 45% completely clean
            writer.writerow([npi, name, "clean", True])
            count_clean += 1

    out.close()

    total = count_real + count_cosmetic + count_clean
    print("=" * 55)
    print("  Generating dirty data")
    print("=" * 55)
    print(f"❌ Real errors (rejected)   : {count_real}")
    print(f"🧹 Cosmetic noise (cleaned) : {count_cosmetic}")
    print(f"✅ Completely clean         : {count_clean}")
    print("-" * 55)
    print(f"📊 Total                    : {total}")
    print(f"📈 Expected to pass         : {count_cosmetic + count_clean}")
    print(f"📉 Expected to be rejected  : {count_real}")
    print(f"📄 File                     : {OUT_PATH}")
    print("=" * 55)


if __name__ == "__main__":
    main()
