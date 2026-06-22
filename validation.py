"""
validation.py
-------------
Data validation hub (Validation Layer).

Any data entering the database passes through here first.
Each column has its own dedicated validation function.

Ready now:
  ✅ is_valid_npi()      — NPI validation

To be added later:
  ⬜ is_valid_phone()
  ⬜ is_valid_address()
  ⬜ is_valid_name()
"""


# ===========================================================================
#  NPI  ✅
# ===========================================================================

def _luhn_check_digit(nine_digits: str) -> int:
    """
    Computes the check digit for the first 9 digits of the NPI using the Luhn formula.
    (Internal helper function — the leading _ means it's for internal use only)

    Steps (exactly like the CMS document):
      1. Start from the right, multiply digits in odd positions by 2.
      2. If the result is two digits (like 14) sum its digits (1+4).
      3. Sum everything + the constant 24.
      4. Round up to the nearest number ending in zero and subtract → the check digit.
    """
    total = 24  # The constant representing the prefix 80840
    digits = [int(d) for d in nine_digits]

    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:                # Odd positions from the right → × 2
            doubled = d * 2
            total += doubled // 10 + doubled % 10
        else:                          # Even positions → as is
            total += d

    return (10 - (total % 10)) % 10


def is_valid_npi(npi) -> bool:
    """Returns True if the NPI is valid, and False if invalid."""
    npi = str(npi).strip()

    # 1) Must be exactly 10 digits (digits only)
    if not npi.isdigit() or len(npi) != 10:
        return False

    # 2) First digit must be 1 or 2
    if npi[0] not in ("1", "2"):
        return False

    # 3) The check digit must match the computation
    return _luhn_check_digit(npi[:9]) == int(npi[9])


# ===========================================================================
#  PHONE  ⬜  (we'll build it later)
# ===========================================================================
# def is_valid_phone(phone) -> bool:
#     ...


# ===========================================================================
#  ADDRESS  ⬜  (we'll build it later)
# ===========================================================================
# def is_valid_address(address) -> bool:
#     ...


# ===========================================================================
#  NAME  ⬜  (we'll build it later)
# ===========================================================================
# def is_valid_name(name) -> bool:
#     ...


# ---------------------------------------------------------------------------
# Quick tests — run the file directly to see the result
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    npi_tests = [
        ("1234567893", True,  "Official CMS example — valid"),
        ("1234567890", False, "wrong check digit"),
        ("3456789012", False, "starts with 3 (not 1 or 2)"),
        ("123456789",  False, "only 9 digits"),
        ("12345678933", False, "11 digits"),
        ("12345abcde",  False, "contains letters"),
    ]

    print("=" * 55)
    print("  Testing is_valid_npi")
    print("=" * 55)
    for npi, expected, note in npi_tests:
        result = is_valid_npi(npi)
        mark = "✅" if result == expected else "❌ (unexpected!)"
        status = "valid" if result else "rejected"
        print(f"{mark}  {npi:<13} → {status:<7} | {note}")
    print("=" * 55)
