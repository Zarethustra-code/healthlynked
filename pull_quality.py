"""
pull_quality.py
---------------
تقييم جودة السحبة (Pull Quality Score) — خطوة 4 من خريطة النظام.

بيفحص "الدفعة كلها" (مش السجل الواحد) ويدّي درجة من 100.
دالة عامة: تاخد اسم أي جدول وتفحصه.

الأوزان (زي خريطة النظام بالظبط):
  السحب بدون errors            20
  عدد الصفوف منطقي             15
  الأعمدة الأساسية موجودة      15
  البيانات مش ناقصة بشكل كبير  15
  مفيش تكرارات غريبة           15
  القيم في الأعمدة الصح        10
  تاريخ السحب واضح             10
  ─────────────────────────  ────
  الإجمالي                    100

قاعدة ذهبية: كل نقص في الدرجة له سبب واضح وإجراء.
"""

import sqlite3
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"

# الأعمدة الأساسية المتوقعة في أي سحبة مزوّدين
KEY_COLUMNS = ["npi", "phone", "city", "state"]

# العدد المتوقع تقريباً (لو قلّ كتير = خطر)
# ملاحظة: TARGET في fetch_data = 1000، والمصدر التاني صف لكل طبيب،
# فأقصى عدد ممكن ≈ 1000. لازم EXPECTED_MIN يفضل أقل من ده وإلا الفحص هيفشل دايمًا.
EXPECTED_MIN = 800    # أقل من كده يعتبر سحبة ناقصة


def check_pull_quality(table, expected_min=EXPECTED_MIN):
    """
    بتفحص جودة سحبة (جدول) وترجّع (score, report).
    report = قائمة بكل فحص: (الاسم، النقاط المكتسبة، النقاط الكاملة، السبب).
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # نجيب أسماء الأعمدة الموجودة فعلاً
    existing_cols = [r[1] for r in cur.execute(f"PRAGMA table_info({table})")]

    # نجيب كل الصفوف
    rows = cur.execute(f"SELECT * FROM {table}").fetchall()
    total = len(rows)
    col_index = {name: i for i, name in enumerate(existing_cols)}

    report = []

    # --- 1) السحب بدون errors (20) : فيه بيانات أصلاً؟ ---
    if total > 0:
        report.append(("السحب بدون errors", 20, 20, "السحبة فيها بيانات"))
    else:
        report.append(("السحب بدون errors", 0, 20, "❌ السحبة فاضية تماماً"))

    # --- 2) عدد الصفوف منطقي (15) ---
    if total >= expected_min:
        report.append(("عدد الصفوف منطقي", 15, 15, f"{total} صف (متوقع ≥ {expected_min})"))
    else:
        pts = round(15 * total / expected_min) if expected_min else 0
        report.append(("عدد الصفوف منطقي", pts, 15,
                        f"⚠️ {total} بس (متوقع ≥ {expected_min}) — drop مفاجئ"))

    # --- 3) الأعمدة الأساسية موجودة (15) ---
    missing_cols = [c for c in KEY_COLUMNS if c not in existing_cols]
    if not missing_cols:
        report.append(("الأعمدة الأساسية موجودة", 15, 15, "كل الأعمدة المهمة موجودة"))
    else:
        pts = round(15 * (len(KEY_COLUMNS) - len(missing_cols)) / len(KEY_COLUMNS))
        report.append(("الأعمدة الأساسية موجودة", pts, 15,
                        f"❌ ناقص: {', '.join(missing_cols)}"))

    # --- 4) البيانات مش ناقصة بشكل كبير (15) ---
    # نحسب نسبة الفاضي في الأعمدة المهمة الموجودة
    present_keys = [c for c in KEY_COLUMNS if c in col_index]
    empty_count = 0
    cells = 0
    for row in rows:
        for c in present_keys:
            cells += 1
            val = row[col_index[c]]
            if val is None or str(val).strip() == "":
                empty_count += 1
    empty_ratio = (empty_count / cells) if cells else 0
    if empty_ratio <= 0.05:
        report.append(("البيانات مش ناقصة", 15, 15,
                        f"نسبة الفاضي {empty_ratio:.1%} (مقبولة)"))
    else:
        pts = max(0, round(15 * (1 - empty_ratio)))
        report.append(("البيانات مش ناقصة", pts, 15,
                        f"⚠️ نسبة الفاضي {empty_ratio:.1%} (عالية)"))

    # --- 5) مفيش تكرارات غريبة (15) ---
    if "npi" in col_index:
        npis = [row[col_index["npi"]] for row in rows]
        unique = len(set(npis))
        dup_ratio = 1 - (unique / total) if total else 0
        if dup_ratio <= 0.02:
            report.append(("مفيش تكرارات غريبة", 15, 15,
                            f"{unique} فريد من {total}"))
        else:
            pts = max(0, round(15 * (1 - dup_ratio)))
            report.append(("مفيش تكرارات غريبة", pts, 15,
                            f"⚠️ تكرار {dup_ratio:.1%}"))
    else:
        report.append(("مفيش تكرارات غريبة", 0, 15, "❌ مفيش عمود npi للفحص"))

    # --- 6) القيم في الأعمدة الصح (10) : فحص بسيط — الـ npi 10 أرقام ---
    if "npi" in col_index:
        bad = sum(1 for row in rows
                  if not str(row[col_index["npi"]] or "").isdigit()
                  or len(str(row[col_index["npi"]] or "")) != 10)
        bad_ratio = bad / total if total else 0
        if bad_ratio <= 0.02:
            report.append(("القيم في الأعمدة الصح", 10, 10, "الـ NPI في شكله الصح"))
        else:
            pts = max(0, round(10 * (1 - bad_ratio)))
            report.append(("القيم في الأعمدة الصح", pts, 10,
                            f"⚠️ {bad_ratio:.1%} NPI شكلهم غلط"))
    else:
        report.append(("القيم في الأعمدة الصح", 5, 10, "مفيش npi للفحص"))

    # --- 7) تاريخ السحب واضح (10) ---
    if "fetched_at" in existing_cols:
        report.append(("تاريخ السحب واضح", 10, 10, "fetched_at موجود"))
    else:
        report.append(("تاريخ السحب واضح", 0, 10, "⚠️ مفيش عمود تاريخ"))

    conn.close()

    score = sum(p for _, p, _, _ in report)
    return score, report


def print_report(table):
    score, report = check_pull_quality(table)

    print("=" * 60)
    print(f"  تقرير جودة السحب: {table}")
    print("=" * 60)
    for name, pts, full, reason in report:
        mark = "✅" if pts == full else "⚠️ "
        print(f"  {mark} {name:<24} {pts:>2}/{full:<2}  | {reason}")
    print("-" * 60)
    print(f"  📊 Pull Quality Score: {score}/100")

    # الحكم + الإجراء (زي أمثلة الرسم)
    if score >= 85:
        print("  ✅ السحبة سليمة — كمّل للمقارنة")
    elif score >= 60:
        print("  ⚠️  السحبة فيها ملاحظات — راجع الأسباب قبل المقارنة")
    else:
        print("  ❌ السحبة معطوبة — أوقف المقارنة وحقّق في السبب")
    print("=" * 60)


if __name__ == "__main__":
    print_report("external_data")