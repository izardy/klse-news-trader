import sys
sys.path.insert(0, "/home/sandbox/klse-news-trader")
from data.db import init_db, get_table_counts
from data.macro import run_full_macro_backfill

init_db()
print("Initial state:", get_table_counts())
print()

counts = run_full_macro_backfill()
print("\nFinal state:", get_table_counts())
