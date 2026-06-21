"""
test_normalize.py
-----------------
اختبارات وحدة لـ normalize.py — الاسم / التخصص / التليفون / العنوان.

التشغيل:
    python3 test_normalize.py
    python3 -m unittest -v
"""

import unittest

from normalize import (
    normalize_name, normalize_specialty, normalize_phone, normalize_address,
)


class NormalizeNameTest(unittest.TestCase):

    def test_none_returns_empty(self):
        self.assertEqual(normalize_name(None), {"display": "", "compare": ""})

    def test_blank_returns_empty(self):
        self.assertEqual(normalize_name("   "), {"display": "", "compare": ""})

    def test_basic_titlecase(self):
        r = normalize_name("ahmed hassan")
        self.assertEqual(r["display"], "Ahmed Hassan")
        self.assertEqual(r["compare"], "ahmed hassan")

    def test_uppercase_with_dots(self):
        r = normalize_name("AHMED M. HASSAN")
        self.assertEqual(r["display"], "Ahmed M Hassan")
        self.assertEqual(r["compare"], "ahmed m hassan")

    def test_strips_leading_title(self):
        self.assertEqual(normalize_name("Dr. Sara Ali")["display"], "Sara Ali")

    def test_strips_prof_title(self):
        self.assertEqual(normalize_name("Prof. Sara Ali")["display"], "Sara Ali")

    def test_comma_suffix_md_still_clean(self):
        # "Sara Ali, MD" — الفاصلة بتتعامل كـ Last,First بس MD لقب فبيتشال
        self.assertEqual(normalize_name("Sara Ali, MD")["display"], "Sara Ali")

    def test_last_first_flip(self):
        self.assertEqual(normalize_name("Hassan, Ahmed")["display"], "Ahmed Hassan")

    def test_apostrophe_preserved_in_display(self):
        r = normalize_name("Dr. John O'Connor")
        self.assertEqual(r["display"], "John O'Connor")   # مش "O'connor"
        self.assertEqual(r["compare"], "john oconnor")

    def test_hyphen_preserved_in_display(self):
        r = normalize_name("Sara Al-Hassan")
        self.assertEqual(r["display"], "Sara Al-Hassan")  # مش "Al-hassan"
        self.assertEqual(r["compare"], "sara alhassan")


class NormalizeSpecialtyTest(unittest.TestCase):

    def test_code_uppercased(self):
        self.assertEqual(
            normalize_specialty("207rc0000x", "Cardiovascular Disease")["code"],
            "207RC0000X",
        )

    def test_desc_display_and_compare(self):
        r = normalize_specialty("x", "General  Practice")   # مسافة مزدوجة
        self.assertEqual(r["display"], "General Practice")
        self.assertEqual(r["compare"], "general practice")

    def test_none_inputs(self):
        self.assertEqual(
            normalize_specialty(None, None),
            {"code": "", "display": "", "compare": ""},
        )


class NormalizePhoneTest(unittest.TestCase):

    def test_formats_ten_digits(self):
        self.assertEqual(
            normalize_phone("2125551234"),
            {"compare": "2125551234", "display": "(212) 555-1234"},
        )

    def test_strips_punctuation(self):
        self.assertEqual(normalize_phone("(212) 555-1234")["compare"], "2125551234")

    def test_drops_leading_country_code(self):
        r = normalize_phone("1-212-555-1234")
        self.assertEqual(r["compare"], "2125551234")
        self.assertEqual(r["display"], "(212) 555-1234")

    def test_plus_one_prefix(self):
        self.assertEqual(normalize_phone("+1 (212) 555-1234")["compare"], "2125551234")

    def test_too_few_digits_is_invalid(self):
        self.assertEqual(normalize_phone("555-1234"), {"compare": "", "display": ""})

    def test_none(self):
        self.assertEqual(normalize_phone(None), {"compare": "", "display": ""})

    def test_empty(self):
        self.assertEqual(normalize_phone(""), {"compare": "", "display": ""})


class NormalizeAddressTest(unittest.TestCase):

    def test_expands_abbreviations(self):
        r = normalize_address("456 N Park Ave", "New York", "NY", "10001")
        self.assertEqual(r["street"], "456 North Park Avenue")
        self.assertEqual(r["state"], "NY")
        self.assertEqual(r["zip"], "10001")

    def test_separates_suite_after_comma(self):
        r = normalize_address("456 N Park Ave, Suite 200", "New York", "NY", "10001")
        self.assertEqual(r["street"], "456 North Park Avenue")
        self.assertEqual(r["unit"], "200")

    def test_separates_inline_hash_unit(self):
        r = normalize_address("123 Main St #5", "Brooklyn", "ny", "11201")
        self.assertEqual(r["street"], "123 Main Street")
        self.assertEqual(r["unit"], "5")
        self.assertEqual(r["city"], "Brooklyn")
        self.assertEqual(r["state"], "NY")

    def test_zip_plus_four_truncated_to_five(self):
        self.assertEqual(
            normalize_address("1 Rd", "X", "NY", "10001-1234")["zip"], "10001")

    def test_compare_pipe_format(self):
        r = normalize_address("123 main st", "new york", "ny", "10001")
        self.assertEqual(r["compare"], "123 main street||new york|ny|10001")

    def test_empty_inputs(self):
        r = normalize_address("", "", "", "")
        self.assertEqual(r["street"], "")
        self.assertEqual(r["unit"], "")
        self.assertEqual(r["zip"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
