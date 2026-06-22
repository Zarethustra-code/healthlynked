"""
llm_extract.py
--------------
The LLM as a "scalpel for ambiguity": pull a provider's directory facts out of a
practice website's FREE TEXT and feed them into the SAME confidence engine as
any other source. This is the one place an LLM earns its cost — turning
unstructured prose into structured fields — and it is wired as an ordinary
source Adapter, so its output is corroborated against NPPES and gated by the
safe-update rules exactly like every other source. The LLM never decides; it
only proposes a candidate that the deterministic engine then scores.

Cost discipline (see cost_estimate.py): the cheapest model (Haiku 4.5), a
fixed/cacheable instruction prompt, and structured outputs so the result is
guaranteed-valid JSON (no parsing/repair tokens). Most records never reach here
— only the hard fraction that has a practice page worth reading. At scale these
calls are submitted via the Batch API (-50%).

Implementation note: this calls the Anthropic Messages API over raw HTTPS with
urllib — NO SDK — to preserve the project's zero-dependency, pure-stdlib
property (the same pattern fetch_data.py uses for NPPES). Set ANTHROPIC_API_KEY
to run it live; with no key (or --offline) a clearly-labeled canned response is
used so the flow runs anywhere, including for reviewers without a key.

    python3 llm_extract.py            # extract from a bundled practice page
    python3 llm_extract.py --live     # force a live API call (needs the key)
    python3 llm_extract.py --pipeline # LLM extraction -> corroborate -> decide
"""

from __future__ import annotations

import json
import os
import sys
from urllib.request import urlopen, Request

API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL = "claude-haiku-4-5"        # cheapest tier; the LLM is a last resort, not the default

# Fixed instructions — kept byte-stable so the shared prefix is cache-friendly
# at scale (few-shot examples + this schema would be the cached prefix).
SYSTEM = (
    "You extract ONE healthcare provider's directory facts from the raw text of "
    "a practice web page. Copy values exactly as written; do not infer, guess, "
    "or normalize. If a field is not present in the text, return an empty string."
)

# Structured-output schema: the model must return exactly these string fields.
# (Kept to plain strings + additionalProperties:false — the well-supported
# subset of structured-output schemas.)
_FIELDS = ["name", "phone", "street", "city", "state", "zip", "specialty"]
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {f: {"type": "string"} for f in _FIELDS},
    "required": _FIELDS,
}


# ===========================================================================
#  Live Messages API call (raw HTTPS, no SDK)
# ===========================================================================

def _call_anthropic(page_text, model=MODEL, max_tokens=400):
    """Call the live Messages API with structured outputs. Returns a dict of the
    extracted fields, or None if no API key is configured."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return None

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": SYSTEM,
        "messages": [{
            "role": "user",
            "content": f"Practice page text:\n\n{page_text}\n\n"
                       f"Extract the provider's directory fields.",
        }],
        # Guarantees the response is valid JSON matching SCHEMA.
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
    }
    req = Request(
        API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    # With output_config.format the first text block is guaranteed-valid JSON.
    for block in data.get("content", []):
        if block.get("type") == "text":
            out = json.loads(block["text"])
            out["_mode"] = "live"
            return out
    return None


# ===========================================================================
#  Bundled offline demo (so the flow runs with no API key)
# ===========================================================================

# A realistic, messy "Contact Us" blurb — exactly the unstructured input a
# regex would struggle with and an LLM handles cleanly.
DEMO_PAGE = (
    "Welcome to ABC Heart Group! Our cardiology practice recently moved. "
    "You can now find Dr. John Smith and the team at our new home: "
    "250 Health Park Drive, Suite 300, Fort Myers, Florida 33908. "
    "Appointments: call the front desk at (239) 555-9000. "
    "We specialize in Cardiovascular Disease and look forward to seeing you."
)

# What Haiku returns for DEMO_PAGE (captured shape; used when no key is set).
_DEMO_CANNED = {
    "name": "Dr. John Smith", "phone": "(239) 555-9000",
    "street": "250 Health Park Drive, Suite 300", "city": "Fort Myers",
    "state": "Florida", "zip": "33908", "specialty": "Cardiovascular Disease",
    "_mode": "offline-canned",
}


def extract_provider_fields(page_text, offline=False, model=MODEL):
    """Extract provider fields from free text.

    Live (ANTHROPIC_API_KEY set, offline=False) -> real Haiku call.
    Otherwise -> the bundled canned response for DEMO_PAGE, else None.
    """
    if not offline:
        live = _call_anthropic(page_text, model=model)
        if live is not None:
            return live
        # A key was present but the live call returned nothing -> a real
        # failure. Don't mask it with the canned demo answer.
        if os.environ.get("ANTHROPIC_API_KEY"):
            return None
    # Offline / no-key path.
    if page_text == DEMO_PAGE:
        return dict(_DEMO_CANNED)
    return None


# ===========================================================================
#  Source Adapter — plugs LLM extraction into live_verify.py / confidence.py
# ===========================================================================

def practice_site_adapter(page_text, offline=False):
    """Build an Adapter(npi) -> {source, fields} that extracts `page_text` via
    the LLM. The result is consumed by live_verify.verify_record exactly like
    the NPPES adapter — so the LLM's output is corroborated and safety-gated."""
    def adapter(npi):
        fields = extract_provider_fields(page_text, offline=offline)
        if not fields:
            return None
        carried = {f: fields[f] for f in _FIELDS if fields.get(f)}
        return {"source": "practice_site", "fields": carried}
    return adapter


# ===========================================================================
#  CLI / demos
# ===========================================================================

def _print_extraction(offline):
    fields = extract_provider_fields(DEMO_PAGE, offline=offline)
    print("=" * 64)
    print("  LLM field extraction from a practice web page")
    print("=" * 64)
    print("INPUT (free text):\n  " + DEMO_PAGE)
    print("-" * 64)
    print(f"EXTRACTED ({fields.get('_mode') if fields else 'none'}):")
    print(json.dumps({k: v for k, v in (fields or {}).items() if k != "_mode"}, indent=2))
    print("=" * 64)


def _pipeline_demo(offline):
    """Show the LLM output flowing through the real confidence engine: the
    practice site (LLM-extracted) and NPPES agree on a new phone, so the change
    is corroborated and auto-applies; the address is owner-backed but
    single-source, so its confidence (~0.79) falls just under the address
    auto-update bar (0.86) and is held for human review."""
    import live_verify

    stored = {
        "provider_id": "HL_001", "npi": "1234567890",
        "name": "John Smith", "specialty": "Cardiovascular Disease",
        "phone": "(239) 555-1234", "street": "100 Main St",
        "city": "Naples", "state": "FL", "zip": "34102", "is_active": 1,
    }

    llm_adapter = practice_site_adapter(DEMO_PAGE, offline=offline)

    def nppes(npi):  # independent corroboration on the phone
        return {"source": "nppes", "fields": {"phone": "239-555-9000"}}

    result = live_verify.verify_record(stored, [llm_adapter, nppes])
    print("=" * 64)
    print("  LLM extraction -> corroborate (NPPES) -> confidence decision")
    print("=" * 64)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def main():
    args = sys.argv[1:]
    offline = "--offline" in args or not os.environ.get("ANTHROPIC_API_KEY")
    if "--live" in args:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("ANTHROPIC_API_KEY is not set — cannot run --live.")
            return
        offline = False

    if "--pipeline" in args:
        _pipeline_demo(offline)
    else:
        _print_extraction(offline)
        if offline:
            print("(offline canned response — set ANTHROPIC_API_KEY for a live Haiku call)")


if __name__ == "__main__":
    main()
