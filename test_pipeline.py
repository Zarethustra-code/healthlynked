"""
test_pipeline.py
----------------
اختبار تكامل (integration test) للـ pipeline من غير إنترنت.

الفكرة:
  بدل ما نسحب من NPPES (شبكة + بطيء + مش ثابت)، بنزرع جدول providers
  بإيدينا (ده بالظبط مخرج خطوة fetch)، وبنحطّ مصدر تاني (external_data)
  بفروقات معروفة سلفًا — فنقدر نتأكد من قرار كل حقل بالظبط.

  بيغطّي: compare → apply_changes → audit_log → export_review

  ده كمان regression test للـ bugs اللي اتصلّحت:
    • CHECK بتاع audit_log كان بيرفض AUTO_UPDATED / FLAGGED_REVIEW
      (كان بيكسّر Stage 6 بالكامل).
    • تريجر updated_at لازم يضرب عند التحديث التلقائي بس.

التشغيل:
    python3 test_pipeline.py            # أو
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

# الأعمدة الكاملة اللي بنزرع بيها providers (نفس اللي fetch_data بيكتبها)
_PROVIDER_COLS = ("npi", "name", "taxonomy_code", "specialty", "is_active",
                  "phone", "street", "unit", "city", "state", "zip")

# قيم مشتركة لكل الأطباء — أي اختلاف هنحطّه عمدًا في المصدر التاني
_COMMON = dict(
    name="Dr Test", taxonomy_code="207RC0000X", specialty="Cardiovascular Disease",
    is_active=1, phone="(212) 555-1111", street="100 Main Street", unit="",
    city="New York", state="NY", zip="10001",
)

_OLD_TS = "2000-01-01 00:00:00"   # تاريخ قديم عشان نتأكد التريجر بيحدّثه


def _quiet(fn, *a, **k):
    """بيشغّل دالة وهو ساكت (بيبلع الـ print بتاعها)."""
    with redirect_stdout(io.StringIO()):
        return fn(*a, **k)


class _TempDBTest(unittest.TestCase):
    """أساس مشترك: قاعدة بيانات مؤقتة + توجيه كل الموديولات ليها."""

    # الموديولات اللي بتقرا DB_PATH كـ global وقت التشغيل
    _MODULES = (compare, apply_changes, export_review, make_second_source)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.db = tmp / "test.db"
        self.out_json = tmp / "review_data.json"

        _quiet(database.create_database, self.db)

        # نوجّه كل موديول للقاعدة المؤقتة، ونحفظ الأصل عشان نرجّعه
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

    # --- أدوات مساعدة ---
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
            # نخلّي الطوابع قديمة عشان نقدر نتأكد التريجر ضرب بعدين
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
    أربع حالات مصمّمة عشان كل قرار يطلع ثابت ومتوقّع:
      P1: تليفون اتغيّر      → ثقة عالية → AUTO_UPDATE
      P2: مدينة اتغيّرت      → ثقة متوسطة → NEEDS_REVIEW
      P3: حالة (is_active)   → المصدر مش صاحب سلطة → NEEDS_REVIEW
      P4: زي الأصل بالظبط     → مفيش تغيير
    """

    def setUp(self):
        super().setUp()
        # providers
        self._add_provider("1000000001")
        self._add_provider("1000000002")
        self._add_provider("1000000003")
        self._add_provider("1000000004")
        # المصدر التاني — كل صف بيغيّر حقل واحد بس، والباقي زيّ الأصل
        self._add_external("1000000001", phone="(212) 555-2222")     # AUTO
        self._add_external("1000000002", city="Brooklyn")            # REVIEW
        self._add_external("1000000003", is_active=0)                # REVIEW
        self._add_external("1000000004")                            # لا تغيير
        # نشغّل المرحلتين قيد الاختبار
        _quiet(compare.main)
        _quiet(apply_changes.main)

    def _changes_by_npi(self):
        with self._conn() as c:
            return {r["npi"]: r for r in c.execute(
                "SELECT * FROM proposed_changes").fetchall()}

    def test_compare_detects_exactly_the_seeded_changes(self):
        ch = self._changes_by_npi()
        self.assertEqual(set(ch), {"1000000001", "1000000002", "1000000003"},
                         "لازم 3 تغييرات بالظبط — P4 المتطابق مايتسجّلش")
        self.assertEqual(ch["1000000001"]["field"], "phone")
        self.assertEqual(ch["1000000002"]["field"], "city")
        self.assertEqual(ch["1000000003"]["field"], "is_active")

    def test_decisions_match_scoring_rules(self):
        ch = self._changes_by_npi()
        self.assertEqual(ch["1000000001"]["decision"], "AUTO_UPDATE")
        self.assertEqual(ch["1000000002"]["decision"], "NEEDS_REVIEW")
        self.assertEqual(ch["1000000003"]["decision"], "NEEDS_REVIEW")
        # أرقام الثقة المتوقّعة من المعادلة الموحّدة في confidence.py:
        #   phone (clinic=practice → صاحب سلطة): 0.80 + 0.15*(1-0.80) = 0.83 ≥ bar 0.83 → AUTO
        #   city  (صاحب سلطة كمان): 0.83 < bar 0.86 → REVIEW
        #   is_active 1→0: 0.80، وقاعدة أمان صارمة (إلغاء تفعيل) → REVIEW دايمًا
        self.assertAlmostEqual(ch["1000000001"]["confidence"], 0.83, places=2)
        self.assertAlmostEqual(ch["1000000002"]["confidence"], 0.83, places=2)
        self.assertAlmostEqual(ch["1000000003"]["confidence"], 0.80, places=2)

    def test_every_change_has_a_reason(self):
        # Explainability: مفيش قرار من غير شرح
        for r in self._changes_by_npi().values():
            self.assertTrue(r["reason"] and r["reason"].strip())

    def test_auto_update_is_written_to_providers(self):
        with self._conn() as c:
            phone = c.execute(
                "SELECT phone FROM providers WHERE npi='1000000001'").fetchone()[0]
        self.assertEqual(phone, "(212) 555-2222", "التحديث التلقائي لازم يتكتب فعلاً")

    def test_review_rows_do_not_touch_providers(self):
        with self._conn() as c:
            city = c.execute(
                "SELECT city FROM providers WHERE npi='1000000002'").fetchone()[0]
            active = c.execute(
                "SELECT is_active FROM providers WHERE npi='1000000003'").fetchone()[0]
        self.assertEqual(city, "New York", "اللي للمراجعة مايتطبّقش")
        self.assertEqual(active, 1)

    def test_proposed_changes_statuses(self):
        with self._conn() as c:
            rows = dict(c.execute(
                "SELECT status, COUNT(*) FROM proposed_changes GROUP BY status"))
        self.assertEqual(rows.get("applied"), 1)
        self.assertEqual(rows.get("pending_review"), 2)

    def test_audit_log_actions_regression(self):
        """
        الـ regression الأساسي: قبل الإصلاح كان CHECK بيرفض
        AUTO_UPDATED و FLAGGED_REVIEW فالمرحلة دي كانت بتكراش.
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
        self.assertNotEqual(auto, _OLD_TS, "التريجر لازم يحدّث updated_at للصف المتغيّر")
        self.assertEqual(review, _OLD_TS, "الصف اللي ماتغيّرش لازم يفضل بطابعه القديم")

    def test_export_review_json(self):
        _quiet(export_review.main)
        data = json.loads(self.out_json.read_text(encoding="utf-8"))
        self.assertEqual(len(data), 2, "الاتنين اللي للمراجعة بس")
        self.assertEqual({d["field"] for d in data}, {"city", "is_active"})
        # مرتّب بالثقة تنازليًا (city=0.80 قبل is_active=0.53)
        self.assertEqual([d["field"] for d in data], ["city", "is_active"])
        self.assertEqual(data[0]["name"], "Dr Test")  # اسم الطبيب اتجاب من providers


class SecondSourceSmokeTest(_TempDBTest):
    """فحص دخان لمرحلة 3: المصدر التاني بيشتغل وبيطلّع صف لكل طبيب."""

    def test_make_second_source_runs(self):
        for npi in ("1000000001", "1000000002", "1000000003"):
            self._add_provider(npi)
        make_second_source.random.seed(7)   # نتيجة قابلة للتكرار
        _quiet(make_second_source.main)
        with self._conn() as c:
            rows = c.execute(
                "SELECT source_name FROM external_data").fetchall()
        self.assertEqual(len(rows), 3, "صف لكل طبيب")
        self.assertTrue(all(r["source_name"] == "clinic_site" for r in rows))


class IndependenceScoringTest(_TempDBTest):
    """
    خطوة 7 (الاستقلالية) في المعادلة الموحّدة (confidence.py): التأكيد بيزيد
    لما مصادر *مستقلة* تتفق، والمصادر اللي من نفس العائلة مابتتعدّش مرتين.

    بنختار حقل (specialty) مفيهوش أي من المصادر دي صاحب سلطة، عشان نعزل
    تأثير الاستقلالية لوحده.
      • npi1: مصدر واحد (state_board)                       → تأكيد أقل
      • npi2: مصدرين مستقلين (state_board + practice_site)  → تأكيد أعلى
      • npi3: مصدر واحد (nppes)                              → أساس
      • npi4: مصدرين من نفس العائلة (nppes + cms)            → نفس الأساس (مش بيتعدّوا مرتين)
    """

    def setUp(self):
        super().setUp()
        for npi in ("1000000001", "1000000002", "1000000003", "1000000004"):
            self._add_provider(npi)
        # npi1: مصدر مستقل واحد
        self._add_external("1000000001", source_name="state_board",
                           specialty="Internal Medicine")
        # npi2: مصدرين مستقلين (عائلتين مختلفتين)
        self._add_external("1000000002", source_name="state_board",
                           specialty="Internal Medicine")
        self._add_external("1000000002", source_name="practice_site",
                           specialty="Internal Medicine")
        # npi3: مصدر واحد
        self._add_external("1000000003", source_name="nppes",
                           specialty="Internal Medicine")
        # npi4: مصدرين من نفس العائلة (cms بياخد من nppes)
        self._add_external("1000000004", source_name="nppes",
                           specialty="Internal Medicine")
        self._add_external("1000000004", source_name="cms",
                           specialty="Internal Medicine")
        _quiet(compare.main)

    def _rows(self):
        with self._conn() as c:
            return {r["npi"]: r for r in c.execute(
                "SELECT * FROM proposed_changes").fetchall()}

    def test_independent_corroboration_raises_confidence(self):
        rows = self._rows()
        one  = rows["1000000001"]["confidence"]   # state_board فقط
        two  = rows["1000000002"]["confidence"]   # state_board + practice_site
        self.assertGreater(two, one,
                           "مصدرين مستقلين متفقين لازم يدّوا ثقة أعلى من مصدر واحد")

    def test_same_family_sources_do_not_double_count(self):
        rows = self._rows()
        single = rows["1000000003"]["confidence"]   # nppes فقط
        family = rows["1000000004"]["confidence"]   # nppes + cms (نفس العائلة)
        self.assertAlmostEqual(family, single, places=2,
                               msg="cms مش مصدر مستقل عن nppes فمايزوّدش التأكيد")

    def test_every_change_has_an_explanation(self):
        # Explainability: كل صف لازم يكون ليه شرح واضح
        for r in self._rows().values():
            self.assertTrue(r["reason"] and r["reason"].strip())


class EmptySourceGuardTest(_TempDBTest):
    """
    حماية من فقدان البيانات: المصدر التاني الفاضي مايكتبش فوق قيمة سليمة في الأصل،
    لكن لو الأصل ناقص الحقل ده — المصدر يقدر يملاه (enrichment).
    """

    def test_empty_source_does_not_overwrite_present_value(self):
        self._add_provider("1000000001")            # phone = (212) 555-1111
        self._add_external("1000000001", phone="")  # المصدر التاني مفيهوش تليفون
        _quiet(compare.main)
        _quiet(apply_changes.main)
        with self._conn() as c:
            n = c.execute(
                "SELECT COUNT(*) FROM proposed_changes WHERE field='phone'").fetchone()[0]
            phone = c.execute(
                "SELECT phone FROM providers WHERE npi='1000000001'").fetchone()[0]
        self.assertEqual(n, 0, "مايتقترحش تغيير لما المصدر فاضي والأصل فيه قيمة")
        self.assertEqual(phone, "(212) 555-1111", "البيانات السليمة لازم تفضل زي ما هي")

    def test_empty_base_can_still_be_filled_from_source(self):
        self._add_provider("1000000001", phone="")               # الأصل ناقص التليفون
        self._add_external("1000000001", phone="(212) 555-7777")  # المصدر فيه قيمة
        _quiet(compare.main)
        with self._conn() as c:
            row = c.execute(
                "SELECT new_value FROM proposed_changes WHERE field='phone'").fetchone()
        self.assertIsNotNone(row, "ملء حقل ناقص في الأصل لازم يتقترح عادي")
        self.assertEqual(row[0], "(212) 555-7777")


if __name__ == "__main__":
    unittest.main(verbosity=2)
