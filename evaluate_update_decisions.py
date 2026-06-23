"""
evaluate_update_decisions.py
----------------------------
UPDATE-DECISION accuracy (MVP) — measures whether the confidence engine makes
the RIGHT call on a proposed provider change, not merely whether a record is
well-formed (that is evaluate.py's job).

It runs a small, hand-labeled fixture set through the real scoring engine
(`confidence.score_field`, the same code compare.py and live_verify.py use) and
checks the engine's decision against the expected label for each of the five
behaviors the pipeline must get right:

    no_change       — every source agrees with what we already store
    auto_update     — a corroborated/authoritative change clears the safety bar
    human_review    — a real change that does NOT clear the bar
    conflict        — two sources disagree on the new value (never auto-applied)
    blocked_unsafe  — a HIGH-confidence change a hard safety rule still holds
                      for review (deactivation, name/identity change)

Reported metrics (the ones that matter operationally):
    correct_auto_update   — auto-applied a change that should auto-apply
    false_auto_update     — auto-applied something that should NOT have  (dangerous)
    correct_human_review  — routed to a human a change that needed a human
    false_human_review    — routed to a human a change that was fine     (wasteful)
    missed_change         — saw a real change but proposed nothing       (dangerous)
    correct_no_change     — correctly left an already-correct record alone

HONEST SCOPE: these are SYNTHETIC labels chosen to exercise the decision logic.
Passing here proves the engine behaves as designed on the fixtures and guards
against regressions — it does NOT prove real-world accuracy. Real numbers
require a manually reviewed HealthLynked sample (see PROPOSAL.md §12).

    python3 evaluate_update_decisions.py
"""

from __future__ import annotations

from collections import Counter

import confidence
from normalize import field_compare_form, field_display_form

DISCLAIMER = ("This evaluates pipeline behavior on labeled fixtures. "
              "Real-world accuracy still requires a manually reviewed HealthLynked sample.")


# ===========================================================================
#  Labeled fixtures
#  Each fixture: the field, the value we currently store, what each external
#  source reports (source, raw_value), and the EXPECTED behavior.
# ===========================================================================
FIXTURES = [
    # ---- no_change: sources confirm what we already have ----
    {"id": "NC-phone", "field": "phone", "old": "(212) 555-1234",
     "reports": [("practice_site", "(212) 555-1234"), ("nppes", "(212) 555-1234")],
     "expected": "no_change",
     "note": "phone matches across sources"},
    {"id": "NC-specialty", "field": "specialty", "old": "Cardiovascular Disease",
     "reports": [("nppes", "Cardiovascular Disease")],
     "expected": "no_change",
     "note": "specialty unchanged"},

    # ---- auto_update: corroborated and/or authoritative, clears the bar ----
    {"id": "AU-phone", "field": "phone", "old": "(212) 555-1234",
     "reports": [("clinic_site", "(212) 555-9000")],
     "expected": "auto_update",
     "note": "practice owns phone -> single authoritative source can auto-apply"},
    {"id": "AU-address", "field": "street", "old": "100 Main St",
     "reports": [("practice_site", "250 Health Park Dr"), ("state_board", "250 Health Park Dr")],
     "expected": "auto_update",
     "note": "two independent sources agree on the new address"},
    {"id": "AU-specialty", "field": "specialty", "old": "Internal Medicine",
     "reports": [("nppes", "Cardiovascular Disease"), ("state_board", "Cardiovascular Disease")],
     "expected": "auto_update",
     "note": "registry-of-record + independent board agree on specialty"},

    # ---- human_review: a real change that does NOT clear the bar ----
    {"id": "HR-city", "field": "city", "old": "New York",
     "reports": [("clinic_site", "Brooklyn")],
     "expected": "human_review",
     "note": "single source, city bar is higher than phone -> below bar"},
    {"id": "HR-specialty-uncorroborated", "field": "specialty", "old": "Internal Medicine",
     "reports": [("practice_site", "Cardiovascular Disease")],
     "expected": "human_review",
     "note": "lone non-owner source for a sensitive field -> needs corroboration"},

    # ---- conflict: sources disagree on the new value ----
    {"id": "CF-address", "field": "street", "old": "100 Main St",
     "reports": [("practice_site", "250 Health Park Dr"), ("state_board", "900 Gulf Coast Blvd")],
     "expected": "conflict",
     "note": "two sources propose different addresses"},
    {"id": "CF-phone", "field": "phone", "old": "(212) 555-1234",
     "reports": [("practice_site", "(212) 555-9000"), ("nppes", "(212) 555-7777")],
     "expected": "conflict",
     "note": "two sources propose different phones"},

    # ---- blocked_unsafe: high confidence, but a hard rule holds it for review ----
    {"id": "BU-deactivation", "field": "is_active", "old": 1,
     "reports": [("nppes", 0), ("cms", 0), ("state_board", 0)],
     "expected": "blocked_unsafe",
     "note": "3 sources agree on deactivation, but deactivation is never auto"},
    {"id": "BU-rename", "field": "name", "old": "John Smith",
     "reports": [("nppes", "Jonathan Smith"), ("state_board", "Jonathan Smith")],
     "expected": "blocked_unsafe",
     "note": "name change is an identity/merge risk -> never auto"},
]

# How each expected label maps onto the engine's 3 possible outcomes.
_EXPECTED_OUTCOME = {
    "no_change": "no_change",
    "auto_update": "auto_update",
    "human_review": "human_review",
    "conflict": "human_review",
    "blocked_unsafe": "human_review",
}


def _candidates(field, reports):
    """Build engine candidates from (source, raw_value) pairs using the shared
    normalizer — exactly how compare.py feeds the scorer."""
    return [{"source": src,
             "value": field_compare_form(field, raw),
             "display": field_display_form(field, raw)}
            for src, raw in reports]


def predict(fx):
    """Run the real scoring engine. Returns (outcome, result_dict_or_None)
    where outcome is one of {'no_change', 'auto_update', 'human_review'}."""
    cands = _candidates(fx["field"], fx["reports"])
    result = confidence.score_field(
        fx["field"], fx["old"], cands,
        old_compare=field_compare_form(fx["field"], fx["old"]))
    if result is None:
        return "no_change", None
    return result["decision"], result


def _semantic_ok(fx, outcome, result):
    """Beyond matching the outcome bucket, verify the richer guarantee for the
    two special categories so we don't pass them for the wrong reason."""
    if fx["expected"] == "conflict":
        # must be flagged as a genuine source conflict, not just 'below bar'
        return bool(result and result.get("conflict"))
    if fx["expected"] == "blocked_unsafe":
        # confidence WAS high enough to auto-apply, yet a rule held it for review
        return bool(result and outcome == "human_review"
                    and result["confidence"] >= confidence.required_threshold(fx["field"]))
    return True


def evaluate(fixtures=FIXTURES):
    """Score every fixture. Returns (rows, metrics)."""
    rows = []
    metrics = Counter()

    for fx in fixtures:
        expected_outcome = _EXPECTED_OUTCOME[fx["expected"]]
        outcome, result = predict(fx)
        semantic_ok = _semantic_ok(fx, outcome, result)
        correct = (outcome == expected_outcome) and semantic_ok

        # Tally the operational metrics by what the engine actually decided.
        if outcome == "auto_update":
            metrics["correct_auto_update" if expected_outcome == "auto_update"
                    else "false_auto_update"] += 1
        elif outcome == "human_review":
            metrics["correct_human_review" if expected_outcome == "human_review"
                    else "false_human_review"] += 1
        else:  # no_change
            if expected_outcome != "no_change":
                metrics["missed_change"] += 1
            else:
                metrics["correct_no_change"] += 1

        rows.append({
            "id": fx["id"], "field": fx["field"],
            "expected": fx["expected"], "predicted": outcome,
            "confidence": (result or {}).get("confidence"),
            "correct": correct, "note": fx["note"],
        })

    return rows, metrics


def main(fixtures=FIXTURES):
    rows, metrics = evaluate(fixtures)
    total = len(rows)
    correct = sum(1 for r in rows if r["correct"])

    print("=" * 72)
    print("  UPDATE-DECISION accuracy on labeled fixtures (MVP)")
    print("=" * 72)
    print(f"  Fixtures: {total}   |   correct decisions: {correct}/{total} "
          f"({correct / total:.0%})" if total else "  No fixtures.")
    print("-" * 72)

    print(f"  {'fixture':<28}{'field':<10}{'expected':<15}{'predicted':<14}result")
    for r in rows:
        mark = "✅" if r["correct"] else "❌"
        conf = f"{r['confidence']:.0%}" if r["confidence"] is not None else "  -"
        print(f"  {r['id']:<28}{r['field']:<10}{r['expected']:<15}"
              f"{r['predicted']:<14}{mark} {conf}")
    print("-" * 72)

    print("  Decision metrics:")
    print(f"    🟢 correct_auto_update  : {metrics['correct_auto_update']}")
    print(f"    🔴 false_auto_update    : {metrics['false_auto_update']}   "
          f"(auto-applied a change it should not have — dangerous)")
    print(f"    🟠 correct_human_review : {metrics['correct_human_review']}")
    print(f"    🟡 false_human_review   : {metrics['false_human_review']}   "
          f"(sent a safe change to a human — wasteful)")
    print(f"    ⚪ correct_no_change    : {metrics['correct_no_change']}")
    print(f"    ❗ missed_change        : {metrics['missed_change']}   "
          f"(saw a real change, proposed nothing — dangerous)")
    print("=" * 72)
    print(f"ℹ️  {DISCLAIMER}")
    print("=" * 72)

    return rows, metrics


if __name__ == "__main__":
    main()
