"""
analysis/scorer.py - Final Buy Scoring for KLSE News Trader

Combines ML output with value metrics:
- Price near 52-week low = cheap (value opportunity)
- High news sentiment + rising price = momentum signal
- ML anomaly score + cluster quality

Output: BUY/WATCH/AVOID with entry/stop/target levels.
"""

import sqlite3
import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict

import os
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "klse.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_fundamental_score(symbol, conn):
    """Get fundamental score from annual report data (0-100).
    
    Combines value, quality, growth, and dividend metrics.
    Returns (score_dict, fundamentals_row) or (None, None) if no data.
    """
    fund = conn.execute(
        "SELECT * FROM company_fundamentals WHERE symbol = ? ORDER BY year DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    
    if not fund:
        return None, None
    
    score = {}
    
    # Value scoring (30% weight)
    value_score = 50  # neutral base
    if fund['pe_ratio']:
        if fund['pe_ratio'] < 10:
            value_score += 25
        elif fund['pe_ratio'] < 15:
            value_score += 15
        elif fund['pe_ratio'] > 30:
            value_score -= 20
    if fund['pb_ratio']:
        if fund['pb_ratio'] < 1:
            value_score += 15
        elif fund['pb_ratio'] > 3:
            value_score -= 10
    if fund['debt_to_equity']:
        if fund['debt_to_equity'] < 0.5:
            value_score += 10
        elif fund['debt_to_equity'] > 1.5:
            value_score -= 15
    score['value'] = max(0, min(100, value_score))
    
    # Quality scoring (30% weight)
    quality_score = 50
    if fund['roe']:
        if fund['roe'] > 0.15:
            quality_score += 25
        elif fund['roe'] > 0.10:
            quality_score += 15
        elif fund['roe'] < 0.05:
            quality_score -= 15
    if fund['roa']:
        if fund['roa'] > 0.08:
            quality_score += 15
        elif fund['roa'] < 0.03:
            quality_score -= 10
    if fund['current_ratio']:
        if fund['current_ratio'] > 2:
            quality_score += 10
        elif fund['current_ratio'] < 1:
            quality_score -= 10
    if fund['interest_coverage']:
        if fund['interest_coverage'] > 5:
            quality_score += 10
        elif fund['interest_coverage'] < 2:
            quality_score -= 15
    score['quality'] = max(0, min(100, quality_score))
    
    # Growth scoring (20% weight)
    growth_score = 50
    # Get previous year for comparison
    prev = conn.execute(
        "SELECT * FROM company_fundamentals WHERE symbol = ? AND year < ? ORDER BY year DESC LIMIT 1",
        (symbol, fund['year'])
    ).fetchone()
    if prev:
        if fund['revenue'] and prev['revenue'] and prev['revenue'] > 0:
            rev_growth = (fund['revenue'] - prev['revenue']) / prev['revenue']
            if rev_growth > 0.2:
                growth_score += 25
            elif rev_growth > 0.1:
                growth_score += 15
            elif rev_growth < -0.1:
                growth_score -= 20
        if fund['net_profit'] and prev['net_profit'] and prev['net_profit'] > 0:
            profit_growth = (fund['net_profit'] - prev['net_profit']) / prev['net_profit']
            if profit_growth > 0.2:
                growth_score += 25
            elif profit_growth > 0.1:
                growth_score += 15
            elif profit_growth < -0.1:
                growth_score -= 20
    score['growth'] = max(0, min(100, growth_score))
    
    # Dividend scoring (20% weight)
    div_score = 50
    if fund['dividend_yield']:
        if fund['dividend_yield'] > 0.05:
            div_score += 25
        elif fund['dividend_yield'] > 0.03:
            div_score += 15
        elif fund['dividend_yield'] < 0.01:
            div_score -= 10
    if fund['payout_ratio']:
        if 0.3 < fund['payout_ratio'] < 0.7:
            div_score += 15  # sustainable payout
        elif fund['payout_ratio'] > 0.9:
            div_score -= 10  # unsustainable
    if fund['dividend_per_share'] and prev and prev['dividend_per_share']:
        if fund['dividend_per_share'] > prev['dividend_per_share']:
            div_score += 10  # growing dividend
    score['dividend'] = max(0, min(100, div_score))
    
    # Composite
    composite = (
        score['value'] * 0.30 +
        score['quality'] * 0.30 +
        score['growth'] * 0.20 +
        score['dividend'] * 0.20
    )
    score['composite'] = round(composite, 1)

    return score, dict(fund)


def get_52week_high_low(symbol, conn):
    """Get 52-week high and low prices."""
    one_year_ago = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT MIN(low) as low_52w, MAX(high) as high_52w FROM prices WHERE symbol = ? AND date >= ?",
        (symbol, one_year_ago)
    ).fetchone()
    return rows["low_52w"], rows["high_52w"]

    



def get_current_price(symbol, conn):
    """Get latest closing price."""
    row = conn.execute(
        "SELECT close FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    return row['close'] if row else None


def get_avg_price(symbol, conn, days=20):
    """Get average closing price over N days."""
    rows = conn.execute(
        "SELECT close FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT ?",
        (symbol, days)
    ).fetchall()
    if not rows:
        return None
    return sum(r['close'] for r in rows) / len(rows)


def compute_value_score(current_price, low_52w, high_52w):
    """
    Value score: how close to 52-week low (cheap = potential value).
    Returns 0-10 where 10 = very cheap (near 52w low).
    """
    if high_52w == low_52w or high_52w is None or low_52w is None:
        return 5.0
    position = (current_price - low_52w) / (high_52w - low_52w)
    # Invert: near low = high value score
    value_score = (1 - position) * 10
    return max(0, min(10, value_score))


def compute_momentum_score(ml_features):
    """
    Momentum score: high news sentiment + rising price.
    Returns 0-10.
    """
    score = 5.0
    
    # Price momentum
    mom_5d = ml_features.get('momentum_5d', 0) or 0
    mom_20d = ml_features.get('momentum_20d', 0) or 0
    score += mom_5d * 20  # scale
    score += mom_20d * 10
    
    # News sentiment
    sentiment = ml_features.get('news_sentiment_10d', 0) or 0
    score += sentiment * 2
    
    # Volume confirmation
    vol_change = ml_features.get('volume_change_20d', 0) or 0
    if vol_change > 0.3 and mom_5d > 0:
        score += 1.0
    
    # RSI not overbought
    rsi = ml_features.get('rsi_14d', 50) or 50
    if rsi > 75:
        score -= 2.0
    elif rsi < 30:
        score += 1.0  # oversold bounce potential
    
    return max(0, min(10, score))


def compute_entry_stop_target(current_price, volatility, signal):
    """
    Compute entry, stop-loss, and target prices.
    Uses volatility for position sizing.
    """
    if current_price is None or volatility is None:
        return None, None, None
    
    if signal == "BUY":
        entry = current_price
        # Stop loss: 2x daily volatility below entry
        daily_vol = volatility / np.sqrt(252)
        stop_loss = round(entry * (1 - 2 * daily_vol), 3)
        # Target: 3x daily volatility above entry
        target = round(entry * (1 + 3 * daily_vol), 3)
    elif signal == "WATCH":
        entry = current_price
        daily_vol = volatility / np.sqrt(252)
        stop_loss = round(entry * (1 - 1.5 * daily_vol), 3)
        target = round(entry * (1 + 2 * daily_vol), 3)
    else:  # AVOID
        entry = current_price
        stop_loss = None
        target = None
    
    return entry, stop_loss, target


def determine_signal(final_score, value_score, momentum_score, ml_score):
    """
    Determine BUY/WATCH/AVOID signal based on combined scores.
    """
    if final_score >= 7.0 and momentum_score >= 6.0:
        return "BUY"
    elif final_score >= 5.0 or (value_score >= 7.0 and momentum_score >= 4.0):
        return "WATCH"
    else:
        return "AVOID"


def generate_reason(signal, value_score, momentum_score, ml_score, ml_features):
    """Generate human-readable reason for the recommendation."""
    reasons = []
    
    if signal == "BUY":
        if momentum_score >= 7:
            reasons.append("Strong momentum")
        if value_score >= 6:
            reasons.append("Attractive value")
        if ml_features.get('news_sentiment_10d', 0) and ml_features['news_sentiment_10d'] > 0.3:
            reasons.append("Positive news sentiment")
        if ml_features.get('ma_crossover_signal', 0) and ml_features['ma_crossover_signal'] >= 0.5:
            reasons.append("Bullish MA crossover")
    elif signal == "WATCH":
        if value_score >= 7:
            reasons.append("Near 52-week low - potential value")
        if momentum_score >= 5:
            reasons.append("Building momentum")
        if ml_features.get('news_frequency_spike', 1) and ml_features['news_frequency_spike'] > 1.5:
            reasons.append("Unusual news activity")
    else:
        if momentum_score < 4:
            reasons.append("Weak momentum")
        if value_score < 4:
            reasons.append("Not attractive value")
        if ml_features.get('rsi_14d', 50) and ml_features['rsi_14d'] > 70:
            reasons.append("Overbought")
    
    return "; ".join(reasons) if reasons else "Mixed signals"


def run_scoring(ml_results):
    """
    Run final scoring combining ML output with value metrics.
    
    Args:
        ml_results: list of dicts from ml_model.run_ml_analysis()
    
    Returns:
        list of recommendation dicts
    """
    conn = get_db()
    recommendations = []
    
    for result in ml_results:
        symbol = result['symbol']
        ml_score = result['ml_score']
        features = result['features']
        
        # Get price data
        current_price = get_current_price(symbol, conn)
        low_52w, high_52w = get_52week_high_low(symbol, conn)
        
        if current_price is None:
            continue
        
        # Compute component scores
        value_score = compute_value_score(current_price, low_52w, high_52w)
        momentum_score = compute_momentum_score(features)
        
        # Combined score (weighted average)
        final_score = round(
            ml_score * 0.3 +
            value_score * 0.3 +
            momentum_score * 0.4,
            1
        )
        
        # Determine signal
        signal = determine_signal(final_score, value_score, momentum_score, ml_score)
        
        # Compute entry/stop/target
        volatility = features.get('volatility_20d', 0.2) or 0.2
        entry, stop_loss, target = compute_entry_stop_target(current_price, volatility, signal)
        
        # Generate reason
        reason = generate_reason(signal, value_score, momentum_score, ml_score, features)
        
        recommendations.append({
            'symbol': symbol,
            'date': datetime.now().strftime("%Y-%m-%d"),
            'score': final_score,
            'signal': signal,
            'entry_price': entry,
            'stop_loss': stop_loss,
            'target_price': target,
            'reason': reason,
            'ml_score': ml_score,
            'value_score': round(value_score, 1),
            'momentum_score': round(momentum_score, 1),
        })
    
    conn.close()
    return recommendations


def store_recommendations(recommendations, conn):
    """Store recommendations in the database."""
    today = datetime.now().strftime("%Y-%m-%d")
    
    # Clear today's existing recommendations
    conn.execute("DELETE FROM recommendations WHERE date = ?", (today,))
    
    for rec in recommendations:
        conn.execute("""
            INSERT INTO recommendations 
            (symbol, date, score, signal, entry_price, stop_loss, target_price, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            rec['symbol'], rec['date'], rec['score'], rec['signal'],
            rec['entry_price'], rec['stop_loss'], rec['target_price'],
            rec['reason']
        ))
    
    conn.commit()


def run_full_pipeline():
    """Run the complete analysis pipeline."""
    from analysis.features import run_feature_engineering
    from analysis.ml_model import run_ml_analysis
    
    # Step 1: Feature engineering
    print("Step 1: Computing features...")
    features = run_feature_engineering()
    print(f"  Computed features for {len(features)} stocks")
    
    # Step 2: ML analysis
    print("Step 2: Running ML analysis...")
    ml_results = run_ml_analysis()
    print(f"  ML analysis complete for {len(ml_results)} stocks")
    
    # Step 3: Final scoring
    print("Step 3: Computing final scores...")
    recommendations = run_scoring(ml_results)
    
    # Store recommendations
    conn = get_db()
    store_recommendations(recommendations, conn)
    conn.close()
    
    print(f"  Generated {len(recommendations)} recommendations")
    
    # Sort by score
    recommendations.sort(key=lambda x: x['score'], reverse=True)
    
    return recommendations


if __name__ == "__main__":
    recs = run_full_pipeline()
    print("\n=== TOP RECOMMENDATIONS ===")
    for rec in recs[:10]:
        print(f"  {rec['symbol']}: {rec['signal']} (score={rec['score']}) "
              f"entry={rec['entry_price']} stop={rec['stop_loss']} target={rec['target_price']}")
        print(f"    Reason: {rec['reason']}")
