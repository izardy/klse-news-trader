# KLSE News Trader

KLSE stock analysis platform: news sentiment + price patterns + ML buy signals.

## Architecture

```
klse-news-trader/
├── README.md
├── requirements.txt
├── config.py
├── run.py                  # Flask app entry point
├── data/
│   ├── db.py               # SQLite schema + queries
│   ├── prices.py           # iTick price fetcher
│   └── news.py             # News scraper (Bursa, Edge Malaysia, etc.)
├── analysis/
│   ├── features.py         # Feature engineering
│   ├── ml_model.py         # ML pattern detection
│   └── scorer.py           # Buy signal scoring
├── templates/
│   └── index.html          # Dashboard
└── static/
    └── style.css
```

## Agents
- **DATA_DEV**: data/db.py, data/prices.py, data/news.py
- **APP_DEV**: run.py, templates/, static/, config.py
- **TRADING_AGENT**: analysis/features.py, analysis/ml_model.py, analysis/scorer.py
