"""
cms_source.py
-------------
Second LIVE source adapter: the CMS "Doctors and Clinicians" National
Downloadable File (dataset mj5m-pzi6), queried by NPI over the public Provider
Data Catalog API. Free, keyless, pure-stdlib HTTPS — the same Adapter contract
as the NPPES adapter in live_verify.py.

Why this is a *real* second source (not a re-publish of NPPES): the NDF's
practice locations come from Medicare enrollment (PECOS), a different collection
process than NPPES's self-reported registry. The two genuinely disagree in the
wild (a provider can update one system but not the other), so when they AGREE it
is meaningful cross-source corroboration — which is exactly why confidence.py
puts NPPES and CMS in *different* independence groups.

Fields carried: name, phone, street, city, state, zip. Specialty is omitted on
purpose — CMS's `pri_spec` ("CARDIOVASCULAR DISEASE (CARDIOLOGY)") is a
different vocabulary than NPPES's taxonomy description, so comparing them would
manufacture false "changes". Each adapter should only carry fields it can be
compared on apples-to-apples.

    python3 cms_source.py 1003076902     # look up one NPI in the live CMS NDF
"""

from __future__ import annotations

import json
import sys
from urllib.request import urlopen, Request

CMS_DATASET = "mj5m-pzi6"   # "National Downloadable File" (Doctors and Clinicians)
CMS_QUERY = (
    "https://data.cms.gov/provider-data/api/1/datastore/query/" + CMS_DATASET + "/0"
)


def _query_cms(npi, timeout=40):
    """Return the raw CMS NDF rows for an NPI (empty list if none)."""
    url = (f"{CMS_QUERY}?conditions[0][property]=npi&conditions[0][operator]=%3D"
           f"&conditions[0][value]={npi}&limit=1")
    req = Request(url, headers={
        "User-Agent": "HealthLynked-verify/1.0", "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")).get("results", [])


def cms_adapter(npi):
    """Adapter(npi) -> {"source": "cms", "fields": {...}} or None.

    Conforms to the same contract live_verify.verify_record consumes, so the
    CMS record flows through the identical confidence/corroboration/safety
    engine as every other source. A network or lookup failure returns None
    (a flaky source must never crash the verification run)."""
    try:
        rows = _query_cms(npi)
    except Exception:
        return None
    if not rows:
        return None
    r = rows[0]

    street = (r.get("adr_ln_1") or "").strip()
    line2 = (r.get("adr_ln_2") or "").strip()
    if line2 and str(r.get("ln_2_sprs", "")).strip().upper() != "Y":
        street = f"{street} {line2}".strip()

    name = f"{(r.get('provider_first_name') or '').strip()} " \
           f"{(r.get('provider_last_name') or '').strip()}".strip()

    fields = {
        "name": name,
        "phone": r.get("telephone_number") or "",
        "street": street,
        "city": r.get("citytown") or "",
        "state": r.get("state") or "",
        "zip": r.get("zip_code") or "",
    }
    carried = {k: v for k, v in fields.items() if v}
    if not carried:                       # all-blank row → treat like no match
        return None
    return {"source": "cms", "fields": carried}


if __name__ == "__main__":
    npi = sys.argv[1] if len(sys.argv) > 1 else "1003076902"
    print(f"Looking up NPI {npi} in the live CMS National Downloadable File ...\n")
    rec = cms_adapter(npi)
    print(json.dumps(rec, indent=2) if rec else "(not found in CMS NDF, or source unreachable)")
