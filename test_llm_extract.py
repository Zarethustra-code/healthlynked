"""
test_llm_extract.py
-------------------
Tests for the LLM source adapter — fully offline (no network, no API key).

Covers:
  * structured extraction returns the expected fields,
  * the adapter conforms to the Adapter contract used by live_verify,
  * the LLM's output, once corroborated by a second independent source, flows
    through the real confidence engine to an auto_update — and single-source
    fields are held for review (the corroboration guarantee end to end).
"""

from __future__ import annotations

import unittest

import llm_extract
import live_verify


class ExtractionTest(unittest.TestCase):
    def test_offline_extracts_all_fields(self):
        fields = llm_extract.extract_provider_fields(llm_extract.DEMO_PAGE, offline=True)
        self.assertIsNotNone(fields)
        for f in ("name", "phone", "street", "city", "state", "zip", "specialty"):
            self.assertTrue(fields.get(f), f"missing/empty field: {f}")
        self.assertEqual(fields["phone"], "(239) 555-9000")
        self.assertEqual(fields["_mode"], "offline-canned")

    def test_unknown_text_offline_returns_none(self):
        # No key + text we have no canned answer for -> graceful None.
        self.assertIsNone(
            llm_extract.extract_provider_fields("some other page", offline=True))

    def test_adapter_shape(self):
        adapter = llm_extract.practice_site_adapter(llm_extract.DEMO_PAGE, offline=True)
        rec = adapter("1234567890")
        self.assertEqual(rec["source"], "practice_site")
        self.assertIn("phone", rec["fields"])
        self.assertNotIn("_mode", rec["fields"])   # internal flag not leaked as a field


class IntegrationTest(unittest.TestCase):
    """LLM extraction -> confidence engine, with a corroborating source."""

    def setUp(self):
        self.stored = {
            "provider_id": "HL_001", "npi": "1234567890",
            "name": "John Smith", "specialty": "Cardiovascular Disease",
            "phone": "(239) 555-1234", "street": "100 Main St",
            "city": "Naples", "state": "FL", "zip": "34102", "is_active": 1,
        }
        llm_adapter = llm_extract.practice_site_adapter(llm_extract.DEMO_PAGE, offline=True)

        def nppes(npi):   # independent corroboration on the phone only
            return {"source": "nppes", "fields": {"phone": "239-555-9000"}}

        self.result = live_verify.verify_record(self.stored, [llm_adapter, nppes])
        self.changes = {c["field"]: c for c in self.result["changes"]}

    def test_corroborated_phone_auto_updates(self):
        # phone is reported by BOTH practice site (LLM) and NPPES -> corroborated.
        self.assertIn("phone", self.changes)
        self.assertEqual(len(self.changes["phone"]["supporting_sources"]), 2)
        self.assertGreaterEqual(self.changes["phone"]["confidence_score"], 0.90)

    def test_single_source_address_is_not_auto(self):
        # street/city/zip come only from the LLM -> below bar -> not auto-applied,
        # so the whole record is routed to human review.
        self.assertEqual(self.result["recommended_action"], "human_review")
        self.assertIn("street", self.changes)

    def test_state_format_difference_is_not_a_change(self):
        # LLM returns "Florida"; stored is "FL" -> normalized equal -> no change.
        self.assertNotIn("state", self.changes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
