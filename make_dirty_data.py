"""
make_dirty_data.py
------------------
بياخد الـ 1000 طبيب الحقيقي من healthlynked.db، وبيعمل منهم نسخة "مبوّظة"
لاختبار النظام.

الفكرة:
  ~30% أغلاط حقيقية   → المفروض النظام يرفضهم   (expected_valid = False)
  ~70% سليمين/فوضى    → المفروض النظام يقبلهم   (expected_valid = True)
                         (الفوضى الشكلية بتتنضّف وتعدّي، مش بتترفض)

كل صف بيتكتب معاه:
  - error_type      : نوع التبويظ اللي عملناه (عشان نراجع)
  - expected_valid  : المفروض النظام يقبله (True) ولا يرفضه (False)

النتيجة بتتكتب في dirty_providers.csv
"""

import sqlite3
import csv
import random
from pathlib import Path

BASE = Path(__file__).parent
DB_PATH = BASE / "healthlynked.db"
OUT_PATH = BASE / "dirty_providers.csv"

random.seed(42)   # عشان النتيجة تطلع نفسها كل مرة (قابلة للتكرار)


# ===========================================================================
#  دوال تبويظ الـ NPI  (كلها أغلاط حقيقية → expected_valid = False)
# ===========================================================================

def npi_luhn_fail(npi):
    """يغيّر آخر رقم عشان الـ check digit يبقى غلط."""
    last = int(npi[-1])
    wrong = (last + 1) % 10
    return npi[:-1] + str(wrong)

def npi_too_short(npi):
    """يشيل أول رقم (زي ما الصفر بيقع في إكسيل)."""
    return npi[1:]

def npi_too_long(npi):
    """يضيف رقم زيادة في الآخر."""
    return npi + "3"

def npi_symbols(npi):
    """يحط شرط وأقواس وسط الرقم."""
    return f"{npi[:3]}-{npi[3:6]}({npi[6:9]}){npi[9]}"

def npi_letters(npi):
    """يبدّل 3 أرقام بحروف."""
    return npi[:5] + "ABC" + npi[8:]

def npi_bad_prefix(npi):
    """يخلّي أول رقم 3 (الأطباء لازم يبدؤوا بـ 1 أو 2)."""
    return "3" + npi[1:]

def npi_null(npi):
    """يشيل الـ NPI تماماً."""
    return ""


# ===========================================================================
#  دوال تبويظ الاسم
# ===========================================================================

# --- أغلاط حقيقية في الاسم → expected_valid = False ---
def name_empty(name):
    """اسم فاضي تماماً."""
    return ""

def name_single_letter(name):
    """اسم حرف واحد (مش طبيب حقيقي)."""
    return random.choice(["J.", "Doc J", "X"])

# --- فوضى شكلية (سليمة، بتتنضّف) → expected_valid = True ---
def name_titles(name):
    """يحيط الاسم بألقاب علمية."""
    return f"Dr. {name}, MD, PhD, FACS"

def name_messy_spaces(name):
    """مسافات مزدوجة ونقط عشوائية."""
    parts = name.split()
    return "  ".join(parts) + " ..."

def name_reversed(name):
    """يقلب الاسم (العائلة الأول) بفاصلة."""
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[-1]}, {parts[0]}"
    return name

def name_muddled_case(name):
    """حروف كبيرة وصغيرة متداخلة."""
    return "".join(
        c.upper() if random.random() > 0.5 else c.lower()
        for c in name
    )


# ===========================================================================
#  التصنيفات
# ===========================================================================

# أغلاط حقيقية (المفروض تترفض)
REAL_ERRORS = [
    ("npi_luhn_fail",   npi_luhn_fail,   "npi"),
    ("npi_too_short",   npi_too_short,   "npi"),
    ("npi_too_long",    npi_too_long,    "npi"),
    ("npi_symbols",     npi_symbols,     "npi"),
    ("npi_letters",     npi_letters,     "npi"),
    ("npi_bad_prefix",  npi_bad_prefix,  "npi"),
    ("npi_null",        npi_null,        "npi"),
    ("name_empty",      name_empty,      "name"),
    ("name_single",     name_single_letter, "name"),
]

# فوضى شكلية (المفروض تعدّي بعد التنظيف)
COSMETIC_NOISE = [
    ("name_titles",       name_titles,       "name"),
    ("name_messy_spaces", name_messy_spaces, "name"),
    ("name_reversed",     name_reversed,     "name"),
    ("name_muddled_case", name_muddled_case, "name"),
]


def main():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT npi, name FROM providers").fetchall()
    conn.close()

    if not rows:
        print("❌ مفيش بيانات في قاعدة البيانات. شغّل fetch_data.py الأول.")
        return

    out = open(OUT_PATH, "w", newline="", encoding="utf-8")
    writer = csv.writer(out)
    writer.writerow(["npi", "name", "error_type", "expected_valid"])

    count_real = 0
    count_cosmetic = 0
    count_clean = 0

    for npi, name in rows:
        roll = random.random()

        if roll < 0.30:
            # 30% غلط حقيقي
            err_name, err_func, field = random.choice(REAL_ERRORS)
            if field == "npi":
                npi = err_func(npi)
            else:
                name = err_func(name)
            writer.writerow([npi, name, err_name, False])
            count_real += 1

        elif roll < 0.55:
            # 25% فوضى شكلية (سليمة)
            err_name, err_func, field = random.choice(COSMETIC_NOISE)
            name = err_func(name)
            writer.writerow([npi, name, err_name, True])
            count_cosmetic += 1

        else:
            # 45% نضيف تماماً
            writer.writerow([npi, name, "clean", True])
            count_clean += 1

    out.close()

    total = count_real + count_cosmetic + count_clean
    print("=" * 55)
    print("  توليد البيانات المبوّظة")
    print("=" * 55)
    print(f"❌ أغلاط حقيقية (تترفض)   : {count_real}")
    print(f"🧹 فوضى شكلية (تتنضّف)    : {count_cosmetic}")
    print(f"✅ نضيفين تماماً          : {count_clean}")
    print("-" * 55)
    print(f"📊 الإجمالي               : {total}")
    print(f"📈 المتوقع يعدّي           : {count_cosmetic + count_clean}")
    print(f"📉 المتوقع يترفض          : {count_real}")
    print(f"📄 الملف                  : {OUT_PATH}")
    print("=" * 55)


if __name__ == "__main__":
    main()