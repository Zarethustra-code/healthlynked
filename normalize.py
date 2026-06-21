"""
normalize.py
------------
مركز تطبيع البيانات (Normalization Layer).

الفرق عن validation.py:
  validation  →  بيقول "صح ولا غلط؟"   (بيرجّع True/False)
  normalize   →  بيحوّل للشكل الموحّد   (بيرجّع نص منظّف)

كل دالة بترجّع نسختين:
  display  →  شكل جميل للعرض والتخزين      (Title Case)
  compare  →  شكل موحّد للمقارنة الداخلية   (lowercase, ألقاب متشالة)

الجاهز دلوقتي:
  ✅ normalize_name()

هنضيف بعدين:
  ⬜ normalize_phone()
  ⬜ normalize_address()
"""

import re


# ===========================================================================
#  NAME  ✅
# ===========================================================================

# الألقاب اللي بنشيلها من نسخة المقارنة (بأحرف صغيرة عشان المطابقة)
_TITLES = {"dr", "doc", "doctor", "prof", "mr", "mrs", "ms",
           "md", "phd", "do", "rn", "np"}


def _cap_name_word(word):
    """
    زي .capitalize() بس بتكبّر أول كل جزء مفصول بـ ' أو -
    عشان "O'Connor" تفضل "O'Connor" مش "O'connor"، و"Al-Hassan" تفضل صح.
    (capitalize العادية بتصغّر كل اللي بعد أول حرف.)
    """
    return re.sub(r"[A-Za-z]+", lambda m: m.group(0).capitalize(), word)


def normalize_name(name):
    """
    بتاخد اسم خام وترجّع dict فيه نسختين:
        {
            "display": "Ahmed M Hassan",   ← للعرض والتخزين
            "compare": "ahmed m hassan"    ← للمقارنة الداخلية
        }

    خطوات التنظيف:
      0. لو الاسم مقلوب بفاصلة ("Hassan, Ahmed") نقلبه لـ "Ahmed Hassan".
      1. نشيل النقط ونحوّلها لمسافة (عشان "St.John" متبقاش "stjohn").
      2. نوحّد المسافات الزيادة.
      3. نشيل الألقاب (Dr, MD ...).
      4. display = Title Case (بنسيب O'Connor جميلة)
         compare = lowercase + ندمج الفواصل العلوية والشرطات (oconnor).
    """
    if name is None:
        return {"display": "", "compare": ""}

    text = str(name).strip()

    # 0) الاسم المقلوب: لو فيه فاصلة واحدة بس ("Hassan, Ahmed")
    #    نقلب الترتيب → "Ahmed Hassan" قبل أي تنظيف تاني.
    if text.count(",") == 1:
        last, first = text.split(",")
        text = f"{first.strip()} {last.strip()}"

    # 1) نشيل النقط ونحوّلها لمسافة (للأسماء زي "St.John")
    #    ملاحظة: مبنلمسش ' ولا - هنا — هنتعامل معاهم في كل نسخة لوحدها.
    text = re.sub(r"\.", " ", text)

    # 2) نوحّد المسافات: أي مسافات متعددة تبقى مسافة واحدة
    text = re.sub(r"\s+", " ", text).strip()

    # 3) نقسّم لكلمات ونشيل الألقاب
    words = [w for w in text.split(" ") if w and w.lower().strip("'-") not in _TITLES]

    # --- نسخة العرض: Title Case مع الحفاظ على الحرف الكبير بعد ' و - (O'Connor, Al-Hassan) ---
    display = " ".join(_cap_name_word(w) for w in words)

    # --- نسخة المقارنة: lowercase + ندمج الفواصل العلوية والشرطات ---
    #     "O'Connor" → "oconnor"  |  "Al-Hassan" → "alhassan"
    compare = " ".join(words).lower()
    compare = re.sub(r"['\-]", "", compare)

    return {"display": display, "compare": compare}


# ===========================================================================
#  SPECIALTY  ✅
# ===========================================================================

def normalize_specialty(code, desc):
    """
    بتاخد كود التخصص ووصفه، وترجّع dict منظّف:
        {
            "code":    "1223G0001X",       ← الكود (موحّد، حروف كبيرة)
            "display": "General Practice",  ← الوصف للعرض
            "compare": "general practice"   ← الوصف للمقارنة
        }

    ملاحظة: الكود زي الـ NPI — معرّف ثابت، فبس بنوحّد شكله (حروف كبيرة)
    من غير ما نغيّر محتواه.
    """
    # الكود: نشيل المسافات ونخليه حروف كبيرة (UPPER) عشان التوحيد
    code = str(code).strip().upper() if code else ""

    # الوصف: نفس منطق تنظيف الأسماء (مسافات موحّدة)
    desc = str(desc).strip() if desc else ""
    desc = re.sub(r"\s+", " ", desc)

    return {
        "code": code,
        "display": desc,
        "compare": desc.lower(),
    }


# ===========================================================================
#  PHONE  ✅
# ===========================================================================

def normalize_phone(phone):
    """
    بتاخد رقم تليفون خام وترجّع dict فيه نسختين:
        {
            "compare": "2125551234",       ← أرقام بس (للمقارنة)
            "display": "(212) 555-1234"    ← شكل أمريكي جميل (للعرض)
        }

    خطوات التنظيف:
      1. نشيل أي حاجة مش رقم (شُرَط، أقواس، مسافات، +).
      2. لو 11 خانة وبادئة بـ 1 (كود أمريكا) نشيل الـ 1.
      3. لو مش 10 خانات في الآخر → رقم غير صالح، نرجّع فاضي.
    """
    if phone is None:
        return {"compare": "", "display": ""}

    # 1) نسيب الأرقام بس
    digits = re.sub(r"\D", "", str(phone))

    # 2) لو 11 خانة وبادئة بـ 1، نشيل الـ 1
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]

    # 3) لازم يكون 10 خانات، غير كده مش صالح
    if len(digits) != 10:
        return {"compare": "", "display": ""}

    display = f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return {"compare": digits, "display": display}


# ===========================================================================
#  ADDRESS  ✅  (نسخة عميقة: توحيد اختصارات + فصل الوحدة)
# ===========================================================================

# قاموس توحيد اختصارات الشوارع → الشكل الكامل (كله lowercase للمطابقة)
_STREET_ABBR = {
    "st": "street", "st.": "street",
    "ave": "avenue", "ave.": "avenue", "av": "avenue",
    "blvd": "boulevard", "blvd.": "boulevard",
    "rd": "road", "rd.": "road",
    "dr": "drive", "dr.": "drive",
    "ln": "lane", "ln.": "lane",
    "ct": "court", "ct.": "court",
    "pl": "place", "pl.": "place",
    "sq": "square", "sq.": "square",
    "ter": "terrace", "ter.": "terrace",
    "pkwy": "parkway", "pkwy.": "parkway",
    "hwy": "highway", "hwy.": "highway",
    "n": "north", "n.": "north",
    "s": "south", "s.": "south",
    "e": "east", "e.": "east",
    "w": "west", "w.": "west",
    "ne": "northeast", "nw": "northwest",
    "se": "southeast", "sw": "southwest",
}

# الكلمات اللي بتدل على رقم وحدة/سويت (بنفصلها لخانة لوحدها)
_UNIT_WORDS = {"suite", "ste", "ste.", "unit", "apt", "apt.",
               "apartment", "fl", "fl.", "floor", "rm", "room", "#"}


def _expand_token(token):
    """بتفك اختصار كلمة واحدة لو موجود في القاموس، غير كده تسيبها."""
    key = token.lower()
    return _STREET_ABBR.get(key, key)


def _smart_title(text):
    """
    زي .title() بس بتسيب اللواحق الرقمية صح:
        "42nd" تفضل "42nd" مش "42Nd"
        "main street" → "Main Street"
    """
    out = []
    for word in text.split():
        # لو الكلمة بتبدأ برقم (زي 42nd, 3rd) نسيبها lowercase زي ما هي
        if word and word[0].isdigit():
            out.append(word.lower())
        else:
            out.append(word.capitalize())
    return " ".join(out)


def normalize_address(street, city, state, postal):
    """
    نسخة عميقة: بتوحّد اختصارات الشارع وبتفصل رقم الوحدة/السويت.

    بترجّع:
        {
            "street":  "456 North Park Avenue",  ← display (نظيف + اختصارات متفكّكة)
            "unit":    "200",                     ← رقم الوحدة منفصل
            "city":    "New York",
            "state":   "NY",
            "zip":     "10001",
            "compare": "456 north park avenue|200|new york|ny|10001"
        }
    """
    raw = re.sub(r"\s+", " ", str(street or "").strip())
    # نحط مسافة حوالين # عشان "#200" تتعامل زي "Suite 200"
    raw = re.sub(r"#", " # ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    # 1) نفصل رقم الوحدة (سويت/شقة/طابق)
    unit = ""
    # نقسّم على الفاصلة الأول (غالباً "123 Main St, Suite 200")
    parts = [p.strip() for p in raw.split(",")]
    street_part = parts[0] if parts else ""

    # ندوّر في باقي الأجزاء عن كلمة وحدة
    for p in parts[1:]:
        words = p.split()
        if words and words[0].lower().strip(".#") in {w.strip(".#") for w in _UNIT_WORDS}:
            # ناخد الأرقام اللي بعد كلمة الوحدة
            nums = re.findall(r"\w+", p)
            unit = nums[-1] if nums else ""

    # لو السويت جوه الشارع نفسه (مفيش فاصلة)، ندوّر عليه
    if not unit:
        tokens = street_part.split()
        for i, tok in enumerate(tokens):
            if tok.lower().strip(".#") in {w.strip(".#") for w in _UNIT_WORDS} or tok == "#":
                # اللي بعده هو رقم الوحدة
                if i + 1 < len(tokens):
                    unit = tokens[i + 1].strip("#")
                # نشيل كلمة الوحدة وما بعدها من الشارع
                street_part = " ".join(tokens[:i])
                break

    # 2) نوحّد اختصارات الشارع (كل كلمة)
    expanded = [_expand_token(t.strip(",")) for t in street_part.split()]
    street_compare = " ".join(expanded).strip()
    street_disp = _smart_title(street_compare)

    # 3) باقي الأجزاء
    city = re.sub(r"\s+", " ", str(city or "").strip())
    city_disp = city.title()
    state_disp = str(state or "").strip().upper()
    zip_digits = re.sub(r"\D", "", str(postal or ""))[:5]

    # 4) نسخة المقارنة الموحّدة
    compare = "|".join([
        street_compare,
        unit,
        city.lower(),
        state_disp.lower(),
        zip_digits,
    ])

    return {
        "street": street_disp,
        "unit": unit,
        "city": city_disp,
        "state": state_disp,
        "zip": zip_digits,
        "compare": compare,
    }


# ---------------------------------------------------------------------------
# اختبارات سريعة — شغّل الملف مباشرة عشان تشوف النتيجة
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    name_tests = [
        "  Dr. AHMED   m. hassan, MD  ",
        "ahmed hassan",
        "AHMED M. HASSAN",
        "Prof. Sara Ali",
        "Hassan, Ahmed",
        "Dr. John O'Connor",
        "Sara Al-Hassan",
        "St.John Medical",
        "   ",
        None,
    ]

    print("=" * 70)
    print("  اختبار normalize_name")
    print("=" * 70)
    for raw in name_tests:
        result = normalize_name(raw)
        print(f"المدخل  : {repr(raw)}")
        print(f"  display: {repr(result['display'])}")
        print(f"  compare: {repr(result['compare'])}")
        print("-" * 70)