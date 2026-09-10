"""Flask app for KLSE News Trader dashboard."""
import sqlite3
from datetime import datetime

from flask import Flask, render_template, jsonify, request, abort

from config import PORT, DEBUG, DB_PATH, get_all_symbols


app = Flask(__name__)


def get_db():
    """Get a SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def compute_svg_sparkline(values):
    """Convert a list of numeric values into SVG polyline points string."""
    if not values or len(values) < 2:
        return ""
    min_v = min(values)
    max_v = max(values)
    range_v = max_v - min_v if max_v != min_v else 1
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = i * (100 / (n - 1))
        y = 30 - ((v - min_v) / range_v * 28)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def get_all_stocks():
    """Fetch all stocks with their latest data for the dashboard."""
    conn = get_db()
    stocks = []
    for symbol in get_all_symbols():
        # Get latest price
        price_row = conn.execute(
            "SELECT close, date FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol,)
        ).fetchone()

        # Get latest score from recommendations table
        score_row = conn.execute(
            "SELECT action, confidence, date FROM recommendations WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol,)
        ).fetchone()

        # Get news count
        news_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM news WHERE matched_symbol = ?",
            (symbol,)
        ).fetchone()

        # Get latest news headline
        latest_news = conn.execute(
            "SELECT title, url, published_at FROM news WHERE matched_symbol = ? ORDER BY published_at DESC LIMIT 1",
            (symbol,)
        ).fetchall()
        latest_news_row = latest_news[0] if latest_news else None

        # Get price history for sparkline (last 30 days)
        price_history = conn.execute(
            "SELECT close, date FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 30",
            (symbol,)
        ).fetchall()
        sparkline_values = [row["close"] for row in reversed(price_history)] if price_history else []

        stock = {
            "symbol": symbol,
            "last_price": price_row["close"] if price_row else None,
            "price_updated": price_row["date"] if price_row else None,
            "score": int(score_row["confidence"] * 10) if score_row and score_row["confidence"] else None,
            "signal": score_row["action"] if score_row else "N/A",
            "score_updated": score_row["date"] if score_row else None,
            "news_count": news_count["cnt"] if news_count else 0,
            "latest_headline": latest_news_row["title"] if latest_news_row else None,
            "latest_news_url": latest_news_row["url"] if latest_news_row else None,
            "sparkline_points": compute_svg_sparkline(sparkline_values),
        }
        stocks.append(stock)
    conn.close()

    # Sort: scored stocks first (by score desc), then unscored
    stocks.sort(key=lambda s: (-(s["score"] or 0), s["symbol"]))
    return stocks


@app.route("/")
def dashboard():
    """Main dashboard page."""
    stocks = get_all_stocks()
    return render_template("index.html", stocks=stocks, now=datetime.now())


@app.route("/stock/<symbol>")
def stock_detail(symbol):
    """Individual stock detail page."""
    if symbol not in get_all_symbols():
        abort(404)

    conn = get_db()
    price_history = conn.execute(
        "SELECT close, date FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 90",
        (symbol,)
    ).fetchall()

    news_items = conn.execute(
        "SELECT title, url, source, published_at FROM news WHERE matched_symbol = ? ORDER BY published_at DESC LIMIT 20",
        (symbol,)
    ).fetchall()

    score_row = conn.execute(
        "SELECT action, confidence, reason, date FROM recommendations WHERE symbol = ? ORDER BY date DESC LIMIT 1",
        (symbol,)
    ).fetchone()
    conn.close()

    return render_template(
        "stock.html",
        symbol=symbol,
        price_history=price_history,
        news_items=news_items,
        score_row=score_row,
        chart_points=compute_chart_points(price_history),
        now=datetime.now(),
    )


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    """Trigger data refresh for all stocks."""
    return jsonify({"status": "ok", "message": "Refresh triggered", "timestamp": datetime.now().isoformat()})


@app.route("/api/analyze/<symbol>")
def api_analyze(symbol):
    """Run analysis on a single stock and return results."""
    if symbol not in get_all_symbols():
        abort(404)

    conn = get_db()
    price_history = conn.execute(
        "SELECT close, date FROM prices WHERE symbol = ? ORDER BY date DESC LIMIT 30",
        (symbol,)
    ).fetchall()

    news_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM news WHERE matched_symbol = ?",
        (symbol,)
    ).fetchone()
    conn.close()

    result = {
        "symbol": symbol,
        "price_data_points": len(price_history),
        "news_count": news_count["cnt"] if news_count else 0,
        "status": "analyzed",
        "timestamp": datetime.now().isoformat(),
    }
    return jsonify(result)


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


def compute_chart_points(price_history, width=600, height=150):
    """Compute SVG polyline points for a price chart from price history rows."""
    if not price_history or len(price_history) < 2:
        return ""
    values = [row["close"] for row in reversed(price_history)]
    min_v = min(values)
    max_v = max(values)
    range_v = max_v - min_v if max_v != min_v else 1
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = i * (width / (n - 1))
        y = (height - 5) - ((v - min_v) / range_v * (height - 10))
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


if __name__ == "__main__":
    print(f"Starting KLSE News Trader on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=DEBUG)
