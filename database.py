"""
database.py
-----------
Creates and manages the HealthLynked database (SQLite).

Tables:
  1. providers            → valid records (those that passed validation)
  2. providers_quarantine → rejected records (isolated with rejection reason)
  3. providers_audit_log  → log of all operations (Audit Trail)
  4. external_data        → second-source data for comparison
  5. proposed_changes     → changes proposed by the comparison engine

Usage:
    python database.py            # creates/updates the database
    python database.py --reset     # wipes and recreates from scratch (dangerous!)

Importing into other code:
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

# Schema version — bump it whenever you change the schema so you can run migrations later
SCHEMA_VERSION = 1


# =====================================================================
# Connection
# =====================================================================
@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    """
    A safe database connection as a context manager:
      - enables foreign keys (disabled by default in SQLite!)
      - enables WAL for faster and safer reads/writes
      - row_factory = Row so you can access columns by name (row["name"])
      - commits on success, rolls back on exception, and always closes
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
# Schema definition (DDL)
# =====================================================================
SCHEMA = [
    # -----------------------------------------------------------------
    # 1) Valid providers table — the official record (source of truth)
    # -----------------------------------------------------------------
    """
    CREATE TABLE IF NOT EXISTS providers (
        npi           TEXT    PRIMARY KEY
                              -- must be exactly 10 digits; GLOB matches the whole string start to end
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
    # 2) Quarantine table — rejected records
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
    # 3) Operations log table — Audit Trail
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
    # 4) External source table — second-source data for comparison
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
    # 5) Proposed changes table — output of the comparison engine
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

# Indexes for performance — all common queries look up by npi or status
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

# Trigger that updates updated_at automatically on any change to providers
TRIGGERS = [
    """
    CREATE TRIGGER IF NOT EXISTS trg_providers_updated_at
    AFTER UPDATE ON providers
    FOR EACH ROW
    -- this condition prevents infinite recursion and respects any manually set updated_at
    WHEN NEW.updated_at = OLD.updated_at
    BEGIN
        -- rowid instead of npi: works correctly even if the npi itself changed
        UPDATE providers SET updated_at = datetime('now') WHERE rowid = NEW.rowid;
    END
    """,
]


# =====================================================================
# Creation / reset
# =====================================================================
def create_database(db_path: Path = DB_PATH) -> None:
    """Creates all tables, indexes, and triggers (safe to run more than once)."""
    with get_connection(db_path) as conn:
        cur = conn.cursor()
        for ddl in SCHEMA:
            cur.execute(ddl)
        for idx in INDEXES:
            cur.execute(idx)
        for trg in TRIGGERS:
            cur.execute(trg)
        cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    print(f"✅ Database created successfully ({len(SCHEMA)} tables, schema version v{SCHEMA_VERSION})")
    print(f"📁 Location: {db_path}")


def reset_database(db_path: Path = DB_PATH) -> None:
    """Deletes the entire database file and recreates it from scratch. ⚠️ Wipes all data."""
    for suffix in ("", "-wal", "-shm"):  # delete the accompanying WAL files too
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    print("🗑️  Old database deleted.")
    create_database(db_path)


# =====================================================================
# CLI
# =====================================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="HealthLynked database management")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="wipes the database and recreates it from scratch (wipes all data!)",
    )
    args = parser.parse_args()

    if args.reset:
        confirm = input("⚠️  This will wipe all data. Type 'yes' to confirm: ").strip().lower()
        if confirm == "yes":
            reset_database()
        else:
            print("❌ Operation cancelled.")
    else:
        create_database()


if __name__ == "__main__":
    main()
