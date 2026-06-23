"""
run_pipeline.py
---------------
Runs every stage of the system with a single command, in the correct order:

  1. database          → creates the tables
  2. fetch_data        → collects data from NPPES
  3. make_second_source→ builds the second source
  4. pull_quality      → checks the quality of the second source pull
  5. compare           → detects changes + makes the decision
  6. apply_changes     → applies AUTO + holds REVIEW

Just run this:
    python3 run_pipeline.py
"""

import sys
import time
from pathlib import Path

from database import create_database, get_connection, DB_PATH
import fetch_data
import make_second_source
import pull_quality
import compare
import apply_changes


class PipelineError(RuntimeError):
    """Raised when the pipeline cannot continue safely (so it never reports a false success)."""


def banner(step, title):
    print("\n" + "█" * 60)
    print(f"  Stage {step}: {title}")
    print("█" * 60)


def count_providers(db_path=DB_PATH):
    """Returns how many rows are currently in the providers table."""
    with get_connection(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM providers").fetchone()[0]


def assert_providers_loaded(db_path=DB_PATH):
    """
    Fail-safe gate after the fetch step.

    If zero providers were loaded (the API/network failed, or the search
    returned nothing), stop the whole pipeline instead of marching through the
    remaining stages and printing a misleading "finished successfully".
    Returns the provider count on success.
    """
    n = count_providers(db_path)
    if n == 0:
        raise PipelineError(
            "Fetch returned 0 provider records. Stopping pipeline instead of "
            "reporting success. Check internet/API access or run the offline demo."
        )
    return n


def main(fresh_start=True):
    start = time.time()
    print("🚀 Starting the full Pipeline")
    print("=" * 60)

    if fresh_start and Path(DB_PATH).exists():
        Path(DB_PATH).unlink()
        print("🗑️  Deleted the old database (clean start)")

    # 1) Tables
    banner(1, "Creating the tables")
    create_database()

    # 2) Collection
    banner(2, "Collecting data (NPPES)")
    fetch_data.main()

    # 2b) Fail-safe gate: never continue (and never report success) on empty data
    loaded = assert_providers_loaded()
    print(f"✅ Provider records loaded: {loaded}")

    # 3) Second source
    banner(3, "Creating the second source")
    make_second_source.main()

    # 4) Pull quality
    banner(4, "Checking pull quality")
    pull_quality.print_report("external_data")

    # 5) Comparison
    banner(5, "Comparison and change detection")
    compare.main()

    # 6) Application
    banner(6, "Applying changes")
    apply_changes.main()

    elapsed = time.time() - start
    print("\n" + "=" * 60)
    print(f"✅ The whole Pipeline finished successfully in {elapsed:.1f} seconds")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except PipelineError as e:
        # Clean, reviewer-friendly stop (no scary traceback) + non-zero exit code
        print("\n" + "=" * 60)
        print(f"❌ {e}")
        print("=" * 60)
        sys.exit(1)
