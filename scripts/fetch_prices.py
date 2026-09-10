import sys
sys.path.insert(0, "/home/sandbox/klse-news-trader")
from data.prices import store_stock_universe, fetch_all_prices
from data.db import get_table_counts

store_stock_universe()
print("Stock universe stored.")

total = fetch_all_prices(delay=0.15)
print(f"\nTotal price bars: {total}")
print(get_table_counts())
