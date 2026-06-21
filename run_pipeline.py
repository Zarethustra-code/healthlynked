"""
run_pipeline.py
---------------
بيشغّل كل مراحل النظام بضغطة واحدة، بالترتيب الصح:

  1. database          → يعمل الجداول
  2. fetch_data        → يجمّع البيانات من NPPES
  3. make_second_source→ يعمل المصدر التاني
  4. pull_quality      → يفحص جودة سحبة المصدر التاني
  5. compare           → يكتشف التغييرات + ياخد القرار
  6. apply_changes     → يطبّق AUTO + يحجز REVIEW

شغّل ده بس:
    python3 run_pipeline.py
"""

import time
from pathlib import Path

from database import create_database, DB_PATH
import fetch_data
import make_second_source
import pull_quality
import compare
import apply_changes


def banner(step, title):
    print("\n" + "█" * 60)
    print(f"  Stage {step}: {title}")
    print("█" * 60)


def main(fresh_start=True):
    start = time.time()
    print("🚀 بدء تشغيل الـ Pipeline الكامل")
    print("=" * 60)

    if fresh_start and Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
        print("🗑️  اتمسحت قاعدة البيانات القديمة (بداية نظيفة)")

    # 1) الجداول
    banner(1, "إنشاء الجداول")
    create_database()

    # 2) التجميع
    banner(2, "تجميع البيانات (NPPES)")
    fetch_data.main()

    # 3) المصدر التاني
    banner(3, "إنشاء المصدر التاني")
    make_second_source.main()

    # 4) جودة السحب
    banner(4, "فحص جودة السحب")
    pull_quality.print_report("external_data")

    # 5) المقارنة
    banner(5, "المقارنة وكشف التغييرات")
    compare.main()

    # 6) التطبيق
    banner(6, "تطبيق التغييرات")
    apply_changes.main()

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"✅ الـ Pipeline كله خلص بنجاح في {elapsed:.1f} ثانية")
    print("=" * 60)


if __name__ == "__main__":
    main()