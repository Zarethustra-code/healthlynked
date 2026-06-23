"""
seed_sample_data.py
-------------------
Offline sample-data seeder — the no-internet replacement for fetch_data.py.

Instead of pulling from the live NPPES/CMS APIs (which need internet and can
return 0 records when access fails), this script creates/resets the SQLite
database using the EXISTING schema and inserts a small, hand-built but realistic
sample that is deliberately shaped to demonstrate every decision path the
pipeline supports:

  * no_change          — a second source that fully agrees (nothing to do)
  * auto_update        — a high-confidence, corroborated/authoritative change
  * human_review       — a real change that does not clear the safety bar
  * conflicting sources— two sources disagree on the new value (never auto-applied)
  * invalid/dirty data — bad NPI / empty name -> quarantined; messy-but-valid -> cleaned
  * duplicate detection— two NPIs that look like the same provider (name + phone)
  * stale records      — providers not re-verified in a long time

Everything is standard-library only (sqlite3) and reuses the project's own
validation/normalization layers, so the offline path exercises the same code as
the live one. No API keys, no network, no `pip install`.

    python3 seed_sample_data.py        # reset healthlynked.db + seed the sample
    (usually run via:  python3 run_offline_demo.py)
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from database import DB_PATH, reset_database
from validation import _luhn_check_digit
from normalize import (normalize_phone, normalize_address,
                       normalize_specialty, normalize_state)
import process

BASE = Path(__file__).parent

PROVIDER_COLS = ("npi", "name", "taxonomy_code", "specialty", "is_active",
                 "phone", "street", "unit", "city", "state", "zip")
EXTERNAL_COLS = ("npi", "source_name", "phone", "street", "unit", "city",
                 "state", "zip", "specialty", "is_active")

# Shared defaults so every provider only needs to spell out what differs.
TAX = "207RC0000X"
SPEC = "Cardiovascular Disease"

_STALE_1 = "2022-06-01 00:00:00"   # well over 180 days old -> re-verify queue
_STALE_2 = "2023-06-01 00:00:00"


def make_npi(seq: int) -> str:
    """Build a *valid* 10-digit NPI from a sequence number (passes the Luhn check).

    Using the project's own Luhn helper guarantees the sample NPIs are accepted
    by validation.is_valid_npi() and by the providers-table CHECK constraint.
    """
    prefix9 = f"10000{seq:04d}"             # 5 + 4 = 9 digits, always starts with 1
    return prefix9 + str(_luhn_check_digit(prefix9))


def _provider(seq, name, phone, street, city, zip_, *,
              specialty=SPEC, taxonomy_code=TAX, is_active=1, unit="", state="NY"):
    return {
        "npi": make_npi(seq), "name": name, "taxonomy_code": taxonomy_code,
        "specialty": specialty, "is_active": is_active, "phone": phone,
        "street": street, "unit": unit, "city": city, "state": state, "zip": zip_,
    }


# ===========================================================================
#  The sample. Each scenario carries the provider row, the external-source rows
#  (given only as overrides relative to the provider; everything else mirrors
#  the provider, i.e. "the sources agree"), and an optional stale timestamp.
# ===========================================================================
def _scenarios():
    return [
        # --- no_change: the second source agrees on everything ---
        {"tag": "no_change",
         "provider": _provider(1, "Sarah Johnson", "(212) 555-0101", "100 Main Street", "New York", "10001"),
         "external": [{"source": "clinic_site"}]},
        {"tag": "no_change",
         "provider": _provider(2, "David Smith", "(212) 555-0102", "200 Broadway", "New York", "10002"),
         "external": [{"source": "nppes"}]},

        # --- auto_update: phone from the practice (its authoritative field) ---
        {"tag": "auto_update",
         "provider": _provider(3, "Emily Davis", "(212) 555-0303", "300 Park Avenue", "New York", "10003"),
         "external": [{"source": "clinic_site", "phone": "(212) 555-9999"}]},

        # --- auto_update: street corroborated by two independent sources ---
        {"tag": "auto_update",
         "provider": _provider(4, "Michael Brown", "(212) 555-0104", "100 Main Street", "New York", "10004"),
         "external": [{"source": "practice_site", "street": "250 Health Park Drive"},
                      {"source": "state_board", "street": "250 Health Park Drive"}]},

        # --- human_review: a city change that does not clear the (higher) bar ---
        {"tag": "human_review",
         "provider": _provider(5, "Jessica Wilson", "(212) 555-0105", "400 Lexington Avenue", "New York", "10005"),
         "external": [{"source": "clinic_site", "city": "Brooklyn"}]},

        # --- human_review: deactivation is high-stakes -> always reviewed ---
        {"tag": "human_review",
         "provider": _provider(6, "Daniel Moore", "(212) 555-0106", "500 Madison Avenue", "New York", "10006"),
         "external": [{"source": "nppes", "is_active": 0}]},

        # --- conflicting sources: two sources disagree on the new street ---
        {"tag": "conflict",
         "provider": _provider(7, "Laura Taylor", "(212) 555-0107", "100 Main Street", "New York", "10007"),
         "external": [{"source": "practice_site", "street": "250 Health Park Drive"},
                      {"source": "state_board", "street": "900 Gulf Coast Boulevard"}]},

        # --- duplicate detection: same name + same phone, two different NPIs ---
        {"tag": "duplicate",
         "provider": _provider(8, "Robert Chen", "(212) 555-0808", "11 First Avenue", "New York", "10008")},
        {"tag": "duplicate",
         "provider": _provider(9, "Robert Chen", "(212) 555-0808", "850 Second Avenue", "New York", "10009")},

        # --- stale records: not re-verified in a long time ---
        {"tag": "stale",
         "provider": _provider(10, "Karen Martinez", "(212) 555-0110", "600 Fifth Avenue", "New York", "10010"),
         "stale": _STALE_1},
        {"tag": "stale",
         "provider": _provider(11, "Steven Anderson", "(212) 555-0111", "700 Eighth Avenue", "New York", "10011"),
         "stale": _STALE_2},

        # --- shared practice location: three providers at one address ---
        {"tag": "shared_location",
         "provider": _provider(12, "Nancy Thomas", "(212) 555-0112", "500 Park Avenue", "New York", "10022")},
        {"tag": "shared_location",
         "provider": _provider(13, "Paul Jackson", "(212) 555-0113", "500 Park Avenue", "New York", "10022")},
        {"tag": "shared_location",
         "provider": _provider(14, "Betty White", "(212) 555-0114", "500 Park Avenue", "New York", "10022")},

        # --- a few more plain providers (padding to a realistic directory size) ---
        {"tag": "no_change",
         "provider": _provider(15, "Mark Harris", "(212) 555-0115", "800 Ninth Avenue", "New York", "10015"),
         "external": [{"source": "clinic_site"}]},
        {"tag": "plain",
         "provider": _provider(16, "Sandra Clark", "(212) 555-0116", "900 Tenth Avenue", "New York", "10016")},
        {"tag": "plain",
         "provider": _provider(17, "Kevin Lewis", "(212) 555-0117", "120 Water Street", "New York", "10017")},
        {"tag": "plain",
         "provider": _provider(18, "Donna Walker", "(212) 555-0118", "130 Pearl Street", "New York", "10018")},
        {"tag": "plain",
         "provider": _provider(19, "George Hall", "(212) 555-0119", "140 Wall Street", "New York", "10019")},
        {"tag": "plain",
         "provider": _provider(20, "Carol Allen", "(212) 555-0120", "150 Front Street", "New York", "10020")},
    ]


# Raw "incoming" records that still need validation. Some are messy-but-valid
# (the normalizer cleans them and they are accepted); some are real errors
# (rejected to the quarantine table with a reason).
def _raw_intake():
    return [
        # messy but valid -> cleaned and accepted into providers
        {"npi": make_npi(50), "name": "  dr. gregory   house, md  ",
         "phone": "212.555.0050", "street": "12 e 14 st", "city": "new york",
         "state": "NY", "zip": "10050"},
        {"npi": make_npi(51), "name": "House, Lisa",
         "phone": "(212) 555-0051", "street": "305 east 47 street", "city": "New York",
         "state": "NY", "zip": "10051"},
        {"npi": make_npi(52), "name": "  miguel    SANTOS  ",
         "phone": "212-555-0052", "street": "77 Hudson Boulevard", "city": "New York",
         "state": "NY", "zip": "10052"},
        # real errors -> quarantined
        {"npi": "1234567890", "name": "Gregory House Jr"},   # NPI fails the Luhn check
        {"npi": make_npi(60), "name": ""},                   # empty name
        {"npi": "12345ABCDE", "name": "Test Provider"},      # NPI has letters
    ]


# ===========================================================================
#  Seeding
# ===========================================================================
def _insert_provider(cur, row):
    cur.execute(
        f"INSERT INTO providers ({','.join(PROVIDER_COLS)}) "
        f"VALUES ({','.join('?' * len(PROVIDER_COLS))})",
        tuple(row[c] for c in PROVIDER_COLS),
    )


def _insert_external(cur, provider, source, overrides):
    """Insert one external row that mirrors the provider except for `overrides`."""
    row = {
        "npi": provider["npi"], "source_name": source,
        "phone": provider["phone"], "street": provider["street"],
        "unit": provider["unit"], "city": provider["city"],
        "state": provider["state"], "zip": provider["zip"],
        "specialty": provider["specialty"], "is_active": provider["is_active"],
    }
    row.update(overrides)
    cur.execute(
        f"INSERT INTO external_data ({','.join(EXTERNAL_COLS)}) "
        f"VALUES ({','.join('?' * len(EXTERNAL_COLS))})",
        tuple(row[c] for c in EXTERNAL_COLS),
    )


def _process_intake(cur, raw):
    """Validate one raw record -> (accepted into providers) or (quarantined)."""
    ok, reason, clean_name = process.check_record(raw["npi"], raw["name"])
    if not ok:
        cur.execute(
            "INSERT INTO providers_quarantine (npi, name, raw_record, rejection_reason, status) "
            "VALUES (?, ?, ?, ?, 'Rejected')",
            (raw["npi"], raw["name"], json.dumps(raw, ensure_ascii=False), reason),
        )
        cur.execute(
            "INSERT INTO providers_audit_log (npi, action, detail) VALUES (?, 'QUARANTINED', ?)",
            (raw["npi"], reason),
        )
        return False

    addr = normalize_address(raw.get("street"), raw.get("city"),
                             raw.get("state"), raw.get("zip"))
    row = {
        "npi": raw["npi"], "name": clean_name,
        "taxonomy_code": raw.get("taxonomy_code", TAX),
        "specialty": normalize_specialty(raw.get("taxonomy_code", ""),
                                         raw.get("specialty", SPEC))["display"] or SPEC,
        "is_active": 1,
        "phone": normalize_phone(raw.get("phone"))["display"],
        "street": addr["street"], "unit": addr["unit"], "city": addr["city"],
        "state": normalize_state(raw.get("state")), "zip": addr["zip"],
    }
    _insert_provider(cur, row)
    cur.execute(
        "INSERT INTO providers_audit_log (npi, action, detail) VALUES (?, 'ACCEPTED', ?)",
        (raw["npi"], "Passed validation (offline sample)"),
    )
    return True


def seed(db_path: Path = DB_PATH) -> dict:
    """Reset the database and load the offline sample. Returns a small summary."""
    reset_database(db_path)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    scenarios = _scenarios()
    for sc in scenarios:
        p = sc["provider"]
        _insert_provider(cur, p)
        if sc.get("stale"):
            # Setting updated_at to a value different from the row's current one
            # means the providers updated_at trigger does NOT overwrite it.
            cur.execute("UPDATE providers SET updated_at = ? WHERE npi = ?",
                        (sc["stale"], p["npi"]))
        for ext in sc.get("external", []):
            source = ext["source"]
            overrides = {k: v for k, v in ext.items() if k != "source"}
            _insert_external(cur, p, source, overrides)

    accepted = quarantined = 0
    for raw in _raw_intake():
        if _process_intake(cur, raw):
            accepted += 1
        else:
            quarantined += 1

    conn.commit()
    n_prov = cur.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
    n_ext = cur.execute("SELECT COUNT(*) FROM external_data").fetchone()[0]
    n_quar = cur.execute("SELECT COUNT(*) FROM providers_quarantine").fetchone()[0]
    conn.close()

    print("=" * 60)
    print("  Offline sample seeded (no network, no API keys)")
    print("=" * 60)
    print(f"👤 Providers loaded        : {n_prov}  "
          f"({len(scenarios)} scenarios + {accepted} cleaned intake)")
    print(f"🔗 External-source rows    : {n_ext}")
    print(f"🔒 Quarantined (invalid)   : {n_quar}")
    print(f"📁 Database                : {db_path}")
    print("=" * 60)

    return {"providers": n_prov, "external": n_ext, "quarantine": n_quar}


if __name__ == "__main__":
    seed()
