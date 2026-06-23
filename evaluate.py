"""
evaluate.py
-----------
RECORD-VALIDITY evaluation — measures only whether the intake gate correctly
accepts well-formed records and quarantines malformed ones.

SCOPE (read this before quoting any number):
  This harness answers ONE narrow question: "is a record valid enough to admit
  to the directory?" — i.e. the NPI/name validation gate from process.py. It is
  a binary valid/invalid classifier scored against synthetic labels.

  It does NOT measure:
    * update-decision accuracy (auto_update / human_review / conflict / blocked) —
      that lives in evaluate_update_decisions.py.
    * real-world provider-update accuracy for address / phone / specialty /
      practice changes — that requires a manually reviewed HealthLynked sample.
    * scalability / throughput — that lives in benchmark.py.

The idea:
  1. Read dirty_providers.csv (which contains expected_valid = the known label).
  2. For each row, the system decides: valid or invalid?
        - The NPI must pass is_valid_npi()
        - The name after cleaning must be at least two characters
  3. Compare the system's decision against the label → build a Confusion Matrix.
  4. Compute Precision / Recall / Accuracy / F1 (for the validity classifier).
  5. Output a breakdown for each error type + save the errors to a file for review.
"""

import csv
from pathlib import Path
from collections import defaultdict

from validation import is_valid_npi
from normalize import normalize_name

BASE = Path(__file__).parent
IN_PATH = BASE / "dirty_providers.csv"
ERRORS_PATH = BASE / "misclassified.csv"

MIN_NAME_LEN = 2   # the name must be at least two characters (after cleaning)

DISCLAIMER = ("This evaluates pipeline behavior on labeled fixtures. "
              "Real-world accuracy still requires a manually reviewed HealthLynked sample.")


def decide(npi, name):
    """
    The system's decision: is this record valid (True) or invalid (False)?
    """
    # Gate 1: the NPI
    if not is_valid_npi(npi):
        return False

    # Gate 2: the name — at least two characters after cleaning
    clean = normalize_name(name)
    if len(clean["compare"].replace(" ", "")) < MIN_NAME_LEN:
        return False

    return True


def main():
    with open(IN_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # the 4 cells
    TP = TN = FP = FN = 0

    # breakdown per error type: how many were caught correctly
    by_type = defaultdict(lambda: {"correct": 0, "wrong": 0})

    # save the rows the system got wrong for review
    misclassified = []

    for r in rows:
        npi = r["npi"]
        name = r["name"]
        expected = (r["expected_valid"] == "True")   # the ground truth
        predicted = decide(npi, name)                 # the system's decision

        # classify the cell
        if expected and predicted:
            TP += 1
        elif not expected and not predicted:
            TN += 1
        elif not expected and predicted:
            FP += 1   # danger: accepted a corrupted record
        else:  # expected and not predicted
            FN += 1   # rejected an innocent valid record

        # track by error type
        etype = r["error_type"]
        if expected == predicted:
            by_type[etype]["correct"] += 1
        else:
            by_type[etype]["wrong"] += 1
            misclassified.append({
                "npi": npi, "name": name, "error_type": etype,
                "expected_valid": expected, "system_decision": predicted,
            })

    total = TP + TN + FP + FN

    # the metrics (with protection against division by zero)
    precision = TP / (TP + FP) if (TP + FP) else 0
    recall    = TP / (TP + FN) if (TP + FN) else 0
    accuracy  = (TP + TN) / total if total else 0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0)

    # ---------------- printing ----------------
    print("=" * 60)
    print("  RECORD-VALIDITY evaluation (NPI + name intake gate)")
    print("=" * 60)
    print("  Scope: accept-vs-quarantine only. This is NOT update-decision")
    print("         accuracy (see evaluate_update_decisions.py) nor real-world")
    print("         provider-update accuracy.")
    print("-" * 60)
    print(f"Total rows: {total}\n")

    print("Confusion Matrix (valid vs. invalid record):")
    print(f"  ✅ TP (valid   → accepted) : {TP}")
    print(f"  ✅ TN (invalid → rejected) : {TN}")
    print(f"  😱 FP (invalid → accepted!): {FP}   ← malformed data got through")
    print(f"  😞 FN (valid   → rejected!): {FN}   ← valid providers were rejected")
    print("-" * 60)

    print("Metrics (record-validity classification only):")
    print(f"  Precision : {precision:.1%}   (of what was accepted, how much is actually valid)")
    print(f"  Recall    : {recall:.1%}   (of the valid ones, how many were caught)")
    print(f"  Accuracy  : {accuracy:.1%}   (proportion of correct accept/reject decisions)")
    print(f"  F1 Score  : {f1:.1%}   (balance of Precision and Recall)")
    print("-" * 60)

    print("Breakdown by error type:")
    for etype in sorted(by_type):
        c = by_type[etype]["correct"]
        w = by_type[etype]["wrong"]
        flag = "" if w == 0 else f"   ⚠️ {w} wrong"
        print(f"  {etype:<20} correct: {c:>3} | wrong: {w:>3}{flag}")
    print("=" * 60)

    # save the errors if any
    if misclassified:
        with open(ERRORS_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=misclassified[0].keys())
            writer.writeheader()
            writer.writerows(misclassified)
        print(f"📄 The rows the system got wrong were saved to: {ERRORS_PATH}")
    else:
        print("🎉 The system caught everything correctly — no errors!")
    print("=" * 60)
    print(f"ℹ️  {DISCLAIMER}")
    print("=" * 60)


if __name__ == "__main__":
    main()