import sys
sys.path.insert(0, '/home/sandbox/klse-news-trader')
from data.db import init_db, get_table_counts
init_db()
print('DB initialized')
print(get_table_counts())
