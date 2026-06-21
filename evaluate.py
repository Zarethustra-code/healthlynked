"""
evaluate.py
-----------
بيقيس دقة النظام على البيانات المبوّظة.

الفكرة:
  1. يقرا dirty_providers.csv (فيها expected_valid = الحقيقة المعروفة).
  2. لكل صف، النظام يقرّر: سليم ولا غلط؟
        - الـ NPI لازم يجتاز is_valid_npi()
        - الاسم بعد التنظيف لازم يكون حرفين على الأقل
  3. يقارن قرار النظام بالحقيقة → يبني Confusion Matrix.
  4. يحسب Precision / Recall / Accuracy.
  5. يطلّع تفصيل لكل نوع غلط + يحفظ الأخطاء في ملف للمراجعة.
"""

import csv
from pathlib import Path
from collections import defaultdict

from validation import is_valid_npi
from normalize import normalize_name

BASE = Path(__file__).parent
IN_PATH = BASE / "dirty_providers.csv"
ERRORS_PATH = BASE / "misclassified.csv"

MIN_NAME_LEN = 2   # الاسم لازم يكون حرفين على الأقل (بعد التنظيف)


def decide(npi, name):
    """
    قرار النظام: هل السجل ده سليم (True) ولا غلط (False)؟
    """
    # بوابة 1: الـ NPI
    if not is_valid_npi(npi):
        return False

    # بوابة 2: الاسم — حرفين على الأقل بعد التنظيف
    clean = normalize_name(name)
    if len(clean["compare"].replace(" ", "")) < MIN_NAME_LEN:
        return False

    return True


def main():
    with open(IN_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # الـ 4 خانات
    TP = TN = FP = FN = 0

    # تفصيل لكل نوع غلط: كام اتمسك صح
    by_type = defaultdict(lambda: {"correct": 0, "wrong": 0})

    # نحفظ الصفوف اللي النظام غلط فيها للمراجعة
    misclassified = []

    for r in rows:
        npi = r["npi"]
        name = r["name"]
        expected = (r["expected_valid"] == "True")   # الحقيقة
        predicted = decide(npi, name)                 # قرار النظام

        # تصنيف الخانة
        if expected and predicted:
            TP += 1
        elif not expected and not predicted:
            TN += 1
        elif not expected and predicted:
            FP += 1   # خطر: قبل معطوب
        else:  # expected and not predicted
            FN += 1   # رفض سليم بريء

        # تتبّع حسب نوع الغلط
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

    # المقاييس (مع حماية من القسمة على صفر)
    precision = TP / (TP + FP) if (TP + FP) else 0
    recall    = TP / (TP + FN) if (TP + FN) else 0
    accuracy  = (TP + TN) / total if total else 0
    f1 = (2 * precision * recall / (precision + recall)
          if (precision + recall) else 0)

    # ---------------- الطباعة ----------------
    print("=" * 60)
    print("  تقييم النظام على البيانات المبوّظة")
    print("=" * 60)
    print(f"إجمالي الصفوف: {total}\n")

    print("Confusion Matrix:")
    print(f"  ✅ TP (سليم → قُبِل)   : {TP}")
    print(f"  ✅ TN (غلط  → رُفِض)   : {TN}")
    print(f"  😱 FP (غلط  → قُبِل!)  : {FP}   ← بيانات معطوبة دخلت")
    print(f"  😞 FN (سليم → رُفِض!)  : {FN}   ← أطباء سليمين اترفضوا")
    print("-" * 60)

    print("المقاييس:")
    print(f"  Precision : {precision:.1%}   (اللي قبلته، قد إيه منه سليم فعلاً)")
    print(f"  Recall    : {recall:.1%}   (السليمين، مسكت منهم كام)")
    print(f"  Accuracy  : {accuracy:.1%}   (نسبة القرارات الصح إجمالاً)")
    print(f"  F1 Score  : {f1:.1%}   (توازن Precision و Recall)")
    print("-" * 60)

    print("التفصيل حسب نوع الغلط:")
    for etype in sorted(by_type):
        c = by_type[etype]["correct"]
        w = by_type[etype]["wrong"]
        flag = "" if w == 0 else f"   ⚠️ {w} غلط"
        print(f"  {etype:<20} صح: {c:>3} | غلط: {w:>3}{flag}")
    print("=" * 60)

    # نحفظ الأخطاء لو فيه
    if misclassified:
        with open(ERRORS_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=misclassified[0].keys())
            writer.writeheader()
            writer.writerows(misclassified)
        print(f"📄 الصفوف اللي النظام غلط فيها اتحفظت في: {ERRORS_PATH}")
    else:
        print("🎉 النظام مسك كل حاجة صح — مفيش أخطاء!")
    print("=" * 60)


if __name__ == "__main__":
    main()