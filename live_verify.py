"""
live_verify.py
--------------
Single-record, LIVE verification against real external sources.

This is the piece that answers the brief's "Example Problem" end to end:
given one stored HealthLynked provider record, it queries the *live* NPPES /
NPI Registry API for that provider's current record, diffs every field through
the shared normalizer, scores each difference with confidence.py, and emits the
exact structured recommendation from the brief
(change_detected / changes[] / overall_confidence / recommended_action / reason).

NPPES is wired up for real (live HTTP, free, no key). Additional sources
(CMS, state medical boards, practice websites) plug in through the same
`Adapter` contract -- pass them via `extra_adapters`. PROPOSAL.md describes how
those are sourced in production; here we keep NPPES live and let the multi-source
machinery be exercised by an optional demo adapter.

Usage:
    python3 live_verify.py                # verify a sample provider from the DB
    python3 live_verify.py 1234567893     # verify a specific NPI
    python3 live_verify.py --demo         # offline multi-source demo (auto + conflict)
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode

import confidence
from normalize import field_compare_form as compare_form, field_display_form as display_form
# Reuse the exact NPPES field extractors the bulk fetcher uses.
from fetch_data import (
    extract_name, extract_taxonomy, extract_status, extract_phone, extract_address,
)
from cms_source import cms_adapter   # second live source (CMS National Downloadable File)

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"
API_URL = "https://npiregistry.cms.hhs.gov/api/"

# Fields we verify (the brief's MVP set that maps onto our schema).
COMPARE_FIELDS = ["name", "specialty", "phone", "street", "city", "state", "zip", "is_active"]


# ===========================================================================
#  Source adapters  (Adapter = callable(npi) -> {source, fields:{field: value}})
# ===========================================================================

def nppes_adapter(npi):
    """LIVE adapter: fetch this provider's current record from NPPES by NPI."""
    url = API_URL + "?" + urlencode({"version": "2.1", "number": str(npi)})
    req = Request(url, headers={"User-Agent": "HealthLynked-verify/1.0"})
    with urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = data.get("results", [])
    if not results:
        return None
    rec = results[0]

    _, tax_desc = extract_taxonomy(rec)
    st, ci, sta, zp = extract_address(rec)
    return {
        "source": "nppes",
        "fields": {
            "name":      extract_name(rec),
            "specialty": tax_desc,
            "phone":     extract_phone(rec),
            "street":    st,
            "city":      ci,
            "state":     sta,
            "zip":       zp,
            "is_active": extract_status(rec),
        },
    }


# ===========================================================================
#  Verification core
# ===========================================================================

def _load_stored(npi, conn):
    row = conn.execute(
        "SELECT npi, name, specialty, phone, street, city, state, zip, is_active "
        "FROM providers WHERE npi = ?", (str(npi),)
    ).fetchone()
    if not row:
        return None
    keys = ["npi", "name", "specialty", "phone", "street", "city", "state", "zip", "is_active"]
    return dict(zip(keys, row))


def verify_record(stored, adapters, provider_id=None):
    """Diff a stored record against external adapters and return the brief schema.

    `stored` is a dict with at least `npi` and the COMPARE_FIELDS.
    `adapters` is a list of callables(npi) -> {"source", "fields": {...}} | None.
    """
    npi = str(stored.get("npi", ""))
    provider_id = provider_id or stored.get("provider_id") or npi

    # Pull every adapter once.
    source_records = []
    sources_consulted = []
    for adapter in adapters:
        try:
            rec = adapter(npi)
        except Exception as e:                       # a flaky source must not crash the run
            rec = None
            sources_consulted.append(f"{getattr(adapter, '__name__', 'source')}(error: {type(e).__name__})")
        if rec:
            source_records.append(rec)
            sources_consulted.append(rec["source"])

    if not source_records:
        return {
            "provider_id": provider_id, "npi": npi,
            "change_detected": False, "changes": [],
            "overall_confidence": 0.0,
            "recommended_action": "human_review",
            "reason": "No external source could be reached to verify this record.",
            "sources_consulted": sources_consulted,
        }

    # For each field, build the candidate list and score it.
    field_results = []
    for field in COMPARE_FIELDS:
        old_val = stored.get(field)
        candidates = []
        for rec in source_records:
            raw = rec["fields"].get(field)
            if raw is None or str(raw).strip() == "":
                continue
            candidates.append({
                "source": rec["source"],
                "value": compare_form(field, raw),
                "display": display_form(field, raw),
            })
        result = confidence.score_field(
            field, old_val, candidates,
            old_compare=compare_form(field, old_val))
        if result:
            field_results.append(result)

    out = confidence.score_record(provider_id, npi, field_results)
    out["sources_consulted"] = sorted(set(sources_consulted))
    return out


def verify_provider(npi, conn=None, extra_adapters=None):
    """Verify one stored provider (by NPI) against live NPPES + any extra sources."""
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
    try:
        stored = _load_stored(npi, conn)
    finally:
        if own_conn:
            conn.close()

    if not stored:
        return {"npi": str(npi), "error": "provider not found in HealthLynked database"}

    # Two genuinely-independent live sources by default: NPPES + CMS NDF.
    adapters = [nppes_adapter, cms_adapter] + list(extra_adapters or [])
    return verify_record(stored, adapters, provider_id=stored["npi"])


# ===========================================================================
#  Offline demo (no network): shows multi-source auto-update and a conflict
# ===========================================================================

def _demo():
    stored = {
        "provider_id": "HL_001", "npi": "1234567890",
        "name": "John Smith", "specialty": "Cardiovascular Disease",
        "phone": "(239) 555-1234", "street": "100 Main St",
        "city": "Naples", "state": "FL", "zip": "34102", "is_active": 1,
    }

    def practice_site(npi):
        return {"source": "practice_site", "fields": {
            "phone": "239-555-9000", "street": "250 Health Park Dr",
            "city": "Fort Myers", "state": "FL", "zip": "33908"}}

    def fake_nppes(npi):
        return {"source": "nppes", "fields": {
            "phone": "239-555-9000", "street": "250 Health Park Dr",
            "city": "Fort Myers", "state": "FL", "zip": "33908"}}

    def state_board(npi):
        return {"source": "state_board", "fields": {
            "street": "250 Health Park Dr", "city": "Fort Myers", "state": "FL", "zip": "33908"}}

    print("=" * 70)
    print("  DEMO 1: address + phone confirmed by 3 sources -> auto_update")
    print("=" * 70)
    print(json.dumps(verify_record(stored, [fake_nppes, practice_site, state_board]), indent=2))

    def conflicting_nppes(npi):
        return {"source": "nppes", "fields": {"street": "900 Gulf Coast Blvd",
                                              "city": "Bonita Springs", "state": "FL", "zip": "34134"}}

    print("\n" + "=" * 70)
    print("  DEMO 2: practice site vs NPPES disagree on address -> human_review")
    print("=" * 70)
    print(json.dumps(verify_record(stored, [practice_site, conflicting_nppes]), indent=2))


# ===========================================================================
#  CLI
# ===========================================================================

def main():
    args = sys.argv[1:]
    if args and args[0] == "--demo":
        _demo()
        return

    if args:
        npi = args[0]
    else:
        # default: a sample provider from the DB
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT npi FROM providers LIMIT 1").fetchone()
        conn.close()
        if not row:
            print("No providers in the database. Run run_pipeline.py first.")
            return
        npi = row[0]

    print(f"Verifying NPI {npi} against live NPPES ...\n")
    result = verify_provider(npi)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
