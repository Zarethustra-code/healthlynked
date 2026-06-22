"""
detect.py
---------
Directory-health detectors that need NO network and NO LLM -- they run over the
data already in healthlynked.db. These cover the brief's bonus items:

  * duplicate detection          -> find_duplicate_providers()
  * practice-location matching   -> find_shared_locations()
  * provider movement detection  -> find_moved_providers()
  * inactive / retired detection -> find_inactive_providers()
  * stale records (re-verify)    -> find_stale_records()

Everything keys off the shared normalizer so "St." and "Street", or
"(212) 555-1234" and "2125551234", collapse before comparison. Each function
returns plain data; `report()` prints a human summary.

    python3 detect.py            # full report on healthlynked.db
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from pathlib import Path

from normalize import normalize_name, normalize_phone, normalize_address

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _connect(db_path=None):
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _location_key(row):
    """Canonical practice-location key from a provider/external row."""
    return normalize_address(row["street"], row["city"], row["state"], row["zip"])["compare"]


# ---------------------------------------------------------------------------
# 1) Duplicate provider detection
# ---------------------------------------------------------------------------

def find_duplicate_providers(conn):
    """Different NPIs that look like the SAME provider.

    NPI is the primary key, so exact-NPI dupes can't exist. Real duplicates show
    up as the same normalized name sharing a phone or a practice address under
    two different NPIs (double-enrollment, data-entry error, or a re-issued NPI).
    Returns a list of clusters, each: {key, reason, npis:[...], name}.
    """
    rows = conn.execute(
        "SELECT npi, name, phone, street, unit, city, state, zip FROM providers"
    ).fetchall()

    by_name_phone = defaultdict(list)
    by_name_loc = defaultdict(list)
    for r in rows:
        name_c = normalize_name(r["name"])["compare"]
        if not name_c:
            continue
        phone_c = normalize_phone(r["phone"])["compare"]
        loc_c = _location_key(r)
        if phone_c:
            by_name_phone[(name_c, phone_c)].append(r)
        if loc_c.strip("|"):
            by_name_loc[(name_c, loc_c)].append(r)

    clusters = []
    seen = set()
    for (name_c, phone_c), group in by_name_phone.items():
        npis = sorted({r["npi"] for r in group})
        if len(npis) > 1:
            key = ("name+phone", name_c, phone_c)
            seen.add(frozenset(npis))
            clusters.append({"reason": "same name + same phone",
                             "name": group[0]["name"], "npis": npis})
    for (name_c, loc_c), group in by_name_loc.items():
        npis = sorted({r["npi"] for r in group})
        if len(npis) > 1 and frozenset(npis) not in seen:
            clusters.append({"reason": "same name + same address",
                             "name": group[0]["name"], "npis": npis})
    return clusters


# ---------------------------------------------------------------------------
# 2) Practice-location matching
# ---------------------------------------------------------------------------

def find_shared_locations(conn, min_providers=2):
    """Group providers by canonical practice address.

    A location with several providers is a practice/group; this is the basis for
    practice-level updates (if the practice moves, everyone at it moved).
    Returns clusters sorted by size: {location, count, npis:[...]}.
    """
    rows = conn.execute(
        "SELECT npi, street, unit, city, state, zip FROM providers"
    ).fetchall()
    by_loc = defaultdict(list)
    labels = {}
    for r in rows:
        key = _location_key(r)
        if key.strip("|"):                 # skip rows with no address at all
            by_loc[key].append(r)
            if key not in labels:          # keep a pretty label from the display form
                a = normalize_address(r["street"], r["city"], r["state"], r["zip"])
                labels[key] = f'{a["street"]}, {a["city"]} {a["state"]}'.strip(", ")

    clusters = []
    for key, group in by_loc.items():
        if len(group) >= min_providers:
            clusters.append({
                "location": labels[key],
                "count": len(group),
                "npis": sorted(r["npi"] for r in group),
            })
    clusters.sort(key=lambda c: c["count"], reverse=True)
    return clusters


# ---------------------------------------------------------------------------
# 3) Provider movement detection
# ---------------------------------------------------------------------------

def find_moved_providers(conn):
    """Providers whose street/city differs from an external source = relocation.

    Returns: {npi, name, source, field, old, new}.
    """
    providers = {r["npi"]: r for r in conn.execute(
        "SELECT npi, name, street, city, state, zip FROM providers")}
    moves = []
    for e in conn.execute(
        "SELECT npi, source_name, street, city, state, zip FROM external_data"
    ):
        p = providers.get(e["npi"])
        if not p:
            continue
        old_loc = normalize_address(p["street"], p["city"], p["state"], p["zip"])["compare"]
        new_loc = normalize_address(e["street"], e["city"], e["state"], e["zip"])["compare"]
        if not new_loc.strip("|"):
            continue                       # source carries no address
        # Compare street + city + state + zip (ignore only unit churn). Including
        # state/zip catches cross-state moves to a same-named street/city
        # (e.g. "100 Main St, Springfield IL" -> "100 Main St, Springfield MA").
        op = old_loc.split("|"); npart = new_loc.split("|")
        if (op[0], op[2], op[3], op[4]) != (npart[0], npart[2], npart[3], npart[4]):
            moves.append({
                "npi": e["npi"], "name": p["name"], "source": e["source_name"],
                "old": f'{p["street"]}, {p["city"]}',
                "new": f'{e["street"]}, {e["city"]}',
            })
    return moves


# ---------------------------------------------------------------------------
# 4) Inactive / retired detection
# ---------------------------------------------------------------------------

def find_inactive_providers(conn):
    """Providers we already hold as inactive, plus providers an external source
    reports as deactivated. Returns: {npi, name, reason}.
    """
    out = []
    for r in conn.execute(
        "SELECT npi, name FROM providers WHERE is_active = 0"
    ):
        out.append({"npi": r["npi"], "name": r["name"], "reason": "marked inactive in directory"})

    held = {r["npi"]: r["name"] for r in conn.execute("SELECT npi, name FROM providers")}
    for e in conn.execute(
        "SELECT DISTINCT npi, source_name FROM external_data WHERE is_active = 0"
    ):
        if e["npi"] in held:
            out.append({"npi": e["npi"], "name": held[e["npi"]],
                        "reason": f'reported inactive by {e["source_name"]}'})
    return out


# ---------------------------------------------------------------------------
# 5) Stale records (cheap freshness signal -> re-verify queue)
# ---------------------------------------------------------------------------

def find_stale_records(conn, days=180):
    """Providers not updated in `days` days -- candidates for re-verification.

    Uses SQLite date math so it costs nothing. Returns: {npi, name, updated_at}.
    """
    rows = conn.execute(
        "SELECT npi, name, updated_at FROM providers "
        "WHERE updated_at < datetime('now', ?) ORDER BY updated_at",
        (f"-{int(days)} days",),
    ).fetchall()
    return [{"npi": r["npi"], "name": r["name"], "updated_at": r["updated_at"]} for r in rows]


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def report(db_path=None):
    conn = _connect(db_path)
    try:
        dupes = find_duplicate_providers(conn)
        locs = find_shared_locations(conn)
        moves = find_moved_providers(conn)
        inactive = find_inactive_providers(conn)
        stale = find_stale_records(conn)
    finally:
        conn.close()

    print("=" * 64)
    print("  HealthLynked directory-health report (no network / no LLM)")
    print("=" * 64)

    print(f"\n[1] Potential duplicate providers : {len(dupes)}")
    for d in dupes[:5]:
        print(f"    - {d['name']:<28} {d['reason']:<24} {d['npis']}")

    print(f"\n[2] Shared practice locations      : {len(locs)} "
          f"(clusters of >= 2 providers)")
    for l in locs[:5]:
        print(f"    - {l['count']:>3} providers @ {l['location']}")

    print(f"\n[3] Provider-movement candidates   : {len(moves)}")
    for m in moves[:5]:
        print(f"    - {m['name']:<24} {m['old']}  ->  {m['new']}  ({m['source']})")

    print(f"\n[4] Inactive / retired flags       : {len(inactive)}")
    for i in inactive[:5]:
        print(f"    - {i['npi']}  {i['name']:<24} {i['reason']}")

    print(f"\n[5] Stale records (>180d, re-verify): {len(stale)}")
    for s in stale[:5]:
        print(f"    - {s['npi']}  {s['name']:<24} last updated {s['updated_at']}")

    print("\n" + "=" * 64)


if __name__ == "__main__":
    report()
