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
    main()
