"""
test_pipeline.py
----------------
Integration test for the pipeline without internet.

The idea:
  Instead of fetching from NPPES (network + slow + not deterministic), we seed
  the providers table by hand (this is exactly the output of the fetch step),
  and we add a second source (external_data) with differences known in advance —
  so we can verify the decision for each field exactly.

  Covers: compare -> apply_changes -> audit_log -> export_review

  This is also a regression test for the bugs that were fixed:
    - The audit_log CHECK constraint used to reject AUTO_UPDATED / FLAGGED_REVIEW
      (which broke Stage 6 entirely).
    - The updated_at trigger must fire only on an automatic update.

Running:
    python3 test_pipeline.py            # or
    python3 -m unittest -v
"""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import database
import compare
import apply_changes
import export_review
import make_second_source
import run_pipeline
import seed_sample_data
import detect
import evaluate
import evaluate_update_decisions

# Full set of columns we seed providers with (the same ones fetch_data writes)
_PROVIDER_COLS = ("npi", "name", "taxonomy_code", "specialty", "is_active",
                  "phone", "street", "unit", "city", "state", "zip")

# Values shared by all providers — any difference is injected deliberately in the second source
_COMMON = dict(
    name="Dr Test", taxonomy_code="207RC0000X", specialty="Cardiovascular Disease",
    is_active=1, phone="(212) 555-1111", street="100 Main Street", unit="",
    city="New York", state="NY", zip="10001",
)

_OLD_TS = "2000-01-01 00:00:00"   # an old date so we can confirm the trigger updates it


def _quiet(fn, *a, **k):
    """Runs a function silently (swallowing its print output)."""
    with redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class _TempDBTest(unittest.TestCase):
    """Shared base: a temporary database + pointing all modules at it."""

    # The modules that read DB_PATH as a global at run time
    _MODULES = (compare, apply_changes, export_review, make_second_source)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.db = tmp / "test.db"
        self.out_json = tmp / "review_data.json"

        _quiet(database.create_database, self.db)

        # Point every module at the temporary database, saving the original to restore it
        self._restore = []
        for mod in self._MODULES:
            self._restore.append((mod, "DB_PATH", mod.DB_PATH))
            mod.DB_PATH = self.db
        self._restore.append((export_review, "OUT_PATH", export_review.OUT_PATH))
        export_review.OUT_PATH = self.out_json

    def tearDown(self):
        for mod, attr, val in self._restore:
            setattr(mod, attr, val)
        self._tmp.cleanup()

    # --- Helper utilities ---
    def _conn(self):
        c = sqlite3.connect(self.db)
        c.row_factory = sqlite3.Row
        return c

    def _add_provider(self, npi, **overrides):
        vals = {**_COMMON, "npi": npi, **overrides}
        with self._conn() as c:
            c.execute(
                f"INSERT INTO providers ({','.join(_PROVIDER_COLS)}) "
                f"VALUES ({','.join('?' * len(_PROVIDER_COLS))})",
                tuple(vals[k] for k in _PROVIDER_COLS),
            )
            # Make the timestamps old so we can confirm the trigger fired afterwards
            c.execute(
                "UPDATE providers SET created_at=?, updated_at=? WHERE npi=?",
                (_OLD_TS, _OLD_TS, npi),
            )
            c.commit()

    def _add_external(self, npi, **overrides):
        vals = {**_COMMON, "npi": npi, "source_name": "clinic_site", **overrides}
        cols = ("npi", "source_name", "phone", "street", "unit", "city",
                "state", "zip", "specialty", "is_active")
        with self._conn() as c:
            c.execute(
                f"INSERT INTO external_data ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                tuple(vals[k] for k in cols),
            )
            c.commit()


class PipelineIntegrationTest(_TempDBTest):
    """
    Four cases designed so every decision is stable and predictable:
      P1: phone changed        -> high confidence -> AUTO_UPDATE
      P2: city changed         -> medium confidence -> NEEDS_REVIEW
      P3: status (is_active)   -> source is not authoritative -> NEEDS_REVIEW
      P4: identical to base     -> no change
    """

    def setUp(self):
        super().setUp()
        # providers
        self._add_provider("1000000001")
        self._add_provider("1000000002")
        self._add_provider("1000000003")
        self._add_provider("1000000004")
        # The second source — each row changes exactly one field, the rest matches the base
        self._add_external("1000000001", phone="(212) 555-2222")     # AUTO
        self._add_external("1000000002", city="Brooklyn")            # REVIEW
        self._add_external("1000000003", is_active=0)                # REVIEW
        self._add_external("1000000004")                            # no change
        # Run the two stages under test
        _quiet(compare.main)
        _quiet(apply_changes.main)

    def _changes_by_npi(self):
        with self._conn() as c:
            return {r["npi"]: r for r in c.execute(
                "SELECT * FROM proposed_changes").fetchall()}

    def test_compare_detects_exactly_the_seeded_changes(self):
        ch = self._changes_by_npi()
        self.assertEqual(set(ch), {"1000000001", "1000000002", "1000000003"},
                         "Exactly 3 changes expected — the identical P4 is not recorded")
        self.assertEqual(ch["1000000001"]["field"], "phone")
        self.assertEqual(ch["1000000002"]["field"], "city")
        self.assertEqual(ch["1000000003"]["field"], "is_active")

    def test_decisions_match_scoring_rules(self):
        ch = self._changes_by_npi()
        self.assertEqual(ch["1000000001"]["decision"], "AUTO_UPDATE")
        self.assertEqual(ch["1000000002"]["decision"], "NEEDS_REVIEW")
        self.assertEqual(ch["1000000003"]["decision"], "NEEDS_REVIEW")
        # Expected confidence numbers from the unified formula in confidence.py:
        #   phone (clinic=practice -> authoritative): 0.80 + 0.15*(1-0.80) = 0.83 >= bar 0.83 -> AUTO
        #   city  (authoritative too): 0.83 < bar 0.86 -> REVIEW
        #   is_active 1->0: 0.80, and a strict safety rule (deactivation) -> REVIEW always
        self.assertAlmostEqual(ch["1000000001"]["confidence"], 0.83, places=2)
        self.assertAlmostEqual(ch["1000000002"]["confidence"], 0.83, places=2)
        self.assertAlmostEqual(ch["1000000003"]["confidence"], 0.80, places=2)

    def test_every_change_has_a_reason(self):
        # Explainability: no decision without an explanation
        for r in self._changes_by_npi().values():
            self.assertTrue(r["reason"] and r["reason"].strip())

    def test_auto_update_is_written_to_providers(self):
        with self._conn() as c:
            phone = c.execute(
                "SELECT phone FROM providers WHERE npi='1000000001'").fetchone()[0]
        self.assertEqual(phone, "(212) 555-2222", "The automatic update must actually be written")

    def test_review_rows_do_not_touch_providers(self):
        with self._conn() as c:
            city = c.execute(
                "SELECT city FROM providers WHERE npi='1000000002'").fetchone()[0]
            active = c.execute(
                "SELECT is_active FROM providers WHERE npi='1000000003'").fetchone()[0]
        self.assertEqual(city, "New York", "Items flagged for review are not applied")
        self.assertEqual(active, 1)

    def test_proposed_changes_statuses(self):
        with self._conn() as c:
            rows = dict(c.execute(
                "SELECT status, COUNT(*) FROM proposed_changes GROUP BY status"))
        self.assertEqual(rows.get("applied"), 1)
        self.assertEqual(rows.get("pending_review"), 2)

    def test_audit_log_actions_regression(self):
        """
        The core regression: before the fix the CHECK constraint rejected
        AUTO_UPDATED and FLAGGED_REVIEW, so this stage used to crash.
        """
        with self._conn() as c:
            actions = dict(c.execute(
                "SELECT action, COUNT(*) FROM providers_audit_log GROUP BY action"))
        self.assertEqual(actions.get("AUTO_UPDATED"), 1)
        self.assertEqual(actions.get("FLAGGED_REVIEW"), 2)

    def test_updated_at_trigger_fires_only_on_autoupdate(self):
        with self._conn() as c:
            auto = c.execute(
                "SELECT updated_at FROM providers WHERE npi='1000000001'").fetchone()[0]
            review = c.execute(
                "SELECT updated_at FROM providers WHERE npi='1000000002'").fetchone()[0]
        self.assertNotEqual(auto, _OLD_TS, "The trigger must update updated_at for the changed row")
        self.assertEqual(review, _OLD_TS, "The unchanged row must keep its old timestamp")

    def test_export_review_json(self):
        _quiet(export_review.main)
        data = json.loads(self.out_json.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2, "Only the two flagged for review")
        self.assertEqual({d["field"] for d in data}, {"city", "is_active"})
        # Sorted by confidence descending (city=0.80 before is_active=0.53)
        self.assertEqual([d["field"] for d in data], ["city", "is_active"])
        self.assertEqual(data[0]["name"], "Dr Test")  # the provider name was pulled from providers


class SecondSourceSmokeTest(_TempDBTest):
    """Smoke test for stage 3: the second source runs and emits one row per provider."""

    def test_make_second_source_runs(self):
        for npi in ("1000000001", "1000000002", "1000000003"):
            self._add_provider(npi)
        make_second_source.random.seed(7)   # reproducible result
        _quiet(make_second_source.main)
        with self._conn() as c:
            rows = c.execute(
                "SELECT source_name FROM external_data").fetchall()
        self.assertEqual(len(rows), 3, "One row per provider")
        self.assertTrue(all(r["source_name"] == "clinic_site" for r in rows))


class IndependenceScoringTest(_TempDBTest):
    """
    Step 7 (independence) of the unified formula (confidence.py): corroboration
    increases when *independent* sources agree, and sources from the same family
    are not counted twice.

    We pick a field (specialty) for which none of these sources is authoritative,
    so we can isolate the independence effect on its own.
      - npi1: a single source (state_board)                       -> lower corroboration
      - npi2: two independent sources (state_board + practice_site) -> higher corroboration
      - npi3: a single source (nppes)                              -> baseline
      - npi4: two sources from the same family (nppes + nppes_bulk) -> same baseline (not counted twice)
        (the API and the bulk feed are the same NPPES data — not independent corroboration)
    """

    def setUp(self):
        super().setUp()
        for npi in ("1000000001", "1000000002", "1000000003", "1000000004"):
            self._add_provider(npi)
        # npi1: a single independent source
        self._add_external("1000000001", source_name="state_board",
                           specialty="Internal Medicine")
        # npi2: two independent sources (two different families)
        self._add_external("1000000002", source_name="state_board",
                           specialty="Internal Medicine")
        self._add_external("1000000002", source_name="practice_site",
                           specialty="Internal Medicine")
        # npi3: a single source
        self._add_external("1000000003", source_name="nppes",
                           specialty="Internal Medicine")
        # npi4: two sources from the same family (NPPES API + NPPES bulk = same data)
        self._add_external("1000000004", source_name="nppes",
                           specialty="Internal Medicine")
        self._add_external("1000000004", source_name="nppes_bulk",
                           specialty="Internal Medicine")
        _quiet(compare.main)

    def _rows(self):
        with self._conn() as c:
            return {r["npi"]: r for r in c.execute(
                "SELECT * FROM proposed_changes").fetchall()}

    def test_independent_corroboration_raises_confidence(self):
        rows = self._rows()
        one  = rows["1000000001"]["confidence"]   # state_board only
        two  = rows["1000000002"]["confidence"]   # state_board + practice_site
        self.assertGreater(two, one,
                           "Two agreeing independent sources must give higher confidence than a single source")

    def test_same_family_sources_do_not_double_count(self):
        rows = self._rows()
        single = rows["1000000003"]["confidence"]   # nppes only
        family = rows["1000000004"]["confidence"]   # nppes + nppes_bulk (same family)
        self.assertAlmostEqual(family, single, places=2,
                               msg="The bulk feed is not independent of the API, so it adds no corroboration")

    def test_every_change_has_an_explanation(self):
        # Explainability: every row must have a clear explanation
        for r in self._rows().values():
            self.assertTrue(r["reason"] and r["reason"].strip())


class EmptySourceGuardTest(_TempDBTest):
    """
    Data-loss protection: an empty second source must not overwrite a valid value
    in the base, but if the base is missing that field — the source may fill it in (enrichment).
    """

    def test_empty_source_does_not_overwrite_present_value(self):
        self._add_provider("1000000001")            # phone = (212) 555-1111
        self._add_external("1000000001", phone="")  # the second source has no phone
        _quiet(compare.main)
        _quiet(apply_changes.main)
        with self._conn() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM proposed_changes WHERE field='phone'").fetchone()[0]
            phone = c.execute(
                "SELECT phone FROM providers WHERE npi='1000000001'").fetchone()[0]
        self.assertEqual(n, 0, "No change is proposed when the source is empty and the base has a value")
        self.assertEqual(phone, "(212) 555-1111", "Valid data must stay as it is")

    def test_empty_base_can_still_be_filled_from_source(self):
        self._add_provider("1000000001", phone="")               # the base is missing the phone
        self._add_external("1000000001", phone="(212) 555-7777")  # the source has a value
        _quiet(compare.main)
        with self._conn() as c:
            row = c.execute(
                "SELECT new_value FROM proposed_changes WHERE field='phone'").fetchone()
        self.assertIsNotNone(row, "Filling a field missing in the base should be proposed normally")
        self.assertEqual(row[0], "(212) 555-7777")


class FailFastOnEmptyFetchTest(_TempDBTest):
    """
    Fail-safe gate: if the fetch step loaded zero providers, the pipeline must
    stop immediately instead of marching through dirty-data generation,
    comparison, scoring and review export and then printing a misleading
    "finished successfully" message.
    """

    def test_count_providers_reflects_table_contents(self):
        self.assertEqual(run_pipeline.count_providers(self.db), 0)
        self._add_provider("1000000001")
        self.assertEqual(run_pipeline.count_providers(self.db), 1)

    def test_assert_raises_on_empty_providers(self):
        # Fresh temp DB has the tables but no rows -> must refuse to continue
        with self.assertRaises(run_pipeline.PipelineError):
            run_pipeline.assert_providers_loaded(self.db)

    def test_assert_message_is_reviewer_friendly(self):
        with self.assertRaises(run_pipeline.PipelineError) as ctx:
            run_pipeline.assert_providers_loaded(self.db)
        msg = str(ctx.exception)
        self.assertIn("0 provider records", msg)
        self.assertIn("Stopping pipeline", msg)

    def test_assert_passes_and_returns_count_when_loaded(self):
        self._add_provider("1000000001")
        self._add_provider("1000000002")
        self.assertEqual(run_pipeline.assert_providers_loaded(self.db), 2)


class OfflineDemoSeedTest(_TempDBTest):
    """
    The offline demo (seed_sample_data.py -> compare -> apply_changes) must run
    with no network and prove every decision path the brief asks for.
    """

    def setUp(self):
        super().setUp()
        _quiet(seed_sample_data.seed, self.db)   # reset + load the offline sample
        _quiet(compare.main)
        _quiet(apply_changes.main)

    def _changes(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT npi, field, decision, reason, status FROM proposed_changes")]

    def test_at_least_20_providers_and_some_quarantine(self):
        with self._conn() as c:
            n_prov = c.execute("SELECT COUNT(*) FROM providers").fetchone()[0]
            n_quar = c.execute("SELECT COUNT(*) FROM providers_quarantine").fetchone()[0]
        self.assertGreaterEqual(n_prov, 20, "the sample must contain at least 20 providers")
        self.assertGreaterEqual(n_quar, 1, "invalid records must be quarantined")

    def test_both_auto_update_and_review_are_present(self):
        decisions = {c["decision"] for c in self._changes()}
        self.assertIn("AUTO_UPDATE", decisions)
        self.assertIn("NEEDS_REVIEW", decisions)

    def test_conflict_is_detected_and_held(self):
        conflicts = [c for c in self._changes() if "conflict" in (c["reason"] or "").lower()]
        self.assertTrue(conflicts, "at least one source-conflict change is expected")
        self.assertTrue(all(c["decision"] == "NEEDS_REVIEW" for c in conflicts),
                        "conflicting sources must never auto-apply")

    def test_deactivation_is_routed_to_review(self):
        with self._conn() as c:
            row = c.execute(
                "SELECT decision FROM proposed_changes WHERE field = 'is_active'").fetchone()
        self.assertIsNotNone(row, "the deactivation scenario should produce a change")
        self.assertEqual(row[0], "NEEDS_REVIEW", "deactivation is high-stakes -> always reviewed")

    def test_auto_updates_were_actually_applied(self):
        with self._conn() as c:
            applied = c.execute(
                "SELECT COUNT(*) FROM proposed_changes WHERE status = 'applied'").fetchone()[0]
            audited = c.execute(
                "SELECT COUNT(*) FROM providers_audit_log WHERE action = 'AUTO_UPDATED'").fetchone()[0]
        self.assertGreaterEqual(applied, 1)
        self.assertEqual(applied, audited, "every applied auto-update must be in the audit log")

    def test_no_change_scenarios_produce_nothing(self):
        # Providers that have an agreeing second source must not generate changes.
        with self._conn() as c:
            with_ext = {r[0] for r in c.execute("SELECT DISTINCT npi FROM external_data")}
            with_change = {r[0] for r in c.execute("SELECT DISTINCT npi FROM proposed_changes")}
        self.assertTrue(with_ext - with_change, "at least one 'no change' provider is expected")

    def test_duplicate_and_stale_detection(self):
        with self._conn() as c:
            dupes = detect.find_duplicate_providers(c)
            stale = detect.find_stale_records(c)
        self.assertGreaterEqual(len(dupes), 1, "the same provider under two NPIs must be flagged")
        self.assertGreaterEqual(len(stale), 2, "old, un-reverified records must be flagged stale")

    def test_dirty_intake_is_cleaned_before_storage(self):
        # The messy "  dr. gregory   house, md  " intake must be normalized.
        with self._conn() as c:
            row = c.execute(
                "SELECT name FROM providers WHERE npi = ?",
                (seed_sample_data.make_npi(50),)).fetchone()
        self.assertIsNotNone(row, "the messy-but-valid record should be accepted")
        self.assertEqual(row[0], "Gregory House", "titles/case/spacing must be cleaned")


class UpdateDecisionEvaluationTest(unittest.TestCase):
    """
    The update-decision harness must classify every labeled fixture correctly
    and — critically — never produce the two dangerous outcomes (a false
    auto-update, or a missed change). This is the regression guard for the
    confidence engine's decisions, separate from record-validity (evaluate.py).
    """

    def setUp(self):
        self.rows, self.metrics = evaluate_update_decisions.evaluate()

    def test_every_fixture_is_classified_correctly(self):
        wrong = [r["id"] for r in self.rows if not r["correct"]]
        self.assertEqual(wrong, [], f"misclassified fixtures: {wrong}")

    def test_no_dangerous_outcomes(self):
        self.assertEqual(self.metrics["false_auto_update"], 0,
                         "must never auto-apply a change that should be blocked/reviewed")
        self.assertEqual(self.metrics["missed_change"], 0,
                         "must never miss a real change")
        self.assertEqual(self.metrics["false_human_review"], 0,
                         "the fixtures should not be over-flagged for review")

    def test_each_expected_category_is_covered(self):
        expected = {fx["expected"] for fx in evaluate_update_decisions.FIXTURES}
        self.assertEqual(
            expected,
            {"no_change", "auto_update", "human_review", "conflict", "blocked_unsafe"})

    def test_metrics_have_real_positives(self):
        # The harness must actually exercise auto-update, review and no-change.
        self.assertGreaterEqual(self.metrics["correct_auto_update"], 1)
        self.assertGreaterEqual(self.metrics["correct_human_review"], 1)
        self.assertGreaterEqual(self.metrics["correct_no_change"], 1)

    def test_blocked_unsafe_is_high_confidence_but_held(self):
        # A blocked change must have cleared the confidence bar yet been reviewed.
        for fx in evaluate_update_decisions.FIXTURES:
            if fx["expected"] != "blocked_unsafe":
                continue
            outcome, result = evaluate_update_decisions.predict(fx)
            self.assertEqual(outcome, "human_review")
            self.assertGreaterEqual(
                result["confidence"],
                evaluate_update_decisions.confidence.required_threshold(fx["field"]),
                f"{fx['id']} should be blocked by a safety rule, not by low confidence")


class EvaluationHonestyTest(unittest.TestCase):
    """Both harnesses must carry the no-overclaim disclaimer verbatim."""

    def test_disclaimer_is_consistent(self):
        self.assertEqual(evaluate.DISCLAIMER, evaluate_update_decisions.DISCLAIMER)
        self.assertIn("manually reviewed HealthLynked sample", evaluate.DISCLAIMER)


if __name__ == "__main__":
    unittest.main(verbosity=2)
