import sys
sys.path.insert(0, "/home/sandbox/klse-news-trader")
from data.news import fetch_all_news
from data.db import get_table_counts

total = fetch_all_news()
print(f"\nTotal news articles: {total}")
print(get_table_counts())
