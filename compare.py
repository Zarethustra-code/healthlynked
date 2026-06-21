"""
compare.py
----------
محرّك المقارنة وكشف التغييرات (خطوات 6 + 7 + 8 من خريطة النظام).

  خطوة 6 (مطابقة):    يقارن كل عمود بين المصدرين باستخدام نسخة compare.
  خطوة 7 (استقلالية): يتأكد المصدرين مستقلين (مش واحد ناقل من التاني).
  خطوة 8 (قرار):      يحسب الثقة + يقرّر AUTO_UPDATE / NEEDS_REVIEW.

المخرجات بتتكتب في جدول proposed_changes.

ملاحظة: أرقام الثقة (placeholders) مبدئية، وبتتعاير من البيانات بعدين.
"""

import sqlite3
from pathlib import Path

from normalize import normalize_phone, normalize_address, normalize_specialty

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"


# ===========================================================================
#  جداول المعرفة (قواعد قابلة للتعديل)
# ===========================================================================

# (1) سلطة كل حقل: مين المصدر "الأصل" (المتخصّص) لكل عمود.
#     لو المصدر اللي جه بالتغيير هو نفسه الأصل → ثقة أعلى.
FIELD_AUTHORITY = {
    "phone":     "clinic_site",   # موقع العيادة أحدث في التليفون
    "street":    "clinic_site",
    "city":      "clinic_site",
    "state":     "clinic_site",
    "zip":       "clinic_site",
    "specialty": "nppes",         # NPPES الرسمي في التخصص
    "is_active": "nppes",         # NPPES الرسمي في الحالة
}

# (2) استقلالية المصادر: مين مستقل عن مين.
#     لو المصدرين مش مستقلين، التطابق مش تأكيد حقيقي.
INDEPENDENT_PAIRS = {
    frozenset({"nppes", "clinic_site"}): True,
    frozenset({"nppes", "cms"}): False,   # CMS بياخد من NPPES (مش مستقل)
}

# (3) حساسية الحقل: بتحدّد القرار (آمن للتحديث التلقائي ولا لأ).
#     قيمة أعلى = أكثر حساسية = أقرب لمراجعة بشرية.
FIELD_SENSITIVITY = {
    "phone":     0.2,   # منخفضة → آمن
    "street":    0.5,
    "city":      0.5,
    "state":     0.5,
    "zip":       0.4,
    "specialty": 0.8,   # عالية
    "is_active": 0.9,   # خطيرة
}

# ثوابت مبدئية (placeholders) — بتتعاير من البيانات بعدين
SOURCE_BASE_CONF = 0.80    # ثقة المصدر الأساسية
AUTHORITY_BONUS  = 0.15    # بوست لو المصدر هو صاحب السلطة على الحقل
AUTO_THRESHOLD   = 0.85    # فوقها → تحديث تلقائي


# ===========================================================================
#  خطوة 6: مطابقة حقل واحد
# ===========================================================================

def _compare_value(field, old_val, new_val):
    """
    بترجّع True لو القيمتين متطابقتين (بعد التطبيع)، False لو مختلفتين.
    بتستخدم نسخة compare المناسبة لكل نوع حقل.
    """
    if field == "phone":
        return normalize_phone(old_val)["compare"] == normalize_phone(new_val)["compare"]

    if field in ("street", "city", "state", "zip"):
        # نقارن الجزء ده لوحده (نطبّعه كعنوان جزئي)
        a = normalize_address(old_val, "", "", "")["compare"].split("|")[0] if field == "street" else str(old_val or "").strip().lower()
        b = normalize_address(new_val, "", "", "")["compare"].split("|")[0] if field == "street" else str(new_val or "").strip().lower()
        return a == b

    if field == "specialty":
        return normalize_specialty("", old_val)["compare"] == normalize_specialty("", new_val)["compare"]

    # is_active وأي حقل تاني: مقارنة مباشرة
    return str(old_val).strip() == str(new_val).strip()


# ===========================================================================
#  خطوة 7: استقلالية المصدرين
# ===========================================================================

def _are_independent(source_a, source_b):
    """بترجّع True لو المصدرين مستقلين (التطابق بينهم تأكيد حقيقي)."""
    pair = frozenset({source_a, source_b})
    return INDEPENDENT_PAIRS.get(pair, True)   # الافتراضي مستقلين


# ===========================================================================
#  خطوة 8: حساب الثقة + القرار
# ===========================================================================

def _score_and_decide(field, source):
    """
    بتحسب ثقة التغيير وتقرّر AUTO_UPDATE ولا NEEDS_REVIEW.
    بترجّع (confidence, decision, reason).
    reason = شرح بلغة بشري ليه اتأخد القرار ده (Explainability).
    """
    conf = SOURCE_BASE_CONF
    reason_parts = []

    # بوست لو المصدر هو صاحب السلطة على الحقل ده
    is_authority = FIELD_AUTHORITY.get(field) == source
    if is_authority:
        conf += AUTHORITY_BONUS
        reason_parts.append(f"المصدر ({source}) صاحب سلطة على «{field}»")
    else:
        reason_parts.append(f"المصدر ({source}) مش صاحب سلطة على «{field}»")

    # تأثير حساسية الحقل
    sensitivity = FIELD_SENSITIVITY.get(field, 0.5)
    conf -= sensitivity * 0.3
    if sensitivity >= 0.8:
        reason_parts.append("الحقل حسّاس جداً (خطر على المريض)")
    elif sensitivity >= 0.5:
        reason_parts.append("الحقل متوسط الحساسية (ممكن انتقال/تغيير مهم)")
    else:
        reason_parts.append("الحقل منخفض الحساسية (آمن نسبياً)")

    conf = max(0.0, min(1.0, conf))
    decision = "AUTO_UPDATE" if conf >= AUTO_THRESHOLD else "NEEDS_REVIEW"

    # نكمّل الشرح بالنتيجة
    if decision == "AUTO_UPDATE":
        reason_parts.append(f"الثقة {conf:.0%} ≥ العتبة → تحديث تلقائي")
    else:
        reason_parts.append(f"الثقة {conf:.0%} < العتبة → مراجعة بشرية")

    reason = " | ".join(reason_parts)
    return round(conf, 3), decision, reason


# ===========================================================================
#  المحرّك الرئيسي
# ===========================================================================

# الأعمدة اللي بنقارنها
COMPARE_FIELDS = ["phone", "street", "city", "state", "zip", "specialty", "is_active"]


def main():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # نفضّي التغييرات القديمة عشان نبدأ نظيف
    cur.execute("DELETE FROM proposed_changes")

    # نجيب الأصل (providers) كـ dict بالـ npi
    providers = {}
    for row in cur.execute(
        "SELECT npi, phone, street, city, state, zip, specialty, is_active FROM providers"
    ):
        providers[row[0]] = dict(zip(
            ["npi", "phone", "street", "city", "state", "zip", "specialty", "is_active"], row))

    # نجيب المصدر التاني
    externals = cur.execute(
        "SELECT npi, source_name, phone, street, city, state, zip, specialty, is_active "
        "FROM external_data"
    ).fetchall()

    changes = 0
    auto = 0
    review = 0

    for ext in externals:
        (npi, source, phone, street, city, state, zip_, specialty, is_active) = ext
        old = providers.get(npi)
        if not old:
            continue   # المصدر التاني فيه طبيب مش عندنا (نتجاهله دلوقتي)

        new_values = {
            "phone": phone, "street": street, "city": city, "state": state,
            "zip": zip_, "specialty": specialty, "is_active": is_active,
        }

        for field in COMPARE_FIELDS:
            old_val = old[field]
            new_val = new_values[field]

            # خطوة 6: هل اتغيّر؟
            if _compare_value(field, old_val, new_val):
                continue   # متطابق → مفيش تغيير

            # خطوة 8: ثقة + قرار + شرح (خطوة 7 الاستقلالية مدمجة في الثقة)
            conf, decision, reason = _score_and_decide(field, source)

            cur.execute(
                "INSERT INTO proposed_changes "
                "(npi, field, old_value, new_value, source, confidence, decision, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (npi, field, str(old_val), str(new_val), source, conf, decision, reason),
            )
            changes += 1
            if decision == "AUTO_UPDATE":
                auto += 1
            else:
                review += 1

    conn.commit()
    conn.close()

    print("=" * 55)
    print("  محرّك المقارنة وكشف التغييرات")
    print("=" * 55)
    print(f"🔍 إجمالي التغييرات المكتشفة : {changes}")
    print(f"🟢 تحديث تلقائي (AUTO)        : {auto}")
    print(f"🟠 مراجعة بشرية (REVIEW)      : {review}")
    print("=" * 55)


if __name__ == "__main__":
    main()