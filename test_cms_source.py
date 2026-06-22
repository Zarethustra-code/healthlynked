"""
test_cms_source.py
------------------
Tests for the CMS National Downloadable File adapter — fully offline. The live
HTTP call (`_query_cms`) is monkeypatched, so no network is required.

Covers:
  * CMS columns map to our field space (and specialty is intentionally dropped),
  * a flaky/empty source degrades to None instead of crashing,
  * the independence fix is real: CMS + NPPES (now distinct groups) corroborate
    a changed phone -> auto_update.
"""

from __future__ import annotations

import unittest

import cms_source
import live_verify

# A representative raw CMS NDF row (same shape the live API returns).
SAMPLE = {
    "npi": "1003076902", "provider_first_name": "ANURADHA",
    "provider_last_name": "LALA-TRINDADE", "telephone_number": "2127317822",
    "adr_ln_1": "1 GUSTAVE L LEVY PL", "adr_ln_2": "", "ln_2_sprs": "",
    "citytown": "NEW YORK", "state": "NY", "zip_code": "100296504",
    "pri_spec": "CARDIOVASCULAR DISEASE (CARDIOLOGY)",
}


class CmsAdapterTest(unittest.TestCase):
    def setUp(self):
        self._orig = cms_source._query_cms

    def tearDown(self):
        cms_source._query_cms = self._orig

    def test_maps_fields(self):
        cms_source._query_cms = lambda npi, timeout=40: [SAMPLE]
        rec = cms_source.cms_adapter("1003076902")
        self.assertEqual(rec["source"], "cms")
        self.assertEqual(rec["fields"]["phone"], "2127317822")
        self.assertEqual(rec["fields"]["city"], "NEW YORK")
        self.assertEqual(rec["fields"]["name"], "ANURADHA LALA-TRINDADE")
        # specialty is intentionally NOT carried (different vocabulary than NPPES)
        self.assertNotIn("specialty", rec["fields"])

    def test_empty_returns_none(self):
        cms_source._query_cms = lambda npi, timeout=40: []
        self.assertIsNone(cms_source.cms_adapter("0000000000"))

    def test_network_error_returns_none(self):
        def boom(npi, timeout=40):
            raise RuntimeError("CMS unreachable")
        cms_source._query_cms = boom
        self.assertIsNone(cms_source.cms_adapter("0000000000"))


class IndependentCorroborationTest(unittest.TestCase):
    """CMS and NPPES are now independent groups, so agreement is corroboration."""

    def setUp(self):
        self._orig = cms_source._query_cms
        # CMS row matches the stored address, differing ONLY on phone, so the
        # phone is the lone change and the record's verdict is unambiguous.
        cms_source._query_cms = lambda npi, timeout=40: [dict(
            SAMPLE, telephone_number="2125559000", adr_ln_1="1 Gustave L Levy Pl",
            citytown="New York", state="NY", zip_code="10029")]

    def tearDown(self):
        cms_source._query_cms = self._orig

    def test_cms_plus_nppes_corroborate_to_auto_update(self):
        stored = {
            "provider_id": "X", "npi": "1", "name": "Anuradha Lala-Trindade",
            "specialty": "Cardiovascular Disease", "phone": "(212) 731-7822",
            "street": "1 Gustave L Levy Pl", "city": "New York",
            "state": "NY", "zip": "10029", "is_active": 1,
        }

        def nppes(npi):   # independently reports the SAME new phone
            return {"source": "nppes", "fields": {"phone": "212-555-9000"}}

        res = live_verify.verify_record(stored, [cms_source.cms_adapter, nppes])
        phone = next(c for c in res["changes"] if c["field"] == "phone")
        self.assertEqual(len(phone["supporting_sources"]), 2)        # CMS + NPPES
        self.assertGreaterEqual(phone["confidence_score"], 0.90)
        self.assertEqual(res["recommended_action"], "auto_update")   # corroborated


if __name__ == "__main__":
    unittest.main(verbosity=2)
