"""
normalize.py
------------
Data normalization hub (Normalization Layer).

Difference from validation.py:
  validation  →  answers "valid or not?"   (returns True/False)
  normalize   →  converts to a unified form (returns cleaned text)

Each function returns two versions:
  display  →  a nice form for display and storage    (Title Case)
  compare  →  a unified form for internal comparison (lowercase, titles stripped)

Ready now:
  ✅ normalize_name()

To be added later:
  ⬜ normalize_phone()
  ⬜ normalize_address()
"""

import re


# ===========================================================================
#  NAME  ✅
# ===========================================================================

# Titles we strip from the comparison version (lowercase for matching)
_TITLES = {"dr", "doc", "doctor", "prof", "mr", "mrs", "ms",
           "md", "phd", "do", "rn", "np"}


def _cap_name_word(word):
    """
    Like .capitalize() but capitalizes the first letter of each segment split by ' or -
    so that "O'Connor" stays "O'Connor" instead of "O'connor", and "Al-Hassan" stays correct.
    (The regular capitalize lowercases everything after the first letter.)
    """
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), word)


def normalize_name(name):
    """
    Takes a raw name and returns a dict with two versions:
        {
            "display": "Ahmed M Hassan",   ← for display and storage
            "compare": "ahmed m hassan"    ← for internal comparison
        }

    Cleaning steps:
      0. If the name is reversed with a comma ("Hassan, Ahmed") flip it to "Ahmed Hassan".
      1. Strip dots and turn them into spaces (so "St.John" doesn't become "stjohn").
      2. Collapse extra spaces.
      3. Strip titles (Dr, MD ...).
      4. display = Title Case (keeping O'Connor nice)
         compare = lowercase + merge apostrophes and hyphens (oconnor).
    """
    if name is None:
        return {"display": "", "compare": ""}

    text = str(name).strip()

    # 0) Reversed name: if there is exactly one comma ("Hassan, Ahmed")
    #    flip the order → "Ahmed Hassan" before any other cleaning.
    if text.count(",") == 1:
        last, first = text.split(",")
        text = f"{first.strip()} {last.strip()}"

    # 1) Strip dots and turn them into spaces (for names like "St.John")
    #    Note: we don't touch ' or - here — we handle them per version.
    text = re.sub(r"\.", " ", text)

    # 2) Collapse spaces: any multiple spaces become a single space
    text = re.sub(r"\s+", " ", text).strip()

    # 3) Split into words and strip titles
    words = [w for w in text.split(" ") if w and w.lower().strip("'-") not in _TITLES]

    # --- Display version: Title Case while keeping the uppercase letter after ' and - (O'Connor, Al-Hassan) ---
    display = " ".join(_cap_name_word(w) for w in words)

    # --- Compare version: lowercase + merge apostrophes and hyphens ---
    #     "O'Connor" → "oconnor"  |  "Al-Hassan" → "alhassan"
    compare = " ".join(words).lower()
    compare = re.sub(r"['\-]", "", compare)

    return {"display": display, "compare": compare}


# ===========================================================================
#  SPECIALTY  ✅
# ===========================================================================

def normalize_specialty(code, desc):
    """
    Takes the specialty code and its description, and returns a cleaned dict:
        {
            "code":    "1223G0001X",       ← the code (unified, uppercase)
            "display": "General Practice",  ← the description for display
            "compare": "general practice"   ← the description for comparison
        }

    Note: the code is like the NPI — a fixed identifier, so we only unify its
    form (uppercase) without changing its content.
    """
    # The code: strip spaces and make it uppercase (UPPER) for unification
    code = str(code).strip().upper() if code else ""

    # The description: same logic as name cleaning (unified spaces)
    desc = str(desc).strip() if desc else ""
    desc = re.sub(r"\s+", " ", desc)

    return {
        "code": code,
        "display": desc,
        "compare": desc.lower(),
    }


# ===========================================================================
#  PHONE  ✅
# ===========================================================================

def normalize_phone(phone):
    """
    Takes a raw phone number and returns a dict with two versions:
        {
            "compare": "2125551234",       ← digits only (for comparison)
            "display": "(212) 555-1234"    ← nice US format (for display)
        }

    Cleaning steps:
      1. Strip anything that isn't a digit (dashes, parentheses, spaces, +).
      2. If 11 digits and starts with 1 (US country code) strip the 1.
      3. If not 10 digits in the end → invalid number, return empty.
    """
    if phone is None:
        return {"compare": "", "display": ""}

    # 1) Keep digits only
    digits = re.sub(r"\D", "", str(phone))

    # 2) If 11 digits and starts with 1, strip the 1
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    # 3) Must be 10 digits, otherwise invalid
    if len(digits) != 10:
        return {"compare": "", "display": ""}

    display = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return {"compare": digits, "display": display}


# ===========================================================================
#  ADDRESS  ✅  (deep version: expand abbreviations + split out the unit)
# ===========================================================================

# Dictionary unifying street abbreviations → full form (all lowercase for matching)
_STREET_ABBR = {
    "st": "street", "st.": "street",
    "ave": "avenue", "ave.": "avenue", "av": "avenue",
    "blvd": "boulevard", "blvd.": "boulevard",
    "rd": "road", "rd.": "road",
    "dr": "drive", "dr.": "drive",
    "ln": "lane", "ln.": "lane",
    "ct": "court", "ct.": "court",
    "pl": "place", "pl.": "place",
    "sq": "square", "sq.": "square",
    "ter": "terrace", "ter.": "terrace",
    "pkwy": "parkway", "pkwy.": "parkway",
    "hwy": "highway", "hwy.": "highway",
    "n": "north", "n.": "north",
    "s": "south", "s.": "south",
    "e": "east", "e.": "east",
    "w": "west", "w.": "west",
    "ne": "northeast", "nw": "northwest",
    "se": "southeast", "sw": "southwest",
}

# Words that indicate a unit/suite number (we split them into their own field)
_UNIT_WORDS = {"suite", "ste", "ste.", "unit", "apt", "apt.",
               "apartment", "fl", "fl.", "floor", "rm", "room", "#"}


def _expand_token(token):
    """Expands a single word's abbreviation if present in the dictionary, otherwise leaves it."""
    key = token.lower()
    return _STREET_ABBR.get(key, key)


def _smart_title(text):
    """
    Like .title() but keeps numeric suffixes correct:
        "42nd" stays "42nd" not "42Nd"
        "main street" → "Main Street"
    """
    out = []
    for word in text.split():
        # If the word starts with a digit (like 42nd, 3rd) keep it lowercase as is
        if word and word[0].isdigit():
            out.append(word.lower())
        else:
            out.append(word.capitalize())
    return " ".join(out)


def normalize_address(street, city, state, postal):
    """
    Deep version: expands street abbreviations and splits out the unit/suite number.

    Returns:
        {
            "street":  "456 North Park Avenue",  ← display (clean + abbreviations expanded)
            "unit":    "200",                     ← unit number split out
            "city":    "New York",
            "state":   "NY",
            "zip":     "10001",
            "compare": "456 north park avenue|200|new york|ny|10001"
        }
    """
    raw = re.sub(r"\s+", " ", str(street or "").strip())
    # Put a space around # so "#200" is treated like "Suite 200"
    raw = re.sub(r"#", " # ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    # 1) Split out the unit number (suite/apartment/floor)
    unit = ""
    # Split on the first comma (usually "123 Main St, Suite 200")
    parts = [p.strip() for p in raw.split(",")]
    street_part = parts[0] if parts else ""

    # Look through the remaining parts for a unit word
    for p in parts[1:]:
        words = p.split()
        if words and words[0].lower().strip(".#") in {w.strip(".#") for w in _UNIT_WORDS}:
            # Take the numbers that come after the unit word
            nums = re.findall(r"\w+", p)
            unit = nums[-1] if nums else ""

    # If the suite is inside the street itself (no comma), look for it
    if not unit:
        tokens = street_part.split()
        for i, tok in enumerate(tokens):
            if tok.lower().strip(".#") in {w.strip(".#") for w in _UNIT_WORDS} or tok == "#":
                # The one after it is the unit number
                if i + 1 < len(tokens):
                    unit = tokens[i + 1].strip("#")
                # Strip the unit word and what follows it from the street
                street_part = " ".join(tokens[:i])
                break

    # 2) Expand street abbreviations (each word)
    expanded = [_expand_token(t.strip(",")) for t in street_part.split()]
    street_compare = " ".join(expanded).strip()
    street_disp = _smart_title(street_compare)

    # 3) Remaining parts
    city = re.sub(r"\s+", " ", str(city or "").strip())
    city_disp = city.title()
    state_disp = str(state or "").strip().upper()
    zip_digits = re.sub(r"\D", "", str(postal or ""))[:5]

    # 4) Unified compare version (the state is unified to a two-letter code so "Florida" == "FL")
    compare = "|".join([
        street_compare,
        unit,
        city.lower(),
        normalize_state(state).lower(),
        zip_digits,
    ])

    return {
        "street": street_disp,
        "unit": unit,
        "city": city_disp,
        "state": state_disp,
        "zip": zip_digits,
        "compare": compare,
    }


# ===========================================================================
#  STATE  ✅  (full name or abbreviation -> USPS 2-letter)
# ===========================================================================

_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


def normalize_state(value):
    """Return the USPS 2-letter code (UPPER). Accepts 'FL', 'fl', or 'Florida'.

    This is what stops a source that writes 'Florida' from looking like a change
    away from a stored 'FL' — same state, different spelling.
    """
    s = ("" if value is None else str(value)).strip()
    if not s:
        return ""
    if len(s) == 2:
        return s.upper()
    return _US_STATES.get(s.lower(), s.upper())


# ===========================================================================
#  FIELD DISPATCH — canonical compare/display form keyed by column name
# ===========================================================================
#  One place that maps a provider column -> its normalized comparison key and
#  its storable display value. Used by both the batch comparator (compare.py)
#  and the live single-record verifier (live_verify.py) so the two never drift.

def field_compare_form(field, value):
    """Canonical comparison key for a given provider column.

    Note: only None / blank string is "empty". The integer 0 (e.g. is_active=0,
    a deactivation) is a real value and must survive normalization.
    """
    if field == "phone":
        return normalize_phone(value)["compare"]
    if field == "street":
        return normalize_address(value, "", "", "")["compare"].split("|")[0]
    if field == "specialty":
        return normalize_specialty("", value)["compare"]
    if field == "name":
        return normalize_name(value)["compare"]
    if field == "state":
        return normalize_state(value).lower()
    if field == "zip":
        return re.sub(r"\D", "", "" if value is None else str(value))[:5]
    return ("" if value is None else str(value)).strip().lower()


def field_display_form(field, value):
    """Storable/displayable value for a given provider column."""
    plain = "" if value is None else str(value)
    if field == "phone":
        return normalize_phone(value)["display"] or plain
    if field == "name":
        return normalize_name(value)["display"] or plain
    if field == "specialty":
        return normalize_specialty("", value)["display"] or plain
    if field == "state":
        return normalize_state(value) or plain
    return plain


# ---------------------------------------------------------------------------
# Quick tests — run the file directly to see the result
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    name_tests = [
        "  Dr. AHMED   m. hassan, MD  ",
        "ahmed hassan",
        "AHMED M. HASSAN",
        "Prof. Sara Ali",
        "Hassan, Ahmed",
        "Dr. John O'Connor",
        "Sara Al-Hassan",
        "St.John Medical",
        "   ",
        None,
    ]

    print("=" * 70)
    print("  normalize_name test")
    print("=" * 70)
    for raw in name_tests:
        result = normalize_name(raw)
        print(f"Input  : {repr(raw)}")
        print(f"  display: {repr(result['display'])}")
        print(f"  compare: {repr(result['compare'])}")
        print("-" * 70)
