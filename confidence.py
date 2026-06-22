"""
confidence.py
-------------
The single, documented confidence-scoring engine for HealthLynked.

Everything that proposes a change to a provider record routes through here so
that the *same* formula and the *same* safety rules are applied whether the
change came from the batch comparator (compare.py) or the live, single-record
verifier (live_verify.py).

Design goals (mapped to the brief's evaluation criteria):
  - Explainability : every score returns a human-readable `reason`.
  - Source reliability : each source has a weight; corroboration is rewarded.
  - Accuracy / safety  : sensitive fields require a higher bar; some changes
                         (deactivation, name change, blanking good data) can
                         never auto-apply.
  - Cost efficiency : pure standard-library arithmetic. No LLM call is needed
                      to score a change once candidate values are in hand.

------------------------------------------------------------------------------
THE FORMULA (see PROPOSAL.md for the narrative version)
------------------------------------------------------------------------------
For a field `f` whose stored value disagrees with one or more external sources
that all report the *same* new value `v`:

  1. Collapse non-independent sources. Sources in the same `independence_group`
     (e.g. CMS re-publishes NPPES) are NOT independent evidence, so we keep only
     the single most reliable source per group.

  2. Corroboration via noisy-OR over the independent supporters' reliabilities:

         corroboration = 1 - Π (1 - reliability[s])

     This stays in [0, 1], rises as independent sources agree, and has natural
     diminishing returns (two 0.85 sources -> 0.9775, not 1.70).

  3. Authority bonus. If the source that *owns* the field (FIELD_AUTHORITY)
     is among the supporters, close part of the remaining gap to certainty:

         conf = corroboration + AUTHORITY_BONUS * (1 - corroboration)

  4. Decision threshold scales with field sensitivity:

         required = AUTO_BASE + SENSITIVITY_WEIGHT * sensitivity[f]

     so a low-stakes phone edit auto-applies more easily than a high-stakes
     specialty change.

  5. Hard safety rules override the score and force human review regardless of
     confidence (deactivation, name change, overwriting good data with blank,
     and inter-source conflict).

`overall` confidence for a record = mean of its per-field confidences (the same
way the brief's worked example averages its per-field scores). A record only
earns `auto_update` if EVERY one of its field changes independently qualifies.
The self-test below reproduces the brief's two *recommended_actions*
(auto_update for multi-source agreement; human_review for a conflict); the exact
per-field scores are this engine's own, not the brief's illustrative numbers.
"""

from __future__ import annotations

from functools import reduce


# ===========================================================================
#  Knowledge tables (tunable policy — no code change needed to retune)
# ===========================================================================

# Per-source reliability and independence. `independence_group` marks sources
# that share an upstream: they do not corroborate each other.
#   live=False marks the simulated source the prototype ships with.
SOURCES = {
    "nppes":         {"reliability": 0.85, "independence_group": "cms",      "live": True},
    "cms":           {"reliability": 0.85, "independence_group": "cms",      "live": True},
    "state_board":   {"reliability": 0.90, "independence_group": "state",    "live": True},
    "practice_site": {"reliability": 0.75, "independence_group": "practice", "live": True},
    # The prototype's simulated second source. Treated as a practice-website
    # signal so the offline demo exercises the same code path as production.
    "clinic_site":   {"reliability": 0.80, "independence_group": "practice", "live": False},
}

# Unknown sources fall back to a conservative reliability and are assumed
# independent (their own group).
DEFAULT_RELIABILITY = 0.60

# Which source is the natural "owner" of each field.
FIELD_AUTHORITY = {
    "phone":     "practice_site",   # the practice publishes its own phone first
    "street":    "practice_site",
    "unit":      "practice_site",
    "city":      "practice_site",
    "state":     "practice_site",
    "zip":       "practice_site",
    "specialty": "nppes",           # NPPES is the registry of record for taxonomy
    "is_active": "nppes",
    "name":      "nppes",
}

# How dangerous an automatic change is. Higher -> higher bar to auto-apply.
FIELD_SENSITIVITY = {
    "phone":     0.2,
    "zip":       0.3,
    "unit":      0.3,
    "street":    0.4,
    "city":      0.4,
    "state":     0.4,
    "specialty": 0.7,
    "name":      0.8,
    "is_active": 0.9,
}
DEFAULT_SENSITIVITY = 0.5

# Tuning constants (calibrate against evaluate.py over labeled data).
AUTHORITY_BONUS    = 0.15   # fraction of the remaining gap closed by the owner
AUTO_BASE          = 0.80   # base bar for auto-update
SENSITIVITY_WEIGHT = 0.15   # how much sensitivity raises the bar

# Fields whose CHANGE direction is high-stakes enough to always require a human,
# no matter how confident we are. Keeps the directory safe from silent harm.
#   - deactivating a provider removes them from patient search
#   - changing a provider's name is almost always an identity/merge issue
NEVER_AUTO_DEACTIVATE = True   # is_active 1 -> 0 always reviewed
NEVER_AUTO_RENAME     = True   # any name change always reviewed


# ===========================================================================
#  Helpers
# ===========================================================================

def reliability(source: str) -> float:
    return SOURCES.get(source, {}).get("reliability", DEFAULT_RELIABILITY)


def independence_group(source: str) -> str:
    # Unknown sources are their own group (assumed independent).
    return SOURCES.get(source, {}).get("independence_group", source)


def required_threshold(field: str) -> float:
    """The confidence a field must reach to auto-update (sensitivity-scaled)."""
    sens = FIELD_SENSITIVITY.get(field, DEFAULT_SENSITIVITY)
    return round(AUTO_BASE + SENSITIVITY_WEIGHT * sens, 4)


def is_authority(field: str, source: str) -> bool:
    """True if `source` belongs to the same family as the field's owner.

    Authority is about the *kind* of source, not its exact name: any
    practice-website signal (group "practice") is authoritative for phone /
    address, so the prototype's `clinic_site` is treated like `practice_site`.
    """
    owner = FIELD_AUTHORITY.get(field)
    if not owner:
        return False
    return independence_group(source) == independence_group(owner)


def _collapse_independent(sources):
    """Keep the single most reliable source per independence group.

    Returns the list of surviving source names. This is what stops CMS (which
    re-publishes NPPES) from counting as a second, independent confirmation.
    """
    best = {}
    for s in sources:
        g = independence_group(s)
        if g not in best or reliability(s) > reliability(best[g]):
            best[g] = s
    return list(best.values())


def _noisy_or(values):
    """1 - Π(1 - v). Probability at least one independent source is right."""
    return 1.0 - reduce(lambda acc, v: acc * (1.0 - v), values, 1.0)


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


# ===========================================================================
#  Core: score a single proposed field change
# ===========================================================================

def score_field(field, old_value, candidates, old_compare=None):
    """Score a proposed change to one field.

    Args:
      field:      the column name (e.g. "phone").
      old_value:  the stored value, in DISPLAY form (echoed back in the output).
      candidates: list of {"source": str, "value": <normalized COMPARE value>,
                  "display": <display value>} reported by external sources.
                  Only sources whose compare-value differs from the stored
                  compare-value are evidence of a change.
      old_compare: the stored value's COMPARE form, used for matching. MUST be
                  produced by the same normalizer that produced each candidate's
                  "value" (otherwise "(212) 555-1111" != "2125551111" and a
                  no-op change is falsely proposed). Defaults to old_value.

    Returns a dict:
      {
        "field", "old_value", "new_value", "confidence",
        "supporting_sources" (display names), "decision"
            in {"auto_update", "human_review", "no_change"},
        "conflict" (bool), "reason" (human-readable)
      }
    or None if there is no real, safe change to propose.
    """
    old_norm = _norm(old_value if old_compare is None else old_compare)

    # Group candidate NEW values (ignore any source that agrees with us).
    by_value = {}
    for c in candidates:
        v = _norm(c.get("value"))
        if v == old_norm:
            continue                      # source agrees with stored value
        if _is_blank(v) and not _is_blank(old_norm):
            # Source simply doesn't carry this field; never treat "missing"
            # as "changed to empty". Do not overwrite good data with a blank.
            continue
        by_value.setdefault(v, []).append(c)

    if not by_value:
        return None                       # nothing to change

    # Conflict = sources disagree on what the NEW value should be. ANY two
    # surviving distinct values is a conflict, regardless of source family — we
    # never silently pick a winner among genuinely disagreeing sources, even if
    # they share an independence group (e.g. two practice-site signals).
    conflict = len(by_value) > 1

    # Choose the best-supported candidate value (most independent corroboration,
    # then highest summed reliability as a tiebreak).
    def support_strength(cs):
        indep = _collapse_independent([c.get("source", "unknown") for c in cs])
        return (len(indep), sum(reliability(s) for s in indep))

    best_value, best_cands = max(by_value.items(), key=lambda kv: support_strength(kv[1]))

    indep_sources = _collapse_independent([c.get("source", "unknown") for c in best_cands])
    corroboration = _noisy_or([reliability(s) for s in indep_sources])

    # Authority bonus if the field's owner (by family) backs this value.
    has_authority = any(is_authority(field, c["source"]) for c in best_cands)
    conf = corroboration + AUTHORITY_BONUS * (1 - corroboration) if has_authority else corroboration

    # Conflict penalty: competing independent values mean we are not sure.
    if conflict:
        conf *= 0.7

    conf = _clamp(conf)

    # Pick a display value if any source supplied one.
    new_display = next((c.get("display") for c in best_cands if c.get("display")), best_value)

    raw_sources = [c.get("source", "unknown") for c in best_cands]
    supporting = sorted({_pretty(s) for s in raw_sources})
    decision, reason = _decide(field, old_value, new_display, conf, supporting, raw_sources, conflict)

    return {
        "field": field,
        "old_value": old_value,
        "new_value": new_display,
        "confidence": round(conf, 3),
        "supporting_sources": supporting,
        "decision": decision,
        "conflict": conflict,
        "reason": reason,
    }


def _decide(field, old_value, new_value, conf, supporting, raw_sources, conflict):
    """Apply thresholds + hard safety rules -> (decision, reason)."""
    required = required_threshold(field)
    parts = []

    if len(supporting) > 1:
        parts.append(f"{len(supporting)} independent sources agree ({', '.join(supporting)})")
    else:
        parts.append(f"reported by {supporting[0]}")

    if any(is_authority(field, src) for src in raw_sources):
        parts.append(f"authoritative source for '{field}'")

    # --- Hard safety rules: force human review regardless of confidence ---
    if conflict:
        parts.append("sources conflict on the new value")
        return "human_review", _join(parts, conf, required, "conflict -> human review")

    if NEVER_AUTO_DEACTIVATE and field == "is_active" and str(old_value) == "1" and str(new_value) == "0":
        parts.append("deactivation is high-stakes")
        return "human_review", _join(parts, conf, required, "provider deactivation always reviewed")

    if NEVER_AUTO_RENAME and field == "name":
        parts.append("name changes can be identity/merge issues")
        return "human_review", _join(parts, conf, required, "name change always reviewed")

    # A single source may auto-update only a field it OWNS. A lone source that is
    # not the field's owner must be corroborated by a second independent source
    # before we touch patient-facing data — this is the corroboration guarantee.
    indep = _collapse_independent(raw_sources)
    has_authority = any(is_authority(field, s) for s in raw_sources)
    if len(indep) < 2 and not has_authority:
        parts.append("single uncorroborated source, not the field owner")
        return "human_review", _join(parts, conf, required,
                                     "needs a 2nd independent source or the field owner -> human review")

    # --- Confidence vs sensitivity-scaled threshold ---
    if conf >= required:
        return "auto_update", _join(parts, conf, required, "confidence >= bar -> auto-update")
    return "human_review", _join(parts, conf, required, "confidence < bar -> human review")


def _join(parts, conf, required, verdict):
    return (" | ".join(parts)
            + f" | confidence {conf:.0%} vs bar {required:.0%} -> {verdict}")


# ===========================================================================
#  Record-level aggregation
# ===========================================================================

def score_record(provider_id, npi, field_results):
    """Combine per-field results (from score_field) into the brief's schema.

    overall_confidence = mean of field confidences.
    recommended_action = auto_update only if EVERY change qualifies for auto.
    """
    changes = [r for r in field_results if r]
    if not changes:
        return {
            "provider_id": provider_id, "npi": npi,
            "change_detected": False, "changes": [],
            "overall_confidence": 1.0,
            "recommended_action": "no_change",
            "reason": "All checked fields match the trusted sources.",
        }

    overall = round(sum(c["confidence"] for c in changes) / len(changes), 3)
    all_auto = all(c["decision"] == "auto_update" for c in changes)
    action = "auto_update" if all_auto else "human_review"

    if action == "auto_update":
        reason = "Confirmed by trusted sources at or above the auto-update bar for every field."
    elif any(c["conflict"] for c in changes):
        reason = "Sources disagree on at least one field. Manual verification recommended."
    else:
        reason = "At least one change did not clear the auto-update bar. Sent to human review."

    # Brief's output schema: confidence_score + supporting_sources per change.
    out_changes = [{
        "field": c["field"],
        "old_value": c["old_value"],
        "new_value": c["new_value"],
        "confidence_score": c["confidence"],
        "supporting_sources": c["supporting_sources"],
    } for c in changes]

    return {
        "provider_id": provider_id, "npi": npi,
        "change_detected": True,
        "changes": out_changes,
        "overall_confidence": overall,
        "recommended_action": action,
        "reason": reason,
    }


# ===========================================================================
#  Tiny normalization helpers (kept local so this module has no DB/IO deps)
# ===========================================================================

def _norm(v):
    return "" if v is None else str(v).strip().lower()


def _is_blank(v):
    return v is None or str(v).strip() == ""


_PRETTY = {
    "nppes": "NPI Registry", "cms": "CMS", "state_board": "State Medical Board",
    "practice_site": "Practice Website", "clinic_site": "Practice Website",
}


def _pretty(source):
    return _PRETTY.get(source, source)


# ---------------------------------------------------------------------------
# Self-test: reproduce the brief's two worked examples.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 70)
    print("  confidence.py self-test (brief's worked examples)")
    print("=" * 70)

    # Example 1: address + phone confirmed by multiple sources -> auto_update.
    addr = score_field(
        "street", "100 Main St",
        [{"source": "nppes", "value": "250 health park dr", "display": "250 Health Park Dr"},
         {"source": "practice_site", "value": "250 health park dr", "display": "250 Health Park Dr"},
         {"source": "state_board", "value": "250 health park dr", "display": "250 Health Park Dr"}],
        old_compare="100 main st",
    )
    phone = score_field(
        "phone", "(239) 555-1234",
        [{"source": "practice_site", "value": "2395559000", "display": "(239) 555-9000"},
         {"source": "nppes", "value": "2395559000", "display": "(239) 555-9000"}],
        old_compare="2395551234",
    )
    rec = score_record("HL_001", "1234567890", [addr, phone])
    import json
    print(json.dumps(rec, indent=2))
    print(f"\n=> recommended_action = {rec['recommended_action']} "
          f"(overall {rec['overall_confidence']:.0%})\n")

    # Example 2: two independent sources disagree on address -> human_review.
    conflict = score_field(
        "street", "100 Main St",
        [{"source": "practice_site", "value": "250 health park dr", "display": "250 Health Park Dr"},
         {"source": "nppes", "value": "900 gulf coast blvd", "display": "900 Gulf Coast Blvd"}],
        old_compare="100 main st",
    )
    rec2 = score_record("HL_001", "1234567890", [conflict])
    print(json.dumps(rec2, indent=2))
    print(f"\n=> recommended_action = {rec2['recommended_action']} "
          f"(overall {rec2['overall_confidence']:.0%})")
