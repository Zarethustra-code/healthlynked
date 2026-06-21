"""
test_validation.py
------------------
اختبارات وحدة لـ validation.py — التحقق من الـ NPI (طول + بادئة + Luhn).

التشغيل:
    python3 test_validation.py
    python3 -m unittest -v
"""

import unittest

from validation import is_valid_npi, _luhn_check_digit


def _make_valid_npi(nine):
    """بيكمّل 9 أرقام بالـ check digit الصح عشان نطلّع NPI سليم."""
    return nine + str(_luhn_check_digit(nine))


class LuhnCheckDigitTest(unittest.TestCase):

    def test_known_cms_example(self):
        # 1234567893 هو مثال CMS الرسمي → الـ check digit لأول 9 = 3
        self.assertEqual(_luhn_check_digit("123456789"), 3)

    def test_always_single_digit(self):
        for nine in ("000000000", "999999999", "111111111", "246813579"):
            self.assertIn(_luhn_check_digit(nine), range(10))


class IsValidNpiTest(unittest.TestCase):

    def test_valid_prefix_1(self):
        self.assertTrue(is_valid_npi("1234567893"))

    def test_valid_prefix_2(self):
        self.assertTrue(is_valid_npi(_make_valid_npi("234567890")))

    def test_wrong_check_digit(self):
        self.assertFalse(is_valid_npi("1234567890"))

    def test_bad_prefix_even_if_luhn_ok(self):
        # نبني رقم Luhn سليم بس بيبدأ بـ 3 → لازم يترفض على البادئة
        npi = _make_valid_npi("345678901")
        self.assertEqual(_luhn_check_digit("345678901"), int(npi[9]))  # تأكيد إنه Luhn-سليم
        self.assertFalse(is_valid_npi(npi))

    def test_too_short(self):
        self.assertFalse(is_valid_npi("123456789"))

    def test_too_long(self):
        self.assertFalse(is_valid_npi("12345678933"))

    def test_has_letters(self):
        self.assertFalse(is_valid_npi("12345abcde"))

    def test_has_symbols(self):
        self.assertFalse(is_valid_npi("123-456-789"))

    def test_empty_string(self):
        self.assertFalse(is_valid_npi(""))

    def test_none(self):
        self.assertFalse(is_valid_npi(None))

    def test_strips_surrounding_whitespace(self):
        self.assertTrue(is_valid_npi("  1234567893  "))

    def test_accepts_integer_input(self):
        self.assertTrue(is_valid_npi(1234567893))


if __name__ == "__main__":
    unittest.main(verbosity=2)
