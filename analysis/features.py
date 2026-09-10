"""
analysis/features.py - Feature Engineering for KLSE News Trader

Computes technical and sentiment features from price and news data:
- Price momentum (5/10/20/50 day)
- Volatility (20-day rolling)
- Volume change (20-day)
- News sentiment score (positive vs negative keywords)
- News frequency spike
- RSI (14-day)
- Moving average crossover signals
"""

import sqlite3
import math
from datetime import datetime, timedelta
from collections import defaultdict

import os
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "klse.db")


def get_db():
    """Get database connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def compute_momentum(prices, days):
    """Compute price momentum over N days: (current - past) / past."""
    if len(prices) < days:
        return None
    current = prices[-1]
    past = prices[-(days + 1)]
    if past == 0:
        return 0.0
    return (current - past) / past


def compute_volatility(prices, days=20):
    """Compute annualized volatility from daily returns over N days."""
    if len(prices) < days + 1:
        return None
    recent = prices[-(days + 1):]
    returns = []
    for i in range(1, len(recent)):
        if recent[i - 1] != 0:
            returns.append((recent[i] - recent[i - 1]) / recent[i - 1])
    if len(returns) < 2:
        return 0.0
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1)
    # Annualize (252 trading days)
    return math.sqrt(variance) * math.sqrt(252)


def compute_volume_change(volumes, days=20):
    """Compute volume change: recent avg vs prior avg."""
    if len(volumes) < days * 2:
        return None
    recent_avg = sum(volumes[-days:]) / days
    prior_avg = sum(volumes[-(days * 2):-days]) / days
    if prior_avg == 0:
        return 0.0
    return (recent_avg - prior_avg) / prior_avg


def compute_rsi(prices, days=14):
    """Compute Relative Strength Index."""
    if len(prices) < days + 1:
        return None
    recent = prices[-(days + 1):]
    gains = []
    losses = []
    for i in range(1, len(recent)):
        change = recent[i] - recent[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    avg_gain = sum(gains) / days
    avg_loss = sum(losses) / days
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_ma_crossover(prices, short=10, long=50):
    """
    Moving average crossover signal.
    Returns: 1 (bullish crossover), -1 (bearish), 0 (no signal)
    Also returns the ratio of short MA to long MA.
    """
    if len(prices) < long:
        return 0, 0.0
    
    short_ma_current = sum(prices[-short:]) / short
    long_ma_current = sum(prices[-long:]) / long
    
    # Previous day MAs
    short_ma_prev = sum(prices[-(short + 1):-1]) / short
    long_ma_prev = sum(prices[-(long + 1):-1]) / long
    
    ratio = short_ma_current / long_ma_current if long_ma_current != 0 else 1.0
    
    # Detect crossover
    if short_ma_prev <= long_ma_prev and short_ma_current > long_ma_current:
        return 1, ratio  # Bullish crossover
    elif short_ma_prev >= long_ma_prev and short_ma_current < long_ma_current:
        return -1, ratio  # Bearish crossover
    elif short_ma_current > long_ma_current:
        return 0.5, ratio  # Above but no new crossover
    else:
        return -0.5, ratio  # Below but no new crossover


def compute_news_sentiment_score(symbol, conn, days=10):
    """
    Compute news sentiment score for a stock over the last N days.
    Returns composite sentiment score (positive - negative) normalized.
    """
    cutoff_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    query = """
        SELECT ns.composite_score
        FROM news n
        JOIN news_sentiment ns ON n.id = ns.news_id
        WHERE n.symbol = ? AND n.date >= ?
    """
    rows = conn.execute(query, (symbol, cutoff_date)).fetchall()
    
    if not rows:
        return 0.0
    
    scores = [row['composite_score'] for row in rows]
    return sum(scores) / len(scores)


def compute_news_frequency_spike(symbol, conn, days=10):
    """
    Detect if news frequency is unusually high compared to baseline.
    Returns ratio of recent frequency to baseline frequency.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    recent_start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    baseline_start = (datetime.now() - timedelta(days=days * 4)).strftime("%Y-%m-%d")
    
    # Recent count
    recent_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM news WHERE symbol = ? AND date >= ? AND date <= ?",
        (symbol, recent_start, today)
    ).fetchone()['cnt']
    
    # Baseline count (per same-length period)
    baseline_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM news WHERE symbol = ? AND date >= ? AND date < ?",
        (symbol, baseline_start, recent_start)
    ).fetchone()['cnt']
    
    if baseline_count == 0:
        return 2.0 if recent_count > 0 else 1.0
    
    # Normalize to same period length
    baseline_per_period = baseline_count / 4.0
    if baseline_per_period == 0:
        return 2.0 if recent_count > 0 else 1.0
    
    return recent_count / baseline_per_period


def compute_all_features(symbol, conn):
    """Compute all features for a given stock symbol."""
    # Get price data (last 60 days)
    rows = conn.execute(
        "SELECT date, close, volume FROM prices WHERE symbol = ? ORDER BY date ASC",
        (symbol,)
    ).fetchall()
    
    if len(rows) < 51:
        return None
    
    prices = [row['close'] for row in rows]
    volumes = [row['volume'] for row in rows]
    latest_date = rows[-1]['date']
    
    # Technical features
    momentum_5d = compute_momentum(prices, 5)
    momentum_10d = compute_momentum(prices, 10)
    momentum_20d = compute_momentum(prices, 20)
    momentum_50d = compute_momentum(prices, 50)
    volatility_20d = compute_volatility(prices, 20)
    volume_change_20d = compute_volume_change(volumes, 20)
    rsi_14d = compute_rsi(prices, 14)
    ma_signal, ma_ratio = compute_ma_crossover(prices)
    
    # Sentiment features
    news_sentiment_10d = compute_news_sentiment_score(symbol, conn, 10)
    news_frequency_spike = compute_news_frequency_spike(symbol, conn, 10)
    
    features = {
        'symbol': symbol,
        'date': latest_date,
        'momentum_5d': momentum_5d,
        'momentum_10d': momentum_10d,
        'momentum_20d': momentum_20d,
        'momentum_50d': momentum_50d,
        'volatility_20d': volatility_20d,
        'volume_change_20d': volume_change_20d,
        'news_sentiment_10d': news_sentiment_10d,
        'news_frequency_spike': news_frequency_spike,
        'rsi_14d': rsi_14d,
        'ma_crossover_signal': ma_signal,
        'ma_ratio': ma_ratio,
    }
    
    return features


def store_features(features, conn):
    """Store computed features in ml_features table."""
    if features is None:
        return
    
    conn.execute("""
        INSERT INTO ml_features 
        (symbol, date, momentum_5d, momentum_10d, momentum_20d, momentum_50d,
         volatility_20d, volume_change_20d, news_sentiment_10d, news_frequency_spike,
         rsi_14d, ma_crossover_signal)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        features['symbol'], features['date'],
        features['momentum_5d'], features['momentum_10d'],
        features['momentum_20d'], features['momentum_50d'],
        features['volatility_20d'], features['volume_change_20d'],
        features['news_sentiment_10d'], features['news_frequency_spike'],
        features['rsi_14d'], features['ma_crossover_signal']
    ))
    conn.commit()


def run_feature_engineering():
    """Run feature engineering for all stocks."""
    conn = get_db()
    symbols = [row['symbol'] for row in conn.execute("SELECT symbol FROM stocks").fetchall()]
    
    results = []
    for symbol in symbols:
        features = compute_all_features(symbol, conn)
        if features:
            store_features(features, conn)
            results.append(features)
    
    conn.close()
    return results


if __name__ == "__main__":
    results = run_feature_engineering()
    print(f"Computed features for {len(results)} stocks")
    for r in results[:5]:
        print(f"  {r['symbol']}: mom5={r['momentum_5d']:.3f}, rsi={r['rsi_14d']:.1f}, sentiment={r['news_sentiment_10d']:.3f}")
