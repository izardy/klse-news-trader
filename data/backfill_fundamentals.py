"""
Backfill script: runs the full annual report pipeline.
1. Download PDFs from Bursa Malaysia (via annual_reports.py)
2. Process them with Bedrock extraction (via process_annual_reports.py)
3. Verify results
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.db import init_db, get_table_counts
from data.annual_reports import fetch_all_annual_reports
from data.process_annual_reports import process_all_reports


def main():
    print("=" * 60)
    print("KLSE Annual Report - Full Backfill Pipeline")
    print("=" * 60)

    # Init DB
    init_db()
    print("\nInitial state:")
    counts = get_table_counts()
    for t, c in counts.items():
        print(f"  {t}: {c}")

    # Step 1: Download PDFs
    print("\n" + "=" * 60)
    print("STEP 1: Downloading annual report PDFs from Bursa Malaysia")
    print("=" * 60)
    reports_found = fetch_all_annual_reports(download=True, delay=1.0)
    print(f"\nTotal reports found/downloaded: {reports_found}")

    # Step 2: Process PDFs
    print("\n" + "=" * 60)
    print("STEP 2: Processing PDFs with AWS Bedrock")
    print("=" * 60)
    processed = process_all_reports(delay=1.0)
    print(f"\nTotal reports processed: {processed}")

    # Final state
    print("\n" + "=" * 60)
    print("FINAL STATE")
    print("=" * 60)
    counts = get_table_counts()
    for t, c in counts.items():
        print(f"  {t}: {c}")


if __name__ == "__main__":
    main()
