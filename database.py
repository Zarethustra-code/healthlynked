"""
database.py
-----------
بينشئ ويدير قاعدة بيانات HealthLynked (SQLite).

الجداول:
  1. providers            → السجلات السليمة (اللي عدّت التحقق)
  2. providers_quarantine → السجلات المرفوضة (معزولة مع سبب الرفض)
  3. providers_audit_log  → سجل كل العمليات (Audit Trail)
  4. external_data        → بيانات المصدر التاني للمقارنة
  5. proposed_changes     → التغييرات المقترحة من محرّك المقارنة

التشغيل:
    python database.py            # ينشئ/يحدّث القاعدة
    python database.py --reset     # يمسح ويعيد الإنشاء من الصفر (خطر!)

الاستيراد في كود تاني:
    from database import get_connection, create_database
    with get_connection() as conn:
        conn.execute("SELECT * FROM providers")
"""

from __future__ import annotations

import argparse
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_PATH = Path(__file__).parent / "healthlynked.db"

# نسخة المخطّط — زوّدها لو غيّرت الـ schema عشان تعرف تعمل migrations بعدين
SCHEMA_VERSION = 1


# =====================================================================
# الاتصال
# =====================================================================
@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """
    اتصال آمن بقاعدة البيانات كـ context manager:
      - بيفعّل foreign keys (مطفية افتراضيًا في SQLite!)
      - بيفعّل WAL عشان قراءة/كتابة أسرع وأأمن
      - row_factory = Row عشان توصل للأعمدة بالاسم (row["name"])
      - بيعمل commit لو نجح، rollback لو حصل استثناء، ويقفل دايمًا
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =====================================================================
# تعريف المخطّط (DDL)
# =====================================================================
SCHEMA = [
    # -----------------------------------------------------------------
    # 1) جدول الأطباء السليمين — السجل الرسمي (source of truth)
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS providers (
        npi           TEXT    PRIMARY KEY
                              -- لازم 10 أرقام بالظبط؛ GLOB متطابق من أول النص لآخره
                              CHECK (npi GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
        name          TEXT    NOT NULL CHECK (length(trim(name)) > 0),
        taxonomy_code TEXT,
        specialty     TEXT,
        is_active     INTEGER NOT NULL DEFAULT 1
                              CHECK (is_active IN (0, 1)),
        phone         TEXT,
        street        TEXT,
        unit          TEXT,
        city          TEXT,
        state         TEXT    CHECK (state IS NULL OR state = '' OR length(state) = 2),
        zip           TEXT    CHECK (zip IS NULL OR zip = '' OR zip GLOB '[0-9][0-9][0-9][0-9][0-9]'),
        created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # -----------------------------------------------------------------
    # 2) جدول العزل — السجلات المرفوضة
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS providers_quarantine (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        npi              TEXT,
        name             TEXT,
        raw_record       TEXT,
        rejection_reason TEXT    NOT NULL,
        status           TEXT    NOT NULL DEFAULT 'Rejected'
                                 CHECK (status IN ('Rejected', 'Pending Review', 'Resolved')),
        created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # -----------------------------------------------------------------
    # 3) جدول سجل العمليات — Audit Trail
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS providers_audit_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        npi        TEXT,
        action     TEXT    NOT NULL
                           CHECK (action IN ('ACCEPTED', 'QUARANTINED', 'UPDATED',
                                             'DEACTIVATED', 'AUTO_UPDATED', 'FLAGGED_REVIEW')),
        detail     TEXT,
        timestamp  TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # -----------------------------------------------------------------
    # 4) جدول المصدر الخارجي — بيانات المصدر التاني للمقارنة
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS external_data (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        npi          TEXT    NOT NULL,
        source_name  TEXT    NOT NULL,
        phone        TEXT,
        street       TEXT,
        unit         TEXT,
        city         TEXT,
        state        TEXT,
        zip          TEXT,
        specialty    TEXT,
        is_active    INTEGER CHECK (is_active IS NULL OR is_active IN (0, 1)),
        fetched_at   TEXT    NOT NULL DEFAULT (datetime('now'))
    )
    """,

    # -----------------------------------------------------------------
    # 5) جدول التغييرات المقترحة — مخرجات محرّك المقارنة
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS proposed_changes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        npi         TEXT    NOT NULL,
        field       TEXT    NOT NULL,
        old_value   TEXT,
        new_value   TEXT,
        source      TEXT,
        confidence  REAL    CHECK (confidence IS NULL OR (confidence BETWEEN 0 AND 1)),
        decision    TEXT    CHECK (decision IS NULL OR decision IN ('AUTO_UPDATE', 'NEEDS_REVIEW')),
        reason      TEXT,
        status      TEXT    NOT NULL DEFAULT 'new'
                            CHECK (status IN ('new', 'applied', 'pending_review', 'rejected')),
        created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (npi) REFERENCES providers (npi) ON DELETE CASCADE
    )
    """,
]

# فهارس عشان الأداء — كل الاستعلامات الشائعة بتدور بـ npi أو status
INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_audit_npi          ON providers_audit_log (npi)",
    "CREATE INDEX IF NOT EXISTS idx_audit_action       ON providers_audit_log (action)",
    "CREATE INDEX IF NOT EXISTS idx_quarantine_status  ON providers_quarantine (status)",
    "CREATE INDEX IF NOT EXISTS idx_external_npi        ON external_data (npi)",
    "CREATE INDEX IF NOT EXISTS idx_external_source     ON external_data (source_name)",
    "CREATE INDEX IF NOT EXISTS idx_proposed_npi        ON proposed_changes (npi)",
    "CREATE INDEX IF NOT EXISTS idx_proposed_status     ON proposed_changes (status)",
    "CREATE INDEX IF NOT EXISTS idx_providers_active    ON providers (is_active)",
]

# تريجر يحدّث updated_at تلقائيًا عند أي تعديل في providers
TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_providers_updated_at
    AFTER UPDATE ON providers
    FOR EACH ROW
    -- الشرط ده بيمنع التكرار اللانهائي ويحترم أي updated_at اتحطّ يدوي
    WHEN NEW.updated_at = OLD.updated_at
    BEGIN
        -- rowid بدل npi: يشتغل صح حتى لو الـ npi نفسه اتغيّر
        UPDATE providers SET updated_at = datetime('now') WHERE rowid = NEW.rowid;
    END
    """,
]


# =====================================================================
# الإنشاء / إعادة الضبط
# =====================================================================
def create_database(db_path: Path = DB_PATH) -> None:
    """ينشئ كل الجداول والفهارس والتريجرات (آمن للتشغيل أكتر من مرة)."""
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        for ddl in SCHEMA:
            cur.execute(ddl)
        for idx in INDEXES:
            cur.execute(idx)
        for trg in TRIGGERS:
            cur.execute(trg)
        cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    print(f"✅ قاعدة البيانات اتنشأت بنجاح ({len(SCHEMA)} جداول، نسخة المخطّط v{SCHEMA_VERSION})")
    print(f"📁 المكان: {db_path}")


def reset_database(db_path: Path = DB_PATH) -> None:
    """يمسح ملف القاعدة بالكامل ويعيد إنشاءه من الصفر. ⚠️ بيمسح كل البيانات."""
    for suffix in ("", "-wal", "-shm"):  # امسح ملفات WAL المصاحبة كمان
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    print("🗑️  اتمسحت القاعدة القديمة.")
    create_database(db_path)


# =====================================================================
# CLI
# =====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="إدارة قاعدة بيانات HealthLynked")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="يمسح القاعدة ويعيد إنشاءها من الصفر (بيمسح كل البيانات!)",
    )
    args = parser.parse_args()

    if args.reset:
        confirm = input("⚠️  ده هيمسح كل البيانات. اكتب 'yes' عشان تأكّد: ").strip().lower()
        if confirm == "yes":
            reset_database()
        else:
            print("❌ اتلغت العملية.")
    else:
        create_database()


if __name__ == "__main__":
    main()
